"""
Quick test to verify Hinglish responses from the gemma2:9b model.
Tests: casual greeting, mixed-language query, empathy, follow-up context.
Run: python test_hinglish.py
"""
import asyncio
import sys
import os

# Ensure we're in the right directory
sys.path.insert(0, os.path.dirname(__file__))

from bot_handler import BotHandler

TEST_MESSAGES = [
    # --- Original tests (preserved) ---
    ("test_user_1", "Bhai kya haal hai?", "Casual Hinglish greeting"),
    ("test_user_1", "Mujhe Python seekhna hai, kaise start karu?", "Mixed-language learning query"),
    ("test_user_1", "Thanks yaar, bohot help hui", "Gratitude in Hinglish"),
    ("test_user_2", "Hello, explain what is AI", "English-first factual question"),
    ("test_user_2", "Aur iske baare me thoda aur batao", "Follow-up context test"),

    # --- New tests (from user requirements) ---
    ("test_user_3", "Mujhe kal ka weather batao", "Mixed-language weather request"),
    ("test_user_3", "Arey yaar bohot mushkil situation hai", "Empathy test - should show care"),
    ("test_user_3", "Aur kuch aur bata", "Follow-up - should remember previous context"),
    ("test_user_4", "Diwali ki planning kya hai?", "Cultural awareness test"),
    ("test_user_4", "Bohot demotivated feel ho raha hai", "Motivation/emotional support test"),
    ("test_user_4", "Abhi tak jaag raha hoon", "Late night check-in test"),
]

async def main():
    print("=" * 70)
    print("🧪 Hinglish Response Test (gemma2:9b)")
    print("=" * 70)

    handler = BotHandler()

    # Check if Ollama is reachable
    try:
        import ollama
        ollama.chat(model="gemma2:9b", messages=[{"role": "user", "content": "test"}])
        print("✅ Ollama connection verified (gemma2:9b)")
    except Exception as e:
        print(f"\n❌ Ollama is not running or gemma2:9b is not installed!")
        print(f"   Error: {e}")
        print("   Run: ollama pull gemma2:9b")
        sys.exit(1)

    passed = 0
    total = len(TEST_MESSAGES)

    for user_id, message, description in TEST_MESSAGES:
        print(f"\n{'─' * 60}")
        print(f"📋 Test: {description}")
        print(f"👤 [{user_id}]: {message}")
        try:
            result = await handler.process_text_message(user_id, message)
            reply = result['message']
            print(f"🤖 Reply: {reply}")

            # Basic quality checks
            is_pure_english = not any(word in reply.lower() for word in
                ['hai', 'hain', 'kya', 'toh', 'aap', 'ji', 'bhai', 'yaar',
                 'hoon', 'kar', 'karo', 'nahi', 'achha', 'theek', 'bohot',
                 'arey', 'arre', 'woh', 'mein', 'aur', 'par', 'dekh', 'batao'])

            if is_pure_english and len(reply.split()) > 5:
                print(f"⚠️  WARNING: Reply seems pure English!")
            else:
                print(f"✅ Hinglish detected")
                passed += 1
        except Exception as e:
            print(f"❌ Error: {e}")

    print(f"\n{'=' * 70}")
    print(f"📊 Results: {passed}/{total} replies contained Hinglish")
    print(f"{'✅ PASS' if passed >= total * 0.7 else '⚠️  NEEDS REVIEW'}")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())
