import asyncio
import datetime
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Inject current directory into python path to load core modules correctly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

# Load from absolute path of fastapi directory
load_dotenv(
    dotenv_path=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
    ),
    override=True,
)

from core.app_state import state
from services.email import send_expiry_email
from services.expiry_checker import check_expiring_keys_task


@pytest.fixture(autouse=True)
def setup_db():
    # Make sure database is initialized and migrated
    state.init_db()
    # Clean up api_keys for a fresh state
    conn = state.get_db_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM api_keys")
    conn.commit()
    conn.close()


def test_database_migration():
    """Verify that the database migration added the expiry_notification_sent column."""
    conn = state.get_db_conn()
    cursor = conn.cursor()
    try:
        # If the column exists, this query succeeds
        cursor.execute("SELECT expiry_notification_sent FROM api_keys LIMIT 1")
        cursor.fetchall()
        has_column = True
    except Exception:
        has_column = False
    finally:
        conn.close()

    assert has_column, "expiry_notification_sent column should exist in database table"


@pytest.mark.asyncio
async def test_expiry_checker_detection():
    """Verify that the checker enqueues only active keys expiring within the warning threshold."""
    now = datetime.datetime.now(datetime.UTC)

    # 1. Expires in 2.5 minutes, TTL 8 minutes (Should be notified: TTL <= 10, lead time 3m)
    key_a = {
        "id": "key_a_jti",
        "name": "Key A",
        "backend": "Z1_SANDBOX",
        "user_id": "user_a",
        "user_email": "user_a@example.com",
        "created_at": (now - datetime.timedelta(minutes=5.5)).isoformat(),
        "expires_at": (now + datetime.timedelta(minutes=2.5)).isoformat(),
        "prefix": "ci_keya",
        "expiry_notification_sent": 0,
    }

    # 2. Expires in 4 minutes, TTL 8 minutes (Should NOT be notified: TTL <= 10, lead time 3m, 4m remaining)
    key_b = {
        "id": "key_b_jti",
        "name": "Key B",
        "backend": "Z1_SANDBOX",
        "user_id": "user_b",
        "user_email": "user_b@example.com",
        "created_at": (now - datetime.timedelta(minutes=4)).isoformat(),
        "expires_at": (now + datetime.timedelta(minutes=4)).isoformat(),
        "prefix": "ci_keyb",
        "expiry_notification_sent": 0,
    }

    # 3. Already expired, TTL 8 minutes (Should NOT be notified)
    key_c = {
        "id": "key_c_jti",
        "name": "Key C",
        "backend": "Z1_SANDBOX",
        "user_id": "user_c",
        "user_email": "user_c@example.com",
        "created_at": (now - datetime.timedelta(minutes=10)).isoformat(),
        "expires_at": (now - datetime.timedelta(minutes=2)).isoformat(),
        "prefix": "ci_keyc",
        "expiry_notification_sent": 0,
    }

    # 4. Expires in 4 minutes, TTL 2 hours (Should be notified: TTL > 10, lead time 5m, 4m remaining)
    key_d = {
        "id": "key_d_jti",
        "name": "Key D",
        "backend": "Z1_SANDBOX",
        "user_id": "user_d",
        "user_email": "user_d@example.com",
        "created_at": (now - datetime.timedelta(hours=1, minutes=56)).isoformat(),
        "expires_at": (now + datetime.timedelta(minutes=4)).isoformat(),
        "prefix": "ci_keyd",
        "expiry_notification_sent": 0,
    }

    # 5. Expires in 2.5 minutes, TTL 8 minutes, but no email address configured (Should NOT be notified)
    key_e = {
        "id": "key_e_jti",
        "name": "Key E",
        "backend": "Z1_SANDBOX",
        "user_id": "user_e",
        "user_email": None,
        "created_at": (now - datetime.timedelta(minutes=5.5)).isoformat(),
        "expires_at": (now + datetime.timedelta(minutes=2.5)).isoformat(),
        "prefix": "ci_keye",
        "expiry_notification_sent": 0,
    }

    # 6. Expires in 2.5 minutes, TTL 8 minutes, but already notified (Should NOT be notified)
    key_f = {
        "id": "key_f_jti",
        "name": "Key F",
        "backend": "Z1_SANDBOX",
        "user_id": "user_f",
        "user_email": "user_f@example.com",
        "created_at": (now - datetime.timedelta(minutes=5.5)).isoformat(),
        "expires_at": (now + datetime.timedelta(minutes=2.5)).isoformat(),
        "prefix": "ci_keyf",
        "expiry_notification_sent": 1,
    }

    conn = state.get_db_conn()
    cursor = conn.cursor()
    query = "INSERT INTO api_keys (id, name, backend, user_id, user_email, created_at, expires_at, prefix, expiry_notification_sent) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
    for key in [key_a, key_b, key_c, key_d, key_e, key_f]:
        cursor.execute(
            query,
            (
                key["id"],
                key["name"],
                key["backend"],
                key["user_id"],
                key["user_email"],
                key["created_at"],
                key["expires_at"],
                key["prefix"],
                key["expiry_notification_sent"],
            ),
        )
    conn.commit()
    conn.close()

    published_messages = []

    async def mock_publish(routing_key, payload):
        published_messages.append((routing_key, payload))

    # Mock asyncio.sleep to bypass startup delay (5s) but break the infinite loop on the second sleep call
    sleep_count = 0

    async def mock_sleep(seconds):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count > 1:
            raise asyncio.CancelledError()

    # Override environments to test checker behavior quickly
    os.environ["KEY_EXPIRATION_CHECK_INTERVAL_SECONDS"] = "0.1"

    with patch("core.queue.publisher.publish", side_effect=mock_publish):
        with patch("asyncio.sleep", side_effect=mock_sleep):
            try:
                await check_expiring_keys_task(state)
            except asyncio.CancelledError:
                pass

    # Verify enqueued jobs
    assert len(published_messages) == 2

    # Sort messages by key ID for deterministic assertion
    published_messages.sort(key=lambda x: x[1]["key_id"])

    routing_key_1, payload_1 = published_messages[0]
    assert routing_key_1 == "notification.email"
    assert payload_1["key_id"] == "key_a_jti"
    assert payload_1["recipient"] == "user_a@example.com"
    assert payload_1["key_name"] == "Key A"
    assert payload_1["prefix"] == "ci_keya"

    routing_key_2, payload_2 = published_messages[1]
    assert routing_key_2 == "notification.email"
    assert payload_2["key_id"] == "key_d_jti"
    assert payload_2["recipient"] == "user_d@example.com"
    assert payload_2["key_name"] == "Key D"
    assert payload_2["prefix"] == "ci_keyd"

    # Verify database was updated
    conn = state.get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, expiry_notification_sent FROM api_keys ORDER BY id")
    rows = cursor.fetchall()
    conn.close()

    db_notified = {row[0]: row[1] for row in rows}
    assert db_notified["key_a_jti"] == 1
    assert db_notified["key_b_jti"] == 0
    assert db_notified["key_c_jti"] == 0
    assert db_notified["key_d_jti"] == 1
    assert db_notified["key_e_jti"] == 0
    assert db_notified["key_f_jti"] == 1


@pytest.mark.asyncio
async def test_send_expiry_email_mock_mode():
    """Verify email sending in Mock Mode (no SendGrid key set) logs output and succeeds."""
    payload = {
        "job_id": "email_job_123",
        "recipient": "user@example.com",
        "key_id": "some-jti",
        "key_name": "Test Key",
        "expires_at": "2026-06-16T12:00:00Z",
        "prefix": "ci_test",
    }

    with patch.dict(os.environ, {}, clear=True):
        # Should complete without error
        await send_expiry_email(payload)


@pytest.mark.asyncio
async def test_send_expiry_email_sendgrid_success():
    """Verify SendGrid HTTP API integration handles success response."""
    payload = {
        "job_id": "email_job_success",
        "recipient": "lamakamal89@gmail.com",
        "key_id": "some-jti",
        "key_name": "Test Key",
        "expires_at": "2026-06-16T12:00:00Z",
        "prefix": "ci_test",
    }

    mock_response = MagicMock()
    mock_response.status_code = 202
    mock_response.text = "Accepted"

    env_vars = {
        "SENDGRID_API_KEY": "dummy-sendgrid-key-for-testing",
        "SENDGRID_FROM_EMAIL": "test-sender@01sandbox.com",
    }

    with patch.dict(os.environ, env_vars):
        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            await send_expiry_email(payload)

            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            assert args[0] == "https://api.sendgrid.com/v3/mail/send"
            assert (
                kwargs["headers"]["Authorization"]
                == "Bearer dummy-sendgrid-key-for-testing"
            )
            assert (
                kwargs["json"]["personalizations"][0]["to"][0]["email"]
                == "lamakamal89@gmail.com"
            )
            assert kwargs["json"]["from"]["email"] == "test-sender@01sandbox.com"


@pytest.mark.asyncio
async def test_send_expiry_email_sendgrid_failure():
    """Verify SendGrid HTTP API failure raises a RuntimeError to prompt RabbitMQ retry."""
    payload = {
        "job_id": "email_job_fail",
        "recipient": "lamakamal89@gmail.com",
        "key_id": "some-jti",
        "key_name": "Test Key",
        "expires_at": "2026-06-16T12:00:00Z",
        "prefix": "ci_test",
    }

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized API key"

    env_vars = {
        "SENDGRID_API_KEY": "dummy-sendgrid-key-for-testing",
        "SENDGRID_FROM_EMAIL": "test-sender@01sandbox.com",
    }

    with patch.dict(os.environ, env_vars):
        with patch("httpx.AsyncClient.post", return_value=mock_response):
            with pytest.raises(RuntimeError) as exc_info:
                await send_expiry_email(payload)

            assert "SendGrid API failed with status 401" in str(exc_info.value)


@pytest.mark.asyncio
async def test_send_real_expiry_email():
    """
    Actually sends a real email to lamakamal89@gmail.com using SendGrid Web API (no mock).
    Use this to verify your API key and sender identity verification.
    """
    payload = {
        "job_id": "email_real_test",
        "recipient": "lamakamal89@gmail.com",
        "key_id": "real-test-jti",
        "key_name": "Real Test Key",
        "created_at": "2026-06-15T12:00:00Z",
        "expires_at": "2026-06-16T12:00:00Z",
        "prefix": "ci_real",
    }

    env_vars = {
        "SENDGRID_API_KEY": os.environ.get("SENDGRID_API_KEY", ""),
        # NOTE: Make sure this email is verified in your SendGrid dashboard as a Sender Identity
        "SENDGRID_FROM_EMAIL": os.environ.get(
            "SENDGRID_FROM_EMAIL", "sandbox@01security.com"
        ),
    }

    with patch.dict(os.environ, env_vars):
        # We run the real send_expiry_email call here without patching the HTTP request.
        # If the sender identity isn't verified in SendGrid, this will raise a 403 / 400 error.
        try:
            await send_expiry_email(payload)
            print(
                "\n[SUCCESS] Real test email successfully sent to lamakamal89@gmail.com!"
            )
        except Exception as e:
            print(f"\n[FAILURE] Failed to send real test email: {e}")
            raise e


@pytest.mark.asyncio
async def test_send_expiry_email_body_formatting():
    """Verify that send_expiry_email correctly calculates the lead time (n) and formats the body with EST."""
    # Scenario 1: TTL = 8 minutes (should trigger 3 minutes lead time)
    payload_3m = {
        "job_id": "email_job_3m",
        "recipient": "user@example.com",
        "key_id": "some-jti-3m",
        "key_name": "Test Key 3M",
        "created_at": "2026-06-16T12:00:00Z",
        "expires_at": "2026-06-16T12:08:00Z",
        "prefix": "ci_test",
    }

    # Scenario 2: TTL = 2 hours (should trigger 1 hour lead time)
    payload_1h = {
        "job_id": "email_job_1h",
        "recipient": "user@example.com",
        "key_id": "some-jti-1h",
        "key_name": "Test Key 1H",
        "created_at": "2026-06-16T12:00:00Z",
        "expires_at": "2026-06-16T14:00:00Z",
        "prefix": "ci_test",
    }

    # Scenario 3: TTL = 10 days (should trigger 24 hours lead time)
    payload_24h = {
        "job_id": "email_job_24h",
        "recipient": "user@example.com",
        "key_id": "some-jti-24h",
        "key_name": "Test Key 24H",
        "created_at": "2026-06-16T12:00:00Z",
        "expires_at": "2026-06-26T12:00:00Z",
        "prefix": "ci_test",
    }

    mock_response = MagicMock()
    mock_response.status_code = 202
    mock_response.text = "Accepted"

    env_vars = {
        "SENDGRID_API_KEY": "dummy-key",
        "SENDGRID_FROM_EMAIL": "test@example.com",
    }

    with patch.dict(os.environ, env_vars):
        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            await send_expiry_email(payload_3m)
            args_3m, kwargs_3m = mock_post.call_args
            body_3m = kwargs_3m["json"]["content"][0]["value"]
            assert "approaching expiration in 3 minutes" in body_3m
            assert "16/06/2026 08:08 AM EST" in body_3m

        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            await send_expiry_email(payload_1h)
            args_1h, kwargs_1h = mock_post.call_args
            body_1h = kwargs_1h["json"]["content"][0]["value"]
            assert "approaching expiration in 1 hour" in body_1h
            assert "16/06/2026 10:00 AM EST" in body_1h

        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            await send_expiry_email(payload_24h)
            args_24h, kwargs_24h = mock_post.call_args
            body_24h = kwargs_24h["json"]["content"][0]["value"]
            assert "approaching expiration in 24 hours" in body_24h
            assert "26/06/2026 08:00 AM EST" in body_24h
