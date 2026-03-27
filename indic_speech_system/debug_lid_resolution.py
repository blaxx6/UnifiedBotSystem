import requests
import json
from config import Config

def debug_contacts(target_search="101133628485854"):
    url = f"{Config.EVOLUTION_API_URL}/chat/findContacts/{Config.EVOLUTION_INSTANCE_NAME}"
    headers = {'apikey': Config.EVOLUTION_API_KEY, 'Content-Type': 'application/json'}
    
    print(f"🔍 Fetching contacts from: {url}")
    try:
        r = requests.post(url, headers=headers)
        if r.status_code == 200:
            contacts = r.json()
            print(f"✅ Fetched {len(contacts)} contacts.")
            
            print(f"🔎 Searching for '{target_search}'...")
            found_count = 0
            for c in contacts:
                # Check ALL fields
                json_str = json.dumps(c)
                if target_search in json_str:
                     print(f"\n🎯 RAW MATCH FOUND (Match #{found_count+1}):")
                     print(json.dumps(c, indent=2))
                     found_count += 1
            
            if found_count == 0:
                print("❌ No matches found.")

        else:
            print(f"❌ API Error: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"❌ Connection Error: {e}")

if __name__ == "__main__":
    debug_contacts()
