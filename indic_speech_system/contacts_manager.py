# contacts_manager.py
import json
import os
from datetime import datetime

CONTACTS_FILE = os.path.join(os.path.dirname(__file__), "data", "contacts.json")
os.makedirs(os.path.dirname(CONTACTS_FILE), exist_ok=True)

# Valid values for relationship and gender fields
VALID_RELATIONSHIPS = [
    "friend", "best friend", "girlfriend", "boyfriend",
    "wife", "husband", "brother", "sister",
    "mother", "father", "family",
    "teacher", "professor", "sir", "madam",
    "colleague", "boss", "senior", "junior",
    "acquaintance", "other"
]
VALID_GENDERS = ["male", "female", "unknown"]


def log_contact(platform, user_id, name):
    """Saves contact to local JSON for Dashboard Auto-fill.
    Preserves existing relationship/gender if already set."""
    # VALIDATION: Reject WhatsApp LIDs
    if platform == "whatsapp" and "@lid" in str(user_id):
        return  # Silently ignore

    # NORMALIZE ID
    user_id = str(user_id).strip()
    if platform == "whatsapp":
        user_id = user_id.replace("@s.whatsapp.net", "").replace(" ", "").replace("\n", "")

    contacts = _load_contacts()
    contact_key = str(platform) + "_" + str(user_id)
    
    # Preserve existing relationship/gender if contact already exists
    existing = contacts.get(contact_key, {})
    
    # Preserve existing real name if new name is generic "User ..."
    existing_name = existing.get("name", "")
    if existing_name and not existing_name.startswith("User ") and (not name or name.startswith("User ")):
        name = existing_name
    
    contacts[contact_key] = {
        "platform": platform,
        "id": str(user_id),
        "name": name if name else "Unknown",
        "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "relationship": existing.get("relationship", "friend"),
        "gender": existing.get("gender", "unknown"),
        # Relationship card fields (preserved across re-logs)
        "nickname": existing.get("nickname", ""),
        "shared_context": existing.get("shared_context", ""),
        "topics": existing.get("topics", []),
    }

    with open(CONTACTS_FILE, "w") as f:
        json.dump(contacts, f, indent=4)

def clean_duplicates():
    """One-time cleanup of existing duplicates in contacts.json"""
    contacts = _load_contacts()
    cleaned_contacts = {}
    
    for key, data in contacts.items():
        platform = data.get("platform")
        user_id = str(data.get("id")).strip()
        name = data.get("name")
        last_seen = data.get("last_seen")

        if platform == "whatsapp":
            user_id = user_id.replace("@s.whatsapp.net", "").replace(" ", "").replace("\n", "")
            if "@lid" in user_id: continue

        new_key = f"{platform}_{user_id}"
        
        # If key exists, keep the one with a "better" name (not starting with User) or more recent
        if new_key in cleaned_contacts:
            existing = cleaned_contacts[new_key]
            # Prefer real names over "User ..." names
            if existing["name"].startswith("User ") and not name.startswith("User "):
                cleaned_contacts[new_key] = {
                    "platform": platform,
                    "id": user_id,
                    "name": name,
                    "last_seen": last_seen
                }
            # If both are generic or both real, keep most recent (simple string comparison for ISO-ish date works enough)
            elif existing["last_seen"] < last_seen:
                 cleaned_contacts[new_key]["last_seen"] = last_seen
        else:
            cleaned_contacts[new_key] = {
                "platform": platform,
                "id": user_id,
                "name": name,
                "last_seen": last_seen
            }
            
    with open(CONTACTS_FILE, "w") as f:
        json.dump(cleaned_contacts, f, indent=4)
    print(f"✅ Contacts cleaned: {len(contacts)} -> {len(cleaned_contacts)}")


def get_all_contacts():
    """Returns all saved contacts as a dictionary."""
    return _load_contacts()


def update_contact_meta(contact_key, relationship=None, gender=None):
    """Updates the relationship and/or gender fields for a given contact."""
    contacts = _load_contacts()
    if contact_key not in contacts:
        return False
    
    if relationship is not None and relationship in VALID_RELATIONSHIPS:
        contacts[contact_key]["relationship"] = relationship
    if gender is not None and gender in VALID_GENDERS:
        contacts[contact_key]["gender"] = gender
    
    with open(CONTACTS_FILE, "w") as f:
        json.dump(contacts, f, indent=4)
    return True


def update_contact_card(contact_key, nickname=None, shared_context=None, topics=None):
    """Updates the relationship card fields (nickname, shared_context, topics) for a contact."""
    contacts = _load_contacts()
    if contact_key not in contacts:
        return False
    
    if nickname is not None:
        contacts[contact_key]["nickname"] = str(nickname).strip()
    if shared_context is not None:
        contacts[contact_key]["shared_context"] = str(shared_context).strip()
    if topics is not None:
        if isinstance(topics, list):
            contacts[contact_key]["topics"] = [str(t).strip() for t in topics[:10]]
        elif isinstance(topics, str):
            contacts[contact_key]["topics"] = [t.strip() for t in topics.split(",") if t.strip()][:10]
    
    with open(CONTACTS_FILE, "w") as f:
        json.dump(contacts, f, indent=4)
    return True


def get_contact_by_name(name):
    """Looks up a contact by display name (case-insensitive).
    Returns the contact dict or None."""
    if not name:
        return None
    contacts = _load_contacts()
    name_lower = name.lower().strip()
    for c in contacts.values():
        if c.get("name", "").lower().strip() == name_lower:
            return c
    return None


def _load_contacts():
    """Load contacts from disk, returning an empty dict on any failure."""
    if os.path.exists(CONTACTS_FILE):
        try:
            with open(CONTACTS_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # Migrate old contacts: add missing relationship/gender fields
                migrated = False
                for key, c in data.items():
                    if "relationship" not in c:
                        c["relationship"] = "friend"
                        migrated = True
                    if "gender" not in c:
                        c["gender"] = "unknown"
                        migrated = True
                    if "nickname" not in c:
                        c["nickname"] = ""
                        migrated = True
                    if "shared_context" not in c:
                        c["shared_context"] = ""
                        migrated = True
                    if "topics" not in c:
                        c["topics"] = []
                        migrated = True
                if migrated:
                    with open(CONTACTS_FILE, "w") as f:
                        json.dump(data, f, indent=4)
                return data
        except Exception:
            pass
    return {}