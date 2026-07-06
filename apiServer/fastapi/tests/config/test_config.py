"""
test_config.py
==============

This file contains beginner-friendly unit tests for the configuration module (`config.py`).
It tests key configuration parsers and fallback logic without using database connections.

Key concepts used:
  - `monkeypatch`: A built-in pytest fixture used to safely modify or delete environment
    variables during a test. Pytest automatically restores the variables afterward.
"""

import pytest
from config import (
    backend_mappings,
    gateway_secret_config,
    opensandbox_headers,
    opensandbox_route_prefix,
)

# ─────────────────────────────────────────────────────────────────────────────
# 1. Tests for opensandbox_route_prefix()
# ─────────────────────────────────────────────────────────────────────────────


def test_route_prefix_default(monkeypatch):
    """
    SCENARIO: OPENSANDBOX_ROUTE_PREFIX is not set in the environment.
    EXPECTATION: Falls back to the default value "/api/v1/01sbx".
    """
    # Arrange: Ensure the env var is deleted before running the test
    monkeypatch.delenv("OPENSANDBOX_ROUTE_PREFIX", raising=False)

    # Act: Call the function under test
    result = opensandbox_route_prefix()

    # Assert: Verify the fallback is correct
    assert result == "/api/v1/01sbx"


def test_route_prefix_custom(monkeypatch):
    """
    SCENARIO: OPENSANDBOX_ROUTE_PREFIX is set to a custom path with a trailing slash.
    EXPECTATION: Returns the path with the trailing slash stripped.
    """
    # Arrange: Set the environment variable to a custom value
    monkeypatch.setenv("OPENSANDBOX_ROUTE_PREFIX", "/custom/api/v2/")

    # Act
    result = opensandbox_route_prefix()

    # Assert: Slashes at the end should be stripped automatically (.rstrip("/"))
    assert result == "/custom/api/v2"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Tests for opensandbox_headers()
# ─────────────────────────────────────────────────────────────────────────────


def test_headers_default(monkeypatch):
    """
    SCENARIO: OPENSANDBOX_API_KEY is not configured.
    EXPECTATION: Returns headers with the fallback key "your-secure-api-key".
    """
    # Arrange
    monkeypatch.delenv("OPENSANDBOX_API_KEY", raising=False)

    # Act
    headers = opensandbox_headers()

    # Assert
    assert headers["Content-Type"] == "application/json"
    assert headers["OPEN-SANDBOX-API-KEY"] == "your-secure-api-key"


def test_headers_custom(monkeypatch):
    """
    SCENARIO: OPENSANDBOX_API_KEY is configured with a secret key.
    EXPECTATION: Returns headers with the configured key.
    """
    # Arrange
    monkeypatch.setenv("OPENSANDBOX_API_KEY", "prod-secret-api-key-123")

    # Act
    headers = opensandbox_headers()

    # Assert
    assert headers["OPEN-SANDBOX-API-KEY"] == "prod-secret-api-key-123"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Tests for gateway_secret_config()
# ─────────────────────────────────────────────────────────────────────────────


def test_gateway_secret_default(monkeypatch):
    """
    SCENARIO: No gateway environment variables are set.
    EXPECTATION: Returns a dictionary populated with standard default values.
    """
    # Arrange: Clear potential environment overrides
    monkeypatch.delenv("GATEWAY_SECRET_NAME", raising=False)
    monkeypatch.delenv("GATEWAY_SECRET_NAMESPACE", raising=False)
    monkeypatch.delenv("GATEWAY_SECRET_KEY", raising=False)

    # Act
    config = gateway_secret_config()

    # Assert: Verify default values
    assert config["name"] == "apikey"
    assert config["namespace"] == "agentgateway-system"
    assert config["key"] == "api-key"


def test_gateway_secret_custom(monkeypatch):
    """
    SCENARIO: Gateway environment variables are customized.
    EXPECTATION: Returns a dictionary populated with the custom values.
    """
    # Arrange
    monkeypatch.setenv("GATEWAY_SECRET_NAME", "custom-sec")
    monkeypatch.setenv("GATEWAY_SECRET_NAMESPACE", "custom-ns")
    monkeypatch.setenv("GATEWAY_SECRET_KEY", "custom-key")

    # Act
    config = gateway_secret_config()

    # Assert: Verify custom values
    assert config["name"] == "custom-sec"
    assert config["namespace"] == "custom-ns"
    assert config["key"] == "custom-key"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Tests for backend_mappings()
# ─────────────────────────────────────────────────────────────────────────────


def test_backend_mappings_default(monkeypatch):
    """
    SCENARIO: Default state with no JSON extension env var set.
    EXPECTATION: Returns mappings for z1sandbox and opensandbox.
    """
    # Arrange
    monkeypatch.delenv("BACKEND_MAPPINGS_JSON", raising=False)
    monkeypatch.setenv("BACKEND_URL_Z1SANDBOX", "http://z1-url")
    monkeypatch.setenv("BACKEND_URL_OPENSANDBOX", "http://os-url")

    # Act
    mappings = backend_mappings()

    # Assert
    assert mappings["z1sandbox"] == "http://z1-url"
    assert mappings["opensandbox"] == "http://os-url"


def test_backend_mappings_json_extend(monkeypatch):
    """
    SCENARIO: A valid JSON string is passed via BACKEND_MAPPINGS_JSON.
    EXPECTATION: Parses the JSON and extends/overrides the dictionary.
    """
    # Arrange: Set default url environment and add a JSON string custom mapping
    monkeypatch.setenv("BACKEND_URL_Z1SANDBOX", "http://default-z1")
    monkeypatch.setenv(
        "BACKEND_MAPPINGS_JSON",
        '{"z1sandbox": "http://override-z1", "new-custom-backend": "http://custom-url"}',
    )

    # Act
    mappings = backend_mappings()

    # Assert:
    # 1. z1sandbox should be overridden by the JSON string
    assert mappings["z1sandbox"] == "http://override-z1"
    # 2. new-custom-backend should be successfully added
    assert mappings["new-custom-backend"] == "http://custom-url"


def test_backend_mappings_invalid_json(monkeypatch):
    """
    SCENARIO: An invalid JSON string is passed via BACKEND_MAPPINGS_JSON.
    EXPECTATION: Catches the JSONDecodeError, prints a warning, and still returns the default mappings.
    """
    # Arrange
    monkeypatch.setenv("BACKEND_URL_Z1SANDBOX", "http://default-z1")
    # Setting an invalid json payload (missing double quotes / wrong structure)
    monkeypatch.setenv("BACKEND_MAPPINGS_JSON", "{invalid-json-payload}")

    # Act & Assert: Should not crash or raise an error
    mappings = backend_mappings()

    # Defaults should still exist unharmed
    assert mappings["z1sandbox"] == "http://default-z1"
