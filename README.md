# Ghost-Data-Still-Exists

Northstar Shop (an imaginary company) lost $2.4 million after one supposedly deleted customer came back as a ghost in its systems. Every internal dashboard showed the deletion request as “Complete.” Weeks later, the same customer tried to create a new account. The platform refused: “This email is already registered.”

This repository holds my solution to the **HackerRank Orchestrate (October 2026)** challenge
[*Ghost Data Still Exists*](https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists):
an AI deletion-audit agent that decides, for 518 GDPR-style erasure requests, whether a customer's
personal data is actually gone across databases, replicas, search indexes, support logs, exports,
analytics, backups and live connectors - and produces the ordered remediation and verification plan
when it is not.

## Design

The agent is a **deterministic evidence engine** with two deliberately narrow model-backed steps.
The model never decides a status, a state, or a step.

| Module | Responsibility |
| --- | --- |
| [code/ghostaudit/identity.py](code/ghostaudit/identity.py) | current + historical identifiers (emails, phones, names, hashes) per `user_id` |
| [code/ghostaudit/evidence.py](code/ghostaudit/evidence.py) | one reader per evidence shape - JSON, structured logs, text registers, SQL captures executed via `sqlite3` - into normalised `Observation` records |
| [code/ghostaudit/registry.py](code/ghostaudit/registry.py) | links identifiers to resources from four sources: evidence content, `LINK_RESOURCE_IDENTITY` catalog rows, `trace_id`-correlated log lines, cached vision facts |
| [code/ghostaudit/intent.py](code/ghostaudit/intent.py) / [intent_model.py](code/ghostaudit/intent_model.py) | does this text authorise erasure? conservative multilingual lexicon, plus a batched Gemini classifier cached by request text |
| [code/ghostaudit/audit.py](code/ghostaudit/audit.py) | rebuilds state at `audit_at`, applies `deletion_rules.csv`, emits ordered `FINDING` / `REMEDIATION` / `VERIFICATION` steps |
| [code/ghostaudit/vision.py](code/ghostaudit/vision.py) | schema-constrained fact extraction from 11 screenshots and 4 PDFs (linking only, never state invention) |
| [code/evaluation/main.py](code/evaluation/main.py) | accuracy scorer (case / step / field / sequence) and output-contract validator |
| [code/evaluation/custom_cases.py](code/evaluation/custom_cases.py) | four participant-authored synthetic worlds with hand-authored expected steps |

Two principles carry most of the score:

1. **Reported statuses are claims, not proof.** A workflow that says `SUCCESS` does not prove the row
   is gone; only an at-or-before-`audit_at` readback does. A log line showing that a lookup *ran* can
   never be reinterpreted as "identifiers were scrubbed".
2. **Attribution is the real problem.** The manifest never says which case a file concerns, so links
   are reconstructed from identifier content, catalog rows, request-scoped `trace_id`s and vision
   facts - with an owner guard so a console screenshot cannot attribute every row on screen to every
   person on it.

## Run

```bash
pip install pandas                                    # only third-party runtime dependency
git clone https://github.com/interviewstreet/hackerrank-orchestrate-october26
cp -r hackerrank-orchestrate-october26/dataset ./dataset     # participant data, not redistributed here
python3 code/main.py                                  # -> ./output.csv (518 rows)
python3 code/main.py --sample                         # 32 public cases -> work/sample_predicted.csv
python3 code/evaluation/main.py                       # metrics vs dataset/sample_output.csv
python3 code/evaluation/custom_cases.py               # authored cases: 4/4 pass
python3 code/evaluation/main.py --validate output.csv # contract checks
python3 code/extract_media.py                         # optional: rebuild vision cache (GEMINI_API_KEY)
```

`GEMINI_API_KEY` is read from the environment only. Without it the agent still completes all 518
cases: intent falls back to the lexicon and cached vision facts are reused. Reruns are byte-identical
(`output.csv` sha256 prefix `793702866636e1f5`).

## Results

Public 32 golden cases:

| case status | step recall | step precision | field accuracy | exact sequence |
| --- | --- | --- | --- | --- |
| 0.969 (31/32) | 0.850 (164/193) | 0.953 | 0.759 | 0.406 |

Submitted output: 518 rows, 2,750 steps, 0 contract violations. Model spend for the whole dataset:
17 calls to `gemini-3-flash-preview`, ~29k tokens, about USD 0.037 (56 tokens per case).

Full failure analysis, the ten defects found and fixed, and the known limits are in
[code/evaluation/evaluation_report.md](code/evaluation/evaluation_report.md); token and cost accounting
is in [code/evaluation/usage_report.md](code/evaluation/usage_report.md).

## Honest limits

* A compliant readback row deliberately carries no identifier, so some golden resources are
  unreachable from evidence alone - the reason the agent is precision-biased (0.95) rather than
  recall-chasing (0.85).
* 18 of 518 cases have no attributable resource at all.
* Support records seen only through replica logs are ambiguous between "redacted, compliant" and
  "must be deleted" (`case_10` is the one public status miss).

No golden label, organizer file, or per-case shortcut is read by the prediction path;
`dataset/sample_output.csv` is opened only by the scorer.
