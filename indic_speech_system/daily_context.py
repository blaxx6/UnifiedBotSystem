# daily_context.py — Manual Daily Context for Clone Mode
"""
Manages a daily context JSON file that lets the owner set their current
mood, activity, and notable events once per day. This is injected into
the clone system prompt so the bot can answer "kya kar rahe ho?" naturally
instead of guessing or fabricating activities.

Data stored in: data/daily_context.json
"""

import os
import json
from datetime import datetime

BASE_DIR = os.path.dirname(__file__)
DAILY_CONTEXT_FILE = os.path.join(BASE_DIR, "data", "daily_context.json")
os.makedirs(os.path.dirname(DAILY_CONTEXT_FILE), exist_ok=True)


def get_daily_context() -> dict:
    """
    Returns today's daily context dict, or empty dict if not set or stale.
    Only returns context whose 'date' matches today (IST).
    """
    if not os.path.exists(DAILY_CONTEXT_FILE):
        return {}
    
    try:
        with open(DAILY_CONTEXT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    
    # Check if the saved date is today
    today = datetime.now().strftime("%Y-%m-%d")
    if data.get("date") != today:
        return {}  # Stale context from a previous day
    
    return data


def set_daily_context(mood: str = "", activity: str = "", notable: str = "") -> dict:
    """
    Sets (or overwrites) today's daily context.
    Returns the saved context dict.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    
    data = {
        "date": today,
        "mood": mood.strip() if mood else "",
        "activity": activity.strip() if activity else "",
        "notable": notable.strip() if notable else "",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    
    try:
        with open(DAILY_CONTEXT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Error saving daily context: {e}")
    
    return data


def build_daily_context_prompt() -> str:
    """
    Returns a formatted string for injection into the clone system prompt.
    Returns empty string if no context is set for today.
    """
    ctx = get_daily_context()
    if not ctx:
        return ""
    
    parts = []
    if ctx.get("mood"):
        parts.append(f"- Mood: {ctx['mood']}")
    if ctx.get("activity"):
        parts.append(f"- Doing: {ctx['activity']}")
    if ctx.get("notable"):
        parts.append(f"- Note: {ctx['notable']}")
    
    if not parts:
        return ""
    
    return (
        "📋 TODAY'S CONTEXT (use to answer personal activity questions):\n"
        + "\n".join(parts)
        + "\nIf someone asks \"kya kar rahe ho?\" → use this instead of guessing."
    )
