"""Gemini-backed structured extraction for image and PDF evidence.

Screenshots and PDFs in this dataset are ordinary business artefacts (admin
consoles, statements, registers).  They are the only place where some
resource-to-subject links appear, so they are converted into the same
fact shape the text readers produce and cached on disk to keep reruns free.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request

from .paths import REPO_ROOT

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
CACHE_PATH = os.path.join(REPO_ROOT, "work", "multimodal_cache.json")

FACT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "captured_at": {"type": "STRING"},
        "subjects": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "user_id": {"type": "STRING"},
                    "email": {"type": "STRING"},
                    "phone": {"type": "STRING"},
                    "name": {"type": "STRING"},
                    "email_hash": {"type": "STRING"},
                },
            },
        },
        "resources": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "system_id": {"type": "STRING"},
                    "resource_id": {"type": "STRING"},
                    "record_type": {"type": "STRING"},
                    "state": {"type": "STRING"},
                    "evidence_text": {"type": "STRING"},
                },
            },
        },
        "statements": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
}

PROMPT = """You are reading one business artefact from a commerce platform's privacy-deletion audit.
Extract every concrete fact that helps decide whether a person's personal data still exists.

Report:
* subjects: each person referenced (internal user id, email, phone, display name, email hash) exactly as written.
* resources: each system record identifier visible (for example account_5762, support_9538, audit_record_003,
  tombstone_17, backup_4978, data_flow_1121, marketing_001, export_1260, search_428, analytics_7438,
  legal_hold_4743, cache_9558, email_delivery_354, object_19) with the owning system, the data category,
  and the state shown for it (PRESENT, ABSENT, IDENTIFIABLE, REDACTED, ANONYMIZED, ACTIVE, DISABLED,
  RESTORABLE, BLOCKED, TOMBSTONE, DELETED, NOT_FOUND). Use "" when the artefact does not state a state.
* statements: short verbatim-anchored notes that matter for a deletion audit, such as a connector being
  enabled, a backup being restorable, a retention obligation, or which identifiers a record still carries.
* captured_at: the artefact's own timestamp if printed on it, otherwise "".

Copy identifiers character-for-character. Do not infer identifiers that are not visible.
"""


class Usage:
    def __init__(self):
        self.calls: list[dict] = []

    def add(self, model: str, usage: dict, purpose: str):
        self.calls.append({
            "model": model,
            "purpose": purpose,
            "input_tokens": int(usage.get("promptTokenCount") or usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("candidatesTokenCount") or usage.get("output_tokens") or 0),
            "thinking_tokens": int(usage.get("thoughtsTokenCount") or 0),
        })

    def totals(self):
        out = {}
        for call in self.calls:
            row = out.setdefault(call["model"], {"calls": 0, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0})
            row["calls"] += 1
            for key in ("input_tokens", "output_tokens", "thinking_tokens"):
                row[key] += call[key]
        return out


def _load_cache():
    if os.path.isfile(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as handle:
                return json.load(handle)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=1)


def call_gemini(model: str, parts, schema=None, usage: Usage | None = None, purpose: str = "",
                temperature: float = 0.0, max_retries: int = 3, timeout: int = 180):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    generation = {"temperature": temperature}
    if schema is not None:
        generation["responseMimeType"] = "application/json"
        generation["responseSchema"] = schema
    body = json.dumps({"contents": [{"role": "user", "parts": parts}], "generationConfig": generation}).encode()
    url = f"{API_BASE}/{model}:generateContent?key={key}"
    last_error = None
    for attempt in range(max_retries):
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            candidate = payload["candidates"][0]
            text = "".join(part.get("text", "") for part in candidate.get("content", {}).get("parts", []))
            if usage is not None:
                usage.add(model, payload.get("usageMetadata", {}), purpose)
            return text
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, IndexError) as exc:
            last_error = exc
            detail = ""
            if isinstance(exc, urllib.error.HTTPError):
                detail = exc.read().decode("utf-8", "replace")[:300]
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Gemini call failed for {purpose or model}: {last_error} {detail}")


def image_parts(path: str):
    with open(path, "rb") as handle:
        data = base64.b64encode(handle.read()).decode()
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    return [{"text": PROMPT}, {"inline_data": {"mime_type": mime, "data": data}}]


def _json_from_text(text: str):
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {"subjects": [], "resources": [], "statements": [], "captured_at": ""}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"subjects": [], "resources": [], "statements": [], "captured_at": ""}


def extract_document(path: str, evidence_id: str, model: str = "gemini-3-flash-preview",
                     usage: Usage | None = None, cache: dict | None = None, force: bool = False):
    """Return the structured facts for one image or PDF, using the disk cache."""
    cache = _load_cache() if cache is None else cache
    stat = os.stat(path)
    token = f"{evidence_id}:{stat.st_size}:{int(stat.st_mtime)}"
    hit = cache.get(evidence_id)
    if not force and hit and hit.get("token") == token:
        return hit["facts"]
    ext = os.path.splitext(path)[1].lower()
    if ext in {".pdf"}:
        text = pdf_text(path)
        parts = [{"text": PROMPT + "\n\nDocument text (order preserved):\n" + text[:60000]}]
    else:
        parts = image_parts(path)
    raw = call_gemini(model, parts, schema=FACT_SCHEMA, usage=usage, purpose=f"document:{evidence_id}")
    facts = _json_from_text(raw)
    cache[evidence_id] = {"token": token, "facts": facts, "model": model}
    _save_cache(cache)
    return facts


def pdf_text(path: str) -> str:
    from pypdf import PdfReader
    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)
