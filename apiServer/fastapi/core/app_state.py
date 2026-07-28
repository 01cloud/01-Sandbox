from __future__ import annotations

import asyncio
import datetime
import os

import psycopg2
import redis
from backends import GenericHTTPBackend, SandboxBackend
from config import opensandbox_base_url


class InstrumentedConnection:
    """Wrapper that increments/decrements active DB connection count gauge."""

    def __init__(self, conn):
        self._conn = conn
        self._closed = False
        from observability.metrics import db_connections_active

        db_connections_active.inc()

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name in ("_conn", "_closed"):
            super().__setattr__(name, value)
        else:
            setattr(self._conn, name, value)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._conn.close()
            finally:
                from observability.metrics import db_connections_active

                db_connections_active.dec()


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
        self.use_postgres = True
        self.use_redis = os.environ.get("REDIS_HOST") is not None
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
        pg_host = os.environ.get("PG_HOST")
        if not pg_host:
            raise RuntimeError(
                "CRITICAL: PostgreSQL environment variable 'PG_HOST' is missing. "
                "PostgreSQL is strictly required across all environments."
            )
        conn = psycopg2.connect(
            host=pg_host,
            port=os.environ.get("PG_PORT", "5432"),
            user=os.environ.get("PG_USER", "postgres"),
            password=os.environ.get("PG_PASSWORD", ""),
            dbname=os.environ.get("PG_DATABASE", "postgres"),
        )
        return InstrumentedConnection(conn)

    def init_db(self):
        import time

        retries = 30
        conn = None
        for i in range(retries):
            try:
                conn = self.get_db_conn()
                print(
                    "[startup] Successfully connected to PostgreSQL for database initialization."
                )
                break
            except Exception as e:
                if i < retries - 1:
                    print(
                        f"[startup] Waiting for PostgreSQL ({e}). Retrying in 2 seconds... ({i+1}/{retries})"
                    )
                    time.sleep(2)
                else:
                    print(
                        f"[startup] Crucial: Failed to connect to PostgreSQL after {retries} retries. Raising error."
                    )
                    raise e

        cursor = conn.cursor()

        # Create system_settings table for cluster-wide settings (e.g. shared JWT keys)
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
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_subscriptions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                backend_id TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                created_at TEXT,
                CONSTRAINT unique_user_backend UNIQUE (user_id, backend_id)
            )
        """
        )

        conn.commit()

        # Schema Guard: Ensure user_email exists (Migration)
        try:
            cursor.execute("ALTER TABLE api_keys ADD COLUMN user_email TEXT")
            conn.commit()
            print("[startup] Database migration: Added user_email column to api_keys")
        except Exception:
            conn.rollback()
            pass

        # Schema Guard: Ensure expiry_notification_sent exists (Migration)
        try:
            cursor.execute(
                "ALTER TABLE api_keys ADD COLUMN expiry_notification_sent INTEGER DEFAULT 0"
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
                    "SELECT value FROM system_settings WHERE key = %s",
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
                            "INSERT INTO system_settings (key, value) VALUES (%s, %s)",
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
                            "SELECT value FROM system_settings WHERE key = %s",
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
                "SELECT id FROM api_keys WHERE is_revoked = 0 AND expires_at > %s",
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
            query_find = "SELECT id FROM api_keys WHERE expires_at < %s"
            cursor.execute(query_find, (now_iso,))
            expired_ids = [row[0] for row in cursor.fetchall()]

            if expired_ids and state.use_redis:
                for eid in expired_ids:
                    state.redis_client.srem("active_api_keys", eid)
                print(f"[Janitor] Removed {len(expired_ids)} expired keys from Redis")

            # 2. Delete from Database
            query_del = "DELETE FROM api_keys WHERE expires_at < %s"
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
