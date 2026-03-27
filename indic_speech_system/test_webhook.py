import requests, time, json

BASE_URL = "http://localhost:3000"

# Realistic payload matching what the REAL Evolution API sends
# (no senderPn, no participant - just remoteJid + pushName)
def send_test(remote_jid, push_name, text):
    payload = {
        "event": "messages.upsert",
        "instance": "your_instance_name",
        "data": {
            "key": {
                "remoteJid": remote_jid,
                "fromMe": False,
                "id": f"TEST_{int(time.time()*1000)}"
            },
            "pushName": push_name,
            "message": {"conversation": text},
            "messageType": "conversation",
            "messageTimestamp": int(time.time()),
            "instanceId": "9164aa4b-10ff-4a48-8654-671450fbd4c6",
            "source": "android"
        },
        "destination": "http://host.docker.internal:3000/webhook/whatsapp",
        "date_time": "2026-02-13T15:10:17.885Z",
        "sender": "919999990099@s.whatsapp.net",
        "server_url": "http://localhost:8080",
        "apikey": "secret_token_here"
    }
    
    print(f"\n📨 TEST: {push_name} ({remote_jid})")
    print(f"   Message: '{text}'")
    try:
        r = requests.post(f"{BASE_URL}/webhook/whatsapp", json=payload, timeout=15)
        print(f"   Response: {r.status_code} - {r.json()}")
    except Exception as e:
        print(f"   ❌ {e}")

# Test 1: TestContactA (LID)
send_test("10000000000001@lid", "TestContactA", "Hi from contact A")

time.sleep(2)

# Test 2: Contact B (LID)
send_test("10000000000002@lid", "User", "Hi from contact B")
