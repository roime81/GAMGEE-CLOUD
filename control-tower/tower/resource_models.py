from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


RESOURCE_API_VERSION = "gamgee.io/v1alpha1"
SUPPORTED_RESOURCE_KINDS = ("Application",)
PLANNED_RESOURCE_KINDS = ("Function", "Machine", "Service", "Workspace", "Route", "Secret")


class ResourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    workspace: str
    generation: int = Field(ge=1)


class ResourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apiVersion: Literal["gamgee.io/v1alpha1"] = RESOURCE_API_VERSION
    kind: Literal["Application"]
    metadata: ResourceMetadata
    spec: dict[str, Any]
    status: dict[str, Any] = Field(default_factory=dict)


def to_resource_document(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": RESOURCE_API_VERSION,
        "kind": resource["kind"],
        "metadata": {
            "id": resource["id"],
            "name": resource["name"],
            "workspace": resource["workspace"],
            "generation": resource["generation"],
        },
        "spec": resource["desired"],
        "status": {
            "phase": resource["phase"],
            "observedGeneration": resource["observed_generation"],
            "observed": resource["observed"],
            "retryCount": resource["retry_count"],
            "lastError": resource["last_error"],
            "updatedAt": resource["updated_at"],
        },
    }
