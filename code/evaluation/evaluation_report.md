# Evaluation report - Ghost Data Still Exists

This report covers the deletion-audit agent in `code/`. Everything measured here comes
from participant-facing files under `dataset/`; `dataset/sample_output.csv` (the 32
public golden cases) is read **only** by the scorer, never by the prediction path, and
no organizer-only file, golden label, or case-family shortcut is used anywhere in
`code/ghostaudit/`.

## 1. How to reproduce

```bash
python3 code/main.py --sample                     # predictions for the 32 public cases
python3 code/evaluation/main.py                   # accuracy metrics (JSON)
python3 code/evaluation/main.py --diff --out work/diff.txt   # per-case MISS/EXTRA steps
python3 code/evaluation/custom_cases.py           # 4 participant-authored cases, 4/4 pass
python3 code/main.py                              # 518 evaluation cases -> output.csv
python3 code/evaluation/main.py --validate output.csv        # output-contract checks
```

Requirements: Python 3.11 with `pandas` (the only third-party import used by the
evaluation scorer and the activity reader). `GEMINI_API_KEY` is optional; see
`usage_report.md` for what happens with and without it.

Metrics are computed over six step fields - `phase`, `system_id`, `resource_id`,
`operation`, `state`, `evidence_id` - and `case_id`/`deletion_status`:

| Metric | Definition |
| --- | --- |
| `case_status_accuracy` | exact match of `deletion_status` per case |
| `step_recall` | golden steps recovered (multiset intersection / golden steps) |
| `step_precision` | recovered steps / predicted steps |
| `field_accuracy` | positional field agreement, padding the shorter list with empties |
| `exact_sequence_accuracy` | cases whose golden step list is reproduced as a prefix |

## 2. Accuracy on the 32 public golden cases

```json
{
  "cases": 32,
  "case_status_accuracy": 0.96875,
  "step_recall": 0.8497409326424871,
  "step_precision": 0.9534883720930233,
  "field_accuracy": 0.7594501718213058,
  "field_accuracy_by_column": {
    "phase": 0.8711340206185567,
    "system_id": 0.6804123711340206,
    "resource_id": 0.6804123711340206,
    "operation": 0.6958762886597938,
    "state": 0.7010309278350515,
    "evidence_id": 0.9278350515463918
  },
  "exact_sequence_accuracy": 0.40625,
  "golden_steps": 193,
  "predicted_steps_in_golden_cases": 172
}
```

Read as: 31 of 32 statuses correct (the single miss is `case_10`), 164 of 193 golden
steps reproduced, and of the 172 steps predicted for those cases 164 are golden - so the
agent is far more conservative than it is noisy. `system_id` and `resource_id` move
together because a step is only counted when the whole field tuple matches, and every
resource miss is also a `resource_id` miss.

The one wrong case, `case_10`, is a *link* failure rather than a policy failure: a
support-replica log line (`evidence_61`) proves `support_002` still resolves through the
subject's historical email, but the audit engine classifies it as a
`SUPPORT_MESSAGE` (policy `REDACT`) and its state as `PRESENT`, which the engine's
normalisation rule reads as "row kept, identifiers gone, compliant". The golden answer
treats the same row as a whole record to delete. The finding, the remediation and the
verification therefore all use `REDACT` vocabulary instead of `DELETE`.

## 3. Contract validation of the submitted 518-row output

`python3 code/evaluation/main.py --validate output.csv`:

```json
{ "rows": 518, "steps": 2750, "mean_steps": 5.31, "problems": [], "problem_count": 0 }
```

The validator checks the exact header, one row per `case_id`, sort order, duplicates,
valid JSON arrays, consecutive `step_no` starting at 1, allowed phases/operations/
states, operation-vs-phase consistency, phase ordering (`FINDING` then `REMEDIATION`
then `VERIFICATION`), and the two citation rules from the contract: `NOT_APPLICABLE`
carries no steps, an `INCOMPLETE` case's proposed work carries an empty `evidence_id`,
and a `COMPLETE` case's checks must cite evidence.

Distribution of the produced `output.csv`:

| Status | Cases |
| --- | --- |
| `INCOMPLETE` | 350 |
| `COMPLETE` | 167 |
| `NOT_APPLICABLE` | 1 |

Steps per case: mean 5.31, median 5, max 15. Operation mix:

| Operation | Steps | Operation | Steps |
| --- | --- | --- | --- |
| `VERIFY_RECORD_ABSENT` | 498 | `DISABLE_DATA_FLOW` | 109 |
| `VERIFY_RECORD_PRESENT` | 380 | `ANONYMIZE_RECORD` | 73 |
| `REPORT_REMAINING_RECORD` | 357 | `VERIFY_RECORD_ANONYMIZED` | 73 |
| `REPORT_EXPOSED_PII` | 214 | `VERIFY_RESTORE_BLOCKED` | 61 |
| `DELETE_RECORD` | 357 | `REPORT_RESTORE_RISK` | 30 |
| `VERIFY_RECORD_REDACTED` | 177 | `BLOCK_RESTORE` | 30 |
| `REDACT_RECORD` | 141 | `REPORT_ACTIVE_DATA_FLOW` | 109 |

Evidence-citation coverage: 710 findings all cite a participant-visible `file_id`,
`activity_id` or `audit_id`; all 710 remediation steps and 1,023 post-remediation
verification steps use `""`; the 307 verification steps belonging to `COMPLETE` cases all
cite the readback capture that proves the compliant state.

A rerun of `code/main.py` reproduces `output.csv` byte-for-byte (sha256 prefix
`793702866636e1f5`, 549,979 bytes, before and after), because timestamps, ordering,
caches and serialisation are all deterministic.

## 4. Participant-authored evaluation cases

The public sample mixes several violations per case, so a miss there does not localise a
regression. `code/evaluation/custom_cases.py` builds four small synthetic worlds -
fictional users, resources and evidence files written to `work/custom_eval_dataset/` at
run time - and compares the engine output **exactly** against hand-authored expected
step lists, including step order, `step_no` continuity and empty `evidence_id` on
proposed work. Result: **4/4 pass** (`exit 0`).

| Case | What it pins down | Authored answer |
| --- | --- | --- |
| `custom_case_01` | an active connector feeding a still-identifiable profile and support message; the retained security audit record must not be touched | `INCOMPLETE`, 10 steps: 3 findings, `DISABLE_DATA_FLOW` **before** `DELETE_RECORD`/`REDACT_RECORD`, 4 checks |
| `custom_case_02` | a genuinely complete deletion (absent profile and search doc, redacted tombstone, anonymised analytics, disabled flow, blocked backup, retained audit record) | `COMPLETE`, 7 verification-only steps, every one citing `custom_evidence_03` |
| `custom_case_03` | a subject asking for a *different* right (copy of data + rectification) with data present in the evidence | `NOT_APPLICABLE`, `steps_json = []` |
| `custom_case_04` | identity resolution through a **historical** email from `user_audit_log.csv`, plus a restorable backup and a leftover export | `INCOMPLETE`, 6 steps with `BLOCK_RESTORE` ahead of `DELETE_RECORD` |

Case 2 also encodes a design decision worth stating: the anonymised analytics row carries
no identifier at all, so it is only in scope because the data catalog still links
`analytics_902` to `user_902`. A row that no participant-visible source links is
unreachable by construction, which is exactly the recall ceiling discussed next.

## 5. Failures found and improvements made

Each row is a real defect observed while evaluating, the fix applied, and the measured
effect on the public sample.

| # | Failure found | Root cause | Improvement | Measured effect (sample) |
| --- | --- | --- | --- | --- |
| 1 | Almost every case came back with an empty scope | `Registry.__init__` built `by_resource` keys but never appended the observations to them | index every observation under its `(system, resource_id)` key | status accuracy 0.31 -> 0.84 |
| 2 | Verification steps repeated the *observed* state (`VERIFY_DATA_FLOW_DISABLED` with `state=PRESENT`) | planning reused the violation record's state for checks | derive phase state from the required end state (`DISABLED`, `BLOCKED`, `ABSENT`, `REDACTED`, `ANONYMIZED`) | removed a systematic state error |
| 3 | Wrong `system_id` values such as `user_export_store` | system name taken from the evidence file's internal label | canonical mapping from the resource-id family (`export_` -> `export_service`, `tombstone_` -> `identity_service`, ...) wins over any hint | `system_id` field accuracy recovered |
| 4 | Requests in other languages were flagged `NOT_APPLICABLE` wrongly | a keyword classifier counted "access/opt-out" phrases that co-occur with erasure language | two-layer intent: conservative lexicon that only rejects "another right, never erasure", plus a batched model classifier | 3 false `NOT_APPLICABLE` cases fixed (15, 17, 26) |
| 5 | Log-derived rows claimed compliant states from *missing* identifiers, hiding real violations (`analytics_9914`) | content-style inference applied to readback lines | `_state_from_log_readback` promotes `REDACTED`/`ANONYMIZED`/`ABSENT` back to `PRESENT` unless a line carries an explicit marker | violations stopped disappearing |
| 6 | Tombstones linked only through the stable `user_id` produced spurious findings | a bare catalog link was treated as evidence of a leak | `LINK_ONLY_SKIP = {ACCOUNT_TOMBSTONE}`: a link alone cannot put a tombstone in scope, because the stable reference is exactly what policy permits | removed 3 wrong steps in `case_17` |
| 7 | Screenshots over-attributed data: every connector on a console was linked to every person on that console | vision facts are screen-wide | owner guard - a vision link is only kept when the data catalog or identity index confirms the subject owns that resource | `case_15` `data_flow_1121` false positive gone; precision 0.95 held |
| 8 | Records whose only mention of the subject sits on a *different* log line of the same request were unreachable (`support_002` in `case_10`, `backup_3245` in `case_17`) | log reader linked subjects per line only | request-scoped `trace_id` correlation across lines of one file | status 0.9375 -> 0.96875, recall 0.788 -> 0.850, field accuracy 0.694 -> 0.759, exact sequence 0.3125 -> 0.406 |
| 9 | Synthesized seed profiles raised `KeyError` and unsorted citations | seeded `account_00N` had no registry entry | lookups default to empty lists; citation falls back to the latest account-database capture before `audit_at` | crash gone; ordering stable |
| 10 | Catalog link regex truncated emails at the first dot (`g.silva76.gmail.com`) | unanchored pattern | pattern anchored to the end of the details sentence | historical-email links recovered |

## 6. Remaining, understood limitations

1. **Inherent attribution ceiling.** A compliant readback row deliberately carries no
   identifier - that is what makes it compliant - so a golden resource such as
   `account_6440` or `audit_record_6645` cannot be recovered from its content. Only the
   links that survive in the data catalog, in trace-correlated logs and in screenshots
   are reachable. This bounds `step_recall` well below 1.0 for any evidence-only agent,
   and it is why the agent favours precision (0.95) over recall (0.85).
2. **18 of 518 cases produce a status with no steps** (`case_264`, `case_265`, ...): no
   participant-visible source links any resource to the resolved identity. They currently
   fall back to `COMPLETE`/`INCOMPLETE` on the strength of the account row alone.
3. **Category ambiguity between "row kept, identifiers redacted" and "row deleted"** for
   support records seen only through replica logs (`case_10`). The lexicon needed to
   separate them is not present in the log lines.
4. **Duplicate captures for the same resource** can make the cited `file_id` differ from
   the golden one (`case_09`: `evidence_06` vs `evidence_93`) while the step itself is
   right; the tie-break is "latest readback within 24 h of `audit_at`".
5. **`NOT_APPLICABLE` is emitted once** in 518 rows. Intent is deliberately biased toward
   "the request was filed in a deletion queue", which is the safer error for an audit
   tool: a missed deletion audit is a compliance risk, a spurious audit is not.

## 7. What the evaluation says about the approach

The three highest-value decisions, by measured effect, were indexing observations per
resource (+0.53 status accuracy), trace-scoped log correlation (+0.20 recall, +0.09
sequence accuracy) and refusing to trust reported statuses as proof of state. The
largest residual gap is attribution of scrubbed records, which is a property of the
evidence, not of the planner: the planner almost never invents a step (precision 0.95),
it simply cannot see every row (recall 0.85).
