"""Text normalisation shared by board matching, duplicate detection and the mock tagger."""
import hashlib
import re
import unicodedata

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_PUNCT = re.compile(r"[^\w\sঀ-৿]", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "").lower().translate(_BN_DIGITS)
    t = t.replace("‌", "").replace("‍", "").replace("।", " ")
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


def stem_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()
