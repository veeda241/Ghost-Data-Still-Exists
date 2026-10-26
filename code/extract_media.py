"""Extract structured facts from image and PDF evidence with Gemini, into the on-disk cache.

Screenshots and PDFs are the only place a few resource-to-subject links appear, so the
audit engine reads them from ``work/multimodal_cache.json``.  That cache is built here:

    python3 code/extract_media.py              # extract anything not already cached
    python3 code/extract_media.py --force      # re-call every artefact and record usage
    python3 code/extract_media.py --usage-out work/usage_multimodal.json

Requires ``GEMINI_API_KEY`` in the environment.  Text, JSON, log and SQL evidence needs
no model: the deterministic readers in ``ghostaudit/evidence.py`` parse those directly.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ghostaudit.evidence import load_manifest
from ghostaudit.paths import DATASET_DIR, REPO_ROOT
from ghostaudit.vision import Usage, extract_document

MEDIA = (".png", ".jpg", ".jpeg", ".pdf")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-call artefacts already cached")
    parser.add_argument("--model", default="gemini-3-flash-preview")
    parser.add_argument("--usage-out", default=os.path.join(REPO_ROOT, "work",
                                                            "usage_multimodal.json"))
    args = parser.parse_args()

    usage = Usage()
    called = 0
    for entry in load_manifest():
        rel = str(entry["file_path"])
        if not rel.lower().endswith(MEDIA):
            continue
        base = DATASET_DIR if rel.startswith(("evidence/", "evidence" + os.sep)) \
            else os.path.join(REPO_ROOT, "dataset")
        path = os.path.join(base, rel.replace("/", os.sep))
        if not os.path.isfile(path):
            continue
        facts = extract_document(path, entry["file_id"], model=args.model,
                                 usage=usage, force=args.force)
        if any(call["purpose"].endswith(entry["file_id"]) for call in usage.calls):
            called += 1
            print(f"{entry['file_id']}: {len(facts.get('subjects') or [])} subjects, "
                  f"{len(facts.get('resources') or [])} resources")
    totals = usage.totals()
    with open(args.usage_out, "w", encoding="utf-8") as handle:
        json.dump({"artefacts_processed": called, "calls": usage.calls, "totals": totals},
                  handle, indent=1)
    print(f"extracted {called} media artefacts in {len(usage.calls)} model calls -> {args.usage_out}")
    print(json.dumps(totals, indent=1))


if __name__ == "__main__":
    main()
