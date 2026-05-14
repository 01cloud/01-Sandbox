import os
import redis
import psycopg2
import sqlite3
import datetime
from typing import Optional
from backends import SandboxBackend, GenericHTTPBackend
from config import opensandbox_base_url

class AppState:
    """Manages active proxy mapping configurations and centralized high-scale persistence."""
    def __init__(self):
        self.backend: SandboxBackend = GenericHTTPBackend(
            "opensandbox",
            opensandbox_base_url()
        )
        self.latest_job_id: str | None = None
        
        # Persistence Config
        self.use_postgres = os.environ.get("PG_HOST") is not None
        self.use_redis = os.environ.get("REDIS_HOST") is not None
        
        self.db_path = os.environ.get("DB_PATH", "/tmp/apikeys.db")
        self.redis_client = None
        
        if self.use_redis:
            try:
                self.redis_client = redis.Redis(
                    host=os.environ.get("REDIS_HOST"),
                    port=int(os.environ.get("REDIS_PORT", 6379)),
                    password=os.environ.get("REDIS_PASSWORD", ""),
                    decode_responses=True
                )
                print(f"[startup] Connected to Redis at {os.environ.get('REDIS_HOST')}")
            except Exception as e:
                print(f"[startup] FAILED to connect to Redis: {str(e)}")
                self.use_redis = False

    def get_db_conn(self):
        if self.use_postgres:
            return psycopg2.connect(
                host=os.environ.get("PG_HOST"),
                port=os.environ.get("PG_PORT"),
                user=os.environ.get("PG_USER"),
                password=os.environ.get("PG_PASSWORD"),
                dbname=os.environ.get("PG_DATABASE")
            )
        return sqlite3.connect(self.db_path)

    def init_db(self):
        conn = self.get_db_conn()
        cursor = conn.cursor()
        
        # Postgres uses slightly different syntax for PRIMARY KEY and types
        if self.use_postgres:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    backend TEXT,
                    user_id TEXT,
                    user_email TEXT,
                    created_at TEXT,
                    expires_at TEXT,
                    last_used_at TEXT,
                    is_revoked INTEGER DEFAULT 0,
                    prefix TEXT
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    backend TEXT,
                    user_id TEXT,
                    user_email TEXT,
                    created_at TEXT,
                    expires_at TEXT,
                    last_used_at TEXT,
                    is_revoked INTEGER DEFAULT 0,
                    prefix TEXT
                )
            """)
        
        conn.commit()
        
        # Schema Guard: Ensure user_email exists (Migration)
        try:
            cursor.execute("ALTER TABLE api_keys ADD COLUMN user_email TEXT" if self.use_postgres else "ALTER TABLE api_keys ADD COLUMN user_email TEXT")
            conn.commit()
            print("[startup] Database migration: Added user_email column to api_keys")
        except Exception:
            conn.rollback()
            pass

        # Sync Active Registry to Redis for line-rate validation
        if self.use_redis:
            now_iso = datetime.datetime.now(datetime.UTC).isoformat()
            cursor.execute("SELECT id FROM api_keys WHERE is_revoked = 0 AND expires_at > %s" if self.use_postgres else "SELECT id FROM api_keys WHERE is_revoked = 0 AND expires_at > ?", (now_iso,))
            active_jtis = cursor.fetchall()
            if active_jtis:
                pipe = self.redis_client.pipeline()
                pipe.delete("active_api_keys") # Refresh
                for (jti,) in active_jtis:
                    pipe.sadd("active_api_keys", jti)
                pipe.execute()
                print(f"[startup] Synced {len(active_jtis)} active keys to Redis registry.")
                
        conn.commit()
        conn.close()

state = AppState()
