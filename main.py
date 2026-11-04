"""App Service replacement for the original Azure RBAC Snapshot Function.

Run using Azure's App Service startup command:
  gunicorn -w 2 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 main:app

The /api/device-access/{device_id} route and x-functions-key header deliberately
match the existing Raspberry Pi / WSL client's configuration.
"""
from datetime import datetime, timedelta, timezone
import hmac
import logging

from fastapi import Depends, FastAPI, HTTPException, Response, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.graph_reader import DirectoryReadError, GraphReader

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Edge RBAC Snapshot Service",
    description=(
        "Azure App Service replacement for Edge RBAC Function. "
        "Use the Authorize control to provide your x-functions-key header. "
        "This snapshot service is for a controlled single-device POC."
    ),
    version="1.0.0",
)

api_key_scheme = APIKeyHeader(name="x-functions-key", auto_error=False)


class AuthorizedUser(BaseModel):
    entraObjectId: str
    username: str
    displayName: str
    enabled: bool
    roles: list[str]


class SnapshotSource(BaseModel):
    assignmentMode: str
    servicePrincipalObjectId: str


class AuthorizationSnapshot(BaseModel):
    schemaVersion: int
    version: int
    deviceId: str
    generatedUtc: str
    validUntilUtc: str
    source: SnapshotSource
    users: list[AuthorizedUser]


def require_api_key(
    provided: str | None = Security(api_key_scheme),
    settings: Settings = Depends(get_settings),
) -> None:
    # Check the POC API key before consulting Graph or returning user data.
    if not settings.api_key or len(settings.api_key) < 32:
        raise HTTPException(status_code=503, detail="RBAC service is not configured")
    if not provided or not hmac.compare_digest(provided, settings.api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API credential")


def get_graph_reader(settings: Settings = Depends(get_settings)) -> GraphReader:
    return GraphReader(settings)


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/", tags=["Health"])
def home():
    return {"service": "Edge RBAC Snapshot API", "documentation": "/docs", "health": "/health"}


@app.get("/health", tags=["Health"])
def health():
    # Liveness only; this does NOT prove Microsoft Graph permissions are correct.
    return {"status": "ok"}


@app.get(
    "/api/device-access/{device_id}",
    response_model=AuthorizationSnapshot,
    tags=["RBAC"],
    dependencies=[Depends(require_api_key)],
)
def device_access(
    device_id: str,
    settings: Settings = Depends(get_settings),
    graph_reader: GraphReader = Depends(get_graph_reader),
):
    if settings.missing_or_weak():
        LOG.error("One or more required application settings are missing or invalid")
        raise HTTPException(status_code=503, detail="RBAC service is not configured")
    if device_id not in settings.allowed_device_ids:
        raise HTTPException(status_code=403, detail="Device is not allowed")

    try:
        users = graph_reader.list_direct_users()
    except DirectoryReadError:
        # Fail closed: never return partial or stale data as a fresh snapshot.
        LOG.exception("Cannot create a complete authorization snapshot")
        raise HTTPException(status_code=502, detail="Directory synchronization failed")
    except Exception:
        LOG.exception("Unexpected authorization snapshot failure")
        raise HTTPException(status_code=502, detail="Directory synchronization failed")

    created = datetime.now(timezone.utc)
    valid_until = created + timedelta(hours=settings.cache_valid_hours)
    return {
        "schemaVersion": 1,
        "version": int(created.timestamp()),
        "deviceId": device_id,
        "generatedUtc": created.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "validUntilUtc": valid_until.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": {
            "assignmentMode": "direct-users-only",
            "servicePrincipalObjectId": settings.edge_app_sp_object_id,
        },
        "users": users,
    }
