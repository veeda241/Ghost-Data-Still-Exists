"""Model-assisted authorisation check for request texts.

The deterministic classifier in :mod:`ghostaudit.intent` is the authority; this module
only flags requests whose *only* ask is a different data-subject right (access,
rectification, export, marketing opt-out). Results are cached on disk, so a rerun costs
nothing, and everything degrades to the deterministic answer when no API key is present.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

from .intent import authorizes_deletion
from .paths import REPO_ROOT
from .vision import call_gemini

CACHE_PATH = os.path.join(REPO_ROOT, "work", "intent_cache.json")
MODEL = os.environ.get("GHOST_INTENT_MODEL", "gemini-3-flash-preview")
BATCH = 40

SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "index": {"type": "INTEGER"},
            "authorizes_deletion": {"type": "BOOLEAN"},
        },
        "required": ["index", "authorizes_deletion"],
    },
}

PROMPT = """You classify privacy requests filed with an e-commerce platform.

For each numbered request below answer `authorizes_deletion = true` when the person asks
that their personal data or account be deleted, erased, removed, closed permanently, or
that a completed deletion be verified.

Answer `false` when the request only asks for another data-subject right: a copy or
export of their data, access to what is held, correction of a field, a marketing or email
opt-out, a password or contact-detail change, or a complaint about a decision.

Requests may be written in any language. Reply with a JSON array only, one object per
request, using the given index.

"""


def _cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    return {}


def _digest(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def _ask_model(texts, usage):
    body = PROMPT + "\n".join(f"{i}. {t.strip()[:400]}" for i, t in enumerate(texts))
    raw = call_gemini(MODEL, [{"text": body}], schema=SCHEMA, usage=usage,
                      purpose="intent_classification")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", raw, re.S)
        payload = json.loads(match.group(0)) if match else []
    answers = {}
    for item in payload if isinstance(payload, list) else []:
        try:
            answers[int(item["index"])] = bool(item["authorizes_deletion"])
        except (KeyError, TypeError, ValueError):
            continue
    return answers


def classify(texts, usage=None, allow_model=True):
    """Return one boolean per request: does it authorise deletion?"""
    cache = _cache()
    authorised = [authorizes_deletion(text) for text in texts]
    if not allow_model or not os.environ.get("GEMINI_API_KEY"):
        return authorised, usage
    todo = [(i, text) for i, text in enumerate(texts)
            if authorised[i] and _digest(text) not in cache]
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        try:
            answers = _ask_model([text for _, text in chunk], usage)
        except Exception:  # a model outage must not stop the audit
            break
        for position, (index, text) in enumerate(chunk):
            flag = answers.get(position, answers.get(index))
            if flag is None:
                continue
            cache[_digest(text)] = flag
            authorised[index] = authorised[index] and flag
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as handle:
        json.dump(cache, handle, indent=1, sort_keys=True)
    return authorised, usage
