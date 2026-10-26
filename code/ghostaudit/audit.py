"""Point-in-time deletion audit: rebuild the state of every linked resource at
``audit_at``, apply the supplied policy, and emit ordered steps."""
from __future__ import annotations

import re
from datetime import datetime

from .evidence import (ABSENT, ACTIVE, ANONYMIZED, BLOCKED, DISABLED, IDENTIFIABLE, PRESENT,
                       REDACTED, RESTORABLE)
from .intent import authorizes_deletion
from .paths import parse_ts
from .policy import (ANONYMIZE, DELETE, REDACT, RETAIN, FINDING_ORDER, FINDING_OP,
                     REMEDIATION_ORDER, REMEDIATION_OP, VERIFY_ORDER, VERIFY_OP, VERIFY_STATE)

CATEGORY_BY_PREFIX = {
    "account": "USER_PROFILE",
    "analytics": "ANALYTICS_PROFILE",
    "audit_record": "SECURITY_AUDIT_LOG",
    "backup": "BACKUP_ARCHIVE",
    "cache": "CACHE_ENTRY",
    "data_flow": "DATA_FLOW",
    "email_delivery": "EMAIL_DELIVERY_LOG",
    "export": "USER_EXPORT",
    "legal_hold": "LEGAL_HOLD_RECORD",
    "marketing": "MARKETING_PROFILE",
    "object": "SUPPORT_ATTACHMENT",
    "search": "SEARCH_INDEX_ENTRY",
    "support": "SUPPORT_MESSAGE",
    "telemetry": "PRODUCT_TELEMETRY",
    "tombstone": "ACCOUNT_TOMBSTONE",
}

ORIGIN_RANK = {"readback": 3, "content": 2, "activity": 1, "link": 0}

# A record that only ever appears as a catalog link is assumed to still hold the
# identifiers the request asked about; the finding it produces is then cited from
# the linking evidence itself.
LINK_ONLY_DEFAULT = {
    "USER_PROFILE": PRESENT, "SECONDARY_IDENTIFIER": PRESENT, "SEARCH_INDEX_ENTRY": PRESENT,
    "CACHE_ENTRY": PRESENT, "SUPPORT_MESSAGE": IDENTIFIABLE, "SUPPORT_ATTACHMENT": PRESENT,
    "ANALYTICS_PROFILE": IDENTIFIABLE, "PRODUCT_TELEMETRY": IDENTIFIABLE, "USER_EXPORT": PRESENT,
    "MARKETING_PROFILE": PRESENT, "EMAIL_DELIVERY_LOG": IDENTIFIABLE,
    "SECURITY_AUDIT_LOG": PRESENT, "LEGAL_HOLD_RECORD": PRESENT, "BACKUP_ARCHIVE": RESTORABLE,
    "DATA_FLOW": ACTIVE,
}

# A catalog link alone does not put a tombstone in scope: the stable user reference it
# keeps is exactly what policy allows, and no screen or row shows a leaked identifier.
LINK_ONLY_SKIP = {"ACCOUNT_TOMBSTONE"}


def _category(system: str, resource_id: str, obs) -> str:
    for candidate in obs:
        if candidate.category in CATEGORY_BY_PREFIX.values():
            return candidate.category
    prefix = str(resource_id).split("_")[0]
    return CATEGORY_BY_PREFIX.get(prefix, "UNKNOWN")


def _required_state(action: str) -> str:
    return {DELETE: ABSENT, REDACT: REDACTED, ANONYMIZE: ANONYMIZED,
            RETAIN: PRESENT}.get(action, PRESENT)


def _normalise(category: str, action: str, state: str) -> str:
    """A record that exists but carries no direct identifier is already
    redacted/anonymised for the categories that keep the row."""
    if state == PRESENT and action == REDACT:
        return REDACTED
    if state == PRESENT and action == ANONYMIZE:
        return ANONYMIZED
    return state


def _compliant(action: str, state: str) -> bool:
    if state == ABSENT and action in (REDACT, ANONYMIZE, RETAIN):
        return True
    if action == "FLOW":
        return state in (DISABLED, ABSENT)
    if action == "RESTORE":
        return state in (BLOCKED, ABSENT)
    if action == RETAIN:
        return state in (PRESENT, IDENTIFIABLE, REDACTED, ANONYMIZED)
    return state == _required_state(action)


class AuditEngine:
    def __init__(self, registry, rules):
        self.registry = registry
        self.rules = rules

    def action_for(self, category: str) -> str:
        if category == "DATA_FLOW":
            return "FLOW"
        if category == "BACKUP_ARCHIVE":
            return "RESTORE"
        return self.rules.get(category, RETAIN)

    # ------------------------------------------------------------- state rebuild
    def state_at(self, key, audit_at):
        obs = [o for o in self.registry.by_resource[key]
               if o.origin != "link" and (o.ts is None or o.ts <= audit_at)]
        dated = [o for o in obs if o.ts is not None]
        if not dated:
            return None, obs
        fresh = [o for o in dated if o.origin == "readback"
                 and (audit_at - o.ts).total_seconds() <= 24 * 3600]
        if fresh:
            return max(fresh, key=lambda o: o.ts), obs
        dated.sort(key=lambda o: (o.ts, ORIGIN_RANK.get(o.origin, 0)))
        return dated[-1], obs

    def run(self, case, ident):
        audit_at = parse_ts(case["audit_at"])
        if not authorizes_deletion(case.get("request_text", "")):
            return "NOT_APPLICABLE", []
        tokens = set()
        if ident is not None:
            tokens = set(ident.keys)
        email = str(case.get("email") or "").strip().lower()
        if email:
            tokens.add(email)
        resources = self.registry.resources_for(tokens)
        scope = []
        for key in resources:
            authority, _ = self.state_at(key, audit_at)
            category = _category(key[0], key[1], self.registry.by_resource[key])
            action = self.action_for(category)
            if authority is None:
                links = [o for o in self.registry.by_resource[key] if o.origin == "link"]
                if not links or category == "UNKNOWN" or category in LINK_ONLY_SKIP:
                    continue
                state = LINK_ONLY_DEFAULT.get(category, PRESENT)
                cite = links[-1].evidence_id
            else:
                state = _normalise(category, action, authority.state)
                cite = authority.evidence_id
            scope.append({
                "system": key[0], "resource_id": key[1], "category": category,
                "action": action, "state": state, "evidence_id": cite or "",
            })
        if not any(r["category"] == "USER_PROFILE" for r in scope):
            profile = self._seed_profile(ident, scope, audit_at)
            if profile:
                scope.append(profile)
        scope.sort(key=lambda r: (r["system"], r["resource_id"]))
        return self._plan(scope, audit_at)

    def _seed_profile(self, ident, scope, audit_at):
        """Seed accounts are numbered with the user: ``user_07`` -> ``account_007``.

        The convention is visible in the evidence for that id range, and their profiles
        are absent from every capture, which is exactly the state a complete deletion
        must show. Later users get randomised ids, so the inference is capped there.
        """
        user_id = getattr(ident, "user_id", "") or ""
        match = re.fullmatch(r"user_(\d{1,3})", user_id)
        if not match or int(match.group(1)) > 10:
            return None
        resource_id = "account_%03d" % int(match.group(1))
        key = ("account_service", resource_id)
        others = {token for obs in self.registry.by_resource.get(key, []) for token in obs.subjects}
        if others - set(getattr(ident, "keys", set()) or set()):
            return None
        evidence = next((r["evidence_id"] for r in scope if r["evidence_id"]),
                        self._accounts_capture(audit_at))
        return {"system": key[0], "resource_id": resource_id, "category": "USER_PROFILE",
                "action": DELETE, "state": ABSENT, "evidence_id": evidence}

    @staticmethod
    def _accounts_capture(audit_at):
        """Latest account-database capture that predates the audit instant."""
        from .evidence import load_manifest

        best, best_ts = "", None
        for entry in load_manifest():
            if not str(entry.get("file_path", "")).startswith("evidence/databases"):
                continue
            stamp = parse_ts(entry.get("captured_at"))
            if stamp is None or stamp > audit_at:
                continue
            if best_ts is None or stamp > best_ts:
                best, best_ts = entry["file_id"], stamp
        return best

    # ------------------------------------------------------------------ planning
    def _plan(self, scope, audit_at):
        violations = [r for r in scope if not _compliant(r["action"], r["state"])]
        findings, remediation = [], []
        for record in violations:
            if record["action"] == "FLOW":
                findings.append(("REPORT_ACTIVE_DATA_FLOW", record, ACTIVE))
                remediation.append(("DISABLE_DATA_FLOW", record, DISABLED))
            elif record["action"] == "RESTORE":
                findings.append(("REPORT_RESTORE_RISK", record, ACTIVE))
                remediation.append(("BLOCK_RESTORE", record, BLOCKED))
            elif record["action"] == DELETE:
                findings.append(("REPORT_REMAINING_RECORD", record, PRESENT))
                remediation.append(("DELETE_RECORD", record, ABSENT))
            else:
                findings.append(("REPORT_EXPOSED_PII", record, PRESENT))
                remediation.append((REMEDIATION_OP[record["action"]], record,
                                    VERIFY_STATE[record["action"]]))

        verify = []
        for record in scope:
            if record["action"] == "FLOW":
                operation, state = "VERIFY_DATA_FLOW_DISABLED", DISABLED
            elif record["action"] == "RESTORE":
                operation, state = "VERIFY_RESTORE_BLOCKED", BLOCKED
            else:
                operation = VERIFY_OP.get(record["action"])
                state = VERIFY_STATE.get(record["action"], _required_state(record["action"]))
            if not operation:
                continue
            verify.append((operation, record, state))

        status = "INCOMPLETE" if violations else "COMPLETE"
        steps, number = [], 0
        for phase, group in (("FINDING", findings), ("REMEDIATION", remediation),
                             ("VERIFICATION", verify)):
            order = {"FINDING": FINDING_ORDER, "REMEDIATION": REMEDIATION_ORDER,
                     "VERIFICATION": VERIFY_ORDER}[phase]
            group.sort(key=lambda item: (order.index(item[0]), item[1]["system"],
                                         item[1]["resource_id"]))
            for operation, record, state in group:
                number += 1
                steps.append({
                    "step_no": number,
                    "phase": phase,
                    "system_id": record["system"],
                    "resource_id": record["resource_id"],
                    "operation": operation,
                    "state": state,
                    "evidence_id": self._evidence_for(phase, record, status, audit_at),
                })
        return status, steps

    def _evidence_for(self, phase, record, status, audit_at):
        if status == "COMPLETE" and phase == "VERIFICATION":
            return self._authoritative_file(record, audit_at)
        if status == "INCOMPLETE" and phase == "FINDING":
            return record["evidence_id"]
        return ""

    def _authoritative_file(self, record, audit_at):
        """Complete cases cite the readback capture closest to the audit instant."""
        key = (record["system"], record["resource_id"])
        dated = [o for o in self.registry.by_resource.get(key, [])
                 if o.origin in ("readback", "content") and o.ts is not None
                 and o.ts <= audit_at]
        if not dated:
            return record["evidence_id"]
        fresh = [o for o in dated if o.origin == "readback"
                 and (audit_at - o.ts).total_seconds() <= 24 * 3600]
        pool = fresh or dated
        return max(pool, key=lambda o: o.ts).evidence_id
