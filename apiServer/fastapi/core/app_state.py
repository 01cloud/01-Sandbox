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
        self.active_tasks: dict[str, asyncio.Task] = {}

        # Instantiate reusable job trackers
        from core.jobs.tracker import ReusableJobTracker

        self.job_tracker = ReusableJobTracker(self)

        # Initialize queue metrics tracker
        from core.queue.stats import QueueStatsTracker

        self.queue_stats = QueueStatsTracker()

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

        # Create system_settings table for cluster-wide settings (e.g. shared JWT keys)
        if self.use_postgres:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """
            )
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
                    prefix TEXT,
                    expiry_notification_sent INTEGER DEFAULT 0
                )
            """
            )
        else:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """
            )
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
                    prefix TEXT,
                    expiry_notification_sent INTEGER DEFAULT 0
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

        # Schema Guard: Ensure expiry_notification_sent exists (Migration)
        try:
            cursor.execute(
                "ALTER TABLE api_keys ADD COLUMN expiry_notification_sent INTEGER DEFAULT 0"
                if self.use_postgres
                else "ALTER TABLE api_keys ADD COLUMN expiry_notification_sent INTEGER DEFAULT 0"
            )
            conn.commit()
            print(
                "[startup] Database migration: Added expiry_notification_sent column to api_keys"
            )
        except Exception:
            conn.rollback()
            pass

        # Load or generate stable JWT signing key to avoid signature verification mismatch in multi-pod deployments
        import config

        has_env_key = bool(os.environ.get("JWT_PRIVATE_KEY")) or os.path.exists(
            "private.pem"
        )

        if not has_env_key:
            try:
                cursor.execute(
                    "SELECT value FROM system_settings WHERE key = %s"
                    if self.use_postgres
                    else "SELECT value FROM system_settings WHERE key = ?",
                    ("jwt_private_key",),
                )
                row = cursor.fetchone()
                if row:
                    db_key = row[0]
                    config._ephemeral_private_key_pem = db_key
                    print(
                        "[startup] Loaded cluster-wide JWT Private Key from system_settings table."
                    )
                else:
                    print("[startup] Generating stable cluster-wide JWT Private Key...")
                    from cryptography.hazmat.backends import default_backend
                    from cryptography.hazmat.primitives import serialization
                    from cryptography.hazmat.primitives.asymmetric import rsa

                    generated_key = rsa.generate_private_key(
                        public_exponent=65537,
                        key_size=2048,
                        backend=default_backend(),
                    )
                    pem_key = generated_key.private_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PrivateFormat.PKCS8,
                        encryption_algorithm=serialization.NoEncryption(),
                    ).decode("utf-8")

                    try:
                        cursor.execute(
                            "INSERT INTO system_settings (key, value) VALUES (%s, %s)"
                            if self.use_postgres
                            else "INSERT INTO system_settings (key, value) VALUES (?, ?)",
                            ("jwt_private_key", pem_key),
                        )
                        conn.commit()
                        config._ephemeral_private_key_pem = pem_key
                        print(
                            "[startup] Stored generated JWT Private Key in system_settings table."
                        )
                    except Exception as db_err:
                        # Another pod might have inserted it concurrently
                        conn.rollback()
                        cursor.execute(
                            "SELECT value FROM system_settings WHERE key = %s"
                            if self.use_postgres
                            else "SELECT value FROM system_settings WHERE key = ?",
                            ("jwt_private_key",),
                        )
                        fallback_row = cursor.fetchone()
                        if fallback_row:
                            config._ephemeral_private_key_pem = fallback_row[0]
                            print(
                                "[startup] Loaded concurrent JWT Private Key stored by parallel pod."
                            )
                        else:
                            config._ephemeral_private_key_pem = pem_key
                            print(
                                f"[startup] Database write failed, using local ephemeral key: {db_err}"
                            )
            except Exception as e:
                print(f"[startup] Stable JWT setup error: {e}")

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
