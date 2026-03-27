# test_db.py
from database import db

print("🧪 Testing Database Connection...")
try:
    msg_id = db.save_message(
        platform="whatsapp",
        user_id="test_user_123",
        user_name="Test User",
        message_text="This is a test message from script",
        direction="incoming",
        message_type="text"
    )
    print(f"✅ Success! Message saved with ID: {msg_id}")
    print("👉 Now check your Dashboard. If you see this message, the DB is fine.")
except Exception as e:
    print(f"❌ Database Error: {e}")