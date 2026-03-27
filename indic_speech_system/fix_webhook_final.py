import requests
from config import Config

def force_docker_tunnel():
    # This special address lets Docker talk to your Mac securely
    docker_url = "http://host.docker.internal:3000/webhook/whatsapp"
    
    print(f"🔌 Switching Webhook to Docker Tunnel: {docker_url}")
    
    url = f"{Config.EVOLUTION_API_URL}/webhook/set/{Config.EVOLUTION_INSTANCE_NAME}"
    headers = {
        'apikey': Config.EVOLUTION_API_KEY,
        'Content-Type': 'application/json'
    }
    
    payload = {
        "webhook": {
            "enabled": True,
            "url": docker_url,
            "webhookByEvents": False, 
            "events": ["MESSAGES_UPSERT"] 
        }
    }
    
    try:
        r = requests.post(url, json=payload, headers=headers)
        print(f"Server Response: {r.status_code}")
        if r.status_code in [200, 201]:
            print("✅ Success! Webhook fixed.")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    force_docker_tunnel()