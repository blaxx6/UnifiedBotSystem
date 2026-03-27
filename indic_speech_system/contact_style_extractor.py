"""
contact_style_extractor.py — Per-contact style extraction from ChromaDB.

Queries the vector DB filtered by contact_id to build a dynamic, data-driven
style profile for each contact. Falls back gracefully through three tiers:

  Tier 1 (≥10 messages): Full data-driven extraction
  Tier 2 (1-9 messages):  Blend of extraction + cold-start defaults
  Tier 3 (0 messages):    Pure relationship defaults from cold_start.py

Usage:
    from contact_style_extractor import get_contact_style
    style = get_contact_style("FriendName", "best friend")
"""
from __future__ import annotations

import os
import json
import time
import logging
from collections import Counter
from typing import Optional

from verb_stemmer import stem_verb, detect_pronoun, detect_opener, detect_fillers
from cold_start import get_defaults, PRONOUN_SETS

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(__file__)
CACHE_DIR = os.path.join(BASE_DIR, "data", "contact_styles")
CACHE_TTL_SECONDS = 3600  # 1 hour


def _get_collection():
    """Get a fresh ChromaDB collection reference."""
    try:
        from clone_manager import _get_collection as cm_get_collection
        return cm_get_collection()
    except Exception as e:
        logger.warning("⚠️ Could not get ChromaDB collection: %s", e)
        return None


def _query_contact_data(contact_name: str) -> list[dict]:
    """
    Query ChromaDB for all documents belonging to a contact.
    Returns list of metadata dicts.
    """
    collection = _get_collection()
    if collection is None:
        return []

    try:
        total = collection.count()
        if total == 0:
            return []

        # Query with contact_id filter
        results = collection.get(
            where={"contact_id": contact_name},
            include=["metadatas"],
        )

        if results and results.get("metadatas"):
            return results["metadatas"]
        return []
    except Exception as e:
        # contact_id field might not exist yet (pre-migration data)
        logger.debug("ChromaDB query for contact '%s' failed: %s", contact_name, e)
        return []


def _extract_from_data(contact_data: list[dict]) -> dict:
    """
    Extract style metrics from contact-specific ChromaDB metadata.
    Expects metadata with: verb_roots, verb_surfaces, address_pronoun,
    opener, filler_tokens, reply.
    """
    pronoun_counter: Counter = Counter()
    verb_map_counter: dict[str, Counter] = {}  # root → Counter of surfaces
    opener_counter: Counter = Counter()
    filler_counter: Counter = Counter()
    example_pairs: list[tuple[str, str]] = []

    for meta in contact_data:
        # Pronouns
        pronoun = meta.get("address_pronoun")
        if pronoun:
            pronoun_counter[pronoun] += 1

        # Verb roots → surfaces
        roots = meta.get("verb_roots", "")
        surfaces = meta.get("verb_surfaces", "")
        if isinstance(roots, str) and isinstance(surfaces, str):
            root_list = roots.split(",") if roots else []
            surface_list = surfaces.split(",") if surfaces else []
            for root, surface in zip(root_list, surface_list):
                root = root.strip()
                surface = surface.strip()
                if root and surface:
                    if root not in verb_map_counter:
                        verb_map_counter[root] = Counter()
                    verb_map_counter[root][surface] += 1

        # Openers
        opener = meta.get("opener")
        if opener:
            opener_counter[opener] += 1

        # Fillers
        fillers = meta.get("filler_tokens", "")
        if isinstance(fillers, str) and fillers:
            for f in fillers.split(","):
                f = f.strip()
                if f:
                    filler_counter[f] += 1

        # Example pairs
        incoming = meta.get("incoming", "")
        reply = meta.get("reply", "")
        if incoming and reply:
            example_pairs.append((incoming, reply))

    # Build verb map: root → most common surface
    verb_map = {}
    for root, counter in verb_map_counter.items():
        most_common_surface = counter.most_common(1)[0][0]
        verb_map[root] = most_common_surface

    # Best pronoun
    pronoun = pronoun_counter.most_common(1)[0][0] if pronoun_counter else None
    pronoun_confidence = pronoun_counter.most_common(1)[0][1] if pronoun_counter else 0

    # Top openers and fillers
    top_openers = [w for w, _ in opener_counter.most_common(5)]
    top_fillers = [w for w, _ in filler_counter.most_common(5)]

    # Select diverse example pairs (up to 8)
    selected_examples = _select_diverse_examples(example_pairs, max_count=8)

    return {
        "pronoun": pronoun,
        "pronoun_confidence": pronoun_confidence,
        "verb_map": verb_map,
        "top_openers": top_openers,
        "top_fillers": top_fillers,
        "example_pairs": selected_examples,
        "message_count": len(contact_data),
    }


def _select_diverse_examples(
    pairs: list[tuple[str, str]], max_count: int = 8
) -> list[tuple[str, str]]:
    """Select diverse example pairs, avoiding duplicates and very similar ones."""
    if len(pairs) <= max_count:
        return pairs

    selected = []
    seen_replies = set()

    for incoming, reply in pairs:
        reply_lower = reply.lower().strip()
        # Skip near-duplicates
        if reply_lower in seen_replies:
            continue
        # Skip very short or uninformative replies
        if len(reply.split()) < 2:
            continue
        selected.append((incoming, reply))
        seen_replies.add(reply_lower)
        if len(selected) >= max_count:
            break

    # If we didn't get enough, add short ones too
    if len(selected) < max_count:
        for incoming, reply in pairs:
            reply_lower = reply.lower().strip()
            if reply_lower not in seen_replies:
                selected.append((incoming, reply))
                seen_replies.add(reply_lower)
                if len(selected) >= max_count:
                    break

    return selected


def _load_cache(contact_name: str) -> Optional[dict]:
    """Load cached style for a contact if it exists and isn't stale."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"{contact_name.replace(' ', '_')}.json")
    if not os.path.exists(cache_file):
        return None

    try:
        mtime = os.path.getmtime(cache_file)
        if time.time() - mtime > CACHE_TTL_SECONDS:
            return None  # Stale

        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(contact_name: str, style: dict) -> None:
    """Cache a contact's style to disk."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"{contact_name.replace(' ', '_')}.json")
    try:
        # Make a serializable copy (tuples → lists for JSON)
        serializable = style.copy()
        if "example_pairs" in serializable:
            serializable["example_pairs"] = [
                list(pair) for pair in serializable["example_pairs"]
            ]
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning("⚠️ Could not cache style for %s: %s", contact_name, e)


def get_contact_style(
    contact_name: str,
    relationship_type: str = "unknown",
    force_refresh: bool = False,
) -> dict:
    """
    Get the complete style profile for a contact.

    Three-tier fallback:
      Tier 1 (≥10 messages): Full data-driven extraction
      Tier 2 (1-9 messages):  Blend extraction + cold-start defaults
      Tier 3 (0 messages):    Pure relationship defaults

    Returns dict with:
      pronoun, pronoun_confidence, pronoun_set, verb_map,
      top_openers, top_fillers, example_pairs,
      confidence, tier, message_count
    """
    if not contact_name:
        defaults = get_defaults(relationship_type)
        defaults["confidence"] = "none"
        defaults["tier"] = 3
        defaults["verb_map"] = {}
        defaults["top_openers"] = []
        defaults["top_fillers"] = defaults.get("fillers", [])
        defaults["example_pairs"] = []
        defaults["message_count"] = 0
        defaults["pronoun_confidence"] = 0
        defaults["pronoun_set"] = PRONOUN_SETS.get(defaults["pronoun"], PRONOUN_SETS["aap"])
        return defaults

    # Check cache first
    if not force_refresh:
        cached = _load_cache(contact_name)
        if cached is not None:
            return cached

    # Query ChromaDB for this contact's data
    contact_data = _query_contact_data(contact_name)
    message_count = len(contact_data)

    # Get cold-start defaults
    defaults = get_defaults(relationship_type)

    if message_count >= 10:
        # ─── TIER 1: Full extraction ─────────────────────────────────
        extracted = _extract_from_data(contact_data)
        style = {
            "pronoun": extracted["pronoun"] or defaults["pronoun"],
            "pronoun_confidence": extracted["pronoun_confidence"],
            "pronoun_set": PRONOUN_SETS.get(
                extracted["pronoun"] or defaults["pronoun"],
                PRONOUN_SETS["aap"],
            ),
            "verb_map": extracted["verb_map"],
            "verb_form": _infer_verb_form(extracted["pronoun"] or defaults["pronoun"]),
            "top_openers": extracted["top_openers"] or defaults.get("fillers", []),
            "top_fillers": extracted["top_fillers"] or defaults.get("fillers", []),
            "example_pairs": extracted["example_pairs"],
            "confidence": "high",
            "tier": 1,
            "message_count": message_count,
            "address_style": defaults["address_style"],
        }

    elif message_count >= 1:
        # ─── TIER 2: Blend extraction + defaults ─────────────────────
        extracted = _extract_from_data(contact_data)

        # Use extracted pronoun only if seen 3+ times, else default
        if extracted["pronoun_confidence"] >= 3:
            pronoun = extracted["pronoun"]
        else:
            pronoun = defaults["pronoun"]

        style = {
            "pronoun": pronoun,
            "pronoun_confidence": extracted["pronoun_confidence"],
            "pronoun_set": PRONOUN_SETS.get(pronoun, PRONOUN_SETS["aap"]),
            "verb_map": extracted["verb_map"],  # even partial data is useful
            "verb_form": _infer_verb_form(pronoun),
            "top_openers": extracted["top_openers"] or defaults.get("fillers", []),
            "top_fillers": extracted["top_fillers"] or defaults.get("fillers", []),
            "example_pairs": extracted["example_pairs"],
            "confidence": "low",
            "tier": 2,
            "message_count": message_count,
            "address_style": defaults["address_style"],
        }

    else:
        # ─── TIER 3: Pure cold-start defaults ────────────────────────
        style = {
            "pronoun": defaults["pronoun"],
            "pronoun_confidence": 0,
            "pronoun_set": PRONOUN_SETS.get(defaults["pronoun"], PRONOUN_SETS["aap"]),
            "verb_map": {},
            "verb_form": defaults["verb_form"],
            "top_openers": [],
            "top_fillers": defaults.get("fillers", []),
            "example_pairs": [],
            "confidence": "none",
            "tier": 3,
            "message_count": 0,
            "address_style": defaults["address_style"],
        }

    # Cache it
    _save_cache(contact_name, style)

    logger.info(
        "📇 Contact style for '%s': tier=%d, pronoun=%s (conf=%d), verbs=%d",
        contact_name,
        style["tier"],
        style["pronoun"],
        style["pronoun_confidence"],
        len(style["verb_map"]),
    )

    return style


def _infer_verb_form(pronoun: str) -> str:
    """Map a pronoun to the expected verb conjugation form."""
    return {
        "aap": "AAP",
        "tum": "TUM",
        "tu": "TU",
    }.get(pronoun, "TUM")


def build_style_prompt_block(style: dict, contact_name: str) -> str:
    """
    Build the dynamic style prompt block for injection into the system prompt.
    Returns a formatted string ready to concatenate.
    """
    parts = []

    # Verb map
    verb_map = style.get("verb_map", {})
    if verb_map:
        verb_pairs = " | ".join(f"{root}→{surface}" for root, surface in verb_map.items())
        parts.append(f"VERB FORMS — your real usage with {contact_name}:\n{verb_pairs}")
    elif style.get("verb_form"):
        form = style["verb_form"]
        parts.append(f"VERB FORMS — use {form} conjugation (e.g. karo, bolo, chalo)" if form == "TUM"
                     else f"VERB FORMS — use {form} conjugation (e.g. kariye, boliye, chaliye)")

    # Pronoun
    pronoun = style.get("pronoun", "tum")
    pronoun_set = style.get("pronoun_set", {})
    if pronoun_set:
        pronoun_forms = ", ".join(pronoun_set.values())
        parts.append(f"PRONOUNS — you use: {pronoun}\nUse only: {pronoun_forms}")

        # Never-use list
        other_pronouns = [p for p in ["tu", "tum", "aap"] if p != pronoun]
        parts.append(f"Never use: {' / '.join(other_pronouns)} forms")

    # Openers
    openers = style.get("top_openers", [])
    if openers:
        parts.append(f"OPENERS: {', '.join(openers)}")

    # Fillers
    fillers = style.get("top_fillers", [])
    if fillers:
        parts.append(f"FILLERS: {', '.join(fillers)}")

    # Example pairs
    examples = style.get("example_pairs", [])
    if examples:
        example_lines = []
        for incoming, reply in examples[:8]:
            # Truncate long examples
            inc = incoming[:60] + "..." if len(incoming) > 60 else incoming
            rep = reply[:60] + "..." if len(reply) > 60 else reply
            example_lines.append(f'  "{inc}" → "{rep}"')
        parts.append(f"REAL EXAMPLES WITH {contact_name.upper()}:\n" + "\n".join(example_lines))

    # Confidence note
    confidence = style.get("confidence", "none")
    tier = style.get("tier", 3)
    if tier == 3:
        parts.append(f"⚠️ No chat history with {contact_name} — using defaults for {style.get('address_style', 'casual')} tone.")
    elif tier == 2:
        parts.append(f"📊 Limited data ({style.get('message_count', 0)} messages) — blending with defaults.")

    return "\n\n".join(parts)
