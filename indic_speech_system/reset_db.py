# reset_db.py
from database import db

if __name__ == "__main__":
    print("⚠️  Resetting Database...")
    cursor = db.conn.cursor()
    # Drop old tables to force a clean slate
    cursor.execute("DROP TABLE IF EXISTS unified_messages CASCADE;")
    cursor.execute("DROP TABLE IF EXISTS user_sessions CASCADE;")
    cursor.execute("DROP TABLE IF EXISTS ai_generation_queue CASCADE;")
    db.conn.commit()
    
    # Re-create with new schema
    db.setup_tables()
    print("✅ Database reset complete!")