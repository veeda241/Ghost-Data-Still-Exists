"""Decide whether a request text actually authorises erasure of personal data."""
from __future__ import annotations

import re

# Right-to-erasure vocabulary across the languages used by the dataset.
ERASE = [
    "delete", "deleting", "deletion", "remove", "removal", "erase", "erased", "destroy",
    "withdraw", "close my account", "cancel my account", "my data gone",
    "löschen", "entfernen", "supprimer", "effacer", " cancellare", "eliminare",
    "eliminar", "borrar", "apagar", "excluir", "remover", "apagar",
    "삭제", "지워", "削除", "消して", "删除", "删除我", "擦除", "обработать", "удалить", "удаления",
    "حذف", "מחיקת", "मिटाएँ", "menghapus", "memadam", "hapus",
    "penghapusan", "suppression", "radierung",
]

# Requests that ask for a different data-subject right are not deletion requests.
OTHER_RIGHT = [
    "access to my data", "copy of my data", "export my data", "data portability",
    "rectify", "correct my", "update my address", "change my email", "amend",
    "opt out", "unsubscribe", "marketing emails", "no longer want to receive",
    "stop sending", "right to be informed", "restrict", "objection to processing",
    "copy of the data", "see what data", "what data do you hold", "download my",
    "suspend my", "reset my password", "change my password",
    "zu erhalten", "kopie meiner", "exporter mes", "acceso a mis datos",
    "mis datos", "rectificar", "コピー", "書き換え", "データのコピー", "コピーを",
    "내 정보를", "사본", "정정", "アクセス",
]

DELETION_NOUN = ["deletion", "delete", "erasure", "removal", "削除", "löschen", "suppression",
                 "apagar", "borrar", "eliminar", "удал", "حذف", "삭제", "penghapusan",
                 "delete my", "remove my"]


def _hits(text: str, terms) -> int:
    return sum(1 for term in terms if term in text)


def authorizes_deletion(request_text: str) -> bool:
    """Return True unless the subject only asks for a different data-subject right.

    Erasure is the default reading: the request is filed in a deletion queue and most
    phrasings ("close the account", "confirm the profile is gone", "make sure nothing
    is left") authorise it without using the word ``delete``. A request is treated as
    NOT_APPLICABLE only when it names another right and never mentions erasure,
    account closure, or removal.
    """
    text = str(request_text or "").lower()
    if not text.strip():
        return False
    erase = _hits(text, ERASE) or any(term in text for term in DELETION_NOUN)
    other = _hits(text, OTHER_RIGHT)
    if other and not erase:
        return False
    return True


def classify_batch(texts):
    return [authorizes_deletion(text) for text in texts]
