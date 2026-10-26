"""Ghost Data Still Exists - deletion-audit agent entry point.

    python3 code/main.py            # writes output.csv at the repository root
    python3 code/main.py --sample   # writes work/sample_output.predicted.csv instead
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ghostaudit.audit import AuditEngine
from ghostaudit.identity import IdentityIndex
from ghostaudit.intent_model import classify
from ghostaudit.paths import DATASET_DIR, REPO_ROOT
from ghostaudit.policy import load_rules
from ghostaudit.registry import Registry
from ghostaudit.vision import Usage

FIELDS = ["case_id", "deletion_status", "steps_json"]


def read_cases(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def solve(cases_path, usage=None):
    identities = IdentityIndex()
    registry = Registry(identities=identities)
    engine = AuditEngine(registry, load_rules(os.path.join(DATASET_DIR, "deletion_rules.csv")))
    cases = read_cases(cases_path)
    authorised, usage = classify(cases_texts(cases), usage)
    rows = []
    for case, allowed in zip(cases, authorised):
        identity = identities.resolve(case.get("email", ""))
        if not allowed:
            status, steps = "NOT_APPLICABLE", []
        else:
            status, steps = engine.run(case, identity)
        rows.append({"case_id": case["case_id"], "deletion_status": status,
                     "steps_json": json.dumps(steps, ensure_ascii=False,
                                              separators=(",", ":"))})
    rows.sort(key=lambda row: row["case_id"])
    return rows, usage


def cases_texts(cases):
    return [case.get("request_text", "") for case in cases]


def write_rows(rows, path):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="store_true",
                        help="run the 32 public sample requests instead of the 518 evaluation set")
    parser.add_argument("--out", default=None, help="override the output path")
    args = parser.parse_args()
    cases_path = os.path.join(DATASET_DIR, "sample_deletion_requests.csv" if args.sample
                              else "deletion_requests.csv")
    out_path = args.out or (os.path.join(REPO_ROOT, "work", "sample_predicted.csv")
                            if args.sample else os.path.join(REPO_ROOT, "output.csv"))
    rows, usage = solve(cases_path, Usage())
    write_rows(rows, out_path)
    counts = {}
    for row in rows:
        counts[row["deletion_status"]] = counts.get(row["deletion_status"], 0) + 1
    print(f"wrote {len(rows)} rows to {out_path}: {counts}")
    log_path = os.path.join(REPO_ROOT, "work", "usage_last_run.json")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as handle:
        json.dump({"cases": len(rows), "calls": usage.calls, "totals": usage.totals()},
                  handle, indent=1)


if __name__ == "__main__":
    main()
