from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}$")
_HOST = re.compile(r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$")
_DIGEST = re.compile(r"@sha256:[a-fA-F0-9]{64}$")

SIZE_RESOURCES = {
    "small": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "500m", "memory": "512Mi"}},
    "standard": {"requests": {"cpu": "250m", "memory": "256Mi"}, "limits": {"cpu": "1", "memory": "1Gi"}},
    "large": {"requests": {"cpu": "500m", "memory": "512Mi"}, "limits": {"cpu": "2", "memory": "2Gi"}},
}


class WorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=63)
    owner_email: str = Field(min_length=3, max_length=254)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip().lower()
        if not _DNS_LABEL.fullmatch(value):
            raise ValueError("Workspace name must be a lowercase DNS label (letters, numbers and hyphens).")
        return value

    @field_validator("owner_email")
    @classmethod
    def valid_owner(cls, value: str) -> str:
        value = value.strip().lower()
        if not _EMAIL.fullmatch(value):
            raise ValueError("Owner email must be a valid email address.")
        return value


class WorkloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=63)
    image: str = Field(min_length=3, max_length=255)
    port: int = Field(default=8080, ge=1024, le=65535)
    replicas: int = Field(default=1, ge=1, le=20)
    size: Literal["small", "standard", "large"] = "small"
    health_path: str = Field(default="/", min_length=1, max_length=128)
    hostname: str | None = Field(default=None, max_length=253)
    tls_secret: str | None = Field(default=None, max_length=63)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip().lower()
        if not _DNS_LABEL.fullmatch(value):
            raise ValueError("App name must be a lowercase DNS label.")
        return value

    @field_validator("image")
    @classmethod
    def valid_image(cls, value: str) -> str:
        value = value.strip()
        if not _IMAGE.fullmatch(value) or "://" in value or value.endswith(":latest"):
            raise ValueError("Use a valid OCI image reference with an explicit version tag or sha256 digest; :latest is not allowed.")
        final_component = value.rsplit("/", 1)[-1]
        if not _DIGEST.search(value) and ":" not in final_component:
            raise ValueError("The image must include an explicit tag or sha256 digest.")
        if "@" in value and not _DIGEST.search(value):
            raise ValueError("Only sha256 image digests are accepted.")
        return value

    @field_validator("health_path")
    @classmethod
    def valid_health_path(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//") or ".." in value or any(ord(c) < 32 for c in value):
            raise ValueError("health_path must be a simple absolute HTTP path.")
        return value

    @field_validator("hostname")
    @classmethod
    def valid_hostname(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip().lower()
            if not _HOST.fullmatch(value):
                raise ValueError("Public hostname must be a fully qualified DNS name.")
        return value

    @field_validator("tls_secret")
    @classmethod
    def valid_secret_name(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip().lower()
            if not _DNS_LABEL.fullmatch(value):
                raise ValueError("TLS secret name must be a lowercase DNS label.")
        return value

    @model_validator(mode="after")
    def route_requires_tls(self) -> "WorkloadRequest":
        if bool(self.hostname) != bool(self.tls_secret):
            raise ValueError("Public access requires both a hostname and an existing TLS secret; otherwise the app stays private.")
        return self


class ScaleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replicas: int = Field(ge=0, le=20)


def build_workspace_manifest(request: WorkspaceRequest) -> dict:
    return {
        "apiVersion": "capsule.clastix.io/v1beta2",
        "kind": "Tenant",
        "metadata": {"name": request.name},
        "spec": {
            "owners": [{"kind": "User", "name": request.owner_email}],
            "additionalNamespaces": [f"{request.name}-production", f"{request.name}-staging"],
            "resourceQuotas": {
                "hard": {
                    "requests.cpu": "4",
                    "requests.memory": "8Gi",
                    "limits.cpu": "8",
                    "limits.memory": "16Gi",
                    "pods": "30",
                    "services": "10",
                    "persistentvolumeclaims": "5",
                }
            },
            "namespaceOptions": {
                "additionalMetadata": {
                    "labels": {
                        "environment": "multi-tenant",
                        "gamgee.cloud/tenant": request.name,
                    }
                }
            },
        },
    }


def build_workload_objects(workspace: str, request: WorkloadRequest) -> list[dict]:
    """Compile a small, safe app intent into fixed Kubernetes resources."""
    if not _DNS_LABEL.fullmatch(workspace):
        raise ValueError("Workspace must be a lowercase DNS label.")

    namespace = f"{workspace}-production"
    labels = {
        "app.kubernetes.io/name": request.name,
        "app.kubernetes.io/managed-by": "gamgee",
        "gamgee.cloud/managed-by": "gamgee",
        "gamgee.cloud/tenant": workspace,
        "gamgee.cloud/app": request.name,
    }
    resources = SIZE_RESOURCES[request.size]

    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": request.name, "namespace": namespace, "labels": labels},
        "spec": {
            "replicas": request.replicas,
            "revisionHistoryLimit": 5,
            "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}},
            "selector": {"matchLabels": {"app.kubernetes.io/name": request.name}},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "automountServiceAccountToken": False,
                    "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
                    "containers": [
                        {
                            "name": request.name,
                            "image": request.image,
                            "imagePullPolicy": "IfNotPresent",
                            "ports": [{"name": "http", "containerPort": request.port, "protocol": "TCP"}],
                            "resources": resources,
                            "securityContext": {
                                "runAsNonRoot": True,
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "readinessProbe": {
                                "httpGet": {"path": request.health_path, "port": "http"},
                                "initialDelaySeconds": 5,
                                "periodSeconds": 10,
                            },
                            "livenessProbe": {
                                "httpGet": {"path": request.health_path, "port": "http"},
                                "initialDelaySeconds": 15,
                                "periodSeconds": 20,
                            },
                            "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                        }
                    ],
                    "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}],
                    "terminationGracePeriodSeconds": 20,
                },
            },
        },
    }

    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": request.name, "namespace": namespace, "labels": labels},
        "spec": {
            "type": "ClusterIP",
            "selector": {"app.kubernetes.io/name": request.name},
            "ports": [{"name": "http", "port": 80, "targetPort": "http", "protocol": "TCP"}],
        },
    }

    objects = [deployment, service]
    if request.hostname and request.tls_secret:
        objects.append(
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "Ingress",
                "metadata": {
                    "name": request.name,
                    "namespace": namespace,
                    "labels": labels,
                    "annotations": {"traefik.ingress.kubernetes.io/router.entrypoints": "websecure"},
                },
                "spec": {
                    "ingressClassName": "traefik",
                    "tls": [{"hosts": [request.hostname], "secretName": request.tls_secret}],
                    "rules": [
                        {
                            "host": request.hostname,
                            "http": {
                                "paths": [
                                    {
                                        "path": "/",
                                        "pathType": "Prefix",
                                        "backend": {"service": {"name": request.name, "port": {"name": "http"}}},
                                    }
                                ]
                            },
                        }
                    ],
                },
            }
        )
    return objects
