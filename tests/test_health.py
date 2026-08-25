"""Tests for health and basic endpoints."""

import pytest
from fastapi.testclient import TestClient


def test_health_check(client: TestClient):
    """Test the health check endpoint."""
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "reachable"


def test_root_404(client: TestClient):
    """Test that root returns 404."""
    response = client.get("/")
    assert response.status_code == 404