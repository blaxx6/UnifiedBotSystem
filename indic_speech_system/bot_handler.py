from __future__ import annotations
import os
import re
import asyncio
import ollama  # Uses your local Mac API
from config import Config
from collections import defaultdict, deque
from typing import Optional
from clone_manager import (
    get_ai_settings, retrieve_style_examples, get_style_profile,
    classify_message_type, AI_SPEAK_PHRASES, EMOJI_PATTERN,
    should_retrieve,
)
from contact_style_extractor import get_contact_style, build_style_prompt_block
from contacts_manager import get_contact_by_name
from topic_memory import get_memory_prompt_injection, save_conversation_topics
from daily_context import build_daily_context_prompt
import random
import logging
import time as _time
import requests as http_requests  # renamed to avoid clash with local vars

logger = logging.getLogger(__name__)
import prompt_loader

# --- AI Model Configuration ---
AI_MODEL = Config.AI_MODEL
AI_MODEL_FAST = Config.AI_MODEL_FAST
MAX_HISTORY = 10  # Keeps context for a natural conversation flow
OWNER_ID = Config.OWNER_ID  # Parsed once in Config class

# Message types that are simple enough for the fast (quantised) model
_FAST_MODEL_TYPES = {"greeting", "reaction", "acknowledgment"}


# ─── CIRCUIT BREAKER ─────────────────────────────────────────────────────────
# Tracks consecutive Ollama failures. After THRESHOLD failures within
# RESET_SEC seconds, the breaker "opens" and skips Ollama entirely,
# falling through to Groq/Gemini. Alerts the owner once on state change.

class CircuitBreaker:
    def __init__(self, threshold: int = 3, reset_sec: int = 60):
        self.threshold = threshold
        self.reset_sec = reset_sec
        self.failures = 0
        self.last_failure_time = 0.0
        self.state = "CLOSED"  # CLOSED (normal) | OPEN (skip) | HALF_OPEN (testing)
        self._alerted = False

    def record_success(self) -> None:
        """Reset on success."""
        if self.state != "CLOSED":
            logger.info("✅ Circuit breaker CLOSED — Ollama recovered")
        self.failures = 0
        self.state = "CLOSED"
        self._alerted = False

    def record_failure(self) -> None:
        """Track a failure. Opens breaker if threshold exceeded."""
        now = _time.time()
        # Reset failure count if last failure was long ago
        if now - self.last_failure_time > self.reset_sec:
            self.failures = 0
        self.failures += 1
        self.last_failure_time = now
        if self.failures >= self.threshold:
            self.state = "OPEN"
            if not self._alerted:
                self._alert_owner()
                self._alerted = True
            logger.error(
                "🔴 Circuit breaker OPEN — %d Ollama failures in %ds. Routing to cloud fallbacks.",
                self.failures, self.reset_sec,
            )

    def is_open(self) -> bool:
        """Check if breaker is open (should skip Ollama)."""
        if self.state == "OPEN":
            # Auto-reset after reset_sec of open state
            if _time.time() - self.last_failure_time > self.reset_sec:
                self.state = "HALF_OPEN"
                logger.info("🟡 Circuit breaker HALF_OPEN — will test Ollama on next request")
                return False
            return True
        return False

    def _alert_owner(self) -> None:
        """Send a WhatsApp alert to the owner about Ollama being down."""
        try:
            owner_jid = Config.OWNER_PHONE_NUMBER
            alert_msg = (
                "⚠️ *Circuit Breaker Alert*\n\n"
                f"Ollama failed {self.failures}× in {self.reset_sec}s.\n"
                "Switched to cloud fallback (Groq/Gemini).\n"
                "Will auto-retry Ollama in 60s."
            )
            from unified_api import bot_manager
            bot_manager.send_whatsapp_message(
                owner_jid.split('@')[0], alert_msg
            )
        except Exception as e:
            logger.warning("Circuit breaker alert failed: %s", e)


_circuit_breaker = CircuitBreaker(
    threshold=Config.CIRCUIT_BREAKER_THRESHOLD,
    reset_sec=Config.CIRCUIT_BREAKER_RESET_SEC,
)

# --- GRAMMAR FIX MAP ---
# Loaded from prompts/grammar_fixes.json at startup.
# Rule-based post-processing to catch common Hinglish grammar errors.
_grammar_data = prompt_loader.get_grammar_fixes()

# Validate AND pre-compile regex patterns at load time — skip malformed entries
def _safe_compile_fixes(entries: list) -> list:
    """Filter and PRE-COMPILE grammar fix entries."""
    valid = []
    for entry in entries:
        try:
            p, r = entry
            compiled = re.compile(p, re.IGNORECASE)
            valid.append((compiled, r))
        except (re.error, ValueError) as e:
            logger.warning("⚠️ Skipping malformed grammar fix: %s — %s", entry, e)
    return valid

HINGLISH_FIXES = _safe_compile_fixes(_grammar_data.get("hinglish_fixes", []))

# --- MASCULINE VERB GENDER ENFORCEMENT ---
# Loaded from prompts/grammar_fixes.json (includes lambda-based fixes).
_masc_simple = _safe_compile_fixes(_grammar_data.get("masculine_verb_fixes", []))
_masc_lambda = []
for entry in _grammar_data.get("masculine_verb_fixes_lambda", []):
    try:
        _find, _repl = entry["find"], entry["replace"]
        compiled = re.compile(entry["pattern"], re.IGNORECASE)
        _masc_lambda.append(
            (compiled, lambda m, f=_find, r=_repl: m.group().replace(f, r))
        )
    except (re.error, KeyError, ValueError) as e:
        logger.warning("⚠️ Skipping malformed lambda grammar fix: %s — %s", entry, e)
MASCULINE_VERB_FIXES = _masc_simple + _masc_lambda

# --- SEED CONVERSATION ---
# Loaded from prompts/seed_conversation.json.
# Gold-standard exchanges injected at the start of every conversation.
SEED_CONVERSATION = prompt_loader.get_seeds()

# --- RESPECTFUL & EMPATHETIC SYSTEM PROMPT ---
# Loaded from prompts/empathetic_system.md (with YAML frontmatter stripped).
EMPATHETIC_SYSTEM_PROMPT = prompt_loader.get_prompt("empathetic_system")


# ─────────────────────────────────────────────────────────────────────────────
# Context.txt Cache — read once, invalidate on file change
# ─────────────────────────────────────────────────────────────────────────────
_context_cache: tuple[float, str] | None = None


def _load_context_file() -> str:
    """Load context.txt with mtime-based caching. Returns content or empty string."""
    global _context_cache
    context_path = os.path.join(os.path.dirname(__file__), "context.txt")
    if not os.path.exists(context_path):
        return ""
    try:
        current_mtime = os.path.getmtime(context_path)
        if _context_cache is not None and _context_cache[0] == current_mtime:
            return _context_cache[1]
        with open(context_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        _context_cache = (current_mtime, content)
        return content
    except Exception as e:
        logger.warning("⚠️ Context file read error: %s", e)
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Formal-to-Casual Vocabulary Map — PRE-COMPILED regex patterns
# ─────────────────────────────────────────────────────────────────────────────
_FORMAL_TO_CASUAL_RAW = {
    "certainly": "haan",
    "absolutely": "bilkul",
    "i understand": "samjha",
    "of course": "haan bhai",
    "however": "par",
    "therefore": "toh",
    "additionally": "aur",
    "furthermore": "aur bhi",
    "nevertheless": "phir bhi",
    "regarding": "ke baare mein",
    "approximately": "lagbhag",
    "subsequently": "phir",
}
# Compile once at module load
FORMAL_TO_CASUAL: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\b' + re.escape(formal) + r'\b', re.IGNORECASE), casual)
    for formal, casual in _FORMAL_TO_CASUAL_RAW.items()
]

# ─────────────────────────────────────────────────────────────────────────────
# Hindi Verb Conjugation Map (tu → tum → aap forms)
# ─────────────────────────────────────────────────────────────────────────────
# Format: (tu_form, tum_form, aap_form)
VERB_CONJUGATION_MAP = [
    # Movement / Action
    ("chal",    "chalo",    "chaliye"),
    ("mil",     "milo",     "miliye"),
    ("aa",      "aao",      "aaiye"),
    ("ja",      "jao",      "jaiye"),
    ("de",      "do",       "dijiye"),
    ("le",      "lo",       "lijiye"),
    ("kar",     "karo",     "kariye"),
    ("bol",     "bolo",     "boliye"),
    ("sun",     "suno",     "suniye"),
    ("dekh",    "dekho",    "dekhiye"),
    ("bata",    "batao",    "bataiye"),
    ("ruk",     "ruko",     "rukiye"),
    ("padh",    "padho",    "padhiye"),
    ("likh",    "likho",    "likhiye"),
    ("kha",     "khao",     "khaiye"),
    ("pi",      "piyo",     "pijiye"),
    ("so",      "soyo",     "soiye"),
    ("uth",     "utho",     "uthiye"),
    ("baith",   "baitho",   "baithiye"),
    ("samajh",  "samjho",   "samjhiye"),
    ("bhej",    "bhejo",    "bhejiye"),
    ("soch",    "socho",    "sochiye"),
    ("rakh",    "rakho",    "rakhiye"),
    ("dikhaa",  "dikhao",   "dikhaiye"),
    ("bheej",   "bheejo",   "bheejiye"),
]

# Pronoun map: tu → tum → aap — PRE-COMPILED
PRONOUN_MAP = {
    "tu_to_tum": [(re.compile(r'\btu\b', re.IGNORECASE), 'tum'), (re.compile(r'\btera\b', re.IGNORECASE), 'tumhara'), (re.compile(r'\bteri\b', re.IGNORECASE), 'tumhari'), (re.compile(r'\btere\b', re.IGNORECASE), 'tumhare'), (re.compile(r'\btujhe\b', re.IGNORECASE), 'tumhe')],
    "tu_to_aap": [(re.compile(r'\btu\b', re.IGNORECASE), 'aap'), (re.compile(r'\btum\b', re.IGNORECASE), 'aap'), (re.compile(r'\btera\b', re.IGNORECASE), 'aapka'), (re.compile(r'\bteri\b', re.IGNORECASE), 'aapki'), (re.compile(r'\btere\b', re.IGNORECASE), 'aapke'), (re.compile(r'\btujhe\b', re.IGNORECASE), 'aapko'), (re.compile(r'\btumhe\b', re.IGNORECASE), 'aapko'), (re.compile(r'\btumhara\b', re.IGNORECASE), 'aapka'), (re.compile(r'\btumhari\b', re.IGNORECASE), 'aapki'), (re.compile(r'\btumhare\b', re.IGNORECASE), 'aapke')],
}

# Pre-compiled AI-speak phrase patterns for _strip_ai_artifacts
_AI_SPEAK_COMPILED = [re.compile(re.escape(str(p)), re.IGNORECASE) for p in AI_SPEAK_PHRASES]

# Pre-compiled static regex patterns used in hot path
_RE_HTML_BR = re.compile(r'<\s*/?\s*br\s*/?>', re.IGNORECASE)
_RE_HTML_TAGS = re.compile(r'<[^>]+>')
_RE_FOREIGN_CHARS = re.compile(r'[\u2E80-\u9FFF\uAC00-\uD7AF\u0600-\u06FF\u3040-\u309F\u30A0-\u30FF]')
_RE_MULTI_SPACE = re.compile(r'\s{2,}')
_RE_ORPHAN_PUNCT = re.compile(r'^\s*[,.:;!?]\s*')


def _build_verb_pattern(root: str) -> str:
    """Build a regex pattern for verb root substitution.
    Short roots (≤3 chars) use lookaround to avoid matching inside words
    like 'yaad', 'baad', 'aara'. Longer roots use standard \\b boundaries.
    """
    escaped = re.escape(root)
    if len(root) <= 3:
        # Lookaround: not preceded/followed by a word character
        return r'(?<!\w)' + escaped + r'(?!\w)'
    return r'\b' + escaped + r'\b'


def apply_verb_conjugation(text: str, address_as: str) -> str:
    """
    Converts Hindi verb forms based on the relationship formality level.
    - address_as contains 'aap' → convert tu/tum forms to aap forms
    - address_as contains 'tum' → convert tu forms to tum forms
    - address_as contains 'tu' only → leave as is (most casual)
    Uses lookaround for short roots to avoid matching inside compound words.
    """
    if not address_as:
        return text
    
    address_lower = address_as.lower()
    
    if 'aap' in address_lower:
        # Most respectful: convert all tu/tum verbs to aap forms
        for tu_form, tum_form, aap_form in VERB_CONJUGATION_MAP:
            text = re.sub(_build_verb_pattern(tu_form), aap_form, text, flags=re.IGNORECASE)
            text = re.sub(_build_verb_pattern(tum_form), aap_form, text, flags=re.IGNORECASE)
        # Convert pronouns tu/tum → aap
        for pattern, replacement in PRONOUN_MAP["tu_to_aap"]:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    
    elif 'tum' in address_lower:
        # Medium respectful: convert tu verbs to tum forms
        for tu_form, tum_form, _aap_form in VERB_CONJUGATION_MAP:
            text = re.sub(_build_verb_pattern(tu_form), tum_form, text, flags=re.IGNORECASE)
        # Convert pronouns tu → tum
        for pattern, replacement in PRONOUN_MAP["tu_to_tum"]:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    
    # If address_as is 'tu/bhai' — no conversion needed, tu forms are fine
    
    return text


def apply_grammar_fixes(text: str, owner_gender: str = "male") -> str:
    """
    Post-processing layer: catches common Hinglish grammar errors
    that the model might still produce despite prompt instructions.
    Applies masculine gender fixes only if the owner is male.
    Uses pre-compiled regex patterns for performance.
    """
    for compiled_pattern, replacement in HINGLISH_FIXES:
        text = compiled_pattern.sub(replacement, text)
        
    if owner_gender.lower() == "male":
        for compiled_pattern, replacement in MASCULINE_VERB_FIXES:
            text = compiled_pattern.sub(replacement, text)
            
    return text


def _get_relationship_context(contact_name: str) -> dict:
    """
    Looks up the contact in contacts.json and returns relationship/gender info.
    Used to adjust tone (avoid 'bhai' for girlfriend, use 'aap' for parents, etc.)
    """
    result = {"type": "friend", "gender": "unknown", "address_as": "bhai", "avoid_terms": [],
              "nickname": "", "shared_context": "", "topics": []}
    
    if not contact_name:
        return result
    
    contact = get_contact_by_name(contact_name)
    if not contact:
        return result
    
    relationship = contact.get("relationship", "friend")
    gender = contact.get("gender", "unknown")
    
    # Extract relationship card fields
    card_nickname = contact.get("nickname", "")
    card_shared_context = contact.get("shared_context", "")
    card_topics = contact.get("topics", [])
    
    # Map relationship to behavior rules
    # Female romantic partners — no bhai/bro
    if relationship in ["girlfriend", "wife"]:
        result = {
            "type": relationship,
            "gender": gender or "female",
            "address_as": "tum/baby",
            "avoid_terms": ["bhai", "bro", "yaar", "dude"],
            "nickname": card_nickname,
            "shared_context": card_shared_context,
            "topics": card_topics,
        }
    # Male romantic partners
    elif relationship in ["boyfriend", "husband"]:
        result = {
            "type": relationship,
            "gender": gender or "male",
            "address_as": "tum/baby",
            "avoid_terms": ["bhai", "bro", "yaar", "dude"],
            "nickname": card_nickname,
            "shared_context": card_shared_context,
            "topics": card_topics,
        }
    # Parents — respectful
    elif relationship in ["mother", "father"]:
        result = {
            "type": relationship,
            "gender": gender or ("female" if relationship == "mother" else "male"),
            "address_as": "aap/ji",
            "avoid_terms": ["bhai", "bro", "yaar", "dude", "tu"],
            "nickname": card_nickname,
            "shared_context": card_shared_context,
            "topics": card_topics,
        }
    # Teachers/professors — respectful
    elif relationship in ["teacher", "professor", "sir", "madam", "boss", "senior"]:
        result = {
            "type": relationship,
            "gender": gender,
            "address_as": "aap/sir" if gender != "female" else "aap/ma'am",
            "avoid_terms": ["bhai", "bro", "yaar", "dude", "tu", "abe"],
            "nickname": card_nickname,
            "shared_context": card_shared_context,
            "topics": card_topics,
        }
    # Siblings
    elif relationship in ["brother", "sister"]:
        result = {
            "type": relationship,
            "gender": gender or ("male" if relationship == "brother" else "female"),
            "address_as": "tum",
            "avoid_terms": ["bro"] if relationship == "sister" else [],
            "nickname": card_nickname,
            "shared_context": card_shared_context,
            "topics": card_topics,
        }
    # Female friends — don't use bhai/bro
    elif gender == "female":
        result = {
            "type": relationship,
            "gender": "female",
            "address_as": "tum",
            "avoid_terms": ["bhai", "bro"],
            "nickname": card_nickname,
            "shared_context": card_shared_context,
            "topics": card_topics,
        }
    # Default male friend — no restrictions
    else:
        result = {
            "type": relationship,
            "gender": gender,
            "address_as": "tum/bhai",
            "avoid_terms": [],
            "nickname": card_nickname,
            "shared_context": card_shared_context,
            "topics": card_topics,
        }
    
    return result


def _strip_ai_artifacts(text: str) -> str:
    """Remove AI-speak phrases, HTML tags, and foreign characters.
    Uses pre-compiled regex patterns for performance."""
    # Single message — take only the first line (prevent double messages)
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if lines:
        text = lines[0]

    # Remove AI-speak phrases (pre-compiled)
    text_lower = text.lower()
    for i, phrase in enumerate(AI_SPEAK_PHRASES):
        if str(phrase) in text_lower:
            text = _AI_SPEAK_COMPILED[i].sub('', text).strip()

    # Strip HTML artifacts (pre-compiled)
    text = _RE_HTML_BR.sub('', text)
    text = _RE_HTML_TAGS.sub('', text)

    # Strip foreign characters (pre-compiled)
    text = _RE_FOREIGN_CHARS.sub('', text)

    # Clean up double spaces and orphaned punctuation (pre-compiled)
    text = _RE_MULTI_SPACE.sub(' ', text).strip()
    text = _RE_ORPHAN_PUNCT.sub('', text).strip()
    return text


def _enforce_length(text: str, style_profile: dict) -> str:
    """Hard truncation based on max_words from style profile."""
    max_words = int(style_profile.get("max_words", 20))
    words = text.split()
    if len(words) > max_words * 2:
        text = " ".join(words[0:max_words])
        last_punct = -1
        for i in range(len(text) - 1, int(len(text) * 0.5), -1):
            if text[i] in {'.', '!', '?', ','}:
                last_punct = i
                break
        if last_punct != -1:
            end_idx = int(last_punct) + 1
            text = text[0:end_idx]
    return text


def _replace_formal_vocab(text: str) -> str:
    """Replace formal English words with casual Hinglish equivalents.
    Uses pre-compiled regex patterns."""
    for compiled_pattern, casual in FORMAL_TO_CASUAL:
        text = compiled_pattern.sub(casual, text)
    return text


def _enforce_emoji_policy(text: str, style_profile: dict) -> str:
    """Limit emoji count based on emoji_freq setting."""
    emoji_freq = style_profile.get("emoji_freq", "low")
    emojis_in_text = EMOJI_PATTERN.findall(text)
    if emoji_freq == "none" or emoji_freq == "never":
        text = EMOJI_PATTERN.sub('', text).strip()
    elif emoji_freq == "low":
        if emojis_in_text:
            if random.random() < 0.7:
                text = EMOJI_PATTERN.sub('', text).strip()
            elif len(emojis_in_text) > 1:
                kept_first = False
                def _keep_first_emoji(match):
                    nonlocal kept_first
                    if not kept_first:
                        kept_first = True
                        return match.group()
                    return ''
                text = EMOJI_PATTERN.sub(_keep_first_emoji, text).strip()
    elif emoji_freq == "medium":
        if len(emojis_in_text) > 2:
            count = [0]
            def _keep_two_emojis(match):
                count[0] += 1
                return match.group() if count[0] <= 2 else ''
            text = EMOJI_PATTERN.sub(_keep_two_emojis, text).strip()
    # "high" — no limiting
    return text


def _enforce_greeting(text: str, incoming_msg_type: str) -> str:
    """If incoming was a greeting, ensure the response greets back."""
    if incoming_msg_type != "greeting":
        return text
    greeting_response_words = {"hi", "hello", "hey", "yo", "sup", "namaste", "haan",
                               "kya", "haal", "bata", "bol", "what's up", "wassup",
                               "kaise", "bhai", "yaar", "kuch", "nothing", "theek",
                               "arey", "oye", "bro"}
    response_lower = text.lower()
    has_greeting = any(w in response_lower for w in greeting_response_words)
    if not has_greeting:
        casual_greetings = ["yo", "haan bhai", "hey", "arey", "haan", "bol"]
        greeting = random.choice(casual_greetings)
        text = f"{greeting}, {text.lower()}" if text else greeting
    return text


def _filter_single_word(text: str) -> str:
    """Replace random single-word nonsense with a safe filler."""
    words_in_response = text.split()
    if len(words_in_response) == 1:
        valid_single_words = {"haan", "nahi", "ok", "accha", "acha", "theek", "sahi",
                              "bilkul", "pakka", "hmm", "yo", "hey", "hi", "bol",
                              "kya", "kyu", "kab", "kaise", "kidhar", "haa", "na",
                              "bhai", "yaar", "arey", "abe", "oye", "damn", "nice",
                              "oof", "lol", "haha", "wow"}
        if words_in_response[0].lower().rstrip('?.!,') not in valid_single_words:
            text = "haan"
    return text


def _enforce_emotional_response(text: str, incoming_msg_type: str, rel_context: dict) -> str:
    """Romantic partners get warm replies instead of cold/dismissive ones."""
    if incoming_msg_type != "emotional":
        return text
    if rel_context["type"] in ["girlfriend", "wife", "boyfriend", "husband"]:
        cold_responses = {"accha", "acha", "ok", "k", "hmm", "theek", "haan", "oh"}
        if text.lower().strip().rstrip('?.!,') in cold_responses:
            warm_replacements = [
                "aww, miss you too ❤️",
                "love you too baby ❤️",
                "arey, kya hua? batao na",
                "haan jaan, bol na",
                "aww that's sweet ❤️",
            ]
            text = random.choice(warm_replacements)
    return text


def _enforce_gender_language(text: str, rel_context: dict) -> str:
    """Remove avoid-terms and apply verb conjugation for the relationship."""
    if rel_context["avoid_terms"]:
        for term in rel_context["avoid_terms"]:
            text = re.sub(r'\b' + re.escape(term) + r'\b\s*', '', text, flags=re.IGNORECASE).strip()
    address_as = rel_context.get("address_as", "")
    if address_as:
        text = apply_verb_conjugation(text, address_as)
    return text


def _normalize_style(text: str, style_profile: dict) -> str:
    """Apply capitalization, punctuation, and whitespace normalization."""
    if not style_profile.get("uses_capitalization", True):
        text = text.lower()
    if not style_profile.get("uses_periods", True):
        text = text.rstrip(".")
    text = re.sub(r'\s{2,}', ' ', text).strip()
    return text


def apply_clone_postprocessing(
    text: str,
    style_profile: dict,
    contact_name: str = "",
    incoming_msg_type: str = "",
    rel_context: dict | None = None,
) -> str:
    """
    Clone Mode post-processing pipeline.
    Accepts optional pre-computed rel_context to avoid redundant lookups.
    """
    if rel_context is None:
        rel_context = _get_relationship_context(contact_name)

    text = _strip_ai_artifacts(text)
    text = _enforce_length(text, style_profile)
    text = _replace_formal_vocab(text)
    text = _enforce_emoji_policy(text, style_profile)
    text = _enforce_greeting(text, incoming_msg_type)
    text = _filter_single_word(text)
    text = _enforce_emotional_response(text, incoming_msg_type, rel_context)
    if contact_name:
        text = _enforce_gender_language(text, rel_context)
    text = _normalize_style(text, style_profile)


    return text


def _enforce_verb_surfaces(text: str, verb_map: dict) -> str:
    """Enforce specific verb surfaces from the per-contact extracted verb map.
    e.g. verb_map = {"kar": "karo", "chal": "chalo"}
    """
    if not verb_map:
        return text
    for root, surface in verb_map.items():
        # Use the safe pattern builder for short roots
        pattern = _build_verb_pattern(root)
        text = re.sub(pattern, surface, text, flags=re.IGNORECASE)
    return text


def _build_persona_card(style_profile: dict, owner_name: str) -> str:
    """
    Builds a compact persona card from the style profile for clone mode.
    This anchors the model's identity beyond just stylistic rules.
    """
    avg_words = style_profile.get("avg_words", 10)
    hindi_ratio = style_profile.get("hindi_english_ratio", "50:50")
    fillers = style_profile.get("common_fillers", [])
    starters = style_profile.get("common_starters", [])
    laughter = style_profile.get("laughter_style", "none")
    agreement = style_profile.get("agreement_style", "haan")
    emoji_freq = style_profile.get("emoji_freq", "medium")
    
    filler_str = ", ".join(fillers[:5]) if fillers else "none specific"
    starter_str = ", ".join(starters[:5]) if starters else "varies"
    
    card = f"""WHO YOU ARE:
- Name: {owner_name}. A real person texting on WhatsApp.
- Texting habits: VERY short replies ({avg_words} words avg). Quick, casual, never formal.
- Language mix: Hinglish (Hindi:English ratio ≈ {hindi_ratio}), sprinkled with fillers like: {filler_str}
- Starts messages with words like: {starter_str}
- Laughs with: {laughter}. Agrees with: "{agreement}".
- Emoji usage: {emoji_freq}.
- You are NOT a helpful AI assistant. You are a REAL PERSON. Text like you would on WhatsApp — short, natural, human."""
    
    return card


def _build_negative_examples(style_profile: dict) -> str:
    """
    Builds anti-pattern examples that show the model what NOT to generate.
    LLMs are better at avoiding observed bad patterns than inferring good ones.
    """
    avg_words = style_profile.get("avg_words", 10)
    
    negative = """
❌ NEVER REPLY LIKE THIS — These are AI patterns, NOT human texting:
- "Haan ji, aap bilkul sahi keh rahe hain! Main samajh sakta hoon aapki situation..." (TOO LONG, too formal)
- "Of course! Here are some tips for you: 1. First... 2. Second... 3. Third..." (AI assistant numbered list)
- "I understand your concern and would be happy to help you with that." (pure English, robotic, not how a real person texts)
- "That's a great question! Let me explain..." (AI filler phrases — REAL people never say this on WhatsApp)
- "I hope this helps! Feel free to ask if you need anything else." (customer support, not a friend)
"""
    
    if avg_words < 6:
        negative += f"\n- CRITICAL: Your messages must be SHORT. Average {avg_words} words. Do NOT write paragraphs.\n"
    
    return negative


def _build_clone_system_prompt(
    owner_name: str,
    user_name: str,
    style_profile: dict,
    examples: list,
    message_type: str,
    memory_context: str = "",
    recent_turns: list = None,
    avoid_phrases: list = None,
    rel_context: dict | None = None,
    contact_style: dict | None = None,
) -> str:
    """
    Dynamically assembles the clone system prompt.
    CHECKS BEFORE EVERY REPLY: relationship, gender, time of day, personal context.
    PRIORITY ORDER: Comprehension > Context Awareness > Relevance > Style
    """
    from datetime import datetime
    
    # Persona card
    persona = _build_persona_card(style_profile, owner_name)
    
    # Negative examples
    negatives = _build_negative_examples(style_profile)
    
    # ── 1. TIME OF DAY CONTEXT ────────────────────────────────────────
    now = datetime.now()
    hour = now.hour
    if 5 <= hour < 12:
        time_period = "morning"
        time_tone = "Fresh, energetic. Appropriate greetings: 'good morning', 'subah subah'"
    elif 12 <= hour < 17:
        time_period = "afternoon"
        time_tone = "Normal energy. Casual tone."
    elif 17 <= hour < 21:
        time_period = "evening"
        time_tone = "Relaxed. Appropriate greetings: 'good evening', can discuss dinner/evening plans"
    else:  # 21-5
        time_period = "late night"
        time_tone = "Late night, chill mood. Keep replies short but still engage with what they say. Don't keep telling them to sleep unless they say goodnight first."
    
    time_context = f"""⏰ TIME: It's currently {now.strftime('%I:%M %p')} ({time_period}).
- {time_tone}
- Match the time naturally. Don't say 'good morning' at midnight."""
    
    # ── 2. RELATIONSHIP & GENDER CONTEXT ────────────────────────────────
    if rel_context is None:
        rel_context = _get_relationship_context(user_name)
    rel = rel_context
    rel_type = rel["type"]
    gender = rel["gender"]
    avoid = rel["avoid_terms"]
    address = rel["address_as"]
    
    # ── 2b. RELATIONSHIP CARD (per-contact enrichment) ────────────────
    relationship_card = ""
    card_nickname = rel.get("nickname", "")
    card_shared_ctx = rel.get("shared_context", "")
    card_topics = rel.get("topics", [])
    if card_nickname or card_shared_ctx or card_topics:
        card_parts = []
        if card_nickname:
            card_parts.append(f"- You call them: {card_nickname}")
        if card_shared_ctx:
            card_parts.append(f"- Shared context: {card_shared_ctx}")
        if card_topics:
            card_parts.append(f"- Common topics: {', '.join(card_topics)}")
        relationship_card = f"\n📇 RELATIONSHIP CARD for {user_name}:\n" + "\n".join(card_parts)
    
    # ── 2c. DAILY CONTEXT SUMMARY ─────────────────────────────────────
    daily_context_block = build_daily_context_prompt()
    
    # Build relationship-specific instruction
    relationship_instruction = ""
    
    if rel_type in ["girlfriend", "wife"]:
        relationship_instruction = f"""🚫 RELATIONSHIP: {user_name} is your {rel_type.upper()} ({gender}).
- Address her as: {address}. NEVER use 'bhai', 'bro', 'yaar', 'dude'.
- Be warm, affectionate, caring. Use 'tum', 'baby', 'jaan', or her name.
- React to her feelings with empathy. Say 'miss you', 'love you' naturally when appropriate.
- Example: "haan chal" NOT "haan bhai chal". "Accha theek" NOT "bhai theek"."""

    elif rel_type in ["boyfriend", "husband"]:
        relationship_instruction = f"""🚫 RELATIONSHIP: {user_name} is your {rel_type.upper()} ({gender}).
- Address him as: {address}. Be warm and affectionate.
- NEVER use 'bhai', 'bro' — he is your partner, not a buddy."""

    elif rel_type in ["mother", "father"]:
        parent_label = "MAA/MUMMY" if rel_type == "mother" else "PAPA/DAD"
        relationship_instruction = f"""🚫 RELATIONSHIP: {user_name} is your {parent_label} ({gender}).
- ALWAYS use 'aap', 'ji'. Be respectful. NEVER use 'tu', 'bhai', 'bro', 'abe'.
- Respond caringly. If they ask about food/health → reassure them.
- Example: "Ji mummy" NOT "Haan bro". "Haan papa, theek hoon" NOT "Yo bhai"."""

    elif rel_type == "family":
        relationship_instruction = f"""RELATIONSHIP: {user_name} is FAMILY ({gender}).
- Be respectful but warm. Use 'aap' or 'tum' based on their age.
- Don't use slang like 'abe', 'bhai' unless they are a cousin/sibling."""

    elif rel_type in ["teacher", "professor", "sir", "madam", "boss", "senior"]:
        title = "Sir" if gender != "female" else "Ma'am"
        relationship_instruction = f"""🚫 RELATIONSHIP: {user_name} is your {rel_type.upper()} ({gender}).
- ALWAYS be respectful. Use 'aap', 'ji', '{title}'.
- NEVER use 'tu', 'tum', 'bhai', 'bro', 'yaar', 'abe'. No slang.
- Keep replies polite but not overly formal. Still be natural.
- Example: "Ji {title}, ho jayega" NOT "Haan bhai dekh lenge"."""

    elif rel_type in ["brother", "sister"]:
        if rel_type == "sister":
            relationship_instruction = f"""RELATIONSHIP: {user_name} is your SISTER ({gender}).
- Don't call her 'bro'. Use 'tu/tum' or her name.
- Be natural sibling tone — teasing is ok."""
        else:
            relationship_instruction = f"""RELATIONSHIP: {user_name} is your BROTHER ({gender}).
- Normal sibling tone. 'bhai', 'tu' are fine."""

    elif rel_type == "best friend":
        relationship_instruction = f"""RELATIONSHIP: {user_name} is your BEST FRIEND ({gender}).
- Be very casual, close. Teasing, inside jokes are fine.
- {'Use bhai/bro naturally.' if gender == 'male' else "Don't use 'bhai/bro' — she's female. Use 'tum' or her name."}"""

    elif rel_type in ["colleague", "junior"]:
        relationship_instruction = f"""RELATIONSHIP: {user_name} is your {rel_type.upper()} ({gender}).
- Casual but professional. No slang like 'abe'.
- {'Bhai is ok.' if gender == 'male' else "Avoid 'bhai/bro' — use 'tum' or name."}"""

    elif rel_type == "acquaintance":
        relationship_instruction = f"""RELATIONSHIP: {user_name} is an ACQUAINTANCE ({gender}).
- Keep it polite, not too casual. Use 'aap' or 'tum'.
- Don't be too familiar. No heavy slang."""

    elif gender == "female" and rel_type == "friend":
        relationship_instruction = f"""RELATIONSHIP: {user_name} is a FEMALE FRIEND.
- Don't call her 'bhai' or 'bro'. Use 'tum' or her name.
- Be natural but gender-appropriate."""

    # Default for male friends / unknown — no special instruction needed
    
    # ── 3. PERSONAL CONTEXT (cached from context.txt) ────────────────
    personal_context = ""
    ctx_data = _load_context_file()
    if ctx_data:
        personal_context = f"""
PERSONAL KNOWLEDGE (use this to answer personal questions naturally):
{ctx_data}
↑ Use this info ONLY when relevant. Don't force it into every reply."""
    
    # ── 4. COMPREHENSION RULES ────────────────────────────────────────
    comprehension_rules = f"""⚠️ RULE #1 — UNDERSTAND BEFORE REPLYING:

Your #1 job is to UNDERSTAND what {user_name if user_name else 'the person'} is saying and respond APPROPRIATELY.
Style matching is secondary.

INTENT MATCHING:
- QUESTION → Give an actual ANSWER
- HI/HELLO → Greet them BACK (match time of day)
- INVITATION → Respond to the INVITATION
- NEWS/FEELINGS → React to the CONTENT
- LOVE/AFFECTION → Respond with appropriate warmth based on your relationship

❌ WRONG: Random short phrase that ignores what they said.
✅ RIGHT: Short, casual reply that ADDRESSES their message.

🚫 RULE #2 — NEVER FABRICATE FACTS:
- You do NOT know what {owner_name} actually did, is doing, or plans to do unless it's in the conversation history or personal context.
- If asked "kya kar rahe ho?" / "kal kya kiya?" / "kahan gaye?" and you DON'T know → give a VAGUE or DEFLECTIVE reply.
- VAGUE replies: "bas kuch nahi yaar", "chill kar raha", "timepass", "ghar pe hoon", "kuch khaas nahi"
- DEFLECTIVE replies: "tu bata", "pehle tu bata", "kyu? kya hua?"
- NEVER invent specific activities like "kal gaye the", "movie dekhi", "market gaya" unless the conversation explicitly mentioned it.
- NEVER make up places, events, or plans.
- NEVER invent people's names or fake meetings/projects.
- NEVER fabricate times, dates, or schedules (like "kal 9 baje", "subah meeting hai").
- When in doubt, deflect with a question: 'kyu pooch raha hai?' or 'tu bata pehle'"""
    
    # ── 5. Message-type-specific instructions ─────────────────────────
    type_instructions = {
        "greeting": f"""[MSG TYPE: GREETING]
⚠️ CRITICAL: They are greeting you. YOUR REPLY MUST START WITH A GREETING WORD.
Good greeting starts: hey, yo, haan bhai, arey, sup, kya haal, bol bhai
❌ WRONG: "acha", "theek", "chilla", "project ka" — these are NOT greetings
✅ RIGHT: "yo kya haal", "hey bhai", "haan bol", "arey kya chal rha"
Max {int(min(style_profile.get('avg_words', 5) + 2, 8))} words.""",

        "reaction": f"[MSG TYPE: REACTION] Reply with a very short reaction that makes sense in context. 1-5 words.",

        "emotional": f"""[MSG TYPE: EMOTIONAL]
They are expressing feelings. You MUST acknowledge their emotion first.
❌ WRONG: "acha", "ok", random topic change
✅ RIGHT: "arey kya hua", "sad mat ho yaar", "miss you too"
Under {style_profile.get('max_words', 15)} words.""",

        "banter": f"[MSG TYPE: BANTER] Keep it fun and contextually relevant to what they said. Under {style_profile.get('avg_words', 8) + 4} words.",

        "factual": f"""[MSG TYPE: QUESTION]
They asked a question. You MUST give an actual answer.
- For YES/NO questions ("...hai?", "...tha?", "...kya?") → Start with "haan" or "nahi", then explain briefly.
- For PERSONAL activity questions ("kya kar rahe ho?", "kahan gaye?", "kal kya kiya?") → Give VAGUE replies, do NOT make up specific activities.
❌ WRONG: "kal market gaya tha", "movie dekhi", "uska project hai" (fabricated events!)
❌ WRONG: "kal subah 9 baje meeting hai", "subah ka meeting hai na?" (fabricated schedules!)
✅ RIGHT: "bas chill kar raha", "kuch nahi yaar", "ghar pe hoon, tu bata"
- For FACTUAL/KNOWLEDGE questions → Answer from general knowledge.
- ⚠️ CRITICAL: Do NOT invent names of people, projects, meetings, or specific times.
Max {style_profile.get('max_words', 20)} words.""",
    }
    type_specific = type_instructions.get(message_type, type_instructions["banter"])
    
    # ── 6. Emoji rules ────────────────────────────────────────────────
    emoji_freq = style_profile.get('emoji_freq', 'low')
    if emoji_freq == 'low':
        emoji_rule = "Emojis: RARELY use emojis. Most messages should have ZERO emojis. If you must, use only ONE from: " + ' '.join(style_profile.get('common_emojis', [])[:3])
    elif emoji_freq == 'none':
        emoji_rule = "Emojis: NEVER use emojis. Not a single one."
    elif emoji_freq == 'medium':
        emoji_rule = "Emojis: Occasionally use 1 emoji. From: " + ' '.join(style_profile.get('common_emojis', [])[:3])
    else:
        emoji_rule = "Emojis: Use naturally."
    
    # ── 7. Style rules ────────────────────────────────────────────────
    style_rules = f"""STYLE RULES (apply AFTER understanding the message):
- Length: ~{style_profile.get('avg_words', 10)} words avg, max {style_profile.get('max_words', 20)} words.
- Periods: {'Use them' if style_profile.get('uses_periods', False) else 'Almost never use periods.'}
- Capitalization: {'Normal' if style_profile.get('uses_capitalization', False) else 'Prefer lowercase.'}
- {emoji_rule}
- Tone: Casual, organic, like a real person on WhatsApp.
- OUTPUT: Send ONLY ONE short message. Never send multiple lines or multiple messages.
"""
    
    # Build examples section
    examples_section = "\nREFERENCE EXAMPLES (for style only, not for content copying):\n"
    for idx, ex in enumerate(examples):
        examples_section += f"\n[Example {idx+1}]\nThey said: \"{ex['incoming']}\"\nI replied: \"{ex['reply']}\"\n"
    examples_section += "\n↑ Use these for STYLE reference. Do NOT copy-paste these replies for different questions.\n"
    
    # Memory context
    memory_section = ""
    if memory_context:
        memory_section = f"\n{memory_context}\n"
    
    # ── RECENT CONVERSATION (so model sees context) ─────────────────
    recent_convo_block = ""
    if recent_turns and len(recent_turns) > 0:
        convo_lines = []
        for turn in recent_turns[-10:]:  # Last 10 turns (5 exchanges)
            role = turn.get('role', '')
            content = turn.get('content', '')
            if role == 'user':
                convo_lines.append(f"  {user_name}: \"{content}\"")
            elif role == 'assistant':
                convo_lines.append(f"  You: \"{content}\"")
        if convo_lines:
            recent_convo_block = "\n💬 RECENT CONVO (what was just said — DO NOT ignore this):\n" + "\n".join(convo_lines)
    
    # ── ANTI-REPETITION ─────────────────────────────────────────────
    anti_repeat_block = ""
    if avoid_phrases:
        anti_repeat_block = "\n⚠️ DO NOT repeat these phrases you already used recently: " + ", ".join(f'"{p}"' for p in avoid_phrases) + "\nSay something DIFFERENT this time.\n"

    # ══════════════════════════════════════════════════════════════════
    # ASSEMBLE FULL PROMPT — Context awareness first, then style
    # ══════════════════════════════════════════════════════════════════
    prompt = f"""You are {owner_name}, a real person texting on WhatsApp. You are chatting with {user_name if user_name else 'a friend'}.

── CONTEXT AWARENESS (CHECK THIS BEFORE EVERY REPLY) ──
{time_context}
{relationship_instruction}
{relationship_card}
{daily_context_block}
{recent_convo_block}
{comprehension_rules}
{personal_context}
{anti_repeat_block}

── YOUR IDENTITY ──
{persona}

── CONTACT-SPECIFIC STYLE ──
{build_style_prompt_block(contact_style, user_name) if contact_style else '(no per-contact data)'}

── THIS MESSAGE ──
{type_specific}

── HOW TO REPLY ──
{style_rules}
{negatives}
{examples_section}
{memory_section}
Send ONLY ONE short reply as {owner_name}. Check context → understand intent → reply in style."""
    
    return prompt


class BotHandler:
    def __init__(self):
        # Conversation memory: deque auto-trims to MAX_HISTORY
        self.conversation_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=MAX_HISTORY))
        # Anti-repetition: track recent bot replies per user (auto-trims to 5)
        self._recent_replies: dict[str, deque] = defaultdict(lambda: deque(maxlen=5))

        self.temp_dir = Config.TEMP_DIR
        os.makedirs(self.temp_dir, exist_ok=True)

        # Whisper STT — LAZY LOADED on first voice message (saves 5-10s cold start)
        self.stt_model = None
        self._stt_loaded = False
        print("👂 Whisper STT: will lazy-load on first voice message")

    def _ensure_whisper(self):
        """Lazy-load whisper model on first use."""
        if not self._stt_loaded:
            try:
                import whisper
                print("👂 Loading Whisper Model (first voice message)...")
                self.stt_model = whisper.load_model("base")
                print("✅ Whisper STT loaded!")
            except Exception as e:
                print(f"❌ Whisper Load Error: {e}")
                self.stt_model = None
            self._stt_loaded = True
        return self.stt_model

    def _get_relevant_context(self, user_text: str) -> str:
        """
        Smart Context Injection:
        Only returns context if user's message contains relevant keywords.
        """
        user_text_lower = user_text.lower()

        # Keywords drawn from context.txt
        keywords = {
            # People (extend with owner's real contact names)
            'mom', 'dad', 'friend', 'relationship',
            # Work/Study
            'project', 'deadline', 'exam', 'placement', 'job', 'work', 'study',
            'coding', 'code', 'debug', 'error', 'python', 'react', 'javascript', 'ml', 'ai',
            # Habits/Lifestyle
            'food', 'biryani', 'momo', 'chai', 'coffee', 'eat', 'dinner', 'lunch',
            'sleep', 'late', 'night', 'tired', 'awake',
            'cricket', 'match', 'india', 'game', 'play',
            # Mood/State
            'stress', 'sad', 'happy', 'bored', 'upset', 'mood', 'feeling',
            # Identity queries
            'who', 'kaun', 'intro', 'know', 'relationship', 'girlfriend', 'gf'
        }

        is_relevant = any(k in user_text_lower for k in keywords)

        if not is_relevant:
            print(f"🕵️ Intent Check: General Query. Skipping context injection.")
            return ""

        print(f"🕵️ Intent Check: Personal Topic Detected. Injecting context.")

        ctx_data = _load_context_file()
        if ctx_data:
            # Strip the first header line for assistant mode injection
            ctx_lines = ctx_data.split('\n')
            clean_lines: list[str] = []
            skipped_header = False
            for line in ctx_lines:
                stripped = line.strip()
                if not stripped:
                    clean_lines.append("")
                    continue
                if not skipped_header and stripped.startswith('#'):
                    skipped_header = True
                    continue
                clean_lines.append(line.rstrip())

            clean_data = "\n".join(clean_lines).strip()
            if clean_data:
                return f"\n<personal_context>\n{clean_data}\n</personal_context>\n"

        return ""

    def _add_to_history(self, user_id: str, role: str, content: str) -> None:
        """Add a message to the user's conversation history (deque auto-trims)."""
        self.conversation_history[user_id].append({
            'role': role,
            'content': content
        })

    async def generate_ai_response(self, user_id: str, text: str, user_name: str = "") -> str:
        """
        Uses LOCAL Ollama (Base Model) to generate a contextual response.
        
        CLONE MODE pipeline:
          1. Classify message type
          2. Retrieve style examples (type-filtered + MMR)
          3. Build dynamic prompt (persona + negatives + type-aware)
          4. Generate via Ollama
          5. Apply grammar fixes + clone post-processing
          6. Save to topic memory
          
        ASSISTANT MODE pipeline (unchanged):
          1. Add user input to history
          2. Build system message with optional personal context
          3. Prepend SEED_CONVERSATION to anchor tone/grammar
          4. Generate via Ollama
          5. Filter echo + apply grammar fixes
          6. Save reply to history
        """
        # 1. Add user input to history
        self._add_to_history(user_id, 'user', text)

        # 2. Build system message
        settings = get_ai_settings()
        is_clone_mode = (settings.get("mode") == "Clone Mode")
        owner_name = settings.get("owner_name", OWNER_ID)
        
        if is_clone_mode:
            # ─── ADVANCED CLONE MODE ─────────────────────────────────────
            style_profile = get_style_profile()
            
            # Classify message type for retrieval + prompt assembly
            message_type = classify_message_type(text)
            
            # Smart RAG: only retrieve when it adds value over reasoning
            if should_retrieve(text, message_type):
                examples = retrieve_style_examples(text, n_results=3)
                retrieval_status = f"retrieval={len(examples)} examples"
            else:
                examples = []
                retrieval_status = "retrieval=SKIPPED (reasoning-only)"
            print(f"🧠 [CLONE] type={message_type}, {retrieval_status}, avg_words={style_profile.get('avg_words')}")
            
            # Get long-term memory context
            memory_context = get_memory_prompt_injection(user_id)
            
            # Anti-repetition: get recent replies for this user
            recent_replies = self._recent_replies.get(user_id, deque())
            avoid_phrases = list(recent_replies)[-5:] if recent_replies else None
            
            # Compute relationship context ONCE, pass to both prompt and postprocessing
            rel_context = _get_relationship_context(user_name if user_name else "")
            
            # Compute per-contact style (three-tier: extracted > blended > cold-start)
            contact_rel_type = rel_context.get("type", "unknown")
            contact_style = get_contact_style(
                contact_name=user_name if user_name else "",
                relationship_type=contact_rel_type,
            )
            print(f"📇 [STYLE] tier={contact_style.get('tier')}, pronoun={contact_style.get('pronoun')}, verbs={len(contact_style.get('verb_map', {}))}")
            
            # Build the dynamic clone system prompt
            full_system_instruction = _build_clone_system_prompt(
                owner_name=owner_name,
                user_name=user_name if user_name else "a friend",
                style_profile=style_profile,
                examples=examples,
                message_type=message_type,
                memory_context=memory_context,
                recent_turns=list(self.conversation_history.get(user_id, deque())),
                avoid_phrases=avoid_phrases,
                rel_context=rel_context,
                contact_style=contact_style,
            )
            
            # Construct message payload (no SEED for clone — it ruins the clone's personality)
            messages = [{'role': 'system', 'content': full_system_instruction}]
            messages.extend(list(self.conversation_history[user_id]))
            
        else:
            # ─── DEFAULT ASSISTANT MODE (unchanged) ──────────────────────
            if user_id == OWNER_ID:
                dynamic_context = self._get_relevant_context(text)
            else:
                dynamic_context = ""
            
            identity_injection = f"\n\n**CURRENT USER INFO:**\nThe person you are talking to right now is named: {user_name}" if user_name else ""
            full_system_instruction = EMPATHETIC_SYSTEM_PROMPT + identity_injection + dynamic_context
    
            # Construct message payload
            messages = [{'role': 'system', 'content': full_system_instruction}]
            messages.extend(SEED_CONVERSATION)
            messages.extend(list(self.conversation_history[user_id]))

        # ── LLM GENERATION WITH FALLBACK CHAIN ────────────────────────────
        ai_reply = await self._generate_with_fallback(messages, is_clone_mode, message_type if is_clone_mode else "unknown")

        # Filter Echo — remove if model repeated the user's input
        last_user_msg = text.strip()
        if ai_reply.lower().startswith(last_user_msg.lower()):
            print(f"⚠️ Echo Detected! Removing prefix: '{last_user_msg}'")
            ai_reply = ai_reply[len(last_user_msg):].strip() # type: ignore
            ai_reply = ai_reply.lstrip('.,!? \n')

        # Apply grammar fixes (both modes)
        owner_gender = settings.get("owner_gender", "male")
        ai_reply = apply_grammar_fixes(ai_reply, owner_gender)
        
        # Apply clone-specific post-processing (CLONE MODE ONLY)
        if is_clone_mode:
            ai_reply = apply_clone_postprocessing(
                ai_reply, style_profile,
                contact_name=user_name,
                incoming_msg_type=message_type,
                rel_context=rel_context,
            )
            
            # Apply per-contact verb surface enforcement
            verb_map = contact_style.get("verb_map", {})
            if verb_map:
                ai_reply = _enforce_verb_surfaces(ai_reply, verb_map)
            
            # Save conversation topics for long-term memory
            try:
                recent_msgs = list(self.conversation_history[user_id])[-4:]
                save_conversation_topics(user_id, recent_msgs)
            except Exception as e:
                print(f"⚠️ Topic memory save error: {e}")

        if not ai_reply:
            # Relationship-aware fallback instead of generic "Ji, kahiye?"
            if is_clone_mode:
                rel_type = rel_context["type"] if is_clone_mode else "friend"
                if rel_type in ["mother", "father", "teacher", "professor", "sir", "madam", "boss", "senior"]:
                    ai_reply = "Ji?"
                else:
                    ai_reply = random.choice(["hm?", "haan?", "bol?", "kya?"])
            else:
                ai_reply = "Ji, kahiye?"

        # Save assistant reply to history
        self._add_to_history(user_id, 'assistant', ai_reply)
        
        # Track for anti-repetition (deque auto-trims to 5)
        self._recent_replies[user_id].append(ai_reply.lower().strip())

        return ai_reply

    async def _generate_with_fallback(self, messages: list, is_clone_mode: bool,
                                       message_type: str = "unknown") -> str:
        """
        LLM fallback chain: Ollama → Groq → Gemini.
        Uses circuit breaker to skip Ollama when it's down.
        Uses fast (quantised) model for simple message types.
        """
        temp = 0.55 if is_clone_mode else 0.6
        top_p = 0.85 if is_clone_mode else 0.9

        # Select model: fast quantised for simple types, full for complex
        model = AI_MODEL_FAST if message_type in _FAST_MODEL_TYPES else AI_MODEL

        # 1. Try Ollama (local, free, private) — with circuit breaker
        if not _circuit_breaker.is_open():
            try:
                response = await asyncio.to_thread(
                    ollama.chat,
                    model=model,
                    messages=messages,
                    options={"temperature": temp, "top_p": top_p}
                )
                reply = str(response.get("message", {}).get("content", "")).strip()
                if reply:
                    _circuit_breaker.record_success()
                    return reply
            except Exception as e:
                _circuit_breaker.record_failure()
                logger.warning("⚠️ Ollama failed (failures=%d), trying Groq fallback: %s",
                               _circuit_breaker.failures, e)
        else:
            logger.info("⏭️ Circuit breaker OPEN — skipping Ollama, using cloud fallback")

        # 2. Try Groq (fast cloud, free tier)
        try:
            api_key = getattr(Config, 'GROQ_API_KEY', None) or os.getenv('GROQ_API_KEY')
            if api_key:
                # Extract system and user messages for Groq format
                groq_messages = []
                for m in messages:
                    groq_messages.append({"role": m["role"], "content": m["content"]})
                resp = await asyncio.to_thread(
                    http_requests.post,
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": "llama-3.3-70b-versatile", "messages": groq_messages,
                          "temperature": temp, "max_tokens": 256},
                    timeout=30,
                )
                if resp.status_code == 200:
                    reply = resp.json()["choices"][0]["message"]["content"].strip()
                    if reply:
                        logger.info("✅ Groq fallback succeeded")
                        return reply
        except Exception as e:
            logger.warning(f"⚠️ Groq fallback failed, trying Gemini: {e}")

        # 3. Try Gemini (Google cloud, free tier)
        try:
            api_key = getattr(Config, 'GEMINI_API_KEY', None) or os.getenv('GEMINI_API_KEY')
            if api_key:
                # Combine messages into a single prompt for Gemini
                combined = "\n".join(f"[{m['role']}]: {m['content']}" for m in messages)
                resp = await asyncio.to_thread(
                    http_requests.post,
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
                    headers={"Content-Type": "application/json"},
                    json={"contents": [{"parts": [{"text": combined}]}],
                          "generationConfig": {"temperature": temp, "maxOutputTokens": 256}},
                    timeout=30,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    reply = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    if reply:
                        logger.info("✅ Gemini fallback succeeded")
                        return reply
        except Exception as e:
            logger.warning(f"⚠️ Gemini fallback failed: {e}")

        # All providers failed
        logger.error("❌ All LLM providers failed")
        return "Maaf kijiye, brain mein thodi dikkat aa gayi hai."

    async def process_text_message(self, user_id: str, text: str, user_name: str = "") -> dict[str, str]:
        print(f"🤖 Text Input from {user_id} ({user_name}): {text}")
        response = await self.generate_ai_response(user_id, text, user_name)
        return {"type": "text", "message": response}

    async def process_voice_message(self, user_id: str, audio_path: str, user_name: str = "") -> dict[str, str]:
        print(f"🎤 Voice Input from {user_id} ({user_name})...")

        # Lazy-load Whisper on first voice message
        stt = self._ensure_whisper()
        if not stt:
            return {"type": "text", "message": "❌ STT Error: Whisper model load nahi hua."}

        try:
            # Transcribe with a Hinglish-aware hint
            result = await asyncio.to_thread(
                stt.transcribe,
                audio_path,
                initial_prompt="This is a polite Hinglish conversation using words like Aap and Kijiye."
            )
            transcribed_text = result["text"]
            print(f"📝 Transcribed: {transcribed_text}")

            ai_reply = await self.generate_ai_response(user_id, transcribed_text, user_name)

            return {
                "type": "text",
                "message": f"📝 *Aapne kaha:* {transcribed_text}\n\n🤖 *Jawaab:* {ai_reply}"
            }

        except Exception as e:
            print(f"❌ Voice Process Error: {e}")
            return {"type": "text", "message": "Maaf kijiye, voice note process nahi ho paya. Dobara bhej dijiye please."}

    async def process_image_message(self, user_id: str, image_path: str, caption: str = "", user_name: str = "") -> dict[str, str]:
        print(f"📸 Image Input from {user_id} ({user_name})...")

        try:
            print("👀 Looking at image...")
            res = await asyncio.to_thread(
                ollama.generate,
                model='llava',
                prompt="Describe this image in detail but concisely.",
                images=[image_path]
            )
            description = res.get('response', '') if isinstance(res, dict) else '' # type: ignore
            print(f"🖼️ Description: {description}")

            user_text = f"[User sent an image. Description: {description}]"
            if caption:
                user_text += f"\nUser's Caption: {caption}"

            ai_reply = await self.generate_ai_response(user_id, user_text, user_name)

            return {
                "type": "text",
                "message": f"🤖 *Jawaab:* {ai_reply}"
            }

        except Exception as e:
            print(f"❌ Image Process Error: {e}")
            return {"type": "text", "message": "Maaf kijiye, main image dekh nahi pa raha hoon. Kya 'llava' model install hai?"}

    def get_help_message(self):
        return "🤖 **IndicBot**\nNamaste! Main aapke laptop par active hoon. Aap mujhse Hindi, English, ya Hinglish mein baat kar sakte hain."