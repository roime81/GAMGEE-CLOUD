import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


_TENANT_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProvisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=2000)
    tenant_name: str = Field(min_length=1, max_length=63)
    owner_email: str = Field(min_length=3, max_length=254)

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must not be blank")
        return value

    @field_validator("tenant_name")
    @classmethod
    def tenant_name_must_be_dns_label(cls, value: str) -> str:
        if not _TENANT_NAME.fullmatch(value):
            raise ValueError("tenant_name must be a lowercase DNS label")
        return value

    @field_validator("owner_email")
    @classmethod
    def owner_email_must_be_valid(cls, value: str) -> str:
        if not _EMAIL.fullmatch(value):
            raise ValueError("owner_email must be a valid email address")
        return value


def build_tenant_manifest(request: ProvisionRequest) -> dict:
    return {
        "apiVersion": "capsule.clastix.io/v1beta2",
        "kind": "Tenant",
        "metadata": {"name": request.tenant_name},
        "spec": {
            "owners": [{"kind": "User", "name": request.owner_email}],
            "additionalNamespaces": [
                f"{request.tenant_name}-production",
                f"{request.tenant_name}-staging",
            ],
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
                        "gamgee.cloud/tenant": request.tenant_name,
                    }
                }
            },
        },
    }
