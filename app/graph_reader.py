"""Read direct user app-role assignments from Microsoft Graph v1.0.

This implementation intentionally does NOT expand group assignments. It also
DOES NOT assert the Entra accountEnabled state: assigned users are considered
eligible for the POC unless their app-role assignment is removed. See README.
"""
from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlparse
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
LOG = logging.getLogger(__name__)


class DirectoryReadError(RuntimeError):
    """The upstream directory cannot provide a complete, reliable snapshot."""


class GraphReader:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(["GET", "POST"]),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def _access_token(self) -> str:
        """OAuth client-credentials flow. This app acts as itself, not as a user."""
        url = (
            f"https://login.microsoftonline.com/"
            f"{self.settings.tenant_id}/oauth2/v2.0/token"
        )
        try:
            response = self.session.post(url, data={
                "client_id": self.settings.graph_client_id,
                "client_secret": self.settings.graph_client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            }, timeout=20)
            response.raise_for_status()
            token = response.json().get("access_token")
            if not token:
                raise DirectoryReadError("Missing Graph access token")
            return token
        except (requests.RequestException, ValueError) as exc:
            # Do not log the request body, response, client secret or token.
            LOG.warning("Microsoft Entra token request failed (%s)", type(exc).__name__)
            raise DirectoryReadError("Microsoft Entra token acquisition failed") from exc

    def _get_json(self, url: str, token: str, params: dict | None = None) -> dict:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc.lower() != "graph.microsoft.com":
            raise DirectoryReadError("Unexpected Microsoft Graph nextLink URL")
        try:
            response = self.session.get(
                url,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                params=params, timeout=25,
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise DirectoryReadError("Invalid Microsoft Graph response")
            return result
        except (requests.RequestException, ValueError) as exc:
            LOG.warning("Microsoft Graph request failed (%s)", type(exc).__name__)
            raise DirectoryReadError("Microsoft Graph read failed") from exc

    def _get_all_pages(self, url: str, token: str) -> list[dict]:
        """Use @odata.nextLink; never silently omit assignments beyond page 1."""
        items: list[dict] = []
        visited: set[str] = set()
        while url:
            if url in visited:
                raise DirectoryReadError("Graph pagination cycle detected")
            visited.add(url)
            if len(visited) > 1000:
                raise DirectoryReadError("Graph pagination exceeded safe limit")
            page = self._get_json(url, token)
            chunk = page.get("value")
            if not isinstance(chunk, list):
                raise DirectoryReadError("Invalid Graph collection response")
            items.extend(chunk)
            url = page.get("@odata.nextLink")
            if url and not isinstance(url, str):
                raise DirectoryReadError("Invalid Graph nextLink")
        return items

    def list_direct_users(self) -> list[dict]:
        """Return only directly assigned Users and their recognized, enabled app roles."""
        token = self._access_token()
        sp_id = self.settings.edge_app_sp_object_id
        principal = self._get_json(
            f"{GRAPH_BASE}/servicePrincipals/{sp_id}", token,
            params={"$select": "id,appRoles"},
        )
        roles = {
            str(role["id"]).lower(): role["value"]
            for role in principal.get("appRoles", [])
            if role.get("isEnabled") and role.get("id") and role.get("value")
            and "User" in role.get("allowedMemberTypes", [])
        }
        assignments = self._get_all_pages(
            f"{GRAPH_BASE}/servicePrincipals/{sp_id}/appRoleAssignedTo", token
        )
        role_ids_by_user: dict[str, set[str]] = defaultdict(set)
        for assignment in assignments:
            if assignment.get("principalType") != "User":
                continue  # This POC explicitly does not expand group assignments.
            user_id = assignment.get("principalId")
            role = roles.get(str(assignment.get("appRoleId", "")).lower())
            if user_id and role:
                role_ids_by_user[str(user_id)].add(role)

        users: list[dict] = []
        for user_id, role_names in sorted(role_ids_by_user.items()):
            profile = self._get_json(
                f"{GRAPH_BASE}/users/{user_id}", token,
                params={"$select": "id,displayName,userPrincipalName,mail"},
            )
            if str(profile.get("id", "")).lower() != user_id.lower():
                raise DirectoryReadError("Unexpected Graph user identity")
            username = profile.get("userPrincipalName") or profile.get("mail")
            if not username:
                raise DirectoryReadError("Assigned user lacks a resolvable username")
            users.append({
                "entraObjectId": user_id,
                "username": username,
                "displayName": profile.get("displayName") or username,
                # POC schema compatibility only: this is NOT Graph accountEnabled.
                # Access revocation is by App Role assignment removal.
                "enabled": True,
                "roles": sorted(role_names),
            })
        users.sort(key=lambda x: x["username"].lower())
        return users
