from database import Database

def purge_mappings():
    db = Database()
    conn = db._get_conn()
    cursor = conn.cursor()
    
    print("🗑️ Purging 'lid_mappings' table...")
    try:
        cursor.execute("TRUNCATE TABLE lid_mappings")
        conn.commit()
        print("✅ Table truncated successfully.")
    except Exception as e:
        print(f"❌ Error purging table: {e}")
            
    cursor.close()
    conn.close()

if __name__ == "__main__":
    purge_mappings()
