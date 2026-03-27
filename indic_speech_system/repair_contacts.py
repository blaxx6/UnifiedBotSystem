# repair_contacts.py (Strict Length Check)
import psycopg2
import requests
import json
from config import Config

try:
    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        database="evolution",
        user="evolution",
        password="evolutionpass123"
    )
    cursor = conn.cursor()
    print("✅ Connected to Database")
except Exception as e:
    print(f"❌ Database Connection Failed: {e}")
    exit()

# 1. CLEANUP: Delete "Fake" Real Numbers (LIDs saved with @s.whatsapp.net)
# Any ID longer than 14 digits (before the @) is likely a LID, not a phone number.
print("🧹 Cleaning up corrupted database entries...")
cursor.execute("""
    DELETE FROM unified_messages 
    WHERE user_id LIKE '%@s.whatsapp.net' 
    AND LENGTH(SPLIT_PART(user_id, '@', 1)) > 14
""")
deleted_count = cursor.rowcount
conn.commit()
if deleted_count > 0:
    print(f"🗑️ Deleted {deleted_count} bad entries (Fake JIDs).")

# 2. SCAN: Find remaining Ghost IDs (@lid)
print("\n🔍 Scanning for Ghost IDs...")
cursor.execute("SELECT DISTINCT user_id, user_name FROM unified_messages WHERE user_id LIKE '%@lid'")
ghost_users = cursor.fetchall()

if not ghost_users:
    print("✨ System Clean! No Ghost IDs found.")
    exit()

print(f"⚠️ Found {len(ghost_users)} Ghost IDs. Attempting to repair...")

api_url = f"{Config.EVOLUTION_API_URL}/chat/checkIsOnWhatsApp/{Config.EVOLUTION_INSTANCE_NAME}"
headers = {"apikey": Config.EVOLUTION_API_KEY, "Content-Type": "application/json"}

for lid, name in ghost_users:
    print(f"\n🔧 Fixing User: {name} ({lid})")
    real_jid = None

    # STRATEGY 1: Internal DB Search (STRICT MODE)
    # Only accept IDs that are 14 digits or shorter (Real Phone Numbers)
    if name:
        try:
            cursor.execute("""
                SELECT user_id FROM unified_messages 
                WHERE user_name = %s 
                AND user_id LIKE '%%@s.whatsapp.net' 
                AND LENGTH(SPLIT_PART(user_id, '@', 1)) <= 14
                ORDER BY timestamp DESC LIMIT 1
            """, (name,))
            result = cursor.fetchone()
            if result:
                real_jid = result[0]
                print(f"   ✅ FOUND IN DB: '{name}' is actually {real_jid}")
        except Exception as e:
            print(f"   ⚠️ DB Search Error: {e}")
            conn.rollback()

    # STRATEGY 2: API Backup
    if not real_jid:
        print("   ⚠️ Not in DB. Trying API lookup...")
        try:
            response = requests.post(api_url, json={"numbers": [lid]}, headers=headers)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) > 0 and data[0].get('exists'):
                    real_jid = data[0].get('jid') or data[0].get('id')
                    print(f"   ✅ API RESOLVED: {real_jid}")
        except:
            pass

    # APPLY FIX
    if real_jid and "@s.whatsapp.net" in real_jid:
        try:
            cursor.execute("""
                UPDATE unified_messages 
                SET user_id = %s 
                WHERE user_id = %s
            """, (real_jid, lid))
            conn.commit()
            print(f"   ✨ FIXED: Merged {lid} -> {real_jid}")
        except Exception as e:
            print(f"   ❌ Update Failed: {e}")
            conn.rollback()
    else:
        print(f"   ❌ FAILED: Still cannot find real number for {name}")

print("\n✅ Repair Complete.")
conn.close()