
import requests
import json
import time

# Use 127.0.0.1 to avoid IPv6 issues on localhost
BASE_URL = "http://127.0.0.1:3000"

print(f"🚀 Sending simulation message to {BASE_URL}/webhook/whatsapp...")

# Payload mimicking a LID message
payload = {
  "event": "messages.upsert",
    "data": {
        "key": {
            "remoteJid": "101133628485854@lid",
            "fromMe": False,
            "id": "TEST_LID_SIM_001"
        },
        "pushName": "User",
        "message": {
            "conversation": "Simulation LID Resolution Test"
        }
    }
}

try:
    r = requests.post(f"{BASE_URL}/webhook/whatsapp", json=payload)
    print(f"✅ Response Code: {r.status_code}")
    print(f"📄 Response Body: {r.text}")
except Exception as e:
    print(f"❌ Could not connect to your bot: {e}")
    print("Make sure 'python run_unified_system.py' is running in another window!")