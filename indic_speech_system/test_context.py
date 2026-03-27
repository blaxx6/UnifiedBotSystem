import requests, time, json

BASE_URL = "http://localhost:3000"
OWNER_JID = "919999990001@s.whatsapp.net"

def send_message(text):
    payload = {
        "event": "messages.upsert",
        "instance": "indic_speech_client",
        "data": {
            "key": {
                "remoteJid": OWNER_JID,
                "fromMe": True, # Simulate TestUser sending (webhook treats fromMe=True as incoming if logic allows? No, logic usually ignores fromMe=True)
                # Wait! My webhook ignores fromMe=True?
                # "if key.get('fromMe'): return jsonify({'status': 'ignored_self'}), 200"
                # So I must send as fromMe=False (incoming from someone else).
                "fromMe": False,
                "id": f"TEST_{int(time.time()*1000)}"
            },
            "pushName": "TestUser",
            "message": {"conversation": text},
            "messageType": "conversation",
            "messageTimestamp": int(time.time()),
            "instanceId": "test_instance",
            "source": "android"
        },
        "sender": OWNER_JID
    }
    
    print(f"📨 SENDING: '{text}'")
    try:
        r = requests.post(f"{BASE_URL}/webhook/whatsapp", json=payload, timeout=5)
        print(f"   Response: {r.status_code} - {r.json()}")
    except Exception as e:
        print(f"   ❌ {e}")

send_message("What is the secret word?")
