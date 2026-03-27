import os
import re
import json
import chromadb
import numpy as np
from collections import Counter
from typing import List, Dict, Optional

# Paths
BASE_DIR = os.path.dirname(__file__)
CHROMA_DB_DIR = os.path.join(BASE_DIR, "chroma_db")
SETTINGS_FILE = os.path.join(BASE_DIR, "ai_settings.json")
STYLE_PROFILE_FILE = os.path.join(BASE_DIR, "style_profile.json")
DEFAULT_OWNER_NAME = "User"

# Emoji Regex to detect emojis in text
EMOJI_PATTERN = re.compile(r'[\U00010000-\U0010ffff]', flags=re.UNICODE)

# ─────────────────────────────────────────────────────────────────────────────
# Common Romanized Hindi Words (used for code-switching ratio detection)
# ─────────────────────────────────────────────────────────────────────────────
HINDI_WORDS = {
    "hai", "hain", "ho", "hoon", "tha", "thi", "the", "hoga", "hogi",
    "kya", "kaise", "kab", "kahan", "kyun", "kaun", "kitna", "kitne",
    "aur", "ya", "par", "lekin", "toh", "bhi", "se", "ka", "ki", "ke",
    "mein", "pe", "ko", "ne", "wala", "wali", "wale",
    "nahi", "mat", "na", "haan", "ji",
    "kar", "karo", "karna", "karte", "karti", "kiya", "kari",
    "bol", "bolo", "bolna", "bola", "boli",
    "de", "do", "dena", "diya", "diye", "dedi",
    "le", "lo", "lena", "liya", "liye", "lelo",
    "ja", "jao", "jaana", "gaya", "gayi", "gaye",
    "aa", "aao", "aana", "aaya", "aayi", "aaye",
    "dekh", "dekho", "dekhna", "dekha", "dekhi",
    "sun", "suno", "sunna", "suna", "suni",
    "samajh", "samjho", "samjha", "samjhi",
    "arre", "yaar", "bhai", "bro", "dude",
    "acha", "accha", "theek", "sahi", "bilkul", "pakka",
    "bohot", "bahut", "thoda", "zyada", "kam", "jyada",
    "abhi", "kal", "aaj", "parso", "subah", "shaam", "raat",
    "kuch", "sab", "log", "banda", "bande", "ladka", "ladki",
    "paani", "khana", "chai", "coffee", "doodh",
    "ghar", "bahar", "andar", "upar", "neeche",
    "chal", "chalo", "chalna", "chalega", "chalegi",
    "mil", "milo", "milna", "mila", "mili",
    "soch", "socho", "sochna", "socha", "sochi",
    "ruk", "ruko", "rukna", "ruka", "ruki",
    "baat", "baatein", "batao", "bataya", "bata",
    "pata", "maloom", "pehle", "baad", "phir",
    "woh", "yeh", "uska", "uski", "mera", "meri", "tera", "teri",
    "apna", "apni", "apne", "humara", "hamari",
    "zindagi", "pyaar", "dost", "dosti", "mazaa", "maza",
    "paisa", "kaam", "padhai", "exam", "sapna",
    "dimag", "dil", "jaan", "yaar",
    "chup", "khamosh", "shaant",
    "jaldi", "dheere", "seedha",
    "peena", "pee", "piyo", "khaana", "khao", "khaya",
}

# AI-speak phrases that real people never say in WhatsApp chats
AI_SPEAK_PHRASES = [
    "i understand your concern",
    "happy to help",
    "here are some tips",
    "i'd be glad to",
    "certainly!",
    "absolutely!",
    "let me explain",
    "i hope this helps",
    "feel free to ask",
    "don't hesitate to",
    "i appreciate you sharing",
    "that's a great question",
    "i'm here for you",
    "remember that",
    "it's important to",
    "please note that",
    "in conclusion",
    "to summarize",
    "as an ai",
    "as a language model",
]

# ─────────────────────────────────────────────────────────────────────────────
# Multilingual Embedding Function for Hinglish
# ─────────────────────────────────────────────────────────────────────────────
_embedding_model = None

def _get_embedding_model():
    """Lazy-load the multilingual embedding model."""
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embedding_model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
            print("✅ Multilingual embedding model loaded (paraphrase-multilingual-MiniLM-L12-v2)")
        except ImportError:
            print("⚠️ sentence-transformers not installed. Using ChromaDB default embeddings.")
            return None
        except Exception as e:
            print(f"⚠️ Could not load multilingual model: {e}. Falling back to default.")
            return None
    return _embedding_model


class HinglishEmbeddingFunction:
    """Custom ChromaDB embedding function using a multilingual model for Hinglish."""
    
    @staticmethod
    def name() -> str:
        return "HinglishMultilingualEmbedding"
    
    def __call__(self, input: List[str]) -> List[List[float]]:
        model = _get_embedding_model()
        if model is None:
            raise RuntimeError("Multilingual model not available")
        embeddings = model.encode(input, show_progress_bar=False)
        return embeddings.tolist()
    
    def embed_query(self, input: List[str]) -> List[List[float]]:
        """Called by ChromaDB for query-time embedding."""
        return self.__call__(input)
    
    def default_space(self) -> str:
        return "cosine"


# ─────────────────────────────────────────────────────────────────────────────
# Initialize ChromaDB Client
# ─────────────────────────────────────────────────────────────────────────────
try:
    chroma_client = chromadb.PersistentClient(path=CHROMA_DB_DIR)
except Exception as e:
    print(f"⚠️ ChromaDB Init Error: {e}")
    chroma_client = None


import time as _time

# Cached collection reference with TTL
_cached_collection = None
_cached_collection_ts: float = 0.0
_COLLECTION_CACHE_TTL = 30.0  # seconds

def _get_collection():
    """
    Returns a CACHED ChromaDB collection reference (30s TTL).
    Re-fetches automatically if the cache is stale or if the collection
    was recreated by the dashboard.
    """
    global _cached_collection, _cached_collection_ts
    
    if chroma_client is None:
        return None
    
    now = _time.time()
    if _cached_collection is not None and (now - _cached_collection_ts) < _COLLECTION_CACHE_TTL:
        return _cached_collection
    
    # Try multilingual v2 first
    model = _get_embedding_model()
    if model is not None:
        try:
            embedding_fn = HinglishEmbeddingFunction()
            _cached_collection = chroma_client.get_or_create_collection(
                name="whatsapp_clone_v2",
                embedding_function=embedding_fn
            )
            _cached_collection_ts = now
            return _cached_collection
        except Exception:
            pass
    
    # Fallback to default v1
    try:
        _cached_collection = chroma_client.get_or_create_collection(name="whatsapp_clone_style")
        _cached_collection_ts = now
        return _cached_collection
    except Exception as e:
        print(f"⚠️ ChromaDB collection error: {e}")
        return None

def invalidate_collection_cache():
    """Call this when the collection is recreated (e.g., from dashboard)."""
    global _cached_collection, _cached_collection_ts
    _cached_collection = None
    _cached_collection_ts = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Settings Management
# ─────────────────────────────────────────────────────────────────────────────

def get_ai_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {"mode": "Assistant Mode", "owner_name": DEFAULT_OWNER_NAME}

def save_ai_settings(mode: str, owner_name: str) -> None:
    settings = {"mode": mode, "owner_name": owner_name}
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)


# ─────────────────────────────────────────────────────────────────────────────
# Message Type Classification (used for retrieval + prompt assembly)
# ─────────────────────────────────────────────────────────────────────────────

def classify_message_type(text: str) -> str:
    """
    Classifies a message into one of 5 types:
    greeting, emotional, factual, banter, reaction
    """
    lowered = text.lower().strip()
    words = lowered.split()
    word_count = len(words)

    # ── GREETING (check FIRST — even single-word greetings must match) ──
    greeting_words = {"hi", "hello", "hey", "sup", "yo", "namaste", "namaskar",
                      "kaise", "haal", "kidhar", "wassup", "hii", "hiii",
                      "heya", "helo", "hy", "heyy", "heyyyy"}
    if words and words[0] in greeting_words:
        return "greeting"
    if re.search(r'\b(kya haal|kaise ho|good morning|good night|gm|gn)\b', lowered):
        return "greeting"

    # ── EMOTIONAL (check before reaction — 'love', 'miss' must not become reactions) ──
    emotional_words = {"sad", "happy", "stressed", "upset", "angry", "worried", "scared", "lonely",
                       "miss", "love", "hate", "cry", "crying", "depressed", "anxious", "tired",
                       "dukhi", "udaas", "pareshan", "gussa", "darr", "akela", "thak"}
    if any(w in emotional_words for w in words):
        return "emotional"

    # ── REACTION: very short, often emoji-only or 1-2 words ──
    if word_count <= 2:
        if EMOJI_PATTERN.search(text) or lowered in {"ok", "k", "hmm", "haan", "acha", "lol", "haha", "😂", "👍", "sahi", "nice", "damn", "oh", "ohh", "oof"}:
            return "reaction"

    # ── FACTUAL / question patterns ──
    if lowered.endswith("?") or re.search(r'\b(what|why|how|when|where|who|explain|tell me|bata|batao|kya hai|kaun|kab)\b', lowered):
        return "factual"

    # ── BANTER: short-medium, casual ──
    if word_count <= 8 and (EMOJI_PATTERN.search(text) or any(w in {"lol", "haha", "hehe", "lmao", "rofl", "bruh", "bro", "yaar"} for w in words)):
        return "banter"

    # Default to banter for short messages, factual for longer
    return "banter" if word_count <= 6 else "factual"


# ─────────────────────────────────────────────────────────────────────────────
# Hindi / Code-Switching Detection
# ─────────────────────────────────────────────────────────────────────────────

def _count_hindi_english(text: str) -> tuple:
    """Returns (hindi_word_count, english_word_count) using romanized Hindi detection."""
    words = re.findall(r'[a-zA-Z]+', text.lower())
    hindi_count = 0
    english_count = 0
    for w in words:
        if w in HINDI_WORDS:
            hindi_count += 1
        else:
            english_count += 1
    return hindi_count, english_count


# ─────────────────────────────────────────────────────────────────────────────
# Style Profile
# ─────────────────────────────────────────────────────────────────────────────

def get_style_profile() -> dict:
    """Loads the extracted style profile of the user."""
    if os.path.exists(STYLE_PROFILE_FILE):
        try:
            with open(STYLE_PROFILE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Error loading style profile: {e}")
    
    # Default fallback profile
    return {
        "avg_words": 10,
        "max_words": 20,
        "uses_periods": False,
        "uses_capitalization": False,
        "emoji_freq": "medium",
        "common_emojis": ["😂", "😊", "👍", "🙏"],
        "hindi_english_ratio": "50:50",
        "common_starters": [],
        "common_closers": [],
        "common_fillers": [],
        "laughter_style": "😂",
        "agreement_style": "haan",
        "disagreement_style": "nahi",
        "question_response_ratio": 0.2,
        "top_50_words": [],
    }


def analyze_and_save_style_profile(replies: List[str]):
    """
    Analyzes the user's historical replies to build a RICH multi-dimensional
    Style Profile with 20+ metrics. Saves to style_profile.json.
    
    Dimensions:
    - Structural: avg/max words, question ratio, punctuation usage
    - Vocabulary: starters, closers, fillers, top words, code-switching ratio
    - Emotional: laughter style, agreement/disagreement patterns, emoji placement
    """
    if not replies:
        return
    
    n = len(replies)
    
    # ── Structural Patterns ──────────────────────────────────────────────
    word_counts = []
    period_count = 0
    question_mark_count = 0
    exclamation_count = 0
    lowercase_starts = 0
    contains_question = 0
    
    # ── Vocabulary Tracking ──────────────────────────────────────────────
    all_words = []
    first_words = []
    last_words = []
    total_hindi = 0
    total_english = 0
    
    # ── Emotional Markers ────────────────────────────────────────────────
    emoji_count_msgs = 0  # messages that contain emoji
    emoji_at_end_count = 0
    emoji_inline_count = 0
    all_emojis_found: Dict[str, int] = {}
    laughter_styles: Dict[str, int] = {}
    agreement_styles: Dict[str, int] = {}
    disagreement_styles: Dict[str, int] = {}
    
    # Common filler words to track
    FILLER_CANDIDATES = {"arre", "yaar", "bhai", "bro", "dude", "like", "basically",
                         "actually", "hmm", "acha", "accha", "haan", "na", "matlab",
                         "waise", "vaise", "dekh", "sun", "chal", "bol"}

    filler_counter: Counter = Counter()
    
    for reply in replies:
        words = reply.split()
        wc = len(words)
        word_counts.append(wc)
        
        # Track all words for vocabulary analysis
        clean_words = [w.lower().strip(".,!?\"'()[]{}") for w in words if len(w) > 1]
        all_words.extend(clean_words)
        
        # First and last words (starters/closers)
        if clean_words:
            first_words.append(clean_words[0])
        if len(clean_words) >= 2:
            last_words.append(clean_words[-1])
        
        # Punctuation
        stripped = reply.strip()
        if stripped.endswith("."):
            period_count += 1
        if "?" in reply:
            question_mark_count += 1
            contains_question += 1
        if "!" in reply:
            exclamation_count += 1
            
        # Capitalization
        if len(stripped) > 0 and stripped[0].islower():
            lowercase_starts += 1
        
        # Code-switching
        h, e = _count_hindi_english(reply)
        total_hindi += h
        total_english += e
        
        # Fillers
        for w in clean_words:
            if w in FILLER_CANDIDATES:
                filler_counter[w] += 1
        
        # Emoji analysis
        emojis = EMOJI_PATTERN.findall(reply)
        if emojis:
            emoji_count_msgs += 1
            for em in emojis:
                all_emojis_found[em] = all_emojis_found.get(em, 0) + 1
            
            # Check placement: is emoji at the end or inline?
            text_without_emoji = EMOJI_PATTERN.sub("", reply).strip()
            if reply.strip().endswith(emojis[-1]):
                emoji_at_end_count += 1
            else:
                emoji_inline_count += 1
        
        # Laughter style detection
        lowered = reply.lower()
        if "😂" in reply or "🤣" in reply:
            laughter_styles["emoji"] = laughter_styles.get("emoji", 0) + 1
        if re.search(r'\bhaha\b', lowered):
            laughter_styles["haha"] = laughter_styles.get("haha", 0) + 1
        if re.search(r'\bhehe\b', lowered):
            laughter_styles["hehe"] = laughter_styles.get("hehe", 0) + 1
        if re.search(r'\blol\b', lowered):
            laughter_styles["lol"] = laughter_styles.get("lol", 0) + 1
        if re.search(r'\blmao\b', lowered):
            laughter_styles["lmao"] = laughter_styles.get("lmao", 0) + 1
        
        # Agreement patterns
        agreement_map = {
            r'\bhaan\b': "haan", r'\bha\b': "ha", r'\bacha\b': "acha",
            r'\baccha\b': "accha", r'\bsahi\b': "sahi", r'\bhmm\b': "hmm",
            r'\bok\b': "ok", r'\byes\b': "yes", r'\bthik\b': "thik",
            r'\bbilkul\b': "bilkul", r'\bsure\b': "sure",
        }
        for pat, label in agreement_map.items():
            if re.search(pat, lowered):
                agreement_styles[label] = agreement_styles.get(label, 0) + 1
        
        # Disagreement patterns
        disagreement_map = {
            r'\bnahi\b': "nahi", r'\bnaah\b': "naah", r'\bno\b': "no",
            r'\bmat\b': "mat", r'\bnah\b': "nah", r'\bnope\b': "nope",
        }
        for pat, label in disagreement_map.items():
            if re.search(pat, lowered):
                disagreement_styles[label] = disagreement_styles.get(label, 0) + 1

    # ── Compute Final Metrics ────────────────────────────────────────────
    
    avg_words = round(sum(word_counts) / n, 1)
    max_words = max(word_counts) if word_counts else 20
    # Use the 90th percentile as a more robust max (excludes outliers)
    p90_words = int(sorted(word_counts)[int(n * 0.9)]) if n > 5 else max_words
    
    period_ratio = period_count / n
    question_ratio = round(contains_question / n, 2)
    lowercase_ratio = lowercase_starts / n
    emoji_ratio = emoji_count_msgs / n
    
    # Emoji frequency label
    if emoji_ratio > 0.4: emoji_freq = "very high"
    elif emoji_ratio > 0.2: emoji_freq = "high"
    elif emoji_ratio > 0.05: emoji_freq = "medium"
    elif emoji_ratio > 0.01: emoji_freq = "low"
    else: emoji_freq = "almost never"
    
    # Top emojis
    sorted_emojis = sorted(all_emojis_found.items(), key=lambda x: x[1], reverse=True)
    common_emojis = [e[0] for e in sorted_emojis[:5]]
    if not common_emojis:
        common_emojis = []
    
    # Emoji placement preference
    emoji_placement = "end" if emoji_at_end_count >= emoji_inline_count else "inline"
    
    # Code-switching ratio
    total_lang = total_hindi + total_english
    if total_lang > 0:
        hindi_pct = round(total_hindi / total_lang * 100)
        english_pct = 100 - hindi_pct
        hindi_english_ratio = f"{hindi_pct}:{english_pct}"
    else:
        hindi_english_ratio = "50:50"
    
    # Top 50 most used words
    word_freq = Counter(all_words)
    # Remove very common stop words from top list
    stop_words = {"i", "a", "the", "is", "to", "in", "it", "of", "and", "or", "for", "on", "at", "an"}
    for sw in stop_words:
        word_freq.pop(sw, None)
    top_50_words = [w for w, _ in word_freq.most_common(50)]
    
    # Common starters (top 10)
    starter_freq = Counter(first_words)
    common_starters = [w for w, _ in starter_freq.most_common(10)]
    
    # Common closers (top 10)
    closer_freq = Counter(last_words)
    common_closers = [w for w, _ in closer_freq.most_common(10)]
    
    # Common fillers (sorted)
    common_fillers = [w for w, _ in filler_counter.most_common(10) if filler_counter[w] >= 2]
    
    # Laughter style (most common)
    laughter_style = max(laughter_styles, key=laughter_styles.get) if laughter_styles else "none"
    
    # Agreement style
    agreement_style = max(agreement_styles, key=agreement_styles.get) if agreement_styles else "haan"
    
    # Disagreement style
    disagreement_style = max(disagreement_styles, key=disagreement_styles.get) if disagreement_styles else "nahi"
    
    # ── Build Profile ────────────────────────────────────────────────────
    
    profile = {
        # Structural
        "avg_words": avg_words,
        "max_words": p90_words,
        "uses_periods": period_ratio > 0.3,
        "uses_capitalization": lowercase_ratio < 0.5,
        "uses_question_marks": question_mark_count / n > 0.1,
        "uses_exclamations": exclamation_count / n > 0.1,
        "question_response_ratio": question_ratio,
        
        # Vocabulary & Code-Switching
        "hindi_english_ratio": hindi_english_ratio,
        "common_starters": common_starters,
        "common_closers": common_closers,
        "common_fillers": common_fillers,
        "top_50_words": top_50_words,
        
        # Emotional Markers
        "emoji_freq": emoji_freq,
        "common_emojis": common_emojis,
        "emoji_placement": emoji_placement,
        "laughter_style": laughter_style,
        "agreement_style": agreement_style,
        "disagreement_style": disagreement_style,
        
        # Stats
        "total_messages_analyzed": n,
    }
    
    with open(STYLE_PROFILE_FILE, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=4, ensure_ascii=False)
        
    print(f"📊 Rich Style Profile Extracted ({n} messages analyzed)")
    print(f"   avg_words={avg_words}, max_words={p90_words}, hindi:english={hindi_english_ratio}")
    print(f"   starters={common_starters[:5]}, laughter={laughter_style}, emoji={emoji_freq}")


# ─────────────────────────────────────────────────────────────────────────────
# Chat Parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_whatsapp_chats(filepaths: List[str], owner_name: str) -> List[Dict[str, str]]:
    """
    Parses WhatsApp .txt exports to find pairs of messages where the owner replied.
    Also calls analyze_and_save_style_profile automatically.
    Preserves the other sender's name as 'contact_id' for per-contact extraction.
    """
    pairs = []
    owner_replies_for_analysis = []
    
    ios_pattern = re.compile(r'^\[.*?\] (.*?): (.*)$')
    android_pattern = re.compile(r'^\d{1,2}/\d{1,2}/\d{2,4}, .*? - (.*?): (.*)$')
    
    for filepath in filepaths:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except Exception as e:
            print(f"Error reading {filepath}: {e}")
            continue
            
        current_sender = None
        current_message = []
        parsed_messages = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            ios_match = ios_pattern.match(line)
            android_match = android_pattern.match(line)
            match = ios_match or android_match
            
            if match:
                if current_sender and current_message:
                    parsed_messages.append({
                        "sender": current_sender.strip(),
                        "text": "\n".join(current_message).strip()
                    })
                
                current_sender = match.group(1).strip()
                current_message = [match.group(2).strip()]
            else:
                if current_sender and current_message:
                    current_message.append(line)
                    
        if current_sender and current_message:
            parsed_messages.append({
                "sender": current_sender.strip(),
                "text": "\n".join(current_message).strip()
            })
            
        for i in range(len(parsed_messages) - 1):
            msg1 = parsed_messages[i]
            msg2 = parsed_messages[i+1]
            
            if msg1["sender"] != owner_name and msg2["sender"] == owner_name:
                skip_media_flags = ["<Media omitted>", "image omitted", "audio omitted", "video omitted"]
                if any(flag in msg1["text"] for flag in skip_media_flags) or \
                   any(flag in msg2["text"] for flag in skip_media_flags):
                    continue
                
                incoming_text = msg1["text"]
                reply_text = msg2["text"]
                
                if len(incoming_text) < 500 and len(reply_text) < 500:
                    pairs.append({
                        "incoming": incoming_text,
                        "reply": reply_text,
                        "contact_id": msg1["sender"],  # Preserve sender name
                    })
                    owner_replies_for_analysis.append(reply_text)
                    
    # Generate rich style profile
    if owner_replies_for_analysis:
        analyze_and_save_style_profile(owner_replies_for_analysis)
                
    return pairs


# ─────────────────────────────────────────────────────────────────────────────
# ChromaDB Ingestion (with conversation type tagging)
# ─────────────────────────────────────────────────────────────────────────────

def ingest_into_vectordb(pairs: List[Dict[str, str]]):
    """
    Ingests pairs into ChromaDB WITH rich metadata:
    - reply text, word_count, has_emoji 
    - conversation_type (greeting/emotional/factual/banter/reaction)
    - contact_id, verb analysis, pronoun, opener, fillers
    """
    # Lazy import to avoid circular dependency at module load time
    from verb_stemmer import stem_all_verbs, detect_pronoun, detect_opener, detect_fillers

    collection = _get_collection()
    if not pairs or collection is None:
        return 0
        
    documents = []
    metadatas = []
    ids = []
    
    for idx, pair in enumerate(pairs):
        incoming = pair["incoming"]
        reply = pair["reply"]
        contact_id = pair.get("contact_id", "unknown")
        
        documents.append(incoming)
        
        # Classify conversation type for this pair
        msg_type = classify_message_type(incoming)
        reply_type = classify_message_type(reply)
        
        word_count = len(reply.split())
        has_emoji = bool(EMOJI_PATTERN.search(reply))
        
        # --- NEW: Verb / pronoun / opener / filler analysis ---
        verbs = stem_all_verbs(reply)
        verb_roots = ",".join(v["root"] for v in verbs) if verbs else ""
        verb_surfaces = ",".join(v["surface"] for v in verbs) if verbs else ""
        verb_forms = ",".join(v["form"] for v in verbs) if verbs else ""
        address_pronoun = detect_pronoun(reply) or ""
        opener = detect_opener(reply) or ""
        fillers = detect_fillers(reply)
        filler_tokens = ",".join(fillers) if fillers else ""
        
        metadatas.append({
            "reply": reply,
            "incoming": incoming,
            "word_count": word_count,
            "has_emoji": has_emoji,
            "msg_type": msg_type,
            "reply_type": reply_type,
            "contact_id": contact_id,
            "verb_roots": verb_roots,
            "verb_surfaces": verb_surfaces,
            "verb_forms": verb_forms,
            "address_pronoun": address_pronoun,
            "opener": opener,
            "filler_tokens": filler_tokens,
        })
        
        ids.append(f"pair_{idx}_{hash(incoming + reply)}")
        
    batch_size = 100
    added_count = 0
    for i in range(0, len(documents), batch_size):
        try:
            collection.add(
                documents=documents[i:i+batch_size],
                metadatas=metadatas[i:i+batch_size],
                ids=ids[i:i+batch_size]
            )
            added_count += len(documents[i:i+batch_size])
        except Exception as e:
            print(f"⚠️ Error adding batch to ChromaDB: {e}")
            
    print(f"📦 Ingested {added_count} pairs into ChromaDB with per-contact metadata")
    return added_count


# ─────────────────────────────────────────────────────────────────────────────
# Multi-Query Expansion
# ─────────────────────────────────────────────────────────────────────────────

def _generate_multi_queries(query: str) -> List[str]:
    """Generates slight semantic variations of a query for broader recall."""
    queries = [query]
    lowered = query.lower()
    
    # Greeting expansions
    if any(w in lowered for w in ["hi", "hello", "hey", "sup", "yo"]):
        queries.extend(["kya haal hai", "aur bhai", "kaise ho"])
    
    # Activity check expansions
    if any(p in lowered for p in ["kya kar raha", "wassup", "what's up", "kidhar"]):
        queries.extend(["aur kya chal raha", "busy hai kya", "kidhar hai"])
    
    # Emotional expansions
    if any(w in lowered for w in ["sad", "stressed", "upset", "tired", "thak"]):
        queries.extend(["kya hua", "sab theek hai", "tension mat le"])
    
    # Opinion / question expansions
    if "?" in lowered or any(w in lowered for w in ["should", "kya karu", "suggest"]):
        queries.extend(["kya lagta hai", "advice", "help"])
        
    return queries[:5]  # Cap at 5 queries max


# ─────────────────────────────────────────────────────────────────────────────
# Smart RAG Trigger — decides whether to retrieve or let the LLM reason
# ─────────────────────────────────────────────────────────────────────────────

def should_retrieve(message_text: str, message_type: str) -> bool:
    """
    Returns True only when RAG retrieval adds value over pure reasoning.
    Greetings, reactions, and emotional messages are handled entirely by
    the persona manual + system prompt rules (no DB search needed).
    """
    # NEVER retrieve for these — reasoning handles them perfectly
    if message_type in ("greeting", "reaction", "emotional"):
        return False
    
    # ALWAYS retrieve if user references a past event / memory
    memory_triggers = [
        "yaad hai", "remember", "pichli baar", "last time",
        "kal baat", "pehle bola", "tune kaha tha", "humne baat",
        "woh jo", "jab humne",
    ]
    lowered = message_text.lower()
    if any(trigger in lowered for trigger in memory_triggers):
        return True
    
    # Retrieve for factual topics (opinion anchoring)
    if message_type == "factual":
        return True
    
    # Retrieve for banter only if message is long enough to benefit from style examples
    if message_type == "banter" and len(message_text.split()) > 5:
        return True
    
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Advanced Retrieval: Type-Filtered + MMR Reranking
# ─────────────────────────────────────────────────────────────────────────────

def retrieve_style_examples(query: str, n_results: int = 5) -> List[Dict[str, str]]:
    """
    Advanced retrieval pipeline:
    1. Classify query message type
    2. Try type-filtered search first, fall back to all types
    3. Multi-query expansion for recall
    4. MMR reranking for diversity
    5. Deduplication
    """
    collection = _get_collection()
    if collection is None:
        return []
        
    try:
        total_count = collection.count()
        if total_count == 0:
            return []
        
        query_type = classify_message_type(query)
        queries_to_run = _generate_multi_queries(query)
        raw_limit = min(25, total_count)
        
        # Step 1: Try type-filtered retrieval
        type_filtered_results = None
        try:
            type_filtered_results = collection.query(
                query_texts=queries_to_run,
                n_results=min(raw_limit, total_count),
                where={"msg_type": query_type},
                include=["metadatas", "documents", "distances"]
            )
        except Exception:
            pass  # Type filter might fail if metadata doesn't exist yet
        
        # Step 2: Also get unfiltered results as fallback
        all_results = collection.query(
            query_texts=queries_to_run,
            n_results=raw_limit,
            include=["metadatas", "documents", "distances"]
        )
        
        # Step 3: Merge and deduplicate
        unique_examples: Dict[str, dict] = {}
        
        def _process_results(results, bonus: float = 0.0):
            """Process ChromaDB results into unique_examples dict. bonus < 0 means preferred."""
            if not results or not results.get("documents"):
                return
            for i, query_docs in enumerate(results["documents"]):
                distances = results["distances"][i]
                metas = results["metadatas"][i]
                
                for j, incoming_doc in enumerate(query_docs):
                    reply = metas[j]["reply"]
                    dist = distances[j] + bonus
                    
                    if reply not in unique_examples or unique_examples[reply]["dist"] > dist:
                        unique_examples[reply] = {
                            "incoming": incoming_doc,
                            "reply": reply,
                            "dist": dist,
                            "msg_type": metas[j].get("msg_type", "unknown"),
                        }
        
        # Give type-matched results a slight boost (lower distance = better)
        _process_results(type_filtered_results, bonus=-0.05)
        _process_results(all_results, bonus=0.0)
        
        # Step 4: MMR Reranking for diversity
        candidates = sorted(unique_examples.values(), key=lambda x: x["dist"])
        selected = _mmr_select(candidates, n_results)
        
        # Step 5: Format output
        final_examples = []
        for item in selected:
            final_examples.append({
                "incoming": item["incoming"],
                "reply": item["reply"],
            })
        
        print(f"🔍 Retrieved {len(final_examples)} examples (query_type={query_type}, pool={len(unique_examples)})")
        return final_examples
        
    except Exception as e:
        print(f"⚠️ Error querying ChromaDB: {e}")
        return []


def _mmr_select(candidates: List[dict], k: int, lambda_param: float = 0.7) -> List[dict]:
    """
    Maximal Marginal Relevance selection.
    Balances relevance (low distance) with diversity (different from already selected).
    Uses reply text overlap as a diversity proxy when embeddings aren't available.
    """
    if len(candidates) <= k:
        return candidates
    
    selected = [candidates[0]]  # Start with the most relevant
    remaining = candidates[1:]
    
    while len(selected) < k and remaining:
        best_score = float('-inf')
        best_idx = 0
        
        for i, candidate in enumerate(remaining):
            # Relevance: lower distance = more relevant → negate for max
            relevance = -candidate["dist"]
            
            # Diversity: max similarity to any already-selected item
            max_sim = 0.0
            for sel in selected:
                # Use word overlap as diversity proxy
                cand_words = set(candidate["reply"].lower().split())
                sel_words = set(sel["reply"].lower().split())
                if cand_words or sel_words:
                    overlap = len(cand_words & sel_words) / max(len(cand_words | sel_words), 1)
                    max_sim = max(max_sim, overlap)
            
            # MMR score
            score = lambda_param * relevance - (1 - lambda_param) * max_sim
            
            if score > best_score:
                best_score = score
                best_idx = i
        
        selected.append(remaining.pop(best_idx))
    
    return selected


# ─────────────────────────────────────────────────────────────────────────────
# Clear / Reset
# ─────────────────────────────────────────────────────────────────────────────

def clear_vectordb():
    """Clears the collection to start fresh."""
    if chroma_client is None:
        return False
        
    try:
        # Try to delete both old and new collection names
        for name in ["whatsapp_clone_style", "whatsapp_clone_v2"]:
            try:
                chroma_client.delete_collection(name)
            except:
                pass
        
        # Fresh collection will be created on next _get_collection() call
        return True
    except Exception as e:
        print(f"⚠️ Error clearing ChromaDB: {e}")
        return False
