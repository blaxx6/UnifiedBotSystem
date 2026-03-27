import requests, time, json

BASE_URL = "http://localhost:3000"
OWNER_JID = "919999990001@s.whatsapp.net"
TARGET_NUMBER = "919999990002"
FRIEND_LID = "119026445500473@lid"

def send_message(jid, push_name, text):
    payload = {
        "event": "messages.upsert",
        "instance": "indic_speech_client",
        "data": {
            "key": {
                "remoteJid": jid,
                "fromMe": False,
                "id": f"TEST_{int(time.time()*1000)}"
            },
            "pushName": push_name,
            "message": {"conversation": text},
            "messageType": "conversation",
            "messageTimestamp": int(time.time()),
            "instanceId": "test_instance",
            "source": "android"
        },
        "sender": jid
    }
    
    print(f"\n📨 SENDING: {push_name} ({jid}) -> '{text}'")
    try:
        r = requests.post(f"{BASE_URL}/webhook/whatsapp", json=payload, timeout=5)
        print(f"   Response: {r.status_code} - {r.json()}")
    except Exception as e:
        print(f"   ❌ {e}")

print("--- STEP 1: Friend sends message (Should trigger Alert to Owner) ---")
send_message(FRIEND_LID, "Friend", "Hello bot (I am unknown)")

time.sleep(2)

print("\n--- STEP 2: Owner sends link command (Should link 'Friend' to 919999990001) ---")
# Owner replies with a number
send_message(OWNER_JID, "User", "919876543210")

time.sleep(2)

print("\n--- STEP 3: Friend sends message again (Should be RESOLVED 200 OK) ---")
send_message(FRIEND_LID, "Friend", "Hello again (Now I am known)")
