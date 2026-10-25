"""
tests/test_health.py
────────────────────
Automated tests for GET /api/health.

Run with:
    pytest -v
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

# Use the synchronous TestClient (no async needed for these tests).
client = TestClient(app)


class TestHealthEndpoint:
    """Tests for GET /api/health."""

    def test_status_code_is_200(self):
        response = client.get("/api/health")
        assert response.status_code == 200

    def test_response_is_json(self):
        response = client.get("/api/health")
        assert response.headers["content-type"].startswith("application/json")

    def test_status_field_is_ok(self):
        data = client.get("/api/health").json()
        assert data["status"] == "ok"

    def test_version_field_present(self):
        data = client.get("/api/health").json()
        assert "version" in data
        assert isinstance(data["version"], str)
        assert data["version"]  # not empty

    def test_environment_field_present(self):
        data = client.get("/api/health").json()
        assert "environment" in data

    def test_response_schema(self):
        """Verify the full shape of the response matches HealthResponse."""
        data = client.get("/api/health").json()
        assert set(data.keys()) == {"status", "version", "environment"}


class TestNotFoundHandler:
    """Verify that unknown paths return the consistent error envelope."""

    def test_404_returns_error_envelope(self):
        response = client.get("/api/does-not-exist")
        assert response.status_code == 404
        data = response.json()
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert data["error"]["code"] == "NOT_FOUND"
