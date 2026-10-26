"""Participant-authored evaluation cases for the Ghost Data Still Exists agent.

The challenge ships 32 public sample cases.  Those cases are noisy: they mix several
violations, decoys and late captures at once, so a failure there does not localise the
regression.  This file adds four small, fully-synthetic worlds - fictional users,
fictional resources, fictional evidence files - each isolating one behaviour the audit
engine must get right:

1. ``custom_case_01`` ordered remediation with an active data flow feeding a violation
   (flow disable must precede the dependent record changes);
2. ``custom_case_02`` a genuinely complete deletion, including a policy-retained
   security audit record and a redacted tombstone, where verification steps must be the
   only phase and must cite evidence;
3. ``custom_case_03`` a subject exercising a *different* right (access + correction),
   which must short-circuit to ``NOT_APPLICABLE`` with no steps;
4. ``custom_case_04`` identity resolution through a historical email plus a restore
   path that can bring prohibited data back.

Run with:

    python3 code/evaluation/custom_cases.py

The fixtures are written to ``work/custom_eval_dataset`` at run time, so nothing here
depends on the supplied dataset, and no golden label from ``dataset/sample_output.csv``
is read.  Expected outputs are authored by hand and checked exactly, including step
order and empty ``evidence_id`` on proposed work.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODE_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(CODE_DIR)
FIXTURE_ROOT = os.path.join(REPO_ROOT, "work", "custom_eval_dataset")

RULES = [
    ("rule_01", "USER_PROFILE", "Always", "DELETE"),
    ("rule_02", "SECONDARY_IDENTIFIER", "Always", "DELETE"),
    ("rule_03", "SEARCH_INDEX_ENTRY", "Always", "DELETE"),
    ("rule_04", "CACHE_ENTRY", "Always", "DELETE"),
    ("rule_05", "SUPPORT_MESSAGE", "Always", "REDACT"),
    ("rule_06", "SUPPORT_ATTACHMENT", "Always", "DELETE"),
    ("rule_07", "ANALYTICS_PROFILE", "Always", "ANONYMIZE"),
    ("rule_08", "PRODUCT_TELEMETRY", "Always", "ANONYMIZE"),
    ("rule_09", "USER_EXPORT", "Always", "DELETE"),
    ("rule_10", "MARKETING_PROFILE", "Always", "DELETE"),
    ("rule_11", "EMAIL_DELIVERY_LOG", "Always", "REDACT"),
    ("rule_12", "SECURITY_AUDIT_LOG", "Always", "RETAIN"),
    ("rule_13", "LEGAL_HOLD_RECORD", "Always", "RETAIN"),
    ("rule_14", "BACKUP_ARCHIVE", "Restore path open", "RETAIN"),
    ("rule_15", "ACCOUNT_TOMBSTONE", "Always", "REDACT"),
    ("rule_16", "DATA_FLOW", "Always", "CONTROL"),
]

IDENTITIES = [
    ("user_901", "Nadia Rahman", "nadia.rahman@example.com", "+65 8123 4567",
     "a" * 64, "2026-06-01T09:00:00Z"),
    ("user_902", "Tomas Vega", "tomas.vega@example.org", "+34 600 112 233",
     "b" * 64, "2026-06-01T09:00:00Z"),
    ("user_903", "Bram Visser", "bram.visser@example.net", "+31 6 1122 3344",
     "c" * 64, "2026-06-01T09:00:00Z"),
    ("user_904", "Priya Nair", "priya.nair.new@example.com", "+91 98765 43210",
     "d" * 64, "2026-07-02T09:00:00Z"),
]

AUDIT_LOG = [
    ("audit_901", "user_901", "2026-05-20T09:00:00Z", "phone",
     "+65 8000 0000", "+65 8123 4567"),
    ("audit_902", "user_904", "2026-06-30T09:00:00Z", "email",
     "priya.nair.old@example.com", "priya.nair.new@example.com"),
]

CASES = [
    ("custom_case_01", "nadia.rahman@example.com", "2026-07-10T09:00:00Z",
     "PARTIALLY_COMPLETE", "2026-07-11T09:00:00Z", "2026-07-12T12:00:00Z",
     "Please delete my account and every copy of my personal data."),
    ("custom_case_02", "tomas.vega@example.org", "2026-07-10T09:00:00Z",
     "COMPLETE", "2026-07-11T09:00:00Z", "2026-07-12T12:00:00Z",
     "Erase my profile and confirm that nothing that identifies me is left."),
    ("custom_case_03", "bram.visser@example.net", "2026-07-10T09:00:00Z",
     "NOT_STARTED", "2026-07-11T09:00:00Z", "2026-07-12T12:00:00Z",
     "Please send me a copy of my data and rectify the spelling of my surname."),
    ("custom_case_04", "priya.nair.old@example.com", "2026-07-10T09:00:00Z",
     "COMPLETE", "2026-07-11T09:00:00Z", "2026-07-12T12:00:00Z",
     "My account must be removed for good, including anything a restore could bring back."),
]

# One read-back capture per synthetic world; every record of one user lives in it.
def _capture(evidence_id: str, path: str, captured_at: str, records: list[dict]) -> tuple:
    return (evidence_id, path, captured_at, records)


CAPTURES = {
    "custom_evidence_01": _capture(
        "custom_evidence_01", "evidence/records/custom_directory_readback.json",
        "2026-07-12T06:00:00Z", [
            {"record_type": "USER_PROFILE", "resource_id": "account_901",
             "result": "IDENTIFIABLE", "recorded_at": "2026-07-12T06:00:00Z",
             "full_name": "Nadia Rahman", "email": "nadia.rahman@example.com"},
            {"record_type": "SUPPORT_MESSAGE", "resource_id": "support_901",
             "result": "IDENTIFIABLE", "recorded_at": "2026-07-12T06:00:00Z",
             "requester_email": "nadia.rahman@example.com",
             "message_text": "Please remove me from the courier list."},
            {"record_type": "SECURITY_AUDIT_LOG", "resource_id": "audit_record_901",
             "result": "FOUND", "recorded_at": "2026-07-12T06:00:00Z",
             "customer_reference": "user_901"},
        ]),
    "custom_evidence_02": _capture(
        "custom_evidence_02", "evidence/records/custom_integrations_readback.json",
        "2026-07-12T06:10:00Z", [
            {"record_type": "DATA_FLOW", "resource_id": "data_flow_9001",
             "result": "ACTIVE", "recorded_at": "2026-07-12T06:10:00Z",
             "subject_selector": "nadia.rahman@example.com", "enabled": "true"},
        ]),
    "custom_evidence_03": _capture(
        "custom_evidence_03", "evidence/records/custom_complete_state_readback.json",
        "2026-07-12T07:00:00Z", [
            {"record_type": "USER_PROFILE", "resource_id": "account_902",
             "result": "NOT_FOUND", "recorded_at": "2026-07-12T07:00:00Z",
             "email": "tomas.vega@example.org"},
            {"record_type": "SEARCH_INDEX_ENTRY", "resource_id": "search_902",
             "result": "NOT_FOUND", "recorded_at": "2026-07-12T07:00:00Z",
             "email": "tomas.vega@example.org"},
            {"record_type": "ACCOUNT_TOMBSTONE", "resource_id": "tombstone_902",
             "result": "REDACTED", "recorded_at": "2026-07-12T07:00:00Z",
             "customer_reference": "user_902"},
            {"record_type": "ANALYTICS_PROFILE", "resource_id": "analytics_902",
             "result": "ANONYMIZED", "recorded_at": "2026-07-12T07:00:00Z",
             "customer_reference": "anon_5511"},
            {"record_type": "SECURITY_AUDIT_LOG", "resource_id": "audit_record_902",
             "result": "FOUND", "recorded_at": "2026-07-12T07:00:00Z",
             "customer_reference": "user_902"},
            {"record_type": "DATA_FLOW", "resource_id": "data_flow_9002",
             "result": "DISABLED", "recorded_at": "2026-07-12T07:00:00Z",
             "customer_reference": "user_902", "enabled": "false"},
            {"record_type": "BACKUP_ARCHIVE", "resource_id": "backup_902",
             "result": "BLOCKED", "recorded_at": "2026-07-12T07:00:00Z",
             "customer_reference": "user_902", "restore_allowed": "false"},
        ]),
    "custom_evidence_04": _capture(
        "custom_evidence_04", "evidence/records/custom_legacy_identifier_capture.json",
        "2026-07-12T06:30:00Z", [
            {"record_type": "USER_EXPORT", "resource_id": "export_904",
             "result": "FOUND", "recorded_at": "2026-07-12T06:30:00Z",
             "email": "priya.nair.old@example.com", "download_available": "true"},
            {"record_type": "BACKUP_ARCHIVE", "resource_id": "backup_904",
             "result": "RESTORABLE", "recorded_at": "2026-07-12T06:30:00Z",
             "email": "priya.nair.old@example.com", "restore_allowed": "true"},
        ]),
}

ACTIVITY = [
    ("activity_901", "2026-07-11T10:00:00Z", "LINK_RESOURCE_IDENTITY",
     "data_catalog", "integration_service", "data_flow_9001", "SUCCESS",
     "Data catalog linked data_flow_9001 (DATA_FLOW) to nadia.rahman@example.com."),
    # The anonymised analytics row keeps no identifier, so only the data catalog
    # still says whose record it used to be.
    ("activity_902", "2026-07-11T10:05:00Z", "LINK_RESOURCE_IDENTITY",
     "data_catalog", "analytics_platform", "analytics_902", "SUCCESS",
     "Data catalog linked analytics_902 (ANALYTICS_PROFILE) to user_902."),
]

EXPECTED = {
    "custom_case_01": ("INCOMPLETE", [
        {"step_no": 1, "phase": "FINDING", "system_id": "account_service",
         "resource_id": "account_901", "operation": "REPORT_REMAINING_RECORD",
         "state": "PRESENT", "evidence_id": "custom_evidence_01"},
        {"step_no": 2, "phase": "FINDING", "system_id": "support_platform",
         "resource_id": "support_901", "operation": "REPORT_EXPOSED_PII",
         "state": "PRESENT", "evidence_id": "custom_evidence_01"},
        {"step_no": 3, "phase": "FINDING", "system_id": "integration_service",
         "resource_id": "data_flow_9001", "operation": "REPORT_ACTIVE_DATA_FLOW",
         "state": "ACTIVE", "evidence_id": "custom_evidence_02"},
        {"step_no": 4, "phase": "REMEDIATION", "system_id": "integration_service",
         "resource_id": "data_flow_9001", "operation": "DISABLE_DATA_FLOW",
         "state": "DISABLED", "evidence_id": ""},
        {"step_no": 5, "phase": "REMEDIATION", "system_id": "account_service",
         "resource_id": "account_901", "operation": "DELETE_RECORD",
         "state": "ABSENT", "evidence_id": ""},
        {"step_no": 6, "phase": "REMEDIATION", "system_id": "support_platform",
         "resource_id": "support_901", "operation": "REDACT_RECORD",
         "state": "REDACTED", "evidence_id": ""},
        {"step_no": 7, "phase": "VERIFICATION", "system_id": "account_service",
         "resource_id": "account_901", "operation": "VERIFY_RECORD_ABSENT",
         "state": "ABSENT", "evidence_id": ""},
        {"step_no": 8, "phase": "VERIFICATION", "system_id": "security_service",
         "resource_id": "audit_record_901", "operation": "VERIFY_RECORD_PRESENT",
         "state": "PRESENT", "evidence_id": ""},
        {"step_no": 9, "phase": "VERIFICATION", "system_id": "support_platform",
         "resource_id": "support_901", "operation": "VERIFY_RECORD_REDACTED",
         "state": "REDACTED", "evidence_id": ""},
        {"step_no": 10, "phase": "VERIFICATION", "system_id": "integration_service",
         "resource_id": "data_flow_9001", "operation": "VERIFY_DATA_FLOW_DISABLED",
         "state": "DISABLED", "evidence_id": ""},
    ]),
    "custom_case_02": ("COMPLETE", [
        {"step_no": 1, "phase": "VERIFICATION", "system_id": "account_service",
         "resource_id": "account_902", "operation": "VERIFY_RECORD_ABSENT",
         "state": "ABSENT", "evidence_id": "custom_evidence_03"},
        {"step_no": 2, "phase": "VERIFICATION", "system_id": "search_service",
         "resource_id": "search_902", "operation": "VERIFY_RECORD_ABSENT",
         "state": "ABSENT", "evidence_id": "custom_evidence_03"},
        {"step_no": 3, "phase": "VERIFICATION", "system_id": "security_service",
         "resource_id": "audit_record_902", "operation": "VERIFY_RECORD_PRESENT",
         "state": "PRESENT", "evidence_id": "custom_evidence_03"},
        {"step_no": 4, "phase": "VERIFICATION", "system_id": "identity_service",
         "resource_id": "tombstone_902", "operation": "VERIFY_RECORD_REDACTED",
         "state": "REDACTED", "evidence_id": "custom_evidence_03"},
        {"step_no": 5, "phase": "VERIFICATION", "system_id": "analytics_platform",
         "resource_id": "analytics_902", "operation": "VERIFY_RECORD_ANONYMIZED",
         "state": "ANONYMIZED", "evidence_id": "custom_evidence_03"},
        {"step_no": 6, "phase": "VERIFICATION", "system_id": "integration_service",
         "resource_id": "data_flow_9002", "operation": "VERIFY_DATA_FLOW_DISABLED",
         "state": "DISABLED", "evidence_id": "custom_evidence_03"},
        {"step_no": 7, "phase": "VERIFICATION", "system_id": "backup_service",
         "resource_id": "backup_902", "operation": "VERIFY_RESTORE_BLOCKED",
         "state": "BLOCKED", "evidence_id": "custom_evidence_03"},
    ]),
    "custom_case_03": ("NOT_APPLICABLE", []),
    "custom_case_04": ("INCOMPLETE", [
        {"step_no": 1, "phase": "FINDING", "system_id": "export_service",
         "resource_id": "export_904", "operation": "REPORT_REMAINING_RECORD",
         "state": "PRESENT", "evidence_id": "custom_evidence_04"},
        {"step_no": 2, "phase": "FINDING", "system_id": "backup_service",
         "resource_id": "backup_904", "operation": "REPORT_RESTORE_RISK",
         "state": "ACTIVE", "evidence_id": "custom_evidence_04"},
        {"step_no": 3, "phase": "REMEDIATION", "system_id": "backup_service",
         "resource_id": "backup_904", "operation": "BLOCK_RESTORE",
         "state": "BLOCKED", "evidence_id": ""},
        {"step_no": 4, "phase": "REMEDIATION", "system_id": "export_service",
         "resource_id": "export_904", "operation": "DELETE_RECORD",
         "state": "ABSENT", "evidence_id": ""},
        {"step_no": 5, "phase": "VERIFICATION", "system_id": "export_service",
         "resource_id": "export_904", "operation": "VERIFY_RECORD_ABSENT",
         "state": "ABSENT", "evidence_id": ""},
        {"step_no": 6, "phase": "VERIFICATION", "system_id": "backup_service",
         "resource_id": "backup_904", "operation": "VERIFY_RESTORE_BLOCKED",
         "state": "BLOCKED", "evidence_id": ""},
    ]),
}


def write_fixtures(root: str) -> None:
    if os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(os.path.join(root, "evidence", "records"), exist_ok=True)

    def csv_rows(name: str, header: list[str], rows: list[tuple]) -> None:
        with open(os.path.join(root, name), "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)

    csv_rows("deletion_requests.csv",
             ["case_id", "email", "requested_at", "reported_status", "reported_at",
              "audit_at", "request_text"], [tuple(case) for case in CASES])
    csv_rows("user_identities.csv",
             ["user_id", "name", "email", "phone", "email_hash", "updated_at"],
             [tuple(row) for row in IDENTITIES])
    csv_rows("user_audit_log.csv",
             ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"],
             [tuple(row) for row in AUDIT_LOG])
    csv_rows("system_activity.csv",
             ["activity_id", "event_time", "event_type", "source_system", "target_system",
              "resource_id", "reported_result", "details"], [tuple(row) for row in ACTIVITY])
    csv_rows("deletion_rules.csv", ["rule_id", "data_category", "condition", "required_action"],
             [tuple(row) for row in RULES])

    manifest = []
    for evidence_id, rel_path, captured_at, records in CAPTURES.values():
        manifest.append((evidence_id, rel_path, "custom_assurance_readback", captured_at))
        payload = {"exported_at": captured_at,
                   "source": "custom_assurance_readback",
                   "records": records}
        with open(os.path.join(root, rel_path.replace("/", os.sep)), "w",
                  encoding="utf-8") as handle:
            json.dump(payload, handle, indent=1)
    csv_rows("evidence_files.csv", ["file_id", "file_path", "origin", "captured_at"], manifest)


def run_engine():
    from ghostaudit.audit import AuditEngine
    from ghostaudit.identity import IdentityIndex
    from ghostaudit.intent import authorizes_deletion
    from ghostaudit.paths import DATASET_DIR
    from ghostaudit.policy import load_rules
    from ghostaudit.registry import Registry

    identities = IdentityIndex()
    registry = Registry(identities=identities)
    engine = AuditEngine(registry, load_rules(os.path.join(DATASET_DIR, "deletion_rules.csv")))
    out = {}
    for case in CASES:
        row = {"case_id": case[0], "email": case[1], "audit_at": case[5],
               "request_text": case[6]}
        allowed = authorizes_deletion(row["request_text"])
        if not allowed:
            out[case[0]] = ("NOT_APPLICABLE", [])
            continue
        out[case[0]] = engine.run(row, identities.resolve(case[1]))
    return out


def show(step: dict) -> str:
    return ("{phase:<12} {operation:<26} {system_id:<22} {resource_id:<16} "
            "{state:<11} evidence={evidence_id}").format(**step)


def main() -> int:
    write_fixtures(FIXTURE_ROOT)
    os.environ["GHOSTAUDIT_DATASET_DIR"] = FIXTURE_ROOT
    sys.path.insert(0, CODE_DIR)
    actual = run_engine()

    failures = 0
    for case_id, (want_status, want_steps) in EXPECTED.items():
        got_status, got_steps = actual[case_id]
        ok = True
        print(f"\n== {case_id} ==")
        if got_status != want_status:
            ok = False
            print(f"  status: expected {want_status}, got {got_status}")
        else:
            print(f"  status: {got_status} (as authored)")
        seen = {(s["phase"], s["operation"], s["system_id"], s["resource_id"],
                 s["state"], s["evidence_id"]) for s in got_steps}
        wanted_keys = [(s["phase"], s["operation"], s["system_id"], s["resource_id"],
                        s["state"], s["evidence_id"]) for s in want_steps]
        for index, step in enumerate(got_steps, start=1):
            mark = "ok " if index <= len(wanted_keys) and (
                step["phase"], step["operation"], step["system_id"], step["resource_id"],
                step["state"], step["evidence_id"]) == wanted_keys[index - 1] else "DIFF"
            if mark == "DIFF":
                ok = False
            print(f"  [{mark}] {index:>2} {show(step)}")
        if len(got_steps) != len(want_steps):
            ok = False
            print(f"  step count: expected {len(want_steps)}, got {len(got_steps)}")
        missing = [k for k in wanted_keys if k not in seen]
        for item in missing:
            ok = False
            print(f"  MISSING {item}")
        numbers = [s["step_no"] for s in got_steps]
        if numbers != list(range(1, len(got_steps) + 1)):
            ok = False
            print(f"  step numbering not consecutive: {numbers}")
        phases = [s["phase"] for s in got_steps]
        phase_order = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
        if [phase_order[p] for p in phases] != sorted(phase_order[p] for p in phases):
            ok = False
            print(f"  phase order violated: {phases}")
        print("  RESULT: " + ("PASS" if ok else "FAIL"))
        failures += 0 if ok else 1

    print(f"\n{len(EXPECTED) - failures}/{len(EXPECTED)} custom evaluation cases pass")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
