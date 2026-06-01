from __future__ import annotations

import asyncio
import datetime
import os
import sqlite3

import psycopg2
import redis
from backends import GenericHTTPBackend, SandboxBackend
from config import opensandbox_base_url


class AppState:
    """Manages active proxy mapping configurations and centralized high-scale persistence."""

    def __init__(self):
        self.backend: SandboxBackend = GenericHTTPBackend(
            "opensandbox", opensandbox_base_url()
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
                    decode_responses=True,
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
                dbname=os.environ.get("PG_DATABASE"),
            )
        return sqlite3.connect(self.db_path)

    def init_db(self):
        conn = self.get_db_conn()
        cursor = conn.cursor()

        # Postgres uses slightly different syntax for PRIMARY KEY and types
        if self.use_postgres:
            cursor.execute(
                """
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
            """
            )
        else:
            cursor.execute(
                """
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
            """
            )

        conn.commit()

        # Schema Guard: Ensure user_email exists (Migration)
        try:
            cursor.execute(
                "ALTER TABLE api_keys ADD COLUMN user_email TEXT"
                if self.use_postgres
                else "ALTER TABLE api_keys ADD COLUMN user_email TEXT"
            )
            conn.commit()
            print("[startup] Database migration: Added user_email column to api_keys")
        except Exception:
            conn.rollback()
            pass

        # Sync Active Registry to Redis for line-rate validation
        if self.use_redis:
            now_iso = datetime.datetime.now(datetime.UTC).isoformat()
            cursor.execute(
                "SELECT id FROM api_keys WHERE is_revoked = 0 AND expires_at > %s"
                if self.use_postgres
                else "SELECT id FROM api_keys WHERE is_revoked = 0 AND expires_at > ?",
                (now_iso,),
            )
            active_jtis = cursor.fetchall()
            if active_jtis:
                # Add all active JTIs to a Redis set called 'active_api_keys'
                pipe = self.redis_client.pipeline()
                pipe.delete("active_api_keys")  # Refresh
                for (jti,) in active_jtis:
                    pipe.sadd("active_api_keys", jti)
                pipe.execute()
                print(
                    f"[startup] Synced {len(active_jtis)} active keys to Redis registry."
                )

        conn.commit()
        conn.close()


state = AppState()


async def cleanup_expired_keys_task():
    """
    Background worker that purges expired keys from Postgres and Redis every hour.
    This ensures the 'Back Door' is closed even if no one is currently trying to use the keys.
    """
    while True:
        try:
            now_iso = datetime.datetime.now(datetime.UTC).isoformat()
            print(f"[Janitor] Running cleanup for keys expired before {now_iso}...")

            conn = state.get_db_conn()
            cursor = conn.cursor()

            # 1. Identify expired keys for Redis cleanup
            query_find = (
                "SELECT id FROM api_keys WHERE expires_at < %s"
                if state.use_postgres
                else "SELECT id FROM api_keys WHERE expires_at < ?"
            )
            cursor.execute(query_find, (now_iso,))
            expired_ids = [row[0] for row in cursor.fetchall()]

            if expired_ids and state.use_redis:
                for eid in expired_ids:
                    state.redis_client.srem("active_api_keys", eid)
                print(f"[Janitor] Removed {len(expired_ids)} expired keys from Redis")

            # 2. Delete from Database
            query_del = (
                "DELETE FROM api_keys WHERE expires_at < %s"
                if state.use_postgres
                else "DELETE FROM api_keys WHERE expires_at < ?"
            )
            cursor.execute(query_del, (now_iso,))
            deleted_count = cursor.rowcount

            conn.commit()
            conn.close()

            if deleted_count > 0:
                print(
                    f"[Janitor] Successfully purged {deleted_count} expired keys from database."
                )

        except Exception as e:
            print(f"[Janitor] Error during cleanup: {str(e)}")

        # Run every minute to handle short-lived keys (like 5-min TTL)
        await asyncio.sleep(60)
