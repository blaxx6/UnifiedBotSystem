# total_wipe.py - Nukes DB and Contacts File
import os
import psycopg2

# 1. Delete the corrupted JSON file
contact_file = "data/contacts.json"
if os.path.exists(contact_file):
    os.remove(contact_file)
    print("✅ Deleted corrupted data/contacts.json")
else:
    print("✨ No contacts file found (Already clean).")

# 2. Wipe the Database Tables
try:
    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        database="evolution",
        user="evolution",
        password="evolutionpass123"
    )
    conn.autocommit = True
    cursor = conn.cursor()
    
    print("🔥 Dropping all database tables...")
    cursor.execute("DROP TABLE IF EXISTS unified_messages CASCADE;")
    cursor.execute("DROP TABLE IF EXISTS user_sessions CASCADE;")
    cursor.execute("DROP TABLE IF EXISTS ai_generation_queue CASCADE;")
    print("✅ Database wiped clean.")
    
except Exception as e:
    print(f"❌ DB Error: {e}")

print("\n🚀 SYSTEM IS NOW 100% CLEAN.")