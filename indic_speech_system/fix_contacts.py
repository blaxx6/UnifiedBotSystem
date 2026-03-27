import json
import os

CONTACTS_FILE = os.path.join(os.path.dirname(__file__), 'data', 'contacts.json')

def clean_contacts():
    if not os.path.exists(CONTACTS_FILE):
        print("❌ No contacts file found.")
        return

    with open(CONTACTS_FILE, 'r') as f:
        contacts = json.load(f)
    
    initial_count = len(contacts)
    new_contacts = {}

    for key, data in contacts.items():
        user_id = data.get('id', '')
        platform = data.get('platform', '')

        # FILTER: Keep only valid WhatsApp IDs (must have @s.whatsapp.net)
        if platform == 'whatsapp' and '@lid' in user_id:
            print(f"🗑️ Removing invalid LID: {data['name']} ({user_id})")
            continue
        
        # Keep everything else
        new_contacts[key] = data

    with open(CONTACTS_FILE, 'w') as f:
        json.dump(new_contacts, f, indent=4)
    
    print(f"✅ Cleanup Complete. Removed {initial_count - len(new_contacts)} invalid contacts.")

if __name__ == "__main__":
    clean_contacts()