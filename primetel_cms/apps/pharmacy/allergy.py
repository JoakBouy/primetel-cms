"""
Drug-allergy check.

The patient's recorded allergies are free text — we tokenise on commas,
semicolons, slashes and newlines, then flag a drug when either:

1. a token appears as a substring of the drug's generic or brand name
   (case-insensitive), or
2. a token names a drug class (e.g. "penicillin", "sulfa", "NSAID") and the
   drug belongs to that class — so a penicillin allergy flags amoxicillin.

This is intentionally conservative: a false positive is recoverable
(clinician overrides with a documented reason); a false negative could harm
a patient. The class list is a safety net, not a full interaction database.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional


_SPLIT = re.compile(r"[,;/\n]+")
_STOPWORDS = {"and", "or", "rash", "itching", "swelling", "none", "n/a", "nil", "no", "known", "allergies"}
_MIN_TOKEN_LEN = 3

# Allergy keyword → (class label, name fragments of drugs in that class).
# Keywords are matched as substrings of an allergy token, so "penicillins"
# and "penicillin allergy" both hit "penicillin".
DRUG_CLASSES: dict[str, tuple[str, tuple[str, ...]]] = {
    "penicillin": ("penicillins", (
        "penicillin", "amoxicillin", "ampicillin", "cloxacillin", "flucloxacillin",
        "dicloxacillin", "piperacillin", "augmentin", "amoxiclav", "co-amoxiclav",
        "benzathine",
    )),
    "cephalosporin": ("cephalosporins", (
        "cef", "ceph",  # cefalexin, ceftriaxone, cefuroxime, cefixime, cephalexin…
    )),
    "sulfa": ("sulfonamides", (
        "sulfa", "sulpha", "sulfamethoxazole", "cotrimoxazole", "co-trimoxazole",
        "septrin", "bactrim", "sulfadoxine", "fansidar", "sulfasalazine",
    )),
    "sulpha": ("sulfonamides", (
        "sulfa", "sulpha", "sulfamethoxazole", "cotrimoxazole", "co-trimoxazole",
        "septrin", "bactrim", "sulfadoxine", "fansidar", "sulfasalazine",
    )),
    "nsaid": ("NSAIDs", (
        "ibuprofen", "diclofenac", "naproxen", "aspirin", "acetylsalicylic",
        "indomethacin", "indometacin", "piroxicam", "meloxicam", "ketoprofen",
        "mefenamic", "celecoxib",
    )),
    "aspirin": ("salicylates", ("aspirin", "acetylsalicylic")),
    "quinolone": ("fluoroquinolones", (
        "ciprofloxacin", "levofloxacin", "norfloxacin", "ofloxacin", "moxifloxacin",
    )),
    "macrolide": ("macrolides", ("erythromycin", "azithromycin", "clarithromycin")),
    "tetracycline": ("tetracyclines", ("tetracycline", "doxycycline", "minocycline")),
    "artemisinin": ("artemisinins", ("artemether", "artesunate", "artemisinin", "dihydroartemisinin")),
    "opioid": ("opioids", ("morphine", "codeine", "tramadol", "pethidine", "fentanyl")),
}


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

    Match strategy: (1) token from patient.allergies appears as substring of
    drug.generic_name or drug.brand_name; (2) token names a drug class the
    drug belongs to. Token must be >=3 chars and not a known stopword.
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
        for keyword, (label, members) in DRUG_CLASSES.items():
            if keyword in token and any(member in haystack for member in members):
                return (
                    f"Patient allergy '{token}' — '{drug.generic_name}' belongs to the "
                    f"{label} class"
                )
    return None
