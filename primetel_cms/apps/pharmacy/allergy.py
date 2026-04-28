"""
Drug-allergy check.

The patient's recorded allergies are free text — we tokenise on commas and
slashes, then look for any token to appear as a substring in the drug's
generic or brand name (case-insensitive). This is intentionally conservative:
a false positive is recoverable (clinician overrides with a documented reason);
a false negative could harm a patient.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional


_SPLIT = re.compile(r"[,;/\n]+")
_STOPWORDS = {"and", "or", "rash", "itching", "swelling", "none", "n/a", "nil", "no", "known", "allergies"}
_MIN_TOKEN_LEN = 3


def _tokenise(text: str) -> Iterable[str]:
    if not text:
        return ()
    for raw in _SPLIT.split(text.lower()):
        token = raw.strip()
        if len(token) < _MIN_TOKEN_LEN:
            continue
        if token in _STOPWORDS:
            continue
        yield token


def check_allergy(patient, drug) -> Optional[str]:
    """
    Return a human-readable description of the allergy match, or None if clear.

    Match strategy: token from patient.allergies appears as substring of
    drug.generic_name or drug.brand_name. Token must be >=3 chars and not
    a known stopword.
    """
    haystack = " ".join(filter(None, [
        (drug.generic_name or "").lower(),
        (drug.brand_name or "").lower(),
    ]))
    if not haystack:
        return None
    for token in _tokenise(getattr(patient, "allergies", "") or ""):
        if token in haystack:
            return f"Patient allergy '{token}' matches drug '{drug.generic_name}'"
    return None
