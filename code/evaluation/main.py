"""Score predicted rows against ``dataset/sample_output.csv`` and print case diffs.

    python3 code/evaluation/main.py [--pred work/sample_predicted.csv]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))

FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def load(path):
    frame = pd.read_csv(path, keep_default_na=False, dtype=str)
    return {row["case_id"]: (row["deletion_status"], json.loads(row["steps_json"] or "[]"))
            for _, row in frame.iterrows()}


def step_key(step):
    return tuple(step.get(field, "") for field in FIELDS)


def score(pred, gold):
    cases = sorted(gold)
    status_hit = sum(1 for c in cases if pred.get(c, ("", []))[0] == gold[c][0])
    field_hits = field_total = 0
    per_field = {field: [0, 0] for field in FIELDS}
    step_hits = step_total = predicted_steps = 0
    exact_sequence = 0
    for case in cases:
        p_status, p_steps = pred.get(case, ("", []))
        g_status, g_steps = gold[case]
        step_total += len(g_steps)
        predicted_steps += len(p_steps)
        g_keys = [step_key(s) for s in g_steps]
        p_keys = [step_key(s) for s in p_steps]
        matched = sum(1 for key in p_keys if key in set(g_keys))
        step_hits += min(matched, len(g_keys))
        for index in range(max(len(g_keys), len(p_keys))):
            gk = g_keys[index] if index < len(g_keys) else ("",) * len(FIELDS)
            pk = p_keys[index] if index < len(p_keys) else ("",) * len(FIELDS)
            for position, field in enumerate(FIELDS):
                field_total += 1
                per_field[field][1] += 1
                if gk[position] == pk[position]:
                    field_hits += 1
                    per_field[field][0] += 1
        if g_keys and p_keys[:len(g_keys)] == g_keys:
            exact_sequence += 1
    return {
        "cases": len(cases),
        "case_status_accuracy": status_hit / len(cases),
        "step_recall": step_hits / step_total if step_total else 0.0,
        "step_precision": step_hits / predicted_steps if predicted_steps else 0.0,
        "field_accuracy": field_hits / field_total if field_total else 0.0,
        "field_accuracy_by_column": {field: hits / total
                                      for field, (hits, total) in per_field.items()},
        "exact_sequence_accuracy": exact_sequence / len(cases),
        "golden_steps": step_total,
        "predicted_steps_in_golden_cases": predicted_steps,
    }


PHASES = {"FINDING", "REMEDIATION", "VERIFICATION"}
OPERATIONS = {
    "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW",
    "REPORT_RESTORE_RISK", "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD",
    "REDACT_RECORD", "ANONYMIZE_RECORD", "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT",
    "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED",
    "VERIFY_RESTORE_BLOCKED",
}
STATES = {"PRESENT", "ACTIVE", "DISABLED", "BLOCKED", "ABSENT", "REDACTED", "ANONYMIZED"}
PHASE_RANK = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
OP_PHASE = {op: ("FINDING" if op.startswith("REPORT_") else
                 "REMEDIATION" if op.startswith(("DISABLE_", "BLOCK_", "DELETE_", "REDACT_",
                                                 "ANONYMIZE_")) else "VERIFICATION")
            for op in OPERATIONS}


def validate(path):
    """Check the serialisation contract for a predicted output file."""
    frame = pd.read_csv(path, keep_default_na=False, dtype=str)
    problems = []
    if list(frame.columns) != ["case_id", "deletion_status", "steps_json"]:
        problems.append(f"header is {list(frame.columns)}")
    if sorted(frame["case_id"].tolist()) != frame["case_id"].tolist():
        problems.append("rows are not sorted by case_id")
    if frame["case_id"].duplicated().any():
        problems.append("duplicate case_id values")
    if not set(frame["deletion_status"]) <= {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}:
        problems.append("deletion_status outside the allowed vocabulary")
    total_steps = 0
    for _, row in frame.iterrows():
        try:
            steps = json.loads(row["steps_json"] or "[]")
        except json.JSONDecodeError:
            problems.append(f"{row['case_id']}: steps_json is not valid JSON")
            continue
        if not isinstance(steps, list):
            problems.append(f"{row['case_id']}: steps_json is not an array")
            continue
        total_steps += len(steps)
        if row["deletion_status"] == "NOT_APPLICABLE" and steps:
            problems.append(f"{row['case_id']}: NOT_APPLICABLE must have no steps")
        ranks = []
        for index, step in enumerate(steps, start=1):
            if set(step) != {"step_no", "phase", "system_id", "resource_id",
                             "operation", "state", "evidence_id"}:
                problems.append(f"{row['case_id']}/{index}: unexpected step fields")
            if step.get("step_no") != index:
                problems.append(f"{row['case_id']}/{index}: step_no is not consecutive")
            if step.get("phase") not in PHASES:
                problems.append(f"{row['case_id']}/{index}: unknown phase")
            elif OP_PHASE.get(step.get("operation")) != step["phase"]:
                problems.append(f"{row['case_id']}/{index}: operation/phase mismatch")
            if step.get("operation") not in OPERATIONS:
                problems.append(f"{row['case_id']}/{index}: unknown operation")
            if step.get("state") not in STATES:
                problems.append(f"{row['case_id']}/{index}: unknown state")
            if row["deletion_status"] == "INCOMPLETE" and step.get("phase") != "FINDING" \
                    and step.get("evidence_id"):
                problems.append(f"{row['case_id']}/{index}: proposed work must not cite evidence")
            if row["deletion_status"] == "COMPLETE" and not step.get("evidence_id"):
                problems.append(f"{row['case_id']}/{index}: complete-case check must cite evidence")
            ranks.append(PHASE_RANK.get(step.get("phase"), 9))
        if ranks != sorted(ranks):
            problems.append(f"{row['case_id']}: phases are out of order")
    return {"rows": len(frame), "steps": total_steps,
            "mean_steps": round(total_steps / len(frame), 2) if len(frame) else 0,
            "problems": problems[:25], "problem_count": len(problems)}


def diff(pred, gold, cases=None, out=None):
    lines = []
    for case in cases or sorted(gold):
        p_status, p_steps = pred.get(case, ("", []))
        g_status, g_steps = gold[case]
        p_keys, g_keys = {step_key(s) for s in p_steps}, {step_key(s) for s in g_steps}
        if p_status == g_status and p_keys == g_keys:
            continue
        lines.append(f"===== {case} pred={p_status} gold={g_status} "
                     f"steps pred={len(p_steps)} gold={len(g_steps)}")
        for s in g_steps:
            if step_key(s) not in p_keys:
                lines.append("  MISS " + json.dumps(s, sort_keys=True))
        for s in p_steps:
            if step_key(s) not in g_keys:
                lines.append("  EXTRA " + json.dumps(s, sort_keys=True))
    text = "\n".join(lines)
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        print(text)
    return text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pred", default=os.path.join(REPO_ROOT, "work",
                                                       "sample_predicted.csv"))
    parser.add_argument("--gold", default=os.path.join(REPO_ROOT, "dataset",
                                                       "sample_output.csv"))
    parser.add_argument("--diff", action="store_true")
    parser.add_argument("--validate", default=None, metavar="CSV",
                        help="check a predicted file against the output contract only")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    if args.validate:
        print(json.dumps(validate(args.validate), indent=2))
        return
    pred, gold = load(args.pred), load(args.gold)
    metrics = score(pred, gold)
    print(json.dumps(metrics, indent=2))
    if args.diff:
        diff(pred, gold, out=args.out)


if __name__ == "__main__":
    main()
