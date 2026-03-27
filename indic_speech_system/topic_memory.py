"""
Topic Memory Module — Long-Term Conversation Continuity for Clone Mode

Stores per-user conversation topics so the clone can reference past interactions
naturally, like a real person would. Data stored in data/user_memories.json.

Only active in Clone Mode (called from bot_handler.py).
"""

import os
import json
import re
from datetime import datetime
from typing import List, Dict, Optional
from collections import Counter

BASE_DIR = os.path.dirname(__file__)
MEMORY_FILE = os.path.join(BASE_DIR, "data", "user_memories.json")

# Topic keywords to detect (beyond just common words)
TOPIC_KEYWORDS = {
    # Emotions/State
    "stressed", "stress", "sad", "happy", "angry", "tired", "bored", "upset",
    "excited", "worried", "anxious", "lonely", "pareshan", "udaas", "khush",
    # Activities
    "gym", "workout", "study", "exam", "project", "coding", "work", "meeting",
    "movie", "game", "cricket", "match", "travel", "trip", "party",
    # Relationships
    "girlfriend", "boyfriend", "friend", "mom", "dad", "family", "bhai",
    # Health
    "sick", "headache", "sleep", "doctor", "medicine", "health",
    # Goals/Plans
    "interview", "placement", "deadline", "plan", "tomorrow", "weekend",
}

# Words to ignore when extracting topics
STOP_WORDS = {
    "i", "me", "my", "you", "your", "we", "they", "he", "she", "it",
    "a", "an", "the", "is", "am", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "can", "may", "might", "shall",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "as",
    "and", "or", "but", "not", "no", "yes", "so", "if", "that", "this",
    "what", "how", "why", "when", "where", "who",
    "hai", "hain", "ho", "ka", "ki", "ke", "se", "ko", "ne", "mein",
    "toh", "bhi", "par", "aur", "ya", "na", "ji", "kya",
}


def _load_memories() -> dict:
    """Load all user memories from file."""
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_memories(memories: dict) -> None:
    """Save all user memories to file."""
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memories, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Error saving topic memory: {e}")


def _extract_topics(messages: List[dict]) -> List[str]:
    """
    Extract key topics from recent messages using keyword matching 
    and word frequency analysis.
    """
    all_text = " ".join(msg.get("content", "") for msg in messages)
    
    # Clean and tokenize
    words = re.findall(r'[a-zA-Z]+', all_text.lower())
    
    # Filter to meaningful words
    meaningful = [w for w in words if w not in STOP_WORDS and len(w) > 2]
    
    # Find topic keywords present
    found_topics = []
    for word in meaningful:
        if word in TOPIC_KEYWORDS:
            found_topics.append(word)
    
    # Also get top frequent meaningful words as additional topics
    freq = Counter(meaningful)
    top_words = [w for w, c in freq.most_common(5) if c >= 2 and w not in found_topics]
    
    # Combine: topic keywords first, then frequent words
    topics = list(dict.fromkeys(found_topics + top_words))  # Deduplicate preserving order
    return topics[:5]  # Max 5 topics


def _extract_followups(messages: List[dict]) -> List[str]:
    """
    Extract potential follow-up items from messages.
    Looks for plans, promises, and future-tense statements.
    """
    followups = []
    
    future_patterns = [
        r'kal (\w+ \w+)',
        r'tomorrow (\w+ \w+)',
        r'will (\w+ \w+)',
        r'going to (\w+ \w+)',
        r'plan (\w+ \w+)',
        r'pakka (\w+ \w+)',
    ]
    
    for msg in messages:
        content = msg.get("content", "").lower()
        for pattern in future_patterns:
            match = re.search(pattern, content)
            if match:
                followups.append(match.group(0).strip())
    
    return followups[:3]  # Max 3 followups


def save_conversation_topics(user_id: str, messages: List[dict]) -> None:
    """
    Extract and save topics from recent messages for a user.
    Called after each AI response in clone mode.
    """
    if not messages:
        return
    
    memories = _load_memories()
    
    topics = _extract_topics(messages)
    followups = _extract_followups(messages)
    
    if not topics and not followups:
        return  # Nothing meaningful to save
    
    # Get or create user memory
    user_mem = memories.get(user_id, {})
    
    # Update topics (merge with existing, keep last 8)
    existing_topics = user_mem.get("last_topics", [])
    merged_topics = list(dict.fromkeys(topics + existing_topics))[:8]
    
    # Update followups (replace with latest)
    if followups:
        user_mem["pending_followups"] = followups
    
    user_mem["last_topics"] = merged_topics
    user_mem["last_interaction"] = datetime.now().isoformat()
    
    memories[user_id] = user_mem
    _save_memories(memories)


def get_user_memory(user_id: str) -> Optional[dict]:
    """Get stored memory for a specific user."""
    memories = _load_memories()
    return memories.get(user_id)


def get_memory_prompt_injection(user_id: str) -> str:
    """
    Returns a formatted string for injection into the clone system prompt.
    Provides natural conversation continuity context.
    """
    user_mem = get_user_memory(user_id)
    
    if not user_mem:
        return ""
    
    parts = []
    
    topics = user_mem.get("last_topics", [])
    if topics:
        topics_str = ", ".join(topics[:5])
        parts.append(f"Recent conversation topics with this person: {topics_str}")
    
    followups = user_mem.get("pending_followups", [])
    if followups:
        followup_str = "; ".join(followups)
        parts.append(f"Things they mentioned doing: {followup_str}")
    
    last_time = user_mem.get("last_interaction")
    if last_time:
        try:
            last_dt = datetime.fromisoformat(last_time)
            now = datetime.now()
            diff = now - last_dt
            if diff.days > 0:
                parts.append(f"Last talked {diff.days} day(s) ago — you can casually mention it's been a while.")
        except:
            pass
    
    if not parts:
        return ""
    
    return "MEMORY (use naturally, don't force it):\n" + "\n".join(f"- {p}" for p in parts)
