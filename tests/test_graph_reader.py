"""Graph role mapping and pagination tests without live Azure calls."""
from app.graph_reader import GraphReader, DirectoryReadError
from app.config import Settings


def settings():
    return Settings(
        tenant_id="dummy", graph_client_id="dummy", graph_client_secret="dummy",
        edge_app_sp_object_id="sp-id", allowed_device_ids=frozenset({"WSL-POC-001"}),
        cache_valid_hours=168, api_key="A" * 40,
    )


def test_multiple_role_assignments_and_group_filter(monkeypatch):
    reader = GraphReader(settings())
    monkeypatch.setattr(reader, "_access_token", lambda: "fake-token")
    def get_json(url, token, params=None):
        if url.endswith("/servicePrincipals/sp-id"):
            return {"id": "sp-id", "appRoles": [
                {"id": "r1", "value": "Operator", "isEnabled": True, "allowedMemberTypes": ["User"]},
                {"id": "r2", "value": "Supervisor", "isEnabled": True, "allowedMemberTypes": ["User"]},
                {"id": "r3", "value": "DisabledRole", "isEnabled": False, "allowedMemberTypes": ["User"]},
            ]}
        if url.endswith("/appRoleAssignedTo"):
            return {"value": [
                {"principalType": "User", "principalId": "U1", "appRoleId": "r1"},
                {"principalType": "Group", "principalId": "G1", "appRoleId": "r2"},
            ], "@odata.nextLink": "https://graph.microsoft.com/v1.0/paged"}
        if url.endswith("/paged"):
            return {"value": [
                {"principalType": "User", "principalId": "U1", "appRoleId": "r2"},
                {"principalType": "User", "principalId": "U2", "appRoleId": "r3"},
            ]}
        if url.endswith("/users/U1"):
            return {"id": "U1", "userPrincipalName": "person@tenant.test", "displayName": "Person"}
        raise AssertionError(f"unexpected mocked URL {url}")
    monkeypatch.setattr(reader, "_get_json", get_json)
    users = reader.list_direct_users()
    assert len(users) == 1
    assert users[0]["roles"] == ["Operator", "Supervisor"]
    assert users[0]["username"] == "person@tenant.test"


def test_reject_untrusted_pagination_url(monkeypatch):
    reader = GraphReader(settings())
    try:
        reader._get_json("https://attacker.example/steal", "secret-token")
    except DirectoryReadError:
        pass
    else:
        raise AssertionError("Untrusted host should be rejected")
