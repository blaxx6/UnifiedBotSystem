
import requests
import json

BASE_URL = "http://localhost:8080"
INSTANCE = "indic_speech_client"
API_KEY = "secret_token_here"  # Hardcoded for debug, should match .env
HEADERS = {"apikey": API_KEY, "Content-Type": "application/json"}

def probe(endpoint):
    url = f"{BASE_URL}{endpoint}"
    print(f"🔎 Probing {url}...")
    try:
        r = requests.get(url, headers=HEADERS) # Try GET
        print(f"   GET Status: {r.status_code}")
        if r.status_code == 200:
            print(f"   Response: {str(r.json())[:500]}") # Truncate
            return r.json()
        
        r = requests.post(url, headers=HEADERS) # Try POST
        print(f"   POST Status: {r.status_code}")
        if r.status_code == 200:
             print(f"   Response: {str(r.json())[:500]}")
             return r.json()
    except Exception as e:
        print(f"   Error: {e}")
    return None

endpoints = [
    f"/chat/findContacts/{INSTANCE}",
    f"/chat/contacts/{INSTANCE}",
    f"/contact/find/{INSTANCE}",
    f"/chat/getAllContacts/{INSTANCE}"
]


print("--- SEARCHING FOR LID ---")
url = f"{BASE_URL}/chat/findContacts/{INSTANCE}"
try:
    r = requests.post(url, headers=HEADERS)
    if r.status_code == 200:
        contacts = r.json()
        print(f"✅ Found {len(contacts)} contacts.")
        
        # Search for LID
        target_lid = "101133628485854@lid"
        
        # SEARCH BY NAME "TestOwner"
        print("--- SEARCHING BY NAME 'TestOwner' ---")
        param = "TestOwner"
        match_count = 0
        for c in contacts:
            pname = c.get('pushName', '')
            name = c.get('name', '')
            if param.lower() in str(pname).lower() or param.lower() in str(name).lower():
                print(f"🎯 FOUND NAME MATCH: {json.dumps(c, indent=2)}")
                match_count += 1
        print(f"Total entries for 'TestOwner': {match_count}")
        
except Exception as e:
    print(f"Error: {e}")
print("--- END ---")
