import hmac
import os

import yaml
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from kubernetes.client.exceptions import ApiException

from app.agent_core import interpret_intent
from app.k8s_client import create_tenant
from app.validator import ProvisionRequest, build_tenant_manifest


app = FastAPI(title="Gamgee Cloud AI Agent", version="1.0.0")
_bearer = HTTPBearer(auto_error=False)


def require_api_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    expected = os.getenv("GAMGEE_API_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Provisioning is not configured.",
        )
    if credentials is None or not hmac.compare_digest(
        credentials.credentials, expected
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid bearer token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.post(
    "/v1/cloud/execute",
    dependencies=[Depends(require_api_token)],
)
def execute_natural_language_intent(request: ProvisionRequest) -> dict:
    try:
        decision = interpret_intent(request.prompt)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not decision.provision_tenant:
        raise HTTPException(status_code=422, detail=decision.reason)

    manifest = build_tenant_manifest(request)
    try:
        create_tenant(manifest)
    except ApiException as exc:
        if exc.status == 409:
            raise HTTPException(status_code=409, detail="Tenant already exists.") from exc
        raise HTTPException(
            status_code=503, detail="Kubernetes could not create the tenant."
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Kubernetes is unavailable."
        ) from exc

    return {
        "status": "success",
        "message": f"Successfully provisioned tenant '{request.tenant_name}'.",
        "generated_manifest": yaml.safe_dump(manifest, sort_keys=False),
    }


@app.get("/healthz")
def health_check() -> dict:
    return {"status": "healthy", "service": "gamgee-cloud-agent"}
