import requests
from config import Config

def update_webhook():
    # Docker-friendly URL
    new_url = "http://host.docker.internal:3000/webhook/whatsapp"
    
    print(f"🔌 Updating Webhook URL to: {new_url}")
    
    url = f"{Config.EVOLUTION_API_URL}/webhook/set/{Config.EVOLUTION_INSTANCE_NAME}"
    headers = {
        'apikey': Config.EVOLUTION_API_KEY,
        'Content-Type': 'application/json'
    }
    
    # FIXED: Uppercase "MESSAGES_UPSERT"
    payload = {
        "webhook": {
            "enabled": True,
            "url": new_url,
            "webhookByEvents": False,
            "webhookBase64": False,
            "events": ["MESSAGES_UPSERT"] 
        }
    }
    
    try:
        r = requests.post(url, json=payload, headers=headers)
        print(f"Server Response: {r.text}")
        
        if r.status_code == 200:
            print("✅ Webhook Updated Successfully!")
        else:
            print(f"❌ Failed to update webhook. Status: {r.status_code}")
    except Exception as e:
        print(f"❌ Connection Error: {e}")

if __name__ == "__main__":
    update_webhook()