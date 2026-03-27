"""
Clone Mode Quality Evaluation Framework

Runs 20 sample messages through BotHandler in Clone Mode and scores each response on:
1. Length Match — how close the response length is to the style profile's avg_words
2. Vocabulary Overlap — % of response words in the user's top-200 vocabulary  
3. Code-Switch Ratio — Hindi:English ratio vs profile target
4. AI-Speak Detection — flags any AI assistant patterns in the response
5. Style Consistency — overall composite score

Run: python test_clone_quality.py
"""

import asyncio
import sys
import os
import json
import re

sys.path.insert(0, os.path.dirname(__file__))

from bot_handler import BotHandler
from clone_manager import (
    get_style_profile, get_ai_settings, save_ai_settings,
    classify_message_type, _count_hindi_english, AI_SPEAK_PHRASES
)


# ─── Test Messages (covering all message types) ────────────────────────────
TEST_MESSAGES = [
    # Greetings
    ("test_eval_1", "Hi", "greeting"),
    ("test_eval_1", "Kaise ho bhai?", "greeting"),
    ("test_eval_1", "Hey wassup", "greeting"),
    
    # Reactions
    ("test_eval_1", "haha", "reaction"),
    ("test_eval_1", "ok", "reaction"),
    ("test_eval_1", "nice 👍", "reaction"),
    
    # Emotional
    ("test_eval_2", "I'm feeling really stressed about my exams", "emotional"),
    ("test_eval_2", "Yaar bahut sad feel ho raha hai", "emotional"),
    ("test_eval_2", "I miss my friends", "emotional"),
    
    # Factual
    ("test_eval_3", "What is machine learning?", "factual"),
    ("test_eval_3", "Python mein async kaise kaam karta hai?", "factual"),
    ("test_eval_3", "Best laptop for coding konsa hai?", "factual"),
    
    # Banter
    ("test_eval_4", "Bro yesterday was insane 😂", "banter"),
    ("test_eval_4", "Yaar kya bakwas movie thi", "banter"),
    ("test_eval_4", "India ka match dekha?", "banter"),
    
    # Mixed / Real-world
    ("test_eval_5", "Kal gym jaana hai kya?", "banter"),
    ("test_eval_5", "Should I learn React or Vue?", "factual"),
    ("test_eval_5", "Good night bhai", "greeting"),
    ("test_eval_5", "Tell me a joke", "banter"),
    ("test_eval_5", "I can't sleep", "emotional"),
]


def _score_length_match(response: str, style_profile: dict) -> float:
    """Score: 0.0 (terrible) to 1.0 (perfect) based on word count match."""
    target_avg = style_profile.get("avg_words", 10)
    actual = len(response.split())
    
    if target_avg == 0:
        return 1.0 if actual == 0 else 0.0
    
    deviation = abs(actual - target_avg) / target_avg
    score = max(0.0, 1.0 - deviation)
    return round(score, 2)


def _score_vocabulary_overlap(response: str, style_profile: dict) -> float:
    """Score: % of response words that appear in user's top vocabulary."""
    top_words = set(style_profile.get("top_50_words", []))
    if not top_words:
        return 0.5  # Can't score without data
    
    response_words = set(re.findall(r'[a-zA-Z]+', response.lower()))
    if not response_words:
        return 0.5
    
    overlap = len(response_words & top_words)
    score = overlap / len(response_words)
    return round(min(score, 1.0), 2)


def _score_code_switch(response: str, style_profile: dict) -> float:
    """Score: how close the Hindi:English ratio is to the profile target."""
    target_ratio = style_profile.get("hindi_english_ratio", "50:50")
    try:
        target_hindi_pct = int(target_ratio.split(":")[0])
    except:
        target_hindi_pct = 50
    
    hindi_count, english_count = _count_hindi_english(response)
    total = hindi_count + english_count
    if total == 0:
        return 0.5  # Can't score empty
    
    actual_hindi_pct = (hindi_count / total) * 100
    deviation = abs(actual_hindi_pct - target_hindi_pct) / 100
    score = max(0.0, 1.0 - deviation * 2)  # 2x penalty
    return round(score, 2)


def _check_ai_speak(response: str) -> list:
    """Returns list of AI-speak phrases found in the response."""
    found = []
    lowered = response.lower()
    for phrase in AI_SPEAK_PHRASES:
        if phrase in lowered:
            found.append(phrase)
    return found


async def run_evaluation():
    print("=" * 70)
    print("🧪 Clone Mode Quality Evaluation")
    print("=" * 70)
    
    # Ensure we're in Clone Mode
    settings = get_ai_settings()
    original_mode = settings.get("mode", "Assistant Mode")
    if original_mode != "Clone Mode":
        print(f"⚠️ Switching from '{original_mode}' to 'Clone Mode' for testing...")
        save_ai_settings("Clone Mode", settings.get("owner_name", "OwnerName"))
    
    style_profile = get_style_profile()
    print(f"\n📊 Style Profile: avg_words={style_profile.get('avg_words')}, "
          f"max_words={style_profile.get('max_words')}, "
          f"hindi:english={style_profile.get('hindi_english_ratio')}")
    print(f"   emoji={style_profile.get('emoji_freq')}, "
          f"periods={style_profile.get('uses_periods')}, "
          f"caps={style_profile.get('uses_capitalization')}")
    
    handler = BotHandler()
    
    results = []
    total_length_score = 0
    total_vocab_score = 0
    total_code_switch_score = 0
    ai_speak_violations = 0
    
    for user_id, message, expected_type in TEST_MESSAGES:
        actual_type = classify_message_type(message)
        
        print(f"\n{'─' * 60}")
        print(f"👤 [{user_id}] ({expected_type}→{actual_type}): {message}")
        
        result = await handler.process_text_message(user_id, message)
        response = result["message"]
        print(f"🤖 Reply ({len(response.split())}w): {response}")
        
        # Score it
        length_score = _score_length_match(response, style_profile)
        vocab_score = _score_vocabulary_overlap(response, style_profile)
        code_switch_score = _score_code_switch(response, style_profile)
        ai_phrases = _check_ai_speak(response)
        
        total_length_score += length_score
        total_vocab_score += vocab_score
        total_code_switch_score += code_switch_score
        if ai_phrases:
            ai_speak_violations += 1
        
        status = "✅" if length_score > 0.5 and not ai_phrases else "⚠️"
        print(f"   {status} length={length_score}, vocab={vocab_score}, code_sw={code_switch_score}"
              f"{' 🚨 AI-speak: ' + str(ai_phrases) if ai_phrases else ''}")
        
        results.append({
            "message": message,
            "response": response,
            "type": actual_type,
            "length_score": length_score,
            "vocab_score": vocab_score,
            "code_switch_score": code_switch_score,
            "ai_speak": ai_phrases,
        })
    
    # Summary
    n = len(TEST_MESSAGES)
    print(f"\n{'=' * 70}")
    print(f"📋 CLONE MODE QUALITY SCORECARD ({n} messages)")
    print(f"{'=' * 70}")
    print(f"  Length Match:     {total_length_score/n:.2f} / 1.00  (target: > 0.50)")
    print(f"  Vocab Overlap:    {total_vocab_score/n:.2f} / 1.00  (target: > 0.30)")
    print(f"  Code-Switch:      {total_code_switch_score/n:.2f} / 1.00  (target: > 0.50)")
    print(f"  AI-Speak Free:    {n - ai_speak_violations}/{n}      (target: {n}/{n})")
    
    composite = (total_length_score/n * 0.35 + total_vocab_score/n * 0.25 + 
                 total_code_switch_score/n * 0.25 + (1 - ai_speak_violations/n) * 0.15)
    print(f"\n  ⭐ COMPOSITE SCORE: {composite:.2f} / 1.00")
    
    if composite >= 0.7:
        print("  ✅ Clone quality is GOOD")
    elif composite >= 0.5:
        print("  ⚠️ Clone quality is MODERATE — room for improvement")
    else:
        print("  ❌ Clone quality is LOW — needs tuning")
    
    print(f"{'=' * 70}")
    
    # Restore original mode if changed
    if original_mode != "Clone Mode":
        save_ai_settings(original_mode, settings.get("owner_name", "OwnerName"))
        print(f"\n↩️ Restored mode back to '{original_mode}'")


if __name__ == "__main__":
    asyncio.run(run_evaluation())
