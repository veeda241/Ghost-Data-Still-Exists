"""Resource registry: every participant-visible link between an identifier and a resource.

Three link sources are merged:
  * structured evidence rows (each row already carries its own subject tokens);
  * ``LINK_RESOURCE_IDENTITY`` data-catalog activities;
  * multimodal facts extracted from screenshots and PDFs (links only, never states).
"""
from __future__ import annotations

import json
import os
import re

import pandas as pd

from .evidence import Observation, build_observations, load_manifest, system_for
from .identity import norm_ident
from .paths import DATASET_DIR, REPO_ROOT

RESOURCE_ID_RE = re.compile(r"^(account|analytics|audit_record|backup|cache|data_flow|export|"
                            r"legal_hold|marketing|object|search|support|telemetry|tombstone|"
                            r"email_delivery)_[0-9a-z]+$")


def _valid_resource(resource_id: str) -> bool:
    return bool(RESOURCE_ID_RE.match(str(resource_id or "").strip().lower()))


class Registry:
    def __init__(self, observations=None, identities=None):
        self.observations = list(observations if observations is not None
                                 else build_observations())
        self.identities = identities
        self.resource_owner: dict[tuple[str, str], set[str]] = {}
        self.by_resource: dict[tuple[str, str], list[Observation]] = {}
        self.ids_by_key: dict[str, set[tuple[str, str]]] = {}
        for obs in self.observations:
            key = self.add_resource(obs.system, obs.resource_id)
            self.by_resource[key].append(obs)
            for token in obs.subjects:
                self.link(token, obs.system, obs.resource_id)
        self._load_catalog_links()
        self._load_multimodal_links()

    # ------------------------------------------------------------------ basics
    def add_resource(self, system: str, resource_id: str) -> tuple[str, str]:
        key = (system or system_for(resource_id), str(resource_id).strip())
        self.by_resource.setdefault(key, [])
        return key

    def link(self, token, system: str, resource_id: str):
        token = norm_ident(token)
        if not token:
            return
        key = self.add_resource(system, resource_id)
        self.ids_by_key.setdefault(token, set()).add(key)

    def resources_for(self, tokens) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for token in tokens:
            out |= self.ids_by_key.get(norm_ident(token), set())
        return out

    # ------------------------------------------------------------ extra links
    def _load_catalog_links(self):
        path = os.path.join(DATASET_DIR, "system_activity.csv")
        if not os.path.exists(path):
            return
        act = pd.read_csv(path, keep_default_na=False, dtype=str)
        link = act[act["event_type"] == "LINK_RESOURCE_IDENTITY"]
        pattern = re.compile(r"linked (\S+) \(([^)]*)\) to (.+?)\.\s*$", re.I)
        for _, row in link.iterrows():
            match = pattern.search(row["details"].strip())
            if not match:
                continue
            resource_id = match.group(1).strip()
            if not _valid_resource(resource_id):
                resource_id = match.group(2).strip()
            if not _valid_resource(resource_id):
                continue
            key = self.add_resource(system_for(resource_id, row["target_system"]), resource_id)
            subject_field = f"activity_id={row['activity_id']};"
            for token in re.findall(r"[\w.+-]+@[\w.-]+\.\w+|user_\d+", match.group(3)):
                self.link(token, key[0], key[1])
                if re.fullmatch(r"user_\d+", token):
                    self.resource_owner.setdefault(key, set()).add(token.lower())
            self._link_provenance(key, row["activity_id"], subject_field)

    def _users_of(self, token: str) -> set[str]:
        if re.fullmatch(r"user_\d+", token):
            return {token}
        if self.identities is None:
            return set()
        return set(self.identities.by_key.get(norm_ident(token), set()))

    def _load_multimodal_links(self):
        cache_path = os.path.join(REPO_ROOT, "work", "multimodal_cache.json")
        if not os.path.exists(cache_path):
            return
        with open(cache_path, encoding="utf-8") as handle:
            cache = json.load(handle)
        for evidence_id, entry in cache.items():
            facts = entry.get("facts") or {}
            tokens = set()
            for subject in facts.get("subjects") or []:
                for value in subject.values():
                    token = norm_ident(value)
                    if token and (token.count("@") or re.fullmatch(r"user_\d+", token)):
                        tokens.add(token)
            if not tokens:
                continue
            for resource in facts.get("resources") or []:
                resource_id = str(resource.get("resource_id") or "").strip().lower()
                if not _valid_resource(resource_id):
                    continue
                key = self.add_resource(system_for(resource_id), resource_id)
                owners = self.resource_owner.get(key)
                for token in tokens:
                    if owners and not (self._users_of(token) & owners):
                        # A screenshot lists every connector on the screen; the data
                        # catalog already says which subject owns this one.
                        continue
                    self.link(token, key[0], key[1])
                self._link_provenance(key, evidence_id, "multimodal")

    def _link_provenance(self, key, evidence_id, detail):
        if any(o.evidence_id == evidence_id and o.origin == "link" for o in self.by_resource[key]):
            return
        self.by_resource[key].append(Observation(
            evidence_id=evidence_id, ts=None, system=key[0], resource_id=key[1],
            category="LINK", state="UNKNOWN", subjects=(), origin="link",
            detail={"detail": detail}))
