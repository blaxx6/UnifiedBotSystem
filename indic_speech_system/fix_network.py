import socket
import requests
from config import Config

def get_local_ip():
    """Finds the real IP address of your Mac on the network"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't actually connect, just calculates the route
        s.connect(('8.8.8.8', 80))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def fix_webhook():
    ip = get_local_ip()
    port = Config.WEBHOOK_PORT
    instance = Config.EVOLUTION_INSTANCE_NAME
    apikey = Config.EVOLUTION_API_KEY
    base_url = Config.EVOLUTION_API_URL

    print(f"📍 Detected your Real IP: {ip}")
    
    # The new URL using the Real IP
    webhook_url = f"http://{ip}:{port}/webhook/whatsapp"
    print(f"🔌 Setting Webhook to: {webhook_url}")

    url = f"{base_url}/webhook/set/{instance}"
    headers = {
        'apikey': apikey,
        'Content-Type': 'application/json'
    }
    
    payload = {
        "webhook": {
            "enabled": True,
            "url": webhook_url,
            "webhookByEvents": False, 
            "events": ["MESSAGES_UPSERT", "MESSAGES_UPDATE"] 
        }
    }
    
    try:
        r = requests.post(url, json=payload, headers=headers)
        if r.status_code == 200:
            print("✅ SUCCESS! Webhook updated to Real IP.")
            print("👉 Now send 'Hi' from WhatsApp again.")
        else:
            print(f"❌ Failed: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"❌ Connection Error: {e}")
        print("Make sure Docker is running!")

if __name__ == "__main__":
    fix_webhook()