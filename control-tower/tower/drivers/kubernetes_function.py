from __future__ import annotations

import re
from typing import Any

from tower.cluster import ClusterNotFound, apply_object, get_json, run_kubectl


KEDA_NAMESPACE = "keda"
KEDA_INTERCEPTOR_SERVICE = "keda-add-ons-http-interceptor-proxy"
KEDA_SCALER_ADDRESS = "keda-add-ons-http-external-scaler.keda:9090"


def function_resource_name(workspace: str, name: str, resource_id: str) -> str:
    """Return a stable, collision-resistant Kubernetes name for this resource UUID."""
    prefix = f"fn-{workspace[:10]}-{name[:18]}".strip("-")
    suffix = re.sub(r"[^a-f0-9]", "", resource_id.lower())[:8]
    return f"{prefix[:50].rstrip('-')}-{suffix[:12]}"


def _labels(workspace: str, resource_id: str, name: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/managed-by": "gamgee",
        "gamgee.cloud/managed-by": "gamgee",
        "gamgee.cloud/tenant": workspace,
        "gamgee.cloud/resource-id": resource_id,
        "gamgee.cloud/function": name,
    }


def build_function_objects(resource: dict[str, Any]) -> list[dict[str, Any]]:
    """Compile typed Function intent into SpinApp + KEDA HTTP resources only."""
    workspace = resource["workspace"]
    spec = resource["desired"]
    key = function_resource_name(workspace, resource["name"], resource["id"])
    namespace = f"{workspace}-production"
    proxy_name = f"{key[:57]}-proxy"
    labels = _labels(workspace, resource["id"], resource["name"])
    path = spec["path"]

    hosts = [
        proxy_name,
        f"{proxy_name}.{namespace}.svc.cluster.local",
    ]
    if spec.get("hostname"):
        hosts.append(spec["hostname"])

    service_account = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": key,
            "namespace": namespace,
            "labels": labels,
        },
        "automountServiceAccountToken": False,
    }

    spin_app = {
        "apiVersion": "core.spinkube.dev/v1alpha1",
        "kind": "SpinApp",
        "metadata": {
            "name": key,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "executor": "containerd-shim-spin",
            "image": spec["image"],
            "enableAutoscaling": True,
            "serviceAccountName": key,
            "resources": {
                "requests": {"cpu": "25m", "memory": "64Mi"},
                "limits": {"cpu": "500m", "memory": "256Mi"},
            },
            "podLabels": labels,
        },
    }

    interceptor_route = {
        "apiVersion": "http.keda.sh/v1beta1",
        "kind": "InterceptorRoute",
        "metadata": {
            "name": key,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "target": {"service": key, "port": 80},
            "rules": [
                {"hosts": hosts, "paths": [{"value": path}]},
            ],
            "scalingMetric": {"concurrency": {"targetValue": spec["concurrency"]}},
            "coldStart": {"maxPendingRequests": 100},
        },
    }

    proxy_service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": proxy_name,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "type": "ExternalName",
            "externalName": f"{KEDA_INTERCEPTOR_SERVICE}.{KEDA_NAMESPACE}.svc.cluster.local",
            "ports": [{"name": "http", "port": 8080, "protocol": "TCP"}],
        },
    }

    scaled_object = {
        "apiVersion": "keda.sh/v1alpha1",
        "kind": "ScaledObject",
        "metadata": {
            "name": key,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "scaleTargetRef": {
                "name": key,
                "kind": "Deployment",
                "apiVersion": "apps/v1",
            },
            "minReplicaCount": spec["min_replicas"],
            "maxReplicaCount": spec["max_replicas"],
            "cooldownPeriod": spec["cooldown_seconds"],
            "triggers": [
                {
                    "type": "external-push",
                    "metadata": {
                        "scalerAddress": KEDA_SCALER_ADDRESS,
                        "interceptorRoute": key,
                    },
                }
            ],
        },
    }

    # The public Ingress belongs beside the shared interceptor service. The TLS
    # Secret is therefore intentionally looked up in the keda namespace.
    objects = [service_account, spin_app, interceptor_route, proxy_service, scaled_object]
    if spec.get("hostname") and spec.get("tls_secret"):
        objects.append(
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "Ingress",
                "metadata": {
                    "name": key,
                    "namespace": KEDA_NAMESPACE,
                    "labels": labels,
                    "annotations": {
                        "traefik.ingress.kubernetes.io/router.entrypoints": "websecure",
                    },
                },
                "spec": {
                    "ingressClassName": "traefik",
                    "tls": [
                        {
                            "hosts": [spec["hostname"]],
                            "secretName": spec["tls_secret"],
                        }
                    ],
                    "rules": [
                        {
                            "host": spec["hostname"],
                            "http": {
                                "paths": [
                                    {
                                        "path": path,
                                        "pathType": "Prefix",
                                        "backend": {
                                            "service": {
                                                "name": KEDA_INTERCEPTOR_SERVICE,
                                                "port": {"number": 8080},
                                            }
                                        },
                                    }
                                ]
                            },
                        }
                    ],
                },
            }
        )
    return objects


class KubernetesFunctionDriver:
    """SpinKube WebAssembly function driver with KEDA HTTP scale-to-zero."""

    kind = "Function"

    _RESOURCE_TYPES = {
        "ServiceAccount": "serviceaccount",
        "SpinApp": "spinapps.core.spinkube.dev",
        "InterceptorRoute": "interceptorroutes.http.keda.sh",
        "ScaledObject": "scaledobjects.keda.sh",
        "Service": "service",
        "Ingress": "ingress",
    }

    def _workspace_namespace(self, workspace: str) -> str:
        namespace = f"{workspace}-production"
        item = get_json("namespace", namespace)
        labels = item.get("metadata", {}).get("labels", {})
        if labels.get("gamgee.cloud/tenant") != workspace:
            raise RuntimeError("Workspace namespace is not owned by this GAMGEE tenant.")
        return namespace

    @staticmethod
    def _owned(item: dict[str, Any], resource: dict[str, Any]) -> bool:
        labels = item.get("metadata", {}).get("labels", {})
        return (
            labels.get("gamgee.cloud/managed-by") == "gamgee"
            and labels.get("gamgee.cloud/tenant") == resource["workspace"]
            and labels.get("gamgee.cloud/resource-id") == resource["id"]
        )

    def _existing(
        self,
        resource_type: str,
        name: str,
        namespace: str,
        resource: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            item = get_json(resource_type, name, namespace=namespace)
        except ClusterNotFound:
            return None
        if not self._owned(item, resource):
            raise RuntimeError(
                f"Refusing to change {resource_type} '{name}': it is not owned by this GAMGEE Function."
            )
        return item

    def _check_runtime(self) -> None:
        try:
            get_json("namespace", KEDA_NAMESPACE)
            get_json("customresourcedefinition", "spinapps.core.spinkube.dev")
            get_json("customresourcedefinition", "interceptorroutes.http.keda.sh")
            get_json("customresourcedefinition", "scaledobjects.keda.sh")
        except ClusterNotFound as exc:
            raise RuntimeError(
                "Native Functions are not installed. Apply the GAMGEE Function Cloud profile "
                "(SpinKube + KEDA HTTP add-on) before creating a Function."
            ) from exc

    def reconcile(self, resource: dict[str, Any]) -> dict[str, Any]:
        if resource["desired"].get("deleted"):
            return self._delete(resource)

        namespace = self._workspace_namespace(resource["workspace"])
        self._check_runtime()
        spec = resource["desired"]
        if spec.get("hostname") and spec.get("tls_secret"):
            secret = get_json("secret", spec["tls_secret"], namespace=KEDA_NAMESPACE)
            if (
                secret.get("type") != "kubernetes.io/tls"
                or not {"tls.crt", "tls.key"}.issubset(secret.get("data", {}))
            ):
                raise RuntimeError(
                    "The referenced public Function TLS Secret must exist in the keda namespace "
                    "and contain tls.crt and tls.key."
                )

        objects = build_function_objects(resource)
        key = function_resource_name(resource["workspace"], resource["name"], resource["id"])
        # Preflight every object before applying anything. A conflict on a later
        # object must not leave earlier GAMGEE objects partially created.
        for obj in objects:
            obj_namespace = obj["metadata"]["namespace"]
            resource_type = self._RESOURCE_TYPES[obj["kind"]]
            self._existing(
                resource_type,
                obj["metadata"]["name"],
                obj_namespace,
                resource,
            )

        # Keep this order: KEDA's external-push scaler needs the InterceptorRoute
        # to exist before it registers the ScaledObject.
        for obj in objects:
            apply_object(obj)

        # Remove a former public route when the desired spec becomes private.
        if not spec.get("hostname"):
            old_ingress = self._existing("ingress", key, KEDA_NAMESPACE, resource)
            if old_ingress is not None:
                run_kubectl(
                    ["delete", f"ingress/{key}", "--namespace", KEDA_NAMESPACE,
                     "--ignore-not-found=true", "--wait=false"]
                )
        return self.observe(resource)

    def observe(self, resource: dict[str, Any]) -> dict[str, Any]:
        namespace = self._workspace_namespace(resource["workspace"])
        key = function_resource_name(resource["workspace"], resource["name"], resource["id"])
        spin_app = get_json("spinapps.core.spinkube.dev", key, namespace=namespace)
        if not self._owned(spin_app, resource):
            raise RuntimeError("SpinApp is no longer owned by this GAMGEE Function.")

        route = self._existing("interceptorroutes.http.keda.sh", key, namespace, resource)
        scaled = self._existing("scaledobjects.keda.sh", key, namespace, resource)
        proxy_name = f"{key[:57]}-proxy"
        proxy = self._existing("service", proxy_name, namespace, resource)
        if route is None or scaled is None or proxy is None:
            return {"state": "progressing", "namespace": namespace, "runtime": "spin"}

        status = spin_app.get("status", {})
        deployment_name = status.get("deploymentName") or key
        service_name = status.get("serviceName") or key
        try:
            deployment = get_json("deployment.apps", deployment_name, namespace=namespace)
            service = get_json("service", service_name, namespace=namespace)
        except ClusterNotFound:
            return {
                "state": "progressing",
                "namespace": namespace,
                "runtime": "spin",
                "deployment": deployment_name,
                "service": service_name,
            }

        deployment_spec = deployment.get("spec", {})
        deployment_status = deployment.get("status", {})
        desired_replicas = int(deployment_spec.get("replicas", 0) or 0)
        ready_replicas = int(deployment_status.get("readyReplicas", 0) or 0)
        if desired_replicas == 0 and resource["desired"]["min_replicas"] == 0:
            state = "idle"
        elif desired_replicas > 0 and ready_replicas >= desired_replicas:
            state = "ready"
        else:
            state = "progressing"

        hostname = resource["desired"].get("hostname")
        path = resource["desired"]["path"]
        public_url = f"https://{hostname}{path}" if hostname else None
        internal_url = (
            f"http://{proxy_name}.{namespace}.svc.cluster.local:8080{path}"
        )
        return {
            "state": state,
            "runtime": "spin-wasm",
            "namespace": namespace,
            "spin_app": key,
            "deployment": deployment_name,
            "service": service_name,
            "desired_replicas": desired_replicas,
            "ready_replicas": ready_replicas,
            "min_replicas": resource["desired"]["min_replicas"],
            "max_replicas": resource["desired"]["max_replicas"],
            "image": resource["desired"]["image"],
            "internal_url": internal_url,
            "public_url": public_url,
            "scaled_object": key,
            "interceptor_route": key,
        }

    def _delete(self, resource: dict[str, Any]) -> dict[str, Any]:
        workspace = resource["workspace"]
        namespace = f"{workspace}-production"
        key = function_resource_name(workspace, resource["name"], resource["id"])
        proxy_name = f"{key[:57]}-proxy"

        try:
            get_json("namespace", KEDA_NAMESPACE)
        except ClusterNotFound:
            keda_available = False
        else:
            keda_available = True

        # Check ownership before issuing any delete. Never remove a similarly
        # named object belonging to another workspace or operator.
        if keda_available:
            self._existing("ingress", key, KEDA_NAMESPACE, resource)
        try:
            self._workspace_namespace(workspace)
        except ClusterNotFound:
            workspace_available = False
        else:
            workspace_available = True

        if workspace_available:
            for resource_type, name in (
                ("serviceaccount", key),
                ("spinapps.core.spinkube.dev", key),
                ("interceptorroutes.http.keda.sh", key),
                ("scaledobjects.keda.sh", key),
                ("service", proxy_name),
            ):
                self._existing(resource_type, name, namespace, resource)

        if keda_available:
            run_kubectl(
                ["delete", f"ingress/{key}", "--namespace", KEDA_NAMESPACE,
                 "--ignore-not-found=true", "--wait=false"]
            )
        if workspace_available:
            for resource_name, name in (
                ("scaledobjects.keda.sh", key),
                ("interceptorroutes.http.keda.sh", key),
                ("service", proxy_name),
                ("spinapps.core.spinkube.dev", key),
                ("serviceaccount", key),
            ):
                run_kubectl(
                    ["delete", f"{resource_name}/{name}", "--namespace", namespace,
                     "--ignore-not-found=true", "--wait=false"]
                )

        remaining: list[str] = []
        if keda_available and self._existing("ingress", key, KEDA_NAMESPACE, resource) is not None:
            remaining.append("ingress")
        if workspace_available:
            for resource_type, name, label in (
                ("spinapps.core.spinkube.dev", key, "spinapp"),
                ("interceptorroutes.http.keda.sh", key, "interceptorroute"),
                ("scaledobjects.keda.sh", key, "scaledobject"),
                ("service", proxy_name, "proxy_service"),
                ("serviceaccount", key, "service_account"),
            ):
                if self._existing(resource_type, name, namespace, resource) is not None:
                    remaining.append(label)
        return {
            "state": "deleting" if remaining else "deleted",
            "namespace": namespace,
            "remaining_resources": remaining,
        }
