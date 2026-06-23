import asyncio
import datetime
import os
import sqlite3
import uuid


async def check_expiring_keys_task(app_state) -> None:
    """
    Background loop that periodically checks the database for API keys approaching expiration,
    enqueues an email notification job to RabbitMQ for each key, and updates the database.
    """
    # Wait for a brief moment at startup to ensure dependencies (like database, rabbitmq/ redis) are ready
    await asyncio.sleep(5)

    print("[Notifier] Expiration notifier background task started.")

    while True:
        try:
            check_interval = float(
                os.environ.get("KEY_EXPIRATION_CHECK_INTERVAL_SECONDS", "10.0")
            )
            now = datetime.datetime.now(datetime.UTC)

            conn = app_state.get_db_conn()
            if app_state.use_postgres:
                from psycopg2.extras import RealDictCursor

                cursor = conn.cursor(cursor_factory=RealDictCursor)
                query = """
                    SELECT id, name, user_id, user_email, created_at, expires_at, prefix
                    FROM api_keys
                    WHERE is_revoked = 0
                      AND expiry_notification_sent = 0
                """
            else:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                query = """
                    SELECT id, name, user_id, user_email, created_at, expires_at, prefix
                    FROM api_keys
                    WHERE is_revoked = 0
                      AND expiry_notification_sent = 0
                """

            cursor.execute(query)
            rows = cursor.fetchall()

            if rows:
                from core.queue.publisher import publish

                for row in rows:
                    key_id = row["id"]
                    recipient = row["user_email"]
                    if not recipient:
                        continue

                    # Parse timestamps timezone-aware safely
                    val_c = row["created_at"]
                    if isinstance(val_c, str):
                        created_at = datetime.datetime.fromisoformat(
                            val_c.replace("Z", "+00:00")
                        ).astimezone(datetime.UTC)
                    else:
                        created_at = val_c.astimezone(datetime.UTC)

                    val_e = row["expires_at"]
                    if isinstance(val_e, str):
                        expires_at = datetime.datetime.fromisoformat(
                            val_e.replace("Z", "+00:00")
                        ).astimezone(datetime.UTC)
                    else:
                        expires_at = val_e.astimezone(datetime.UTC)

                    # Calculate total TTL and remaining time in minutes
                    ttl_minutes = (expires_at - created_at).total_seconds() / 60.0
                    remaining_minutes = (expires_at - now).total_seconds() / 60.0

                    # Dynamic warning lead time based on the TTL:
                    # - TTL <= 10 minutes -> warn 3 minutes before expiration (keeps unit tests passing)
                    # - 10 min < TTL <= 1 hour -> warn 10 minutes before expiration
                    # - 1 hour < TTL <= 24 hours -> warn 1 hour before expiration
                    # - 24 hours < TTL <= 7 days -> warn 12 hours before expiration
                    # - TTL > 7 days -> warn 24 hours before expiration
                    if ttl_minutes <= 10.0:
                        lead_time_minutes = 3.0
                    elif ttl_minutes <= 60.0:
                        lead_time_minutes = 10.0
                    elif ttl_minutes <= 1440.0:
                        lead_time_minutes = 60.0
                    elif ttl_minutes <= 10080.0:
                        lead_time_minutes = 720.0
                    else:
                        lead_time_minutes = 1440.0

                    # Notify if key is active, not expired, and within the lead time window
                    if 0 < remaining_minutes <= lead_time_minutes:
                        # Publish notification job to RabbitMQ
                        job_id = f"email_{uuid.uuid4()}"
                        payload = {
                            "job_id": job_id,
                            "recipient": recipient,
                            "key_id": key_id,
                            "key_name": row["name"],
                            "expires_at": row["expires_at"],
                            "prefix": row["prefix"],
                        }

                        try:
                            # Publish to RabbitMQ
                            await publish("notification.email", payload)

                            # Update database that notification is enqueued/sent
                            update_query = (
                                "UPDATE api_keys SET expiry_notification_sent = 1 WHERE id = %s"
                                if app_state.use_postgres
                                else "UPDATE api_keys SET expiry_notification_sent = 1 WHERE id = ?"
                            )
                            cursor.execute(update_query, (key_id,))
                            conn.commit()
                            print(
                                f"[Notifier] Enqueued dynamic notification {job_id[:8]} for key {key_id[:8]} "
                                f"({remaining_minutes:.1f} mins remaining, TTL {ttl_minutes:.1f} mins) to {recipient}"
                            )

                        except Exception as pub_err:
                            print(
                                f"[Notifier] Failed to publish notification for key {key_id[:8]}: {pub_err}"
                            )
                            conn.rollback()

            conn.close()

        except Exception as e:
            print(f"[Notifier] Error in check_expiring_keys_task loop: {str(e)}")

        await asyncio.sleep(check_interval)
