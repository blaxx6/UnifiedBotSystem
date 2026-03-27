from database import Database

def inspect_mappings():
    db = Database()
    conn = db._get_conn()
    cursor = conn.cursor()
    
    print("🔍 Inspecting 'lid_mappings' table...")
    cursor.execute("SELECT * FROM lid_mappings")
    rows = cursor.fetchall()
    
    if not rows:
        print("✅ Table is empty.")
    else:
        print(f"⚠️ Found {len(rows)} mappings:")
        for row in rows:
            print(f"  {row}")
            
    cursor.close()
    conn.close()

if __name__ == "__main__":
    inspect_mappings()
