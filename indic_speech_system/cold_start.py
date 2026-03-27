"""
cold_start.py — Relationship-type defaults for contacts with no chat history.

When a contact has zero or very few messages in ChromaDB, we can't extract
their style from data. This module provides sensible defaults based on
the relationship type from contacts.json.
"""
from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────────────
# Defaults per relationship type
# Keys match the "relationship" field in data/contacts.json
# ─────────────────────────────────────────────────────────────────────────────
RELATIONSHIP_DEFAULTS: dict[str, dict] = {
    # Formal — aap forms, respectful
    "professor":    {"pronoun": "aap", "verb_form": "AAP", "fillers": ["ji"], "address_style": "formal"},
    "teacher":      {"pronoun": "aap", "verb_form": "AAP", "fillers": ["ji"], "address_style": "formal"},
    "boss":         {"pronoun": "aap", "verb_form": "AAP", "fillers": ["ji"], "address_style": "formal"},
    "senior":       {"pronoun": "aap", "verb_form": "AAP", "fillers": ["ji"], "address_style": "formal"},
    "sir":          {"pronoun": "aap", "verb_form": "AAP", "fillers": ["ji"], "address_style": "formal"},
    "madam":        {"pronoun": "aap", "verb_form": "AAP", "fillers": ["ji"], "address_style": "formal"},

    # Family — aap for elders
    "mother":       {"pronoun": "aap", "verb_form": "AAP", "fillers": ["ji"], "address_style": "respectful"},
    "father":       {"pronoun": "aap", "verb_form": "AAP", "fillers": ["ji"], "address_style": "respectful"},
    "parent":       {"pronoun": "aap", "verb_form": "AAP", "fillers": ["ji"], "address_style": "respectful"},

    # Family — tum for siblings
    "brother":      {"pronoun": "tum", "verb_form": "TUM", "fillers": ["bhai"], "address_style": "casual"},
    "sister":       {"pronoun": "tum", "verb_form": "TUM", "fillers": [], "address_style": "casual"},
    "family":       {"pronoun": "tum", "verb_form": "TUM", "fillers": [], "address_style": "casual"},

    # Friends — tum, casual
    "friend":       {"pronoun": "tum", "verb_form": "TUM", "fillers": ["yaar", "bhai"], "address_style": "casual"},
    "best friend":  {"pronoun": "tum", "verb_form": "TUM", "fillers": ["yaar", "bhai", "abe"], "address_style": "very_casual"},

    # Romantic — tum, warm
    "girlfriend":   {"pronoun": "tum", "verb_form": "TUM", "fillers": [], "address_style": "warm"},
    "boyfriend":    {"pronoun": "tum", "verb_form": "TUM", "fillers": [], "address_style": "warm"},
    "wife":         {"pronoun": "tum", "verb_form": "TUM", "fillers": [], "address_style": "warm"},
    "husband":      {"pronoun": "tum", "verb_form": "TUM", "fillers": [], "address_style": "warm"},

    # Professional — tum but polite
    "colleague":    {"pronoun": "tum", "verb_form": "TUM", "fillers": [], "address_style": "polite"},
    "junior":       {"pronoun": "tum", "verb_form": "TUM", "fillers": [], "address_style": "casual"},
    "acquaintance": {"pronoun": "aap", "verb_form": "AAP", "fillers": [], "address_style": "polite"},

    # Fallbacks
    "stranger":     {"pronoun": "aap", "verb_form": "AAP", "fillers": [], "address_style": "polite"},
    "unknown":      {"pronoun": "aap", "verb_form": "AAP", "fillers": [], "address_style": "polite"},
    "other":        {"pronoun": "tum", "verb_form": "TUM", "fillers": [], "address_style": "casual"},
}

# The ultimate fallback if relationship type isn't in the map
_DEFAULT_FALLBACK = {"pronoun": "aap", "verb_form": "AAP", "fillers": [], "address_style": "polite"}

# Pronoun → full pronoun set mapping
PRONOUN_SETS = {
    "aap": {
        "subject": "aap",
        "possessive_m": "aapka",
        "possessive_f": "aapki",
        "possessive_pl": "aapke",
        "object": "aapko",
        "ablative": "aapse",
    },
    "tum": {
        "subject": "tum",
        "possessive_m": "tumhara",
        "possessive_f": "tumhari",
        "possessive_pl": "tumhare",
        "object": "tumhe",
        "ablative": "tumse",
    },
    "tu": {
        "subject": "tu",
        "possessive_m": "tera",
        "possessive_f": "teri",
        "possessive_pl": "tere",
        "object": "tujhe",
        "ablative": "tujhse",
    },
}


def get_defaults(relationship_type: str) -> dict:
    """
    Get cold-start defaults for a relationship type.

    Returns dict with: pronoun, verb_form, fillers, address_style, pronoun_set
    """
    base = RELATIONSHIP_DEFAULTS.get(
        relationship_type.lower(),
        _DEFAULT_FALLBACK,
    ).copy()

    # Attach the full pronoun set
    base["pronoun_set"] = PRONOUN_SETS.get(base["pronoun"], PRONOUN_SETS["aap"])

    return base
