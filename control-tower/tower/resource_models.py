from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tower.workloads import WorkloadRequest


RESOURCE_API_VERSION = "gamgee.io/v1alpha1"
SUPPORTED_RESOURCE_KINDS = ("Application", "Function")
PLANNED_RESOURCE_KINDS = ("Machine", "Service", "Workspace", "Route", "Secret")

_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_IMAGE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,253}$")
_SHA256_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


class ResourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    workspace: str
    generation: int = Field(ge=1)


class ResourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apiVersion: Literal["gamgee.io/v1alpha1"] = RESOURCE_API_VERSION
    kind: Literal["Application", "Function"]
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


class ApplicationResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: str = Field(min_length=1, max_length=63)
    spec: WorkloadRequest

    @field_validator("workspace")
    @classmethod
    def valid_workspace(cls, value: str) -> str:
        value = value.strip().lower()
        if not _DNS_LABEL.fullmatch(value):
            raise ValueError("Workspace must be a lowercase DNS label.")
        return value


class FunctionSpec(BaseModel):
    """Safe GAMGEE function intent; callers supply a published Spin OCI artifact."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=63)
    image: str = Field(min_length=5, max_length=255)
    path: str = Field(default="/", min_length=1, max_length=256)
    min_replicas: int = Field(default=0, ge=0, le=20)
    max_replicas: int = Field(default=10, ge=1, le=20)
    concurrency: int = Field(default=50, ge=1, le=1000)
    cooldown_seconds: int = Field(default=120, ge=30, le=1800)
    hostname: str | None = Field(default=None, max_length=253)
    tls_secret: str | None = Field(default=None, max_length=63)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip().lower()
        if not _DNS_LABEL.fullmatch(value):
            raise ValueError("Function name must be a lowercase DNS label.")
        return value

    @field_validator("image")
    @classmethod
    def valid_immutable_spin_image(cls, value: str) -> str:
        value = value.strip()
        if not _IMAGE_COMPONENT.fullmatch(value) or "/" not in value:
            raise ValueError("Function image must be a valid registry-qualified OCI reference.")
        if "@sha256:" in value:
            if not _SHA256_DIGEST.search(value):
                raise ValueError("Only complete sha256 image digests are accepted.")
        else:
            final_component = value.rsplit("/", 1)[-1]
            if ":" not in final_component:
                raise ValueError("Function image must include an explicit version tag or sha256 digest.")
            if final_component.rsplit(":", 1)[-1].lower() == "latest":
                raise ValueError("The latest tag is mutable; pin a version tag or sha256 digest.")
        return value

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or value.startswith("//")
            or ".." in value.split("/")
            or "?" in value
            or "#" in value
            or any(ord(char) < 32 for char in value)
        ):
            raise ValueError("Function path must be a simple absolute path prefix.")
        return value

    @field_validator("hostname")
    @classmethod
    def valid_hostname(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip().lower().rstrip(".")
            labels = value.split(".")
            if len(labels) < 2 or any(not _DNS_LABEL.fullmatch(label) for label in labels):
                raise ValueError("Public hostname must be a fully qualified DNS name.")
        return value

    @field_validator("tls_secret")
    @classmethod
    def valid_tls_secret(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip().lower()
            if not _DNS_LABEL.fullmatch(value):
                raise ValueError("TLS Secret name must be a lowercase DNS label.")
        return value

    @model_validator(mode="after")
    def validate_scaling_and_route(self) -> "FunctionSpec":
        if self.min_replicas > self.max_replicas:
            raise ValueError("min_replicas cannot exceed max_replicas.")
        if bool(self.hostname) != bool(self.tls_secret):
            raise ValueError("Public function access requires both hostname and TLS Secret.")
        return self


class FunctionResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: str = Field(min_length=1, max_length=63)
    spec: FunctionSpec

    @field_validator("workspace")
    @classmethod
    def valid_workspace(cls, value: str) -> str:
        value = value.strip().lower()
        if not _DNS_LABEL.fullmatch(value):
            raise ValueError("Workspace must be a lowercase DNS label.")
        return value
