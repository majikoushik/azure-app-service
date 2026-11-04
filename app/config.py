"""Read configuration from App Service Environment variables (never from GitHub)."""
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    tenant_id: str
    graph_client_id: str
    graph_client_secret: str
    edge_app_sp_object_id: str
    allowed_device_ids: frozenset[str]
    cache_valid_hours: int
    api_key: str

    @classmethod
    def from_env(cls) -> "Settings":
        raw_hours = os.getenv("RBAC_CACHE_VALID_HOURS", "168").strip()
        try:
            hours = int(raw_hours)
        except ValueError as exc:
            raise ValueError("RBAC_CACHE_VALID_HOURS must be an integer") from exc
        if not 1 <= hours <= 720:
            raise ValueError("RBAC_CACHE_VALID_HOURS must be between 1 and 720")

        ids = frozenset(
            value.strip() for value in os.getenv("ALLOWED_DEVICE_IDS", "").split(",")
            if value.strip()
        )
        return cls(
            tenant_id=os.getenv("ENTRA_TENANT_ID", "").strip(),
            graph_client_id=os.getenv("GRAPH_CLIENT_ID", "").strip(),
            graph_client_secret=os.getenv("GRAPH_CLIENT_SECRET", "").strip(),
            edge_app_sp_object_id=os.getenv("EDGE_APP_SP_OBJECT_ID", "").strip(),
            allowed_device_ids=ids,
            cache_valid_hours=hours,
            api_key=os.getenv("RBAC_API_KEY", ""),
        )

    def missing_or_weak(self) -> list[str]:
        missing = [
            name for name, value in {
                "ENTRA_TENANT_ID": self.tenant_id,
                "GRAPH_CLIENT_ID": self.graph_client_id,
                "GRAPH_CLIENT_SECRET": self.graph_client_secret,
                "EDGE_APP_SP_OBJECT_ID": self.edge_app_sp_object_id,
                "ALLOWED_DEVICE_IDS": self.allowed_device_ids,
                "RBAC_API_KEY": self.api_key,
            }.items() if not value
        ]
        if self.api_key and len(self.api_key) < 32:
            missing.append("RBAC_API_KEY (minimum 32 characters)")
        return missing


def get_settings() -> Settings:
    """Read env per request; portal updates apply when App Service restarts."""
    return Settings.from_env()
