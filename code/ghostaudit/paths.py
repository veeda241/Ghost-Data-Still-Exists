"""Filesystem and time helpers shared by the evidence readers."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# ``GHOSTAUDIT_DATASET_DIR`` lets the participant-authored evaluation fixtures in
# ``code/evaluation/custom_cases.py`` point the same engine at a synthetic dataset.
DATASET_DIR = os.path.abspath(os.environ.get("GHOSTAUDIT_DATASET_DIR")
                              or os.path.join(REPO_ROOT, "dataset"))
EVIDENCE_DIR = os.path.join(DATASET_DIR, "evidence")


def dataset_path(*parts: str) -> str:
    return os.path.join(DATASET_DIR, *parts)


def parse_ts(value: str) -> datetime | None:
    """Parse the ISO-8601 timestamps used across the dataset."""
    if value is None:
        return None
    text = str(value).strip().strip('"')
    if not text or text.lower() in {"null", "none", "n/a", "-"}:
        return None
    text = re.sub(r"(\+\d{2})$", r"\g<1>:00", text)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?", text)
        if not match:
            return None
        y, mo, d, h, mi, s = match.groups()
        dt = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0), tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt_ts(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_ident(value: str | None) -> str:
    """Lower-case and strip one layer of quoting for identifier comparisons."""
    if value is None:
        return ""
    return str(value).strip().strip('"').strip("'").lower()
