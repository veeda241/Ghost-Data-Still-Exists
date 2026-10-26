"""Current and historical identity resolution for a request email."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field

from .paths import DATASET_DIR, norm_ident, parse_ts


@dataclass
class Identity:
    user_id: str | None
    emails: set[str] = field(default_factory=set)
    hashes: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    names: set[str] = field(default_factory=set)

    @property
    def keys(self) -> set[str]:
        out = set(self.emails) | set(self.hashes) | set(self.phones) | set(self.names)
        if self.user_id:
            out.add(self.user_id)
        return {k for k in out if k}


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


class IdentityIndex:
    def __init__(self, identities_path=None, audit_path=None):
        self.by_email: dict[str, str] = {}
        self.by_key: dict[str, set[str]] = {}
        self.users: dict[str, Identity] = {}
        self._load(identities_path or f"{DATASET_DIR}/user_identities.csv",
                   audit_path or f"{DATASET_DIR}/user_audit_log.csv")

    def _register(self, user_id: str, value: str):
        token = norm_ident(value)
        if not token:
            return
        self.by_key.setdefault(token, set()).add(user_id)
        if user_id in self.users:
            ident = self.users[user_id]
            if "@" in token:
                ident.emails.add(token)
            elif len(token) == 64 and re.fullmatch(r"[0-9a-f]{64}", token):
                ident.hashes.add(token)
            elif token.startswith("user_"):
                pass
            elif _digits(token):
                ident.phones.add(token)
            else:
                ident.names.add(token)

    def _load(self, identities_path, audit_path):
        with open(identities_path, encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                user_id = row["user_id"].strip()
                ident = self.users.setdefault(user_id, Identity(user_id))
                ident.updated_at = parse_ts(row.get("updated_at", ""))
                for value in (row.get("email"), row.get("email_hash"), row.get("phone"), row.get("name")):
                    self._register(user_id, value)
                if row.get("email"):
                    self.by_email[norm_ident(row["email"])] = user_id
        with open(audit_path, encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            user_id = row["user_id"].strip()
            ident = self.users.setdefault(user_id, Identity(user_id))
            for value in (row.get("old_value"), row.get("new_value")):
                self._register(user_id, value)

    def resolve(self, email: str) -> Identity:
        token = norm_ident(email)
        user_id = self.by_email.get(token)
        if user_id is None:
            candidates = self.by_key.get(token)
            user_id = sorted(candidates)[0] if candidates else None
        if user_id is None:
            ident = Identity(None, emails={token} if token else set())
            digits = _digits(token)
            if digits:
                ident.phones.add(token)
            return ident
        ident = self.users[user_id]
        base = Identity(user_id)
        base.emails = set(ident.emails) | {token}
        base.hashes = set(ident.hashes)
        base.phones = set(ident.phones)
        base.names = set(ident.names)
        return base
