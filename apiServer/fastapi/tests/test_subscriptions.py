from unittest.mock import MagicMock

import pytest
from core.app_state import state
from fastapi import HTTPException
from proxy.router import check_backend_subscription


@pytest.fixture(autouse=True)
def setup_db():
    state.init_db()
    conn = state.get_db_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_subscriptions")
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_check_backend_subscription_z1sandbox():
    # Z1_SANDBOX (01 Sandbox) should always be allowed
    user_data = {"sub": "user_123"}
    # Should not raise any exception
    await check_backend_subscription("Z1_SANDBOX", user_data, state)
    await check_backend_subscription("01sbx", user_data, state)
    await check_backend_subscription("z1sandbox", user_data, state)


@pytest.mark.asyncio
async def test_check_backend_subscription_unsubscribed():
    user_data = {"sub": "user_123"}
    # AWS_VPC_SANDBOX is not subscribed
    with pytest.raises(HTTPException) as exc_info:
        await check_backend_subscription("AWS_VPC_SANDBOX", user_data, state)
    assert exc_info.value.status_code == 403
    assert "Subscription to backend" in exc_info.value.detail


@pytest.mark.asyncio
async def test_check_backend_subscription_subscribed():
    user_data = {"sub": "user_123"}
    # Subscribe the user
    conn = state.get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO user_subscriptions (id, user_id, backend_id, status) VALUES (%s, %s, %s, %s)",
        ("sub_user123_AWS_VPC_SANDBOX", "user_123", "AWS_VPC_SANDBOX", "active"),
    )
    conn.commit()
    conn.close()

    # Now it should pass without raising
    await check_backend_subscription("AWS_VPC_SANDBOX", user_data, state)


@pytest.mark.asyncio
async def test_check_backend_subscription_api_key_scoping():
    # Test that scoped API keys are restricted to their scoped backend
    user_data = {"sub": "user_123", "backend": "Z1_SANDBOX"}

    # Allowed because requested is Z1_SANDBOX and key is scoped to Z1_SANDBOX
    await check_backend_subscription("Z1_SANDBOX", user_data, state)

    # Forbidden because requested is AWS_VPC_SANDBOX but key is scoped to Z1_SANDBOX
    with pytest.raises(HTTPException) as exc_info:
        await check_backend_subscription("AWS_VPC_SANDBOX", user_data, state)
    assert exc_info.value.status_code == 403
    assert "API Key is scoped to backend" in exc_info.value.detail
