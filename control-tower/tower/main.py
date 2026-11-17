from __future__ import annotations

import hmac
import logging
import re
import threading
import uuid
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from tower.catalog import CatalogError, load_catalog
from tower.cluster import (
    ClusterError,
    ClusterNotFound,
    apply_object,
    apply_objects,
    delete_gamgee_app,
    get_json,
    run_kubectl,
)
from tower.config import ROOT, STATIC_DIR, settings
from tower.recommendation import ai_factory_ready, detect_host_capacity, recommend_local_bundle
from tower.planner import build_plan
from tower.runner import execute_job
from tower.store import JobStore
from tower.lifecycle import resume_replicas
from tower.resource_guard import is_gamgee_managed
from tower.resource_store import IdempotencyConflict, ResourceStore
from tower.resource_engine import ResourceEngine
from tower.resource_models import ApplicationResourceRequest, to_resource_document
from tower.drivers.kubernetes_application import KubernetesApplicationDriver
from tower.workloads import (
    ScaleRequest,
    WorkloadRequest,
    WorkspaceRequest,
    build_workload_objects,
    build_workspace_manifest,
)

app = FastAPI(
    title="GAMGEE Cloud OS",
    version="0.2.0",
    description="Outcome-first cloud provisioning and safe platform lifecycle management.",
)
bearer = HTTPBearer(auto_error=False)
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class PlanRequest(BaseModel):
    profiles: list[str] = Field(min_length=1, max_length=7)


class JobRequest(PlanRequest):
    plan_hash: str = Field(min_length=64, max_length=64)
    mode: Literal["dry-run", "apply"] = "dry-run"
    approved: bool = False


def require_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    expected = settings.api_token
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="Control Tower is not configured with a strong API token.")
    if credentials is None or not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=401, detail="A valid GAMGEE bearer token is required.")


def _store() -> JobStore:
    return JobStore(settings.database_path)


_RESOURCE_STORE_INSTANCE: ResourceStore | None = None


def _resource_store() -> ResourceStore:
    global _RESOURCE_STORE_INSTANCE
    if _RESOURCE_STORE_INSTANCE is None:
        _RESOURCE_STORE_INSTANCE = ResourceStore(settings.database_path)
    return _RESOURCE_STORE_INSTANCE


def _resource_engine() -> ResourceEngine:
    return ResourceEngine(_resource_store(), [KubernetesApplicationDriver()])


def _application_resource(workspace: str, name: str) -> dict:
    resource = _resource_store().find("Application", workspace, name)
    if (
        resource is None
        or resource["phase"] in {"deleted", "deleting"}
        or resource["desired"].get("deleted")
    ):
        raise HTTPException(status_code=404, detail="GAMGEE Application resource not found or deletion has already started.")
    return resource


def _validate_tls_secret(namespace: str, secret_name: str) -> None:
    try:
        secret = get_json("secret", secret_name, namespace=namespace)
    except ClusterNotFound as exc:
        raise HTTPException(status_code=409, detail="The referenced TLS Secret does not exist in this workspace.") from exc
    except ClusterError as exc:
        raise _cluster_failure(exc) from exc
    if secret.get("type") != "kubernetes.io/tls" or not {"tls.crt", "tls.key"}.issubset(secret.get("data", {})):
        raise HTTPException(status_code=422, detail="The referenced Secret must be a valid kubernetes.io/tls Secret containing tls.crt and tls.key.")


def _submit_application(
    workspace: str,
    request: WorkloadRequest,
    idempotency_key: str | None = None,
) -> dict:
    namespace = _workspace_namespace(workspace)
    if request.tls_secret:
        _validate_tls_secret(namespace, request.tls_secret)
    try:
        resource, operation_id, replayed = _resource_engine().submit(
            kind="Application",
            workspace=workspace,
            name=request.name,
            desired=request.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    observed = resource["observed"]
    return {
        "workspace": workspace,
        "namespace": namespace,
        "app": request.name,
        "resource_id": resource["id"],
        "generation": resource["generation"],
        "operation_id": operation_id,
        "status": resource["phase"],
        "observed_status": observed.get("state"),
        "private_by_default": request.hostname is None,
        "public_url": f"https://{request.hostname}" if request.hostname else None,
        "idempotent_replay": replayed,
        "message": "GAMGEE accepted the desired state. The Resource Engine will reconcile it and retry transient failures.",
    }


_RESOURCE_STOP = threading.Event()
_RESOURCE_THREAD: threading.Thread | None = None
_RESOURCE_LOG = logging.getLogger("gamgee.resource-engine")


def _resource_reconcile_loop() -> None:
    while not _RESOURCE_STOP.is_set():
        try:
            result = _resource_engine().reconcile_due(limit=25)
            _RESOURCE_STOP.wait(1 if result["claimed"] else 4)
        except Exception:
            _RESOURCE_LOG.exception("Resource Engine worker loop failed.")
            _RESOURCE_STOP.wait(5)


def _start_resource_worker() -> None:
    global _RESOURCE_THREAD
    if _RESOURCE_THREAD is not None and _RESOURCE_THREAD.is_alive():
        return
    _RESOURCE_STOP.clear()
    _RESOURCE_THREAD = threading.Thread(
        target=_resource_reconcile_loop,
        name="gamgee-resource-reconciler",
        daemon=True,
    )
    _RESOURCE_THREAD.start()


def _stop_resource_worker() -> None:
    _RESOURCE_STOP.set()
    if _RESOURCE_THREAD is not None and _RESOURCE_THREAD.is_alive():
        _RESOURCE_THREAD.join(timeout=5)


def _cluster_failure(exc: ClusterError) -> HTTPException:
    return HTTPException(status_code=503, detail=f"Cloud data plane is unavailable: {str(exc)[:700]}")


def _validate_path_label(value: str, kind: str) -> str:
    value = value.strip().lower()
    if not _DNS_LABEL.fullmatch(value):
        raise HTTPException(status_code=422, detail=f"{kind} must be a lowercase DNS label.")
    return value


def _existing_resource(resource: str, name: str, namespace: str, workspace: str) -> dict | None:
    try:
        item = get_json(resource, name, namespace=namespace)
    except ClusterNotFound:
        return None
    except ClusterError as exc:
        raise _cluster_failure(exc) from exc
    if not is_gamgee_managed(item, workspace):
        raise HTTPException(
            status_code=409,
            detail=f"{resource} '{name}' already exists but is not managed by GAMGEE in this workspace.",
        )
    return item


def _workspace_namespace(workspace: str) -> str:
    name = _validate_path_label(workspace, "Workspace")
    namespace = f"{name}-production"
    try:
        item = get_json("namespace", namespace)
    except ClusterNotFound as exc:
        raise HTTPException(status_code=409, detail="Workspace is still being created or does not exist.") from exc
    except ClusterError as exc:
        raise _cluster_failure(exc) from exc

    labels = item.get("metadata", {}).get("labels", {})
    if labels.get("gamgee.cloud/tenant") != name:
        raise HTTPException(status_code=404, detail="Workspace not found or namespace is not owned by GAMGEE.")
    return namespace


def _managed_deployment(workspace: str, app_name: str) -> tuple[str, dict]:
    namespace = _workspace_namespace(workspace)
    name = _validate_path_label(app_name, "App name")
    try:
        item = get_json("deployment.apps", name, namespace=namespace)
    except ClusterNotFound as exc:
        raise HTTPException(status_code=404, detail="GAMGEE app not found.") from exc
    except ClusterError as exc:
        raise _cluster_failure(exc) from exc
    labels = item.get("metadata", {}).get("labels", {})
    if labels.get("gamgee.cloud/managed-by") != "gamgee" or labels.get("gamgee.cloud/tenant") != workspace:
        raise HTTPException(status_code=404, detail="GAMGEE app not found.")
    return namespace, item


@app.on_event("startup")
def start_resource_reconciler() -> None:
    _start_resource_worker()


@app.on_event("shutdown")
def stop_resource_reconciler() -> None:
    _stop_resource_worker()


@app.get("/", include_in_schema=False)
def workspace():
    page = STATIC_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="GAMGEE workspace UI is not installed.")
    return FileResponse(page)


@app.get("/healthz")
def health():
    return {"status": "healthy", "service": "gamgee-cloud-os", "version": app.version}


@app.get("/v1/tower/capabilities", dependencies=[Depends(require_token)])
def capabilities():
    return {"apply_enabled": settings.allow_apply, "execution_host": settings.host}


@app.get("/v1/tower/cloud/status", dependencies=[Depends(require_token)])
def cloud_status():
    try:
        result = get_json("nodes")
    except ClusterError as exc:
        raise _cluster_failure(exc) from exc
    nodes = result.get("items", [])
    ready = []
    for node in nodes:
        conditions = node.get("status", {}).get("conditions", [])
        if any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions):
            ready.append(node.get("metadata", {}).get("name", "unknown"))
    return {
        "state": "ready" if nodes and len(ready) == len(nodes) else "degraded",
        "node_count": len(nodes),
        "ready_nodes": ready,
        "not_ready_count": len(nodes) - len(ready),
        "nodes": [
            {
                "name": node.get("metadata", {}).get("name"),
                "roles": sorted(
                    key.removeprefix("node-role.kubernetes.io/")
                    for key in node.get("metadata", {}).get("labels", {})
                    if key.startswith("node-role.kubernetes.io/")
                ),
                "capacity": node.get("status", {}).get("capacity", {}),
                "ready": node.get("metadata", {}).get("name") in ready,
            }
            for node in nodes
        ],
    }


@app.get("/v1/tower/cloud/recommendation", dependencies=[Depends(require_token)])
def cloud_recommendation():
    """Choose the local cloud capabilities that this host can actually support."""
    return recommend_local_bundle(
        kvm_available=Path("/dev/kvm").exists(),
        ai_available=ai_factory_ready(ROOT),
        host_capacity=detect_host_capacity(ROOT),
    )


@app.get("/v1/tower/profiles", dependencies=[Depends(require_token)])
def profiles():
    catalog = load_catalog()
    return {
        "profiles": [
            {
                "id": item["id"],
                "name": item["name"],
                "description": item["description"],
                "depends_on": item["depends_on"],
                "resources": item["resources"],
                "components": item.get("components", []),
                "implementation": item.get("implementation", "adapter"),
                "required_tools": item["required_tools"],
            }
            for item in catalog.values()
        ]
    }


@app.post("/v1/tower/plan", dependencies=[Depends(require_token)])
def plan(request: PlanRequest):
    try:
        return build_plan(request.profiles)
    except CatalogError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/tower/jobs", dependencies=[Depends(require_token)])
def create_job(request: JobRequest, background_tasks: BackgroundTasks):
    try:
        current_plan = build_plan(request.profiles)
    except CatalogError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not hmac.compare_digest(current_plan["plan_hash"], request.plan_hash):
        raise HTTPException(status_code=409, detail="Plan changed. Generate a new plan before execution.")
    if request.mode == "apply":
        if not settings.allow_apply:
            raise HTTPException(status_code=403, detail="Mutating operations are disabled. Set GAMGEE_TOWER_ALLOW_APPLY=true after securing the host.")
        if not request.approved:
            raise HTTPException(status_code=409, detail="Explicit approval is required before applying a plan.")
        if not current_plan["apply_ready"]:
            raise HTTPException(
                status_code=409,
                detail={"message": "Host preflight has blockers or missing tools.", "plan": current_plan},
            )

    details = {
        "mode": request.mode,
        "profiles": [{"id": name, "status": "pending"} for name in current_plan["ordered_profiles"]],
        "current_profile": None,
    }
    store = _store()
    job = store.create(current_plan["ordered_profiles"], current_plan["plan_hash"], details)
    if request.mode == "dry-run":
        store.update(job["id"], status="succeeded", append_log="Dry-run completed. No host changes were made.\n")
    else:
        background_tasks.add_task(execute_job, job["id"], current_plan)
    return store.get(job["id"])


@app.get("/v1/tower/jobs", dependencies=[Depends(require_token)])
def list_jobs(limit: int = Query(default=25, ge=1, le=100)):
    return {"jobs": _store().list(limit)}


@app.get("/v1/tower/jobs/{job_id}", dependencies=[Depends(require_token)])
def get_job(job_id: str):
    job = _store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@app.get("/v1/resources", dependencies=[Depends(require_token)])
def list_resources(
    workspace: str | None = Query(default=None, min_length=1, max_length=63),
    kind: str | None = Query(default="Application"),
    include_deleted: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
):
    if kind not in (None, "Application"):
        raise HTTPException(status_code=422, detail="Only Application resources have a registered driver in this release.")
    items = _resource_store().list(workspace=workspace, kind=kind, include_deleted=include_deleted, limit=limit)
    return {"apiVersion": "gamgee.io/v1alpha1", "resources": [to_resource_document(item) for item in items]}


@app.get("/v1/resources/{resource_id}", dependencies=[Depends(require_token)])
def get_resource(resource_id: str, workspace: str = Query(min_length=1, max_length=63)):
    item = _resource_store().get(resource_id)
    if item is None or item["workspace"] != workspace:
        raise HTTPException(status_code=404, detail="GAMGEE resource not found.")
    return to_resource_document(item)


@app.get("/v1/resources/{resource_id}/operations", dependencies=[Depends(require_token)])
def list_resource_operations(resource_id: str, workspace: str = Query(min_length=1, max_length=63)):
    item = _resource_store().get(resource_id)
    if item is None or item["workspace"] != workspace:
        raise HTTPException(status_code=404, detail="GAMGEE resource not found.")
    return {"operations": _resource_store().list_operations(resource_id)}


@app.get("/v1/resources/{resource_id}/events", dependencies=[Depends(require_token)])
def list_resource_events(resource_id: str, workspace: str = Query(min_length=1, max_length=63)):
    item = _resource_store().get(resource_id)
    if item is None or item["workspace"] != workspace:
        raise HTTPException(status_code=404, detail="GAMGEE resource not found.")
    return {"events": _resource_store().events(resource_id)}


@app.delete("/v1/resources/{resource_id}", status_code=202, dependencies=[Depends(require_token)])
def delete_resource(
    resource_id: str,
    workspace: str = Query(min_length=1, max_length=63),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        resource, operation_id, replayed = _resource_engine().delete(
            resource_id,
            workspace=workspace,
            idempotency_key=idempotency_key,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if resource is None:
        raise HTTPException(status_code=404, detail="GAMGEE resource not found.")
    return {
        "resource_id": resource_id,
        "operation_id": operation_id,
        "generation": resource["generation"],
        "status": resource["phase"],
        "idempotent_replay": replayed,
    }


@app.post("/v1/resources/applications", status_code=202, dependencies=[Depends(require_token)])
def create_application_resource(
    request: ApplicationResourceRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    workspace = _validate_path_label(request.workspace, "Workspace")
    return _submit_application(workspace, request.spec, idempotency_key)


@app.get("/v1/tower/workspaces", dependencies=[Depends(require_token)])
def list_workspaces():
    try:
        result = get_json("tenants.capsule.clastix.io")
    except ClusterError as exc:
        raise _cluster_failure(exc) from exc
    return {
        "workspaces": [
            {
                "name": item.get("metadata", {}).get("name"),
                "created_at": item.get("metadata", {}).get("creationTimestamp"),
                "namespaces": [
                    f"{item.get('metadata', {}).get('name')}-production",
                    f"{item.get('metadata', {}).get('name')}-staging",
                ],
            }
            for item in result.get("items", [])
        ]
    }


@app.post("/v1/tower/workspaces", status_code=201, dependencies=[Depends(require_token)])
def create_workspace(request: WorkspaceRequest):
    try:
        get_json("tenants.capsule.clastix.io", request.name)
    except ClusterNotFound:
        pass
    except ClusterError as exc:
        raise _cluster_failure(exc) from exc
    else:
        raise HTTPException(status_code=409, detail="Workspace already exists. Choose another name.")

    try:
        output = apply_object(build_workspace_manifest(request))
    except ClusterError as exc:
        raise _cluster_failure(exc) from exc
    return {
        "name": request.name,
        "status": "provisioning",
        "namespaces": [f"{request.name}-production", f"{request.name}-staging"],
        "message": "Workspace request accepted. Capsule is creating the isolated namespaces.",
        "operation": output.strip()[-500:],
    }


@app.get("/v1/tower/workspaces/{workspace}/apps", dependencies=[Depends(require_token)])
def list_apps(workspace: str):
    _workspace_namespace(workspace)
    resources = _resource_store().list(workspace=workspace, kind="Application", limit=500)
    apps = []
    for resource in resources:
        desired = resource["desired"]
        observed = resource["observed"]
        phase = resource["phase"]
        apps.append(
            {
                "name": resource["name"],
                "resource_id": resource["id"],
                "generation": resource["generation"],
                "image": desired.get("image"),
                "desired_replicas": desired.get("replicas", 1),
                "ready_replicas": observed.get("ready_replicas", 0),
                "available_replicas": observed.get("available_replicas", 0),
                "status": (
                    phase if phase in {"pending", "reconciling", "deleting", "error"}
                    else observed.get("state", phase)
                ),
                "last_error": resource["last_error"],
            }
        )
    return {"workspace": workspace, "namespace": f"{workspace}-production", "apps": apps}


@app.post("/v1/tower/workspaces/{workspace}/apps", status_code=202, dependencies=[Depends(require_token)])
def deploy_app(
    workspace: str,
    request: WorkloadRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return _submit_application(_validate_path_label(workspace, "Workspace"), request, idempotency_key)


@app.post("/v1/tower/workspaces/{workspace}/apps/{app_name}/scale", dependencies=[Depends(require_token)])
def scale_app(workspace: str, app_name: str, request: ScaleRequest):
    workspace = _validate_path_label(workspace, "Workspace")
    resource = _application_resource(workspace, _validate_path_label(app_name, "App name"))
    desired = dict(resource["desired"])
    current_replicas = int(desired.get("replicas", 1))
    if request.replicas == 0:
        if current_replicas > 0:
            desired["_gamgee_previous_replicas"] = current_replicas
    else:
        desired["_gamgee_previous_replicas"] = request.replicas
    desired["replicas"] = request.replicas
    engine = _resource_engine()
    updated, operation_id, replayed = engine.submit(
        kind="Application",
        workspace=workspace,
        name=resource["name"],
        desired=desired,
    )
    return {
        "resource_id": updated["id"],
        "generation": updated["generation"],
        "operation_id": operation_id,
        "replicas": request.replicas,
        "status": updated["phase"],
        "idempotent_replay": replayed,
    }


@app.post("/v1/tower/workspaces/{workspace}/apps/{app_name}/resume", dependencies=[Depends(require_token)])
def resume_app(workspace: str, app_name: str):
    workspace = _validate_path_label(workspace, "Workspace")
    resource = _application_resource(workspace, _validate_path_label(app_name, "App name"))
    desired = dict(resource["desired"])
    target = int(desired.get("_gamgee_previous_replicas", max(1, int(desired.get("replicas", 1)))))
    target = min(20, max(1, target))
    desired["replicas"] = target
    desired["_gamgee_previous_replicas"] = target
    updated, operation_id, replayed = _resource_engine().submit(
        kind="Application", workspace=workspace, name=resource["name"], desired=desired
    )
    return {
        "resource_id": updated["id"],
        "generation": updated["generation"],
        "operation_id": operation_id,
        "replicas": target,
        "status": updated["phase"],
        "idempotent_replay": replayed,
    }


@app.post("/v1/tower/workspaces/{workspace}/apps/{app_name}/restart", dependencies=[Depends(require_token)])
def restart_app(workspace: str, app_name: str):
    workspace = _validate_path_label(workspace, "Workspace")
    resource = _application_resource(workspace, _validate_path_label(app_name, "App name"))
    desired = dict(resource["desired"])
    desired["_gamgee_restart_token"] = uuid.uuid4().hex
    updated, operation_id, replayed = _resource_engine().submit(
        kind="Application", workspace=workspace, name=resource["name"], desired=desired
    )
    return {
        "resource_id": updated["id"],
        "generation": updated["generation"],
        "operation_id": operation_id,
        "status": updated["phase"],
        "idempotent_replay": replayed,
    }


@app.delete("/v1/tower/workspaces/{workspace}/apps/{app_name}", status_code=202, dependencies=[Depends(require_token)])
def delete_app(
    workspace: str,
    app_name: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    workspace = _validate_path_label(workspace, "Workspace")
    resource = _application_resource(workspace, _validate_path_label(app_name, "App name"))
    deleted, operation_id, replayed = _resource_engine().delete(
        resource["id"],
        workspace=workspace,
        idempotency_key=idempotency_key,
    )
    return {
        "resource_id": resource["id"],
        "generation": deleted["generation"] if deleted else None,
        "operation_id": operation_id,
        "status": deleted["phase"] if deleted else "not_found",
        "idempotent_replay": replayed,
    }


@app.get("/readyz")
def ready():
    return {"status": "ready", "service": "gamgee-control-tower"}
