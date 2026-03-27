import requests
from config import Config

# ==========================================
# 👇 PASTE YOUR NGROK URL BELOW (Inside quotes)
# Example: "https://a1b2-c3d4.ngrok-free.app"
NGROK_URL = "https://ba36-2401-4900-1f3d-68a8-8961-26a6-3500-6a7b.ngrok-free.app" 
# ==========================================

def set_ngrok_webhook():
    # Remove trailing slash if user accidentally added it
    clean_url = NGROK_URL.rstrip('/')
    webhook_url = f"{clean_url}/webhook/whatsapp"
    
    print(f"🔌 Setting Webhook to Tunnel: {webhook_url}")
    
    url = f"{Config.EVOLUTION_API_URL}/webhook/set/{Config.EVOLUTION_INSTANCE_NAME}"
    headers = {
        'apikey': Config.EVOLUTION_API_KEY,
        'Content-Type': 'application/json'
    }
    
    payload = {
        "webhook": {
            "enabled": True,
            "url": webhook_url,
            "webhookByEvents": False, 
            "events": ["MESSAGES_UPSERT"] 
        }
    }
    
    try:
        print(f"🚀 Sending request to Evolution API at: {url}")
        r = requests.post(url, json=payload, headers=headers)
        
        print(f"📡 Server Response Code: {r.status_code}")
        print(f"📄 Response Text: {r.text}")
        
        if r.status_code in [200, 201]:
            print("\n✅ SUCCESS! Webhook is now connected via Ngrok.")
            print("👉 You can now restart your bot and send 'Hi'.")
        else:
            print("\n❌ FAILED. The Evolution API rejected the request.")
            print("Check if Docker is running and your API Key is correct.")
            
    except Exception as e:
        print(f"\n❌ CONNECTION ERROR: {e}")
        print("Is the Evolution API (Docker) running?")

if __name__ == "__main__":
    set_ngrok_webhook()