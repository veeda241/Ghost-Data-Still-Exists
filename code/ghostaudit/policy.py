"""Deletion policy: map a data category onto its required treatment and states."""
from __future__ import annotations

import csv
import re

from .evidence import (ABSENT, ACTIVE, ANONYMIZED, BLOCKED, DISABLED, IDENTIFIABLE, PRESENT,
                       REDACTED, RESTORABLE)

DELETE = "DELETE"
REDACT = "REDACT"
ANONYMIZE = "ANONYMIZE"
RETAIN = "RETAIN"

# Categories named in deletion_rules.csv, normalised.
RULE_CATEGORY = {
    "USER_PROFILE": DELETE,
    "SECONDARY_IDENTIFIER": DELETE,
    "SEARCH_INDEX_ENTRY": DELETE,
    "CACHE_ENTRY": DELETE,
    "SUPPORT_MESSAGE": REDACT,
    "SUPPORT_ATTACHMENT": DELETE,
    "ANALYTICS_PROFILE": ANONYMIZE,
    "PRODUCT_TELEMETRY": ANONYMIZE,
    "USER_EXPORT": DELETE,
    "MARKETING_PROFILE": DELETE,
    "EMAIL_DELIVERY_LOG": REDACT,
    "SECURITY_AUDIT_LOG": RETAIN,
    "LEGAL_HOLD_RECORD": RETAIN,
    "BACKUP_ARCHIVE": RETAIN,
    "ACCOUNT_TOMBSTONE": REDACT,
    "DATA_FLOW": "CONTROL",
}

# Categories whose records may legitimately disappear without a violation.
DELETABLE = {DELETE}

FINDING_OP = {
    DELETE: "REPORT_REMAINING_RECORD",
    REDACT: "REPORT_EXPOSED_PII",
    ANONYMIZE: "REPORT_EXPOSED_PII",
}
REMEDIATION_OP = {DELETE: "DELETE_RECORD", REDACT: "REDACT_RECORD", ANONYMIZE: "ANONYMIZE_RECORD"}
VERIFY_OP = {DELETE: "VERIFY_RECORD_ABSENT", REDACT: "VERIFY_RECORD_REDACTED",
             ANONYMIZE: "VERIFY_RECORD_ANONYMIZED", RETAIN: "VERIFY_RECORD_PRESENT"}
VERIFY_STATE = {DELETE: ABSENT, REDACT: REDACTED, ANONYMIZE: ANONYMIZED, RETAIN: PRESENT}

FINDING_ORDER = ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW",
                 "REPORT_RESTORE_RISK"]
REMEDIATION_ORDER = ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD",
                     "ANONYMIZE_RECORD"]
VERIFY_ORDER = ["VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
                "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"]


def load_rules(path: str):
    """Read ``deletion_rules.csv`` and keep the category -> action mapping data driven."""
    actions = {}
    with open(path, encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            category = re.sub(r"[^A-Z_]", "", str(row["data_category"]).upper())
            action = str(row["required_action"]).strip().upper()
            if category and action:
                actions[category] = action
    for category, action in RULE_CATEGORY.items():
        actions.setdefault(category, action)
    return actions


def compliant(category: str, action: str, state: str) -> bool:
    if action == DELETE:
        return state == ABSENT
    if action == REDACT:
        return state in (REDACTED, ANONYMIZED)
    if action == ANONYMIZE:
        return state == ANONYMIZED
    if action == RETAIN:
        return state != ABSENT
    if action == "CONTROL":
        return state in (DISABLED, BLOCKED, ABSENT)
    return True
