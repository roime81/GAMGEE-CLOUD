from __future__ import annotations

import hmac
import re
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
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
from tower.config import STATIC_DIR, settings
from tower.planner import build_plan
from tower.runner import execute_job
from tower.store import JobStore
from tower.lifecycle import resume_replicas
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


def _cluster_failure(exc: ClusterError) -> HTTPException:
    return HTTPException(status_code=503, detail=f"Cloud data plane is unavailable: {str(exc)[:700]}")


def _validate_path_label(value: str, kind: str) -> str:
    value = value.strip().lower()
    if not _DNS_LABEL.fullmatch(value):
        raise HTTPException(status_code=422, detail=f"{kind} must be a lowercase DNS label.")
    return value


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
    namespace = _workspace_namespace(workspace)
    try:
        result = get_json(
            "deployments.apps",
            namespace=namespace,
            label_selector="gamgee.cloud/managed-by=gamgee",
        )
    except ClusterError as exc:
        raise _cluster_failure(exc) from exc
    apps = []
    for item in result.get("items", []):
        spec = item.get("spec", {})
        status = item.get("status", {})
        containers = item.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        apps.append(
            {
                "name": item.get("metadata", {}).get("name"),
                "image": containers[0].get("image") if containers else None,
                "desired_replicas": spec.get("replicas", 1),
                "ready_replicas": status.get("readyReplicas", 0),
                "available_replicas": status.get("availableReplicas", 0),
                "status": (
                    "stopped" if spec.get("replicas", 1) == 0
                    else "ready" if status.get("readyReplicas", 0) >= spec.get("replicas", 1)
                    else "progressing"
                ),
            }
        )
    return {"workspace": workspace, "namespace": namespace, "apps": apps}


@app.post("/v1/tower/workspaces/{workspace}/apps", status_code=201, dependencies=[Depends(require_token)])
def deploy_app(workspace: str, request: WorkloadRequest):
    namespace = _workspace_namespace(workspace)
    try:
        apply_objects(build_workload_objects(workspace, request))
    except ClusterError as exc:
        raise _cluster_failure(exc) from exc
    return {
        "workspace": workspace,
        "namespace": namespace,
        "app": request.name,
        "status": "deploying",
        "private_by_default": request.hostname is None,
        "public_url": f"https://{request.hostname}" if request.hostname else None,
        "message": "GAMGEE has submitted the app. Check its readiness in the workspace view.",
    }


@app.post("/v1/tower/workspaces/{workspace}/apps/{app_name}/scale", dependencies=[Depends(require_token)])
def scale_app(workspace: str, app_name: str, request: ScaleRequest):
    namespace, deployment = _managed_deployment(workspace, app_name)
    try:
        if request.replicas == 0:
            previous = int(deployment.get("spec", {}).get("replicas", 1))
            if previous > 0:
                run_kubectl([
                    "annotate", f"deployment/{app_name}",
                    f"gamgee.cloud/previous-replicas={previous}",
                    "--overwrite", "--namespace", namespace,
                ])
        run_kubectl(["scale", f"deployment/{app_name}", f"--replicas={request.replicas}", "--namespace", namespace])
        if request.replicas > 0:
            run_kubectl([
                "annotate", f"deployment/{app_name}",
                f"gamgee.cloud/previous-replicas={request.replicas}",
                "--overwrite", "--namespace", namespace,
            ])
    except ClusterError as exc:
        raise _cluster_failure(exc) from exc
    status = "stopped" if request.replicas == 0 else "scaling"
    return {"workspace": workspace, "app": app_name, "replicas": request.replicas, "status": status}


@app.post("/v1/tower/workspaces/{workspace}/apps/{app_name}/resume", dependencies=[Depends(require_token)])
def resume_app(workspace: str, app_name: str):
    namespace, deployment = _managed_deployment(workspace, app_name)
    target = resume_replicas(deployment)
    try:
        run_kubectl(["scale", f"deployment/{app_name}", f"--replicas={target}", "--namespace", namespace])
    except ClusterError as exc:
        raise _cluster_failure(exc) from exc
    return {"workspace": workspace, "app": app_name, "replicas": target, "status": "resuming"}


@app.post("/v1/tower/workspaces/{workspace}/apps/{app_name}/restart", dependencies=[Depends(require_token)])
def restart_app(workspace: str, app_name: str):
    namespace, _ = _managed_deployment(workspace, app_name)
    try:
        run_kubectl(["rollout", "restart", f"deployment/{app_name}", "--namespace", namespace])
    except ClusterError as exc:
        raise _cluster_failure(exc) from exc
    return {"workspace": workspace, "app": app_name, "status": "restarting"}


@app.delete("/v1/tower/workspaces/{workspace}/apps/{app_name}", dependencies=[Depends(require_token)])
def delete_app(workspace: str, app_name: str):
    namespace, _ = _managed_deployment(workspace, app_name)
    try:
        delete_gamgee_app(app_name, namespace)
    except ClusterError as exc:
        raise _cluster_failure(exc) from exc
    return {"workspace": workspace, "app": app_name, "status": "deleting"}


@app.get("/readyz")
def ready():
    return {"status": "ready", "service": "gamgee-control-tower"}
