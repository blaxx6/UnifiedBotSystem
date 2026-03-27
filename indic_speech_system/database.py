# database.py
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool as pgpool

from config import Config


class Database:
    def __init__(self) -> None:
        self.conn: Optional[psycopg2.extensions.connection] = None
        self._pool: Optional[pgpool.ThreadedConnectionPool] = None
        self._connect()
        # Try to get any connection for table setup
        try:
            conn = self._get_conn()
            self.setup_tables()
            self._return_conn(conn)
        except ConnectionError:
            print("⚠️ Cannot setup tables — no database connection")

    def _connect(self) -> None:
        """Establish database connection pool with fallback to single connection."""
        # Try connection pool first (recommended for concurrent access)
        try:
            self._pool = pgpool.ThreadedConnectionPool(
                minconn=2,
                maxconn=10,
                host=Config.DB_HOST,
                port=Config.DB_PORT,
                database=Config.DB_NAME,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
            )
            print("✅ Database connection pool created (2-10 connections)")
            return
        except Exception as e:
            print(f"⚠️ Connection pool failed, trying single connection: {e}")
            self._pool = None

        # Fallback: single connection (original behavior)
        try:
            conn = psycopg2.connect(
                host=Config.DB_HOST,
                port=Config.DB_PORT,
                database=Config.DB_NAME,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD
            )
            conn.autocommit = False
            self.conn = conn
            print("✅ Database connected (single connection mode)")
        except Exception as e:
            print(f"⚠️ Database connection failed: {e}")
            print("   The system will continue without database support.")
            self.conn = None

    def _get_conn(self):
        """Get a connection from the pool, or fallback to single connection."""
        # Pool mode
        if self._pool is not None:
            try:
                conn = self._pool.getconn()
                conn.autocommit = False
                return conn
            except Exception:
                pass

        # Single connection mode with reconnection
        conn = self.conn
        if conn is None or conn.closed:
            self._connect()
            conn = self.conn
        if conn is None:
            raise ConnectionError("No database connection available")
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            self._connect()
            if self.conn is None:
                raise ConnectionError("No database connection available")
            return self.conn

    def _return_conn(self, conn):
        """Return connection to pool (no-op in single connection mode)."""
        if self._pool is not None and conn is not None:
            try:
                self._pool.putconn(conn)
            except Exception:
                pass

    @contextmanager
    def _connection(self):
        """Context manager for pool-safe connection usage."""
        conn = self._get_conn()
        try:
            yield conn
        finally:
            self._return_conn(conn)

    def _ensure_connection(self) -> bool:
        """Reconnect if the connection was lost. Returns True if connected."""
        try:
            conn = self._get_conn()
            self._return_conn(conn)
            return True
        except ConnectionError:
            return False

    def setup_tables(self):
        """Create necessary tables"""
        try:
            conn = self._get_conn()
        except ConnectionError:
            print("⚠️ Cannot setup tables — no database connection")
            return

        cursor = conn.cursor()

        # Unified messages table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS unified_messages (
            id SERIAL PRIMARY KEY,
            platform VARCHAR(20) NOT NULL,
            user_id VARCHAR(100) NOT NULL,
            user_name VARCHAR(255),
            message_text TEXT,
            message_type VARCHAR(50) DEFAULT 'text',
            audio_path TEXT,
            media_url TEXT,
            direction VARCHAR(10) NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status VARCHAR(20) DEFAULT 'received',
            session_data JSONB,
            metadata JSONB
        );

        CREATE INDEX IF NOT EXISTS idx_platform ON unified_messages(platform);
        CREATE INDEX IF NOT EXISTS idx_timestamp ON unified_messages(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_user ON unified_messages(user_id);
        CREATE INDEX IF NOT EXISTS idx_status ON unified_messages(status);
        """)

        # User sessions table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            id SERIAL PRIMARY KEY,
            platform VARCHAR(20) NOT NULL,
            user_id VARCHAR(100) NOT NULL,
            src_lang VARCHAR(50) DEFAULT 'hindi',
            tgt_lang VARCHAR(50) DEFAULT 'english',
            mode VARCHAR(20) DEFAULT 'stt',
            preferences JSONB,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(platform, user_id)
        );
        """)

        # AI generation queue
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS ai_generation_queue (
            id SERIAL PRIMARY KEY,
            platform VARCHAR(20),
            user_id VARCHAR(100),
            prompt TEXT NOT NULL,
            generation_type VARCHAR(50) NOT NULL,
            status VARCHAR(20) DEFAULT 'pending',
            result_text TEXT,
            result_audio_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        );
        """)

        # LID to Real JID Mapping Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS lid_mappings (
            lid VARCHAR(100) PRIMARY KEY,
            real_jid VARCHAR(100) NOT NULL,
            push_name VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        conn.commit()
        cursor.close()
        print("✅ Database tables created")

    def save_message(self, platform, user_id, user_name, message_text,
                     direction, message_type='text', audio_path=None, metadata=None):
        """Save a message to database"""
        try:
            conn = self._get_conn()
        except ConnectionError:
            print("⚠️ Cannot save message — no database connection")
            return None

        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO unified_messages
                (platform, user_id, user_name, message_text, message_type,
                 audio_path, direction, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (platform, user_id, user_name, message_text, message_type,
                  audio_path, direction, json.dumps(metadata or {})))

            message_id = cursor.fetchone()[0]
            conn.commit()
            return message_id
        except Exception as e:
            print(f"⚠️ Error saving message: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return None
        finally:
            cursor.close()

    def get_recent_messages(self, limit=100, platform=None):
        """Get recent messages"""
        try:
            conn = self._get_conn()
        except ConnectionError:
            return []

        cursor = conn.cursor(cursor_factory=RealDictCursor)

        try:
            if platform:
                cursor.execute("""
                    SELECT * FROM unified_messages
                    WHERE platform = %s
                    ORDER BY timestamp DESC LIMIT %s
                """, (platform, limit))
            else:
                cursor.execute("""
                    SELECT * FROM unified_messages
                    ORDER BY timestamp DESC LIMIT %s
                """, (limit,))

            messages = cursor.fetchall()
            return messages
        except Exception as e:
            print(f"⚠️ Error fetching messages: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return []
        finally:
            cursor.close()

    def get_user_messages(self, user_id, platform=None):
        """Get messages for a specific user"""
        try:
            conn = self._get_conn()
        except ConnectionError:
            return []

        cursor = conn.cursor(cursor_factory=RealDictCursor)

        try:
            if platform:
                cursor.execute("""
                    SELECT * FROM unified_messages
                    WHERE user_id = %s AND platform = %s
                    ORDER BY timestamp DESC
                """, (user_id, platform))
            else:
                cursor.execute("""
                    SELECT * FROM unified_messages
                    WHERE user_id = %s
                    ORDER BY timestamp DESC
                """, (user_id,))

            messages = cursor.fetchall()
            return messages
        except Exception as e:
            print(f"⚠️ Error fetching user messages: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return []
        finally:
            cursor.close()

    def get_active_users(self):
        """Get list of active users across platforms"""
        try:
            conn = self._get_conn()
        except ConnectionError:
            return []

        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cursor.execute("""
                SELECT DISTINCT platform, user_id, user_name,
                       MAX(timestamp) as last_message
                FROM unified_messages
                GROUP BY platform, user_id, user_name
                ORDER BY last_message DESC
            """)

            users = cursor.fetchall()
            return users
        except Exception as e:
            print(f"⚠️ Error fetching active users: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return []
        finally:
            cursor.close()

    def save_generation_task(self, platform, user_id, prompt, generation_type):
        """Save AI generation task"""
        try:
            conn = self._get_conn()
        except ConnectionError:
            return None

        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO ai_generation_queue
                (platform, user_id, prompt, generation_type)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (platform, user_id, prompt, generation_type))

            task_id = cursor.fetchone()[0]
            conn.commit()
            return task_id
        except Exception as e:
            print(f"⚠️ Error saving generation task: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return None
        finally:
            cursor.close()

    def update_generation_task(self, task_id, status, result_text=None, result_audio=None):
        """Update generation task"""
        try:
            conn = self._get_conn()
        except ConnectionError:
            return
        
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE ai_generation_queue
                SET status = %s, result_text = %s, result_audio_path = %s,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (status, result_text, result_audio, task_id))

            conn.commit()
        except Exception as e:
            print(f"⚠️ Error updating generation task: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            cursor.close()


    def save_lid_mapping(self, lid, real_jid, push_name=None):
        """Save LID to Real JID mapping"""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO lid_mappings (lid, real_jid, push_name)
                VALUES (%s, %s, %s)
                ON CONFLICT (lid) DO UPDATE 
                SET real_jid = EXCLUDED.real_jid, push_name = EXCLUDED.push_name
            """, (lid, real_jid, push_name))
            conn.commit()
            cursor.close()
            print(f"💾 Saved LID Mapping: {lid} -> {real_jid}")
            return True
        except Exception as e:
            print(f"⚠️ Error saving LID mapping: {e}")
            return False

    def get_real_jid_from_lid(self, lid):
        """Get Real JID from LID"""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT real_jid FROM lid_mappings WHERE lid = %s", (lid,))
            result = cursor.fetchone()
            cursor.close()
            if result:
                return result[0]
            return None
        except Exception as e:
            print(f"⚠️ Error fetching LID mapping: {e}")
            return None


# Lazy initialization — won't crash if DB is unavailable
db = Database()
