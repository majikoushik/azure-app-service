# Edge RBAC Snapshot Service — Azure App Service + Microsoft Graph

An Azure App Service (Linux / Python 3.11) replacement for the `EdgeRbacReaderFunction` Azure Function in the WSL/Raspberry Pi edge POC.

**Design objective:** keep the existing edge client's request path and header compatible:

- `GET /api/device-access/WSL-POC-001`
- `x-functions-key: <RBAC_API_KEY>`
- JSON snapshot fields: `schemaVersion`, `version`, `deviceId`, `generatedUtc`, `validUntilUtc`, `source`, `users`.

The architecture remains:

```text
Microsoft Entra (EdgeDeviceApplication App Roles / assignments)
                    |
             Microsoft Graph
                    ^
          EdgeRbacReaderFunction
           confidential identity
                    ^
            Azure App Service
             (this FastAPI)
                    |
        GET /api/device-access/id
                    |
         edge/rbac_client.py -> auth_cache.db -> offline login
```

This project **does not** replace MSAL device-code enrollment, offline PIN handling, or on-device enforcement. It replaces only the server-side RBAC snapshot API.

## Start here (browser-only setup)

Read **[AZURE_PORTAL_DEPLOYMENT_GUIDE.md](AZURE_PORTAL_DEPLOYMENT_GUIDE.md)** for the full sequence: Azure Portal setup, GitHub deployment, Postman testing, and troubleshooting.

### Repository layout

```text
central-rbac-appservice/
├── main.py                       # FastAPI app; HTTP endpoint compatible with edge
├── app/
│   ├── __init__.py
│   ├── config.py                  # Env vars and validation
│   └── graph_reader.py            # OAuth token + Graph API reads
├── requirements.txt              # Azure install dependencies
├── requirements-dev.txt          # Tests dependencies
├── tests/test_api.py             # Security & response tests
├── tests/test_graph_reader.py    # Graph pagination and role mapping tests
├── .env.example                  # Names only / placeholders
├── .gitignore
├── AZURE_PORTAL_DEPLOYMENT_GUIDE.md
└── deployment_templates/
    └── deploy-appservice-oidc.yml # Optional if portal cannot create workflow
```

### Python application startup (Azure App Service Configuration > General settings)

```text
gunicorn -w 2 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 main:app
```

### Azure App Service environment variables

| Name | Example or value | Meaning |
|---|---|---|
| `ENTRA_TENANT_ID` | Your tenant GUID | Token issuer directory |
| `GRAPH_CLIENT_ID` | EdgeRbacReaderFunction Application (client) ID | OAuth client |
| `GRAPH_CLIENT_SECRET` | The **secret Value** | Stored **only** in App Service |
| `EDGE_APP_SP_OBJECT_ID` | EdgeDeviceApplication **Enterprise Application Object ID** | Target of Graph app-role query |
| `ALLOWED_DEVICE_IDS` | `WSL-POC-001` | Comma-separated allowable device IDs |
| `RBAC_CACHE_VALID_HOURS` | `168` | Snapshot expiry window (hours) |
| `RBAC_API_KEY` | Long random secret, at least 32 chars | Required `x-functions-key` header |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` | Azure Oryx installs requirements |

**IMPORTANT:** `GRAPH_CLIENT_SECRET` and `RBAC_API_KEY` must not be added to GitHub. Keep them in App Service settings (later, Key Vault). Graph delegated `User.Read` on the public EdgeDeviceApplication is **not** a substitute for the application permissions granted to EdgeRbacReaderFunction.

### Permissions on EdgeRbacReaderFunction

Microsoft Entra > App registrations > EdgeRbacReaderFunction > API permissions > Microsoft Graph:

- `Application.Read.All` — **Application** permission, admin consent granted.
- `User.Read.All` — **Application** permission, admin consent granted.

These broad directory-level permissions are accepted for this isolated POC; redesign with least privilege before real enterprise rollout.

### Test endpoints

- `GET /health` — public liveness check; HTTP 200, `{ "status": "ok" }` (does not test Graph).
- `GET /docs` — Swagger interface, click **Authorize**, enter `RBAC_API_KEY` in `x-functions-key`.
- `GET /api/device-access/WSL-POC-001` — protected; returns JSON snapshot.

```json
{
  "schemaVersion": 1,
  "version": 1790000000,
  "deviceId": "WSL-POC-001",
  "generatedUtc": "2026-11-04T10:00:00Z",
  "validUntilUtc": "2026-11-11T10:00:00Z",
  "source": {
    "assignmentMode": "direct-users-only",
    "servicePrincipalObjectId": "..."
  },
  "users": [
    {
      "entraObjectId": "...",
      "username": "operator01@yourtenant.onmicrosoft.com",
      "displayName": "Edge Operator",
      "enabled": true,
      "roles": ["Operator"]
    }
  ]
}
```

*Example only; user details and timestamps depend on your tenant.*

## What this POC does **not** do

1. **No groups:** It ignores group-assigned roles and returns direct user assignments only, matching the original repository.
2. **No per-device entitlements:** `ALLOWED_DEVICE_IDS` is an allow-list, not a binding between a user and a device. Every allowed device gets the same user list. Do not add more devices in production without device identity and scoped authorization.
3. **No account-disable synchronization:** The `enabled: true` property is schema compatibility **only**. It means the account has a recognized direct application-role assignment, *not* that Microsoft Graph confirmed `accountEnabled`. Removing the App Role assignment is the supported POC revocation path. Do not depend on this for enforcing disabled Entra accounts.
4. **Shared API key:** The `x-functions-key` header is a shared secret and does not positively authenticate a physical device. In production, require an Entra-protected API and certificate/device-bound credentials, or a suitably configured API gateway with mTLS.
5. **No offline guarantee of immediate revocation:** Offline clients may retain their last authorization snapshot until expiry; evaluate shorter windows for privileged roles.
6. **No tamper-proof cache/signed snapshot:** The client-side cache hardening, snapshot anti-replay, and service-layer permission enforcement remain production work from your existing `RBAC_END_TO_END_IMPLEMENTATION.md`.

### Why the Azure Function was not needed

The original HTTP Function performed three tasks: acquire an application-only Graph token, read assignments and user profiles, then return a JSON snapshot. This FastAPI service performs the same tasks inside an ordinary Linux App Service web process. Everything upstream (Entra/Graph) and downstream (edge cache/PIN) is conceptually unchanged.

## Running locally — optional

You said you prefer browser-based Azure/GitHub configuration, so local execution is **optional**. From an IDE such as VS Code/PyCharm, configure a Run profile for module `uvicorn`, arguments `main:app --reload`, and working directory set to the project root. Populate env vars in the IDE run profile. Never commit credentials.

For automated tests (GitHub Actions handles these without local commands), dependencies are in `requirements-dev.txt`, and the test command is `python -m pytest -q`. The tests mock Graph; they never contact a real tenant.

## Official references

- https://learn.microsoft.com/en-us/azure/app-service/quickstart-python
- https://learn.microsoft.com/en-us/azure/app-service/deploy-github-actions
- https://learn.microsoft.com/en-us/azure/app-service/configure-language-python
- https://learn.microsoft.com/en-us/graph/api/serviceprincipal-list-approleassignedto
- https://learn.microsoft.com/en-us/graph/api/user-get
