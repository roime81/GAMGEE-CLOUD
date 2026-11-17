from __future__ import annotations

from typing import Any

from tower.cluster import ClusterError, ClusterNotFound, apply_objects, get_json, run_kubectl
from tower.resource_guard import is_gamgee_managed, is_gamgee_resource
from tower.workloads import WorkloadRequest, build_workload_objects


class KubernetesApplicationDriver:
    kind = "Application"

    def _workspace_namespace(self, workspace: str) -> str:
        namespace = f"{workspace}-production"
        item = get_json("namespace", namespace)
        labels = item.get("metadata", {}).get("labels", {})
        if labels.get("gamgee.cloud/tenant") != workspace:
            raise RuntimeError("Workspace namespace is not owned by this GAMGEE tenant.")
        return namespace

    def _existing(self, resource_kind: str, name: str, namespace: str, resource_id: str, workspace: str) -> dict[str, Any] | None:
        try:
            item = get_json(resource_kind, name, namespace=namespace)
        except ClusterNotFound:
            return None
        if not is_gamgee_managed(item, workspace) or not is_gamgee_resource(item, resource_id):
            raise RuntimeError(
                f"Refusing to change {resource_kind} '{name}': it is not owned by this GAMGEE resource."
            )
        return item

    def reconcile(self, resource: dict[str, Any]) -> dict[str, Any]:
        workspace = resource["workspace"]
        name = resource["name"]
        resource_id = resource["id"]
        namespace = f"{workspace}-production"

        if resource["desired"].get("deleted"):
            try:
                self._workspace_namespace(workspace)
            except ClusterNotFound:
                # Namespace deletion already removed every namespaced object.
                return {"state": "deleted", "namespace": namespace, "reason": "workspace_namespace_absent"}
            for kind in ("deployment.apps", "service", "ingress"):
                self._existing(kind, name, namespace, resource_id, workspace)
            run_kubectl(
                [
                    "delete",
                    f"deployment/{name}",
                    f"service/{name}",
                    f"ingress/{name}",
                    "--namespace",
                    namespace,
                    "--ignore-not-found=true",
                    "--wait=false",
                ]
            )
            return {"state": "deleted", "namespace": namespace}

        self._workspace_namespace(workspace)
        desired = dict(resource["desired"])
        restart_token = desired.pop("_gamgee_restart_token", None)
        desired.pop("_gamgee_previous_replicas", None)
        request = WorkloadRequest.model_validate(desired)
        objects = build_workload_objects(workspace, request, resource_id=resource_id, restart_token=restart_token)

        wanted_ingress = any(item["kind"] == "Ingress" for item in objects)
        for kind in ("deployment.apps", "service", "ingress"):
            existing = self._existing(kind, name, namespace, resource_id, workspace)
            if kind == "ingress" and existing and not wanted_ingress:
                run_kubectl(["delete", f"ingress/{name}", "--namespace", namespace, "--ignore-not-found=true"])

        if request.tls_secret:
            secret = get_json("secret", request.tls_secret, namespace=namespace)
            if (
                secret.get("type") != "kubernetes.io/tls"
                or not {"tls.crt", "tls.key"}.issubset(secret.get("data", {}))
            ):
                raise RuntimeError("The referenced Secret is not a valid Kubernetes TLS Secret.")

        apply_objects(objects)

        deployment = get_json("deployment.apps", name, namespace=namespace)
        status = deployment.get("status", {})
        spec = deployment.get("spec", {})
        containers = (
            deployment.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        desired_replicas = int(spec.get("replicas", 1))
        ready_replicas = int(status.get("readyReplicas", 0))
        state = "stopped" if desired_replicas == 0 else (
            "ready" if ready_replicas >= desired_replicas else "progressing"
        )
        return {
            "state": state,
            "namespace": namespace,
            "desired_replicas": desired_replicas,
            "ready_replicas": ready_replicas,
            "available_replicas": int(status.get("availableReplicas", 0)),
            "image": containers[0].get("image") if containers else request.image,
            "private_by_default": request.hostname is None,
            "url": f"https://{request.hostname}" if request.hostname else None,
            "kubernetes_generation": deployment.get("metadata", {}).get("generation"),
        }
