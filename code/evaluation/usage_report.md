# Token and cost usage report - Ghost Data Still Exists

Scope: the model usage attributable to the run that produced the submitted root
`output.csv` (518 cases, 2,750 steps). Development-time usage by the coding agent is
reported separately in section 6 and is not part of the solution's runtime cost.

## 1. Providers and models

| Provider | Model | Where used | Calls | Input tokens | Output tokens |
| --- | --- | --- | --- | --- | --- |
| Google (Generative Language API, `v1beta generateContent`) | `gemini-3-flash-preview` | multimodal fact extraction from PNG/PDF evidence (`code/extract_media.py`, `ghostaudit/vision.py`) | 15 | 16,574 (estimated) | 4,688 (estimated) |
| Google (Generative Language API, `v1beta generateContent`) | `gemini-3-flash-preview` | deletion-intent classification of `request_text` (`ghostaudit/intent_model.py`) | 2 | 3,500 (measured) | 946 measured + 3,363 thinking |
| **Total** | one model | | **17** | **20,074** | **8,997** (incl. thinking) |

No other provider, model, embedding index, or vector store is used. Everything else in
the pipeline is deterministic code: CSV/JSON/log/SQLite readers, an identity index, a
resource registry, a point-in-time state rebuild, and the policy planner in
`ghostaudit/audit.py`. SQLite evidence is executed locally with Python's standard
`sqlite3`, not through a model.

## 2. Final full-dataset run

`python3 code/main.py` over all 518 cases made **zero live model calls** on the machine
that produced `output.csv`, because both model-backed steps are content-addressed caches:

* `work/multimodal_cache.json` - keyed by `file_id` plus file size and mtime; 15 artefacts
  (11 screenshots, 4 PDFs) and their extracted facts;
* `work/intent_cache.json` - keyed by `sha256(request_text)[:16]`; 106 distinct request
  texts, so the 518 cases need only 106 classifications.

The 17 calls in section 1 are therefore the cost of building those caches, i.e. the cost
of the run that produced this `output.csv`. A cold clean run on a fresh checkout pays the
same 17 calls; every later run pays none. Verified by re-running the full pipeline and
comparing bytes: identical (`sha256` prefix `793702866636e1f5`).

Runtime of the deterministic pass over 518 cases: 10.2 s on a Windows laptop, reading
94 evidence artefacts (28 JSON, 26 logs, 24 SQL captures executed through SQLite, 11 PNG,
4 PDF, 1 text register).

## 3. Per-request averages (518 evaluation cases)

| Measure | Value |
| --- | --- |
| Model calls per case | 17 / 518 = 0.033 (0 on a warm cache) |
| Tokens per case, all-in | (20,074 + 8,997) / 518 = 56.1 |
| Input tokens per case | 20,074 / 518 = 38.8 |
| Output tokens per case | 8,997 / 518 = 17.4 |
| Estimated cost per case | USD 0.000071 |
| **Estimated total cost for the dataset** | **USD 0.037** |

For the 32-case public sample the same accounting gives 0.53 calls and 893 tokens per
case, because the caches are dominated by the fixed 15 artefacts.

## 4. Cost basis and how the estimates were derived

Pricing used is the published Gemini Developer API standard-tier rate for
`gemini-3-flash-preview` as of 2026-10-25: **USD 0.50 per 1M input tokens** and
**USD 3.00 per 1M output tokens** (thinking tokens billed at the output rate).
Total: `20,074 x 0.50 + 8,997 x 3.00` per 1M = **USD 0.0370**.

* Intent classification (2 calls) - **measured**: the response `usageMetadata`
  (`promptTokenCount`, `candidatesTokenCount`, `thoughtsTokenCount`) is appended to
  `work/usage_last_run.json` by `ghostaudit/vision.py::Usage` on every call:
  `1,751 in / 473 out / 2,200 thinking` and `1,749 in / 473 out / 1,163 thinking`.
* Media extraction (15 calls) - **estimated**, because those calls were originally made
  by an ad-hoc extraction script that did not persist `usageMetadata`; the numbers are
  reproducible from the artefacts themselves:
  * prompt: the 1,203-character extraction instruction in `ghostaudit/vision.py` = 301
    tokens (characters / 4);
  * screenshot: a fixed vision block of 1,024 tokens for an 1800x1200 dashboard PNG
    (Google bills images by 768 px tiles at ~258 tokens, ~4 tiles here);
  * PDF: `pypdf`-extracted text (616-1,003 characters) = 154-251 tokens;
  * output: length of the cached fact JSON / 4 (159-523 tokens per artefact).
  A re-measurement pass (`python3 code/extract_media.py --force`) hit the free-tier
  `429 quota exceeded` limit on the first call, so the estimates stand rather than a
  refreshed measurement; the cache used for `output.csv` was left untouched (verified
  byte-identical to the pre-attempt backup).

## 5. Degraded and offline modes

* No `GEMINI_API_KEY`: `intent_model.classify()` returns the deterministic lexicon
  verdict and `extract_media.py` skips - the pipeline still produces a complete 518-row
  `output.csv`. Only the ~20 links that screenshots and PDFs contribute, and any intent
  judgement the lexicon cannot make, are lost.
* API failure: `call_gemini` retries three times with exponential backoff, then raises a
  structured error naming the artefact and purpose - no silent fallback to a placeholder
  and no invented facts.
* Responses are schema-constrained (`responseMimeType=application/json` +
  `responseSchema`), `temperature=0.0`, so extractions and intent verdicts are stable
  across reruns.

## 6. Development-time agent usage (not part of the solution runtime)

The code, prompts, evaluation harness and this report were written with the **Qoder CLI**
coding agent, runtime model identifier `qfmodel` (Qwen3.8-Flash, per the harness routing
log `Validated model 'qfmodel' (Qwen3.8-Flash) from catalog`). That usage is billed
through the IDE subscription, is not token-accounted by this repository, and is recorded
turn-by-turn in the root `log.txt` submitted as the chat transcript. No keys, tokens or
credentials appear in prompts, code, `log.txt`, or these reports.

## 7. Cost-control decisions worth repeating

1. Cache multimodal extraction by content hash - the 15 artefacts would otherwise be
   re-read on every one of the 518 cases.
2. Batch intent classification (40 texts per call) and deduplicate by request text -
   518 requests collapse to 106 unique texts, and only 2 calls were needed after the
   first pass.
3. Keep the model in the *linking* and *intent* steps only. State reconstruction, policy
   application and step ordering are plain code, which is what makes the 518-row rerun
   byte-identical and free.
