"""
verb_stemmer.py — Suffix-based Hindi/Hinglish verb stemmer.

Given a conjugated verb surface form, strips the suffix to identify:
  1. The root (stem)
  2. The conjugation form (IMPERATIVE_AAP, IMPERATIVE_TUM, IMPERATIVE_TU, etc.)

Usage:
    from verb_stemmer import stem_verb, stem_all_verbs
    stem_verb("karo")     → {"root": "kar", "surface": "karo", "form": "IMPERATIVE_TUM"}
    stem_verb("kariye")   → {"root": "kar", "surface": "kariye", "form": "IMPERATIVE_AAP"}
    stem_verb("hello")    → None  (not a recognized verb pattern)
"""
from __future__ import annotations

import re
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Known verb roots — used to validate that a stem is actually a verb root.
# This prevents false positives like "bro" → root "br" + suffix "o".
# ─────────────────────────────────────────────────────────────────────────────
KNOWN_ROOTS = {
    # Movement
    "aa", "ja", "chal", "aaja", "chala",
    # Communication
    "bol", "bata", "kah", "sun", "puch", "bul",
    # Action
    "kar", "de", "le", "rakh", "dal", "likh", "padh",
    "bhej", "dikha", "samjha", "soch", "dekh",
    # State
    "ruk", "baith", "uth", "so", "kha", "pi",
    # Misc
    "mil", "ban", "sik", "man", "rok", "tod",
    "chhod", "pakad", "maan", "laga", "hata",
    "gira", "uda", "dho", "pehen", "nikal",
    "suna", "bhool", "yaad", "samajh",
}


# ─────────────────────────────────────────────────────────────────────────────
# Suffix rules — ordered longest-first so greedy match works.
# Each rule: (suffix_regex, form_label, suffix_length_hint)
#
# IMPERATIVE_AAP  = aap form (respectful): kariye, boliye, chaliye
# IMPERATIVE_TUM  = tum form (casual):     karo, bolo, chalo
# IMPERATIVE_TU   = tu form (intimate):    kar, bol, chal (bare root)
# INFINITIVE      = na/ne ending:          karna, bolna
# HABITUAL        = ta/te/ti ending:       karta, bolte, chalti
# PAST            = a/e/i ending:          kara, bole, chali (past/perfective)
# ─────────────────────────────────────────────────────────────────────────────

# Rules applied in order — first match wins.
# Format: (suffix_pattern, form, min_root_len)
SUFFIX_RULES: list[tuple[str, str, int]] = [
    # --- AAP forms (longest suffixes first) ---
    (r"ijiye$",  "IMPERATIVE_AAP", 2),    # samjhijiye → samjh
    (r"aiye$",   "IMPERATIVE_AAP", 2),    # aaye → aa (less common spelling)
    (r"iye$",    "IMPERATIVE_AAP", 2),     # kariye, boliye, chaliye
    (r"iyo$",    "IMPERATIVE_AAP", 2),     # kariyo (dialectal aap)

    # --- TUM forms ---
    (r"ao$",     "IMPERATIVE_TUM", 2),     # batao, sunao, jagao
    (r"rao$",    "IMPERATIVE_TUM", 2),     # bhejrao (causative-ish)
    (r"o$",      "IMPERATIVE_TUM", 2),     # karo, bolo, chalo, dekho

    # --- INFINITIVE ---
    (r"ne$",     "INFINITIVE", 2),         # karne, bolne, chalne
    (r"na$",     "INFINITIVE", 2),         # karna, bolna, chalna

    # --- HABITUAL / present participle ---
    (r"te$",     "HABITUAL", 2),           # karte, bolte, chalte
    (r"ti$",     "HABITUAL", 2),           # karti, bolti, chalti
    (r"ta$",     "HABITUAL", 2),           # karta, bolta, chalta
]


def stem_verb(word: str) -> Optional[dict]:
    """
    Attempt to stem a single word into root + form.

    Returns:
        {"root": str, "surface": str, "form": str}  if recognized
        None  if the word doesn't match any known verb pattern
    """
    word_lower = word.lower().strip()
    if len(word_lower) < 2:
        return None

    # Try each suffix rule
    for suffix, form, min_root in SUFFIX_RULES:
        match = re.search(suffix, word_lower)
        if match:
            root = word_lower[:match.start()]
            if len(root) >= min_root and root in KNOWN_ROOTS:
                return {"root": root, "surface": word_lower, "form": form}

    # Check if the bare word itself is a known root → IMPERATIVE_TU
    if word_lower in KNOWN_ROOTS:
        return {"root": word_lower, "surface": word_lower, "form": "IMPERATIVE_TU"}

    return None


def stem_all_verbs(text: str) -> list[dict]:
    """
    Stem all words in a text, returning only recognized verbs.

    Returns list of {"root": ..., "surface": ..., "form": ...} dicts.
    """
    words = re.findall(r"[a-zA-Z]+", text.lower())
    results = []
    seen_roots = set()

    for word in words:
        result = stem_verb(word)
        if result and result["root"] not in seen_roots:
            results.append(result)
            seen_roots.add(result["root"])

    return results


def detect_pronoun(text: str) -> Optional[str]:
    """
    Detect the address pronoun used in a text.
    Returns "aap", "tum", "tu", or None.

    Priority: aap > tum > tu (most specific wins).
    """
    text_lower = text.lower()
    words = set(re.findall(r"[a-zA-Z]+", text_lower))

    aap_markers = {"aap", "aapka", "aapki", "aapke", "aapko", "aapse"}
    tum_markers = {"tum", "tumhara", "tumhari", "tumhare", "tumhe", "tumse", "tumko"}
    tu_markers = {"tu", "tera", "teri", "tere", "tujhe", "tujhse", "tujhko"}

    if words & aap_markers:
        return "aap"
    if words & tum_markers:
        return "tum"
    if words & tu_markers:
        return "tu"

    # Infer from verb forms
    for word in words:
        result = stem_verb(word)
        if result:
            if result["form"] == "IMPERATIVE_AAP":
                return "aap"
            if result["form"] == "IMPERATIVE_TUM":
                return "tum"
            if result["form"] == "IMPERATIVE_TU":
                return "tu"

    return None


def detect_opener(text: str) -> Optional[str]:
    """Extract the first meaningful word (opener) from a reply."""
    words = re.findall(r"[a-zA-Z]+", text.lower())
    if words:
        return words[0]
    return None


FILLER_SET = {
    "bhai", "bro", "yaar", "dude", "na", "matlab",
    "arre", "arey", "like", "basically", "actually",
    "waise", "vaise", "abe", "oye", "bas",
}


def detect_fillers(text: str) -> list[str]:
    """Detect filler words in a text."""
    words = re.findall(r"[a-zA-Z]+", text.lower())
    found = []
    seen = set()
    for w in words:
        if w in FILLER_SET and w not in seen:
            found.append(w)
            seen.add(w)
    return found
