"""Turn every participant-facing evidence file into normalised resource observations.

An ``Observation`` is one grounded statement about one resource at one point in
time, produced by a reader that knows the shape of its source format.  Readers
never invent state: when a format carries an explicit result vocabulary
(``FOUND`` / ``NOT_FOUND`` / ``IDENTIFIABLE`` / ...) that value is kept, and
content rows are only converted into a state when the collection semantics make
the mapping unambiguous.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from .paths import DATASET_DIR, EVIDENCE_DIR, fmt_ts, norm_ident, parse_ts

# Canonical resource states -----------------------------------------------
ABSENT = "ABSENT"
PRESENT = "PRESENT"
IDENTIFIABLE = "IDENTIFIABLE"
REDACTED = "REDACTED"
ANONYMIZED = "ANONYMIZED"
ACTIVE = "ACTIVE"
DISABLED = "DISABLED"
RESTORABLE = "RESTORABLE"
BLOCKED = "BLOCKED"

EXPLICIT_RESULTS = {
    "FOUND": PRESENT,
    "PRESENT": PRESENT,
    "NOT_FOUND": ABSENT,
    "ABSENT": ABSENT,
    "DELETED": ABSENT,
    "IDENTIFIABLE": IDENTIFIABLE,
    "REDACTED": REDACTED,
    "ANONYMIZED": ANONYMIZED,
    "ACTIVE": ACTIVE,
    "ENABLED": ACTIVE,
    "DISABLED": DISABLED,
    "BLOCKED": BLOCKED,
    "RETAINED": PRESENT,
    "RESTORABLE": RESTORABLE,
}

# ``system_id`` used by the output contract for each resource family.
SYSTEM_BY_PREFIX = {
    "account": "account_service",
    "analytics": "analytics_platform",
    "audit_record": "security_service",
    "backup": "backup_service",
    "cache": "cache_service",
    "data_flow": "integration_service",
    "email_delivery": "email_delivery_service",
    "export": "export_service",
    "legal_hold": "legal_service",
    "marketing": "marketing_platform",
    "search": "search_service",
    "support": "support_platform",
    "telemetry": "telemetry_service",
    "tombstone": "identity_service",
}

# Collection / record-type name -> canonical data category.
CATEGORY_BY_NAME = {
    "customer_profiles": "USER_PROFILE",
    "customer_profiles_primary": "USER_PROFILE",
    "customer_profiles_failover_replica": "USER_PROFILE",
    "profile_lookups": "USER_PROFILE",
    "profile": "USER_PROFILE",
    "customer_search_documents": "SEARCH_INDEX_ENTRY",
    "search_documents_primary": "SEARCH_INDEX_ENTRY",
    "index": "SEARCH_INDEX_ENTRY",
    "customer_analytics_reports": "ANALYTICS_PROFILE",
    "customer_analytics_profiles": "ANALYTICS_PROFILE",
    "analytics": "ANALYTICS_PROFILE",
    "support_conversations": "SUPPORT_MESSAGE",
    "support_records": "SUPPORT_MESSAGE",
    "messages": "SUPPORT_MESSAGE",
    "ticket_attachments": "SUPPORT_ATTACHMENT",
    "attachments": "SUPPORT_ATTACHMENT",
    "customer_export_jobs": "USER_EXPORT",
    "customer_exports": "USER_EXPORT",
    "export": "USER_EXPORT",
    "connector_configurations": "DATA_FLOW",
    "connector_subscriptions": "DATA_FLOW",
    "backup_catalog": "BACKUP_ARCHIVE",
    "backup_restore_entries": "BACKUP_ARCHIVE",
    "security_audit_events": "SECURITY_AUDIT_LOG",
    "security_audit_records": "SECURITY_AUDIT_LOG",
    "retained_order_records": "LEGAL_HOLD_RECORD",
    "legal_holds": "LEGAL_HOLD_RECORD",
    "account_tombstones": "ACCOUNT_TOMBSTONE",
    "tombstones": "ACCOUNT_TOMBSTONE",
    "cache_entries": "CACHE_ENTRY",
    "marketing_profiles": "MARKETING_PROFILE",
    "product_telemetry": "PRODUCT_TELEMETRY",
    "email_delivery_logs": "EMAIL_DELIVERY_LOG",
    "secondary_identifiers": "SECONDARY_IDENTIFIER",
}

RESULT_BY_RECORD_TYPE = {
    "USER_PROFILE": "USER_PROFILE",
    "SEARCH_INDEX_ENTRY": "SEARCH_INDEX_ENTRY",
    "CACHE_ENTRY": "CACHE_ENTRY",
    "SUPPORT_MESSAGE": "SUPPORT_MESSAGE",
    "SUPPORT_ATTACHMENT": "SUPPORT_ATTACHMENT",
    "ANALYTICS_PROFILE": "ANALYTICS_PROFILE",
    "PRODUCT_TELEMETRY": "PRODUCT_TELEMETRY",
    "USER_EXPORT": "USER_EXPORT",
    "MARKETING_PROFILE": "MARKETING_PROFILE",
    "EMAIL_DELIVERY_LOG": "EMAIL_DELIVERY_LOG",
    "SECURITY_AUDIT_LOG": "SECURITY_AUDIT_LOG",
    "LEGAL_HOLD_RECORD": "LEGAL_HOLD_RECORD",
    "BACKUP_ARCHIVE": "BACKUP_ARCHIVE",
    "ACCOUNT_TOMBSTONE": "ACCOUNT_TOMBSTONE",
    "SECONDARY_IDENTIFIER": "SECONDARY_IDENTIFIER",
    "DATA_FLOW": "DATA_FLOW",
}

IDENTIFIER_FIELDS = ("full_name", "name", "display_name", "email", "phone", "email_hash",
                     "requester_name", "requester_email", "requester_phone", "customer_email",
                     "owner_email", "owner_phone", "subject_reference", "lookup_key",
                     "subject_selector", "match_key", "message_excerpt")


@dataclass
class Observation:
    evidence_id: str
    ts: datetime | None
    system: str
    resource_id: str
    category: str
    state: str
    subjects: tuple[str, ...] = ()
    origin: str = "readback"
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "ts": fmt_ts(self.ts),
            "system": self.system,
            "resource_id": self.resource_id,
            "category": self.category,
            "state": self.state,
            "subjects": list(self.subjects),
            "origin": self.origin,
            "detail": self.detail,
        }


def system_for(resource_id: str, hint: str = "") -> str:
    """Canonical output system for a resource id.

    The resource-id family wins over a source system name, because evidence files
    report internal names (``user_export_store``) while the output contract uses the
    platform service (``export_service``).
    """
    prefix = str(resource_id or "").rsplit("_", 1)[0]
    canonical = SYSTEM_BY_PREFIX.get(prefix)
    return canonical or hint or prefix


def category_for(name: str, resource_id: str) -> str:
    key = (name or "").lower()
    if key in CATEGORY_BY_NAME:
        return CATEGORY_BY_NAME[key]
    prefix = (resource_id or "").rsplit("_", 1)[0]
    return {
        "account": "USER_PROFILE",
        "tombstone": "ACCOUNT_TOMBSTONE",
        "search": "SEARCH_INDEX_ENTRY",
        "cache": "CACHE_ENTRY",
        "analytics": "ANALYTICS_PROFILE",
        "telemetry": "PRODUCT_TELEMETRY",
        "support": "SUPPORT_MESSAGE",
        "export": "USER_EXPORT",
        "marketing": "MARKETING_PROFILE",
        "email_delivery": "EMAIL_DELIVERY_LOG",
        "data_flow": "DATA_FLOW",
        "backup": "BACKUP_ARCHIVE",
        "audit_record": "SECURITY_AUDIT_LOG",
        "legal_hold": "LEGAL_HOLD_RECORD",
    }.get(prefix, "UNKNOWN")


def truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower().strip('"')
    return text in {"1", "true", "yes", "t"}


def is_null(value) -> bool:
    if value is None:
        return True
    text = str(value).strip().lower().strip('"')
    return text in {"", "null", "none", "n/a", "-"}


def collect_subjects(row: dict) -> list[str]:
    out: list[str] = []
    for key, value in row.items():
        if is_null(value):
            continue
        if isinstance(value, dict):
            out.extend(collect_subjects(value))
            continue
        if not isinstance(value, (str, int, float)):
            continue
        token = norm_ident(str(value))
        if not token:
            continue
        if "@" in token or token.startswith("user_") or len(token) == 64 or re.match(r"^\+\d", token):
            out.append(token)
    return sorted(set(out))


def state_from_content(category: str, row: dict, resource_id: str) -> str:
    """Derive a state token from a content record that has no explicit result."""
    def has(*names: str) -> bool:
        return any(not is_null(row.get(n)) for n in names if n in row)

    if category == "USER_PROFILE":
        if str(row.get("account_status", "")).upper() == "TOMBSTONE":
            return REDACTED if not has("full_name", "email", "phone", "email_hash") else IDENTIFIABLE
        return IDENTIFIABLE if has("full_name", "email", "phone", "email_hash") else PRESENT
    if category == "ACCOUNT_TOMBSTONE":
        return REDACTED if not has("full_name", "email", "phone", "email_hash", "name", "display_name") else IDENTIFIABLE
    if category == "SEARCH_INDEX_ENTRY":
        doc = row.get("document")
        if isinstance(doc, dict):
            return IDENTIFIABLE if any(not is_null(doc.get(k)) for k in ("display_name", "email", "customer_reference", "phone", "email_hash")) else ANONYMIZED
        return IDENTIFIABLE if has("display_name", "email", "phone", "email_hash") else ANONYMIZED
    if category in ("ANALYTICS_PROFILE", "PRODUCT_TELEMETRY", "MARKETING_PROFILE"):
        return IDENTIFIABLE if has("user_id", "customer_reference", "email", "phone", "email_hash") else ANONYMIZED
    if category in ("SUPPORT_MESSAGE", "EMAIL_DELIVERY_LOG"):
        text = " ".join(str(row.get(k, "")) for k in ("message_text", "message_excerpt", "body", "subject"))
        if "[REDACTED]" in text and not has("requester_email", "requester_name", "requester_phone", "customer_reference", "subject_reference"):
            return REDACTED
        if has("requester_email", "requester_name", "requester_phone", "customer_email"):
            return IDENTIFIABLE
        if re.search(r"[\w.+-]+@[\w.-]+\.\w+", text):
            return IDENTIFIABLE
        return REDACTED if "[REDACTED]" in text else PRESENT
    if category == "SUPPORT_ATTACHMENT":
        return PRESENT
    if category == "USER_EXPORT":
        if is_null(row.get("download_available")) is False and row.get("download_available") in (False, "false", 0, "0"):
            return ABSENT
        return PRESENT
    if category == "DATA_FLOW":
        if "enabled" in row:
            return ACTIVE if truthy(row.get("enabled")) else DISABLED
        if "last_delivery_status" in row and not is_null(row.get("last_delivery_status")):
            return ACTIVE if str(row.get("last_delivery_status")).lower() != "stopped" else DISABLED
        return PRESENT
    if category == "BACKUP_ARCHIVE":
        if "restore_allowed" in row:
            return RESTORABLE if truthy(row.get("restore_allowed")) else BLOCKED
        return PRESENT
    if category in ("SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD"):
        return PRESENT
    if category == "CACHE_ENTRY":
        return PRESENT
    return PRESENT


# --------------------------------------------------------------------------
# Format readers.  Each returns a list of Observation objects.
# --------------------------------------------------------------------------

def _read_uniform_rows(rows, evidence_id: str, default_ts: datetime | None, source: str):
    obs = []
    for row in rows:
        resource_id = row.get("resource_id")
        if is_null(resource_id):
            continue
        result = norm_ident(row.get("result", "")).upper()
        category = RESULT_BY_RECORD_TYPE.get(str(row.get("record_type", "")).upper(), "")
        if not category:
            category = category_for("", str(resource_id))
        ts = parse_ts(row.get("recorded_at") or row.get("lookup_time") or "") or default_ts
        if result in EXPLICIT_RESULTS:
            state = EXPLICIT_RESULTS[result]
        else:
            state = state_from_content(category, row, str(resource_id))
        obs.append(Observation(
            evidence_id=evidence_id, ts=ts,
            system=system_for(str(resource_id), str(row.get("system", "") or row.get("origin_system", "") or "")),
            resource_id=str(resource_id), category=category, state=state,
            subjects=tuple(collect_subjects(row)), origin="readback",
            detail={"source": source, "raw_result": result},
        ))
    return obs


def read_json_file(path: str, evidence_id: str):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    default_ts = parse_ts(data.get("exported_at") or data.get("captured_at") or "")
    obs: list[Observation] = []

    records = data.get("records")
    if isinstance(records, list) and records and isinstance(records[0], dict) and "record_type" in records[0]:
        obs.extend(_read_uniform_rows(records, evidence_id, default_ts, "uniform_records"))
        return obs

    if isinstance(records, list) and records and isinstance(records[0], dict) and "resource_id" in records[0]:
        collection = str(data.get("collection", ""))
        system = str(data.get("source", ""))
        for row in records:
            category = category_for(collection, str(row["resource_id"]))
            obs.append(Observation(
                evidence_id, default_ts, system_for(str(row["resource_id"]), system),
                str(row["resource_id"]), category,
                state_from_content(category, row, str(row["resource_id"])),
                tuple(collect_subjects(row)), "content", {"collection": collection},
            ))
        return obs

    for snap in data.get("snapshots", []):
        snap_ts = parse_ts(snap.get("captured_at", "")) or default_ts
        rows = snap.get("records", [])
        if rows and isinstance(rows[0], dict) and "record_type" in rows[0]:
            obs.extend(_read_uniform_rows(rows, evidence_id, snap_ts, "operational_record_history"))
            continue
        collection = str(snap.get("collection", ""))
        system = str(snap.get("data_store", ""))
        for row in rows:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("resource_id", ""))
            if is_null(rid):
                continue
            category = category_for(collection, rid)
            merged = dict(row)
            if isinstance(row.get("document"), dict):
                merged.update(row["document"])
            obs.append(Observation(
                evidence_id, snap_ts, system_for(rid, system), rid, category,
                state_from_content(category, merged, rid),
                tuple(collect_subjects(merged)), "content", {"collection": collection},
            ))

    for system, blocks in (data.get("service_exports") or {}).items():
        for block_name, rows in (blocks or {}).items():
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                rid = str(row.get("resource_id", ""))
                if is_null(rid):
                    continue
                ts = parse_ts(row.get("observed_at", "")) or default_ts
                category = category_for(block_name, rid)
                status = row.get("status_code")
                profile = row.get("profile")
                if status == 404 or profile is None and "profile" in row:
                    state = ABSENT
                else:
                    merged = dict(profile or {})
                    merged.update({k: v for k, v in row.items() if k not in ("profile", "status_code", "observed_at")})
                    state = state_from_content(category, merged, rid)
                subjects = tuple(collect_subjects(profile or {})) or tuple(collect_subjects(row))
                obs.append(Observation(evidence_id, ts, system_for(rid, system), rid, category, state,
                                       subjects, "readback", {"block": block_name, "status_code": status}))
    return obs


KV_RE = re.compile(r'(\w+)=("[^"]*"|[^\s]+)')


def _flatten_kv(text: str) -> dict:
    out = {}
    for key, value in KV_RE.findall(text):
        out[key] = value.strip('"')
    return out


EVENT_CATEGORY = {
    "profile_lookup_completed": ("USER_PROFILE", "account_service"),
    "analytics_profile_read_completed": ("ANALYTICS_PROFILE", "analytics_platform"),
    "index_query_completed": ("SEARCH_INDEX_ENTRY", "search_service"),
    "support_ticket_read_completed": ("SUPPORT_MESSAGE", "support_platform"),
    "attachment_scan_completed": ("SUPPORT_ATTACHMENT", "support_platform"),
    "connector_config_read_completed": ("DATA_FLOW", "integration_service"),
    "restore_policy_read_completed": ("BACKUP_ARCHIVE", "backup_service"),
    "restore_authorization_completed": ("BACKUP_ARCHIVE", "backup_service"),
    "export_metadata_read_completed": ("USER_EXPORT", "export_service"),
    "audit_record_read_completed": ("SECURITY_AUDIT_LOG", "security_service"),
    "legal_record_read_completed": ("LEGAL_HOLD_RECORD", "legal_service"),
    "cache_read_completed": ("CACHE_ENTRY", "cache_service"),
    "tombstone_read_completed": ("ACCOUNT_TOMBSTONE", "identity_service"),
}


def _state_from_readback(category: str, kv: dict) -> str | None:
    rows = kv.get("rows")
    status = kv.get("http_status")
    if status in ("404", "410") or (rows is not None and str(rows) == "0"):
        return ABSENT
    if "matched_value" in kv or norm_ident(kv.get("detector", "")) == "direct_identifier":
        return IDENTIFIABLE
    if "result" in kv:
        token = norm_ident(kv["result"]).upper()
        if token in EXPLICIT_RESULTS:
            return EXPLICIT_RESULTS[token]
    if "enabled" in kv:
        return ACTIVE if truthy(kv["enabled"]) else DISABLED
    if "restore_allowed" in kv:
        return RESTORABLE if truthy(kv["restore_allowed"]) else BLOCKED
    if "eligible" in kv:
        return RESTORABLE if truthy(kv["eligible"]) else BLOCKED
    if "marker_found" in kv:
        return PRESENT if not truthy(kv["marker_found"]) else ABSENT
    if "decision" in kv:
        token = norm_ident(kv["decision"]).upper()
        return BLOCKED if token in {"DENIED", "BLOCKED"} else RESTORABLE
    if "archive_present" in kv and not truthy(kv["archive_present"]):
        return ABSENT
    if "available" in kv and not truthy(kv["available"]):
        return ABSENT
    if "total_hits" in kv and str(kv["total_hits"]) == "0":
        return ABSENT
    return None


def _state_from_log_readback(category: str, kv: dict, resource_id: str) -> str:
    """Log lines prove a lookup ran; they never prove identifiers were scrubbed.

    Only explicit markers (``result=``/``rows=0``/``http_status=404``) may report an
    absent, redacted or anonymised record, so a content-style inference is promoted
    back to ``PRESENT``.
    """
    state = state_from_content(category, kv, resource_id)
    if state in (REDACTED, ANONYMIZED, ABSENT):
        return PRESENT
    if state == DISABLED:
        return ACTIVE
    return state


def _log_line_kv(line: str):
    """Split one log line into its timestamp and its ``key=value`` payload."""
    line = line.strip()
    if not line:
        return None, None
    stamp, _, rest = line.partition(" ")
    ts = parse_ts(stamp)
    if '\\"' in rest:
        rest = rest.replace('\\"', '"')
    payload = None
    if rest.startswith("{"):
        try:
            payload = json.loads(rest)
        except json.JSONDecodeError:
            payload = None
    if payload is not None:
        kv = {k: v for k, v in payload.items() if isinstance(v, (str, int, float)) or v is None}
        return ts, {k: ("" if v is None else str(v)) for k, v in kv.items()}
    return ts, _flatten_kv(rest)


def read_log_file(path: str, evidence_id: str):
    obs: list[Observation] = []
    records = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            ts, kv = _log_line_kv(line)
            if kv is None:
                continue
            records.append((ts, kv))

    # A trace names the person it looked up on one line and the record it touched on
    # the next, so the request-scoped ``trace_id`` carries the subject across lines.
    trace_subjects: dict[str, set[str]] = {}
    for _ts, kv in records:
        trace = kv.get("trace_id")
        if trace and not is_null(trace):
            trace_subjects.setdefault(trace, set()).update(collect_subjects(kv))

    def traced_subjects(kv) -> tuple:
        own = set(collect_subjects(kv))
        trace = kv.get("trace_id")
        if trace and not is_null(trace):
            own |= trace_subjects.get(trace, set())
        return tuple(sorted(own))

    for ts, kv in records:
        event = norm_ident(kv.get("event", ""))
        if not event and "record_type" in kv:
            event = "record_lookup"
        rid = kv.get("resource_id", "")
        if is_null(rid):
            continue
        if event == "record_lookup":
            category = RESULT_BY_RECORD_TYPE.get(norm_ident(kv.get("record_type", "")).upper(), "") \
                or category_for("", rid)
            token = norm_ident(kv.get("result", "")).upper()
            state = EXPLICIT_RESULTS.get(token, _state_from_log_readback(category, kv, rid))
            obs.append(Observation(evidence_id, ts, system_for(rid, kv.get("system", "")), rid, category,
                                   state, traced_subjects(kv), "readback", {"event": event}))
            continue
        if event.endswith("_started") or event in {"export_started", "export_completed", "batch_started",
                                                   "connection_acquired", "cache_lookup", "dependency_retry",
                                                   "dependency_recovered", "lease_extended", "telemetry_flushed",
                                                   "delivery_abandoned", "heartbeat"}:
            continue
        mapped = EVENT_CATEGORY.get(event)
        if mapped is None:
            if not any(k in kv for k in ("rows", "http_status", "enabled", "restore_allowed",
                                         "result", "eligible", "matched_value")):
                continue
            category = category_for("", rid)
            if "email_opt_in" in kv and category == "ANALYTICS_PROFILE":
                category = "MARKETING_PROFILE"
            system = system_for(rid, kv.get("service", ""))
        else:
            category, system = mapped
            system = system_for(rid, kv.get("service", kv.get("system", ""))) or system
        state = _state_from_readback(category, kv)
        if state is None:
            state = _state_from_log_readback(category, kv, rid)
        subjects = traced_subjects(kv)
        obs.append(Observation(evidence_id, ts, system, rid, category, state, subjects, "readback",
                               {"event": event, "lookup_key": kv.get("lookup_key") or kv.get("match_key")
                                or kv.get("subject_ref") or kv.get("subject_key") or ""}))
    return obs


def read_text_file(path: str, evidence_id: str):
    obs: list[Observation] = []
    default_ts = None
    header = re.match(r"^Snapshot:\s*(\S+)", open(path, encoding="utf-8", errors="replace").read())
    if header:
        default_ts = parse_ts(header.group(1))
    pattern = re.compile(
        r"^-\s*(\S+)\s*\|\s*([\w]+)\s*\|\s*([\w]+)\s*\|\s*([A-Z_]+)\s*\|\s*([A-Z_]+)\s*\|\s*(.+?)\s*$")
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            m = pattern.match(line.strip())
            if not m:
                continue
            ts, system, rid, record_type, result, subject = m.groups()
            category = RESULT_BY_RECORD_TYPE.get(record_type, category_for("", rid))
            obs.append(Observation(evidence_id, parse_ts(ts) or default_ts, system_for(rid, system), rid,
                                   category, EXPLICIT_RESULTS.get(result, state_from_content(category, {}, rid)),
                                   (norm_ident(subject),) if subject else (), "readback",
                                   {"raw_result": result}))
    return obs


SQL_TABLE_HINTS = {
    "customer_profiles": "USER_PROFILE", "customer_profiles_primary": "USER_PROFILE",
    "customer_profiles_failover_replica": "USER_PROFILE", "customer_search_documents": "SEARCH_INDEX_ENTRY",
    "search_documents_primary": "SEARCH_INDEX_ENTRY", "customer_analytics_profiles": "ANALYTICS_PROFILE",
    "customer_analytics_reports": "ANALYTICS_PROFILE", "support_records": "SUPPORT_MESSAGE",
    "support_conversations": "SUPPORT_MESSAGE", "ticket_attachments": "SUPPORT_ATTACHMENT",
    "customer_exports": "USER_EXPORT", "customer_export_jobs": "USER_EXPORT",
    "connector_subscriptions": "DATA_FLOW", "connector_configurations": "DATA_FLOW",
    "backup_restore_entries": "BACKUP_ARCHIVE", "backup_catalog": "BACKUP_ARCHIVE",
    "security_audit_records": "SECURITY_AUDIT_LOG", "security_audit_events": "SECURITY_AUDIT_LOG",
    "retained_order_records": "LEGAL_HOLD_RECORD", "account_tombstones": "ACCOUNT_TOMBSTONE",
    "customer_profiles_read_replica": "USER_PROFILE",
}


def read_sql_file(path: str, evidence_id: str):
    text = open(path, encoding="utf-8", errors="replace").read()
    captured = re.search(r"Captured at:\s*(\S+)", text)
    default_ts = parse_ts(captured.group(1)) if captured else None
    conn = sqlite3.connect(":memory:")
    conn.executescript(text)
    conn.row_factory = sqlite3.Row
    obs: list[Observation] = []
    scope = {}
    if "snapshot_scope" in [r[0] for r in conn.execute("select name from sqlite_master where type='table'")]:
        for row in conn.execute("select origin_system, captured_at from snapshot_scope"):
            scope[row["origin_system"]] = parse_ts(row["captured_at"]) or default_ts
    tables = [r[0] for r in conn.execute("select name from sqlite_master where type='table'")
              if r[0] != "snapshot_scope"]
    for table in tables:
        rows = conn.execute(f'select * from "{table}"').fetchall()
        replica_role = "replica" if "replica" in table else "primary"
        for row in rows:
            data = dict(row)
            record = data
            if "record_json" in data:
                try:
                    record = json.loads(data["record_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
                table_key = str(data.get("collection_name", "")).lower()
                system_hint = str(data.get("origin_system", ""))
                ts = parse_ts(data.get("updated_at", "")) or default_ts
            else:
                table_key = table.lower()
                system_hint = ""
                ts = parse_ts(data.get("updated_at", "")) or scope.get(system_hint) or default_ts
            rid = str(record.get("resource_id", ""))
            if is_null(rid):
                continue
            category = SQL_TABLE_HINTS.get(table_key) or category_for(table_key, rid)
            merged = dict(record)
            merged.setdefault("customer_reference", record.get("customer_reference"))
            obs.append(Observation(evidence_id, ts, system_for(rid, system_hint), rid, category,
                                   state_from_content(category, merged, rid),
                                   tuple(collect_subjects(merged)), "content",
                                   {"table": table, "role": replica_role}))
    conn.close()
    return obs


def load_manifest():
    rows = []
    with open(os.path.join(DATASET_DIR, "evidence_files.csv"), encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            row["captured_dt"] = parse_ts(row["captured_at"])
            rows.append(row)
    return rows


def build_observations(manifest=None, include=(".json", ".log", ".txt", ".sql")):
    """Parse every structured evidence file into observations."""
    manifest = manifest if manifest is not None else load_manifest()
    all_obs: list[Observation] = []
    for entry in manifest:
        rel = entry["file_path"]
        base = EVIDENCE_DIR if not rel.startswith("evidence" + os.sep) and not rel.startswith("evidence/") else DATASET_DIR
        path = os.path.join(base, rel.replace("/", os.sep))
        ext = os.path.splitext(path)[1].lower()
        if ext not in include or not os.path.isfile(path):
            continue
        if ext == ".json":
            obs = read_json_file(path, entry["file_id"])
        elif ext == ".log":
            obs = read_log_file(path, entry["file_id"])
        elif ext == ".txt":
            obs = read_text_file(path, entry["file_id"])
        else:
            obs = read_sql_file(path, entry["file_id"])
        for one in obs:
            if one.ts is None:
                one.ts = entry["captured_dt"]
        all_obs.extend(obs)
    return all_obs
