from __future__ import annotations

import hmac
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from tower.catalog import CatalogError, load_catalog
from tower.config import STATIC_DIR, settings
from tower.planner import build_plan
from tower.runner import execute_job
from tower.store import JobStore

app = FastAPI(
    title="GAMGEE Control Tower",
    version="0.1.0",
    description="Dependency-aware installation and lifecycle orchestration for GAMGEE Cloud.",
)
bearer = HTTPBearer(auto_error=False)


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
        raise HTTPException(status_code=401, detail="A valid Control Tower bearer token is required.")


def _store() -> JobStore:
    return JobStore(settings.database_path)


@app.get("/", include_in_schema=False)
def workspace():
    page = STATIC_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="Control Tower UI is not installed.")
    return FileResponse(page)


@app.get("/healthz")
def health():
    return {"status": "healthy", "service": "gamgee-control-tower", "version": app.version}


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
