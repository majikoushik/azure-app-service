"""Offline tests: no Azure subscription or Graph credentials needed."""
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.graph_reader import DirectoryReadError
from main import app, get_graph_reader


class StubGraph:
    def list_direct_users(self):
        return [{
            "entraObjectId": "11111111-2222-3333-4444-555555555555",
            "username": "operator01@example.onmicrosoft.com",
            "displayName": "Operator 01",
            "enabled": True,
            "roles": ["Operator"],
        }]


class BrokenGraph:
    def list_direct_users(self):
        raise DirectoryReadError("simulated upstream failure")


@pytest.fixture(autouse=True)
def configured_app():
    settings = Settings(
        tenant_id="11111111-1111-1111-1111-111111111111",
        graph_client_id="22222222-2222-2222-2222-222222222222",
        graph_client_secret="test-only-secret",
        edge_app_sp_object_id="33333333-3333-3333-3333-333333333333",
        allowed_device_ids=frozenset({"WSL-POC-001"}),
        cache_valid_hours=168,
        api_key="testing-only-not-for-production-1234567890",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_graph_reader] = lambda: StubGraph()
    yield
    app.dependency_overrides.clear()


def test_health_public():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_snapshot_rejects_missing_key():
    response = TestClient(app).get("/api/device-access/WSL-POC-001")
    assert response.status_code == 401


def test_snapshot_rejects_bad_key():
    response = TestClient(app).get("/api/device-access/WSL-POC-001", headers={"x-functions-key": "wrong"})
    assert response.status_code == 401


def test_rejects_other_device():
    response = TestClient(app).get("/api/device-access/WSL-POC-999", headers={"x-functions-key": "testing-only-not-for-production-1234567890"})
    assert response.status_code == 403


def test_snapshot_shape_and_role():
    response = TestClient(app).get("/api/device-access/WSL-POC-001", headers={"x-functions-key": "testing-only-not-for-production-1234567890"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["schemaVersion"] == 1
    assert payload["source"]["assignmentMode"] == "direct-users-only"
    assert payload["users"][0]["roles"] == ["Operator"]
    assert payload["deviceId"] == "WSL-POC-001"
    assert payload["validUntilUtc"] > payload["generatedUtc"]
    assert response.headers["cache-control"].startswith("no-store")


def test_graph_failure_returns_502_not_partial_snapshot():
    app.dependency_overrides[get_graph_reader] = lambda: BrokenGraph()
    response = TestClient(app).get("/api/device-access/WSL-POC-001", headers={"x-functions-key": "testing-only-not-for-production-1234567890"})
    assert response.status_code == 502
    assert "simulated" not in response.text
