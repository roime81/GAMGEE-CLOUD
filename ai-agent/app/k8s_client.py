import os

from kubernetes import client, config


def create_tenant(manifest: dict) -> None:
    if os.getenv("KUBERNETES_SERVICE_HOST"):
        config.load_incluster_config()
    else:
        config.load_kube_config()

    client.CustomObjectsApi().create_cluster_custom_object(
        group="capsule.clastix.io",
        version="v1beta2",
        plural="tenants",
        body=manifest,
    )
