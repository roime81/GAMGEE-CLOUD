# 04 — Installation and First Deployment

## Scope

The primary guided installation path targets a fresh Debian/Ubuntu Linux host and installs the GAMGEE Control Tower service. The Control Tower then guides installation of the Core Cloud profile and optional bundles.

This is an engineering-preview installation. It is not a multi-node HA bootstrap and does not establish production-grade tenant isolation, backup, identity or disaster recovery.

## Before you begin

Prepare a supported Debian/Ubuntu Linux host with sudo/root access, stable network connectivity, SSH administration, sufficient disk space and a secure place to store the generated Control Tower token. Use a disposable environment for first-time testing.

The repository does not publish a universal minimum CPU/RAM/disk profile that is safe for every optional bundle. Capacity depends on selected profiles, workload count, image sizes, model sizes and local inference. Start with Core and add bundles only after reviewing available capacity.

## Obtain and inspect the repository

    git clone https://github.com/roime81/GAMGEE-CLOUD.git
    cd GAMGEE-CLOUD
    less scripts/install-gamgee.sh
    less scripts/install-control-tower.sh
    less automation/ansible/core-cloud.yml

Review installation scripts and playbooks before running them.

## Guided installation

    sudo bash scripts/install-gamgee.sh

The guided installer prepares prerequisites and the Python virtual environment, creates the local token/environment file, registers gamgee-control-tower.service with systemd and starts the service.

The current service binds to 127.0.0.1:8091. This loopback binding is intentional. Do not expose the privileged bearer token or service on an untrusted network.

Check service status:

    sudo systemctl status gamgee-control-tower
    sudo journalctl -u gamgee-control-tower -n 100 --no-pager

## Remote administration

Use an SSH tunnel from the administrator workstation:

    ssh -L 8091:127.0.0.1:8091 <admin-user>@<gamgee-host>

Then open http://127.0.0.1:8091. Use the generated installation token when prompted. Treat this token as a privileged cloud-administration credential. Do not paste it into issues, chat, source control or shell history.

## Manual installation mode

    sudo bash scripts/install-control-tower.sh
    sudo bash scripts/run-control-tower.sh

Manual mode defaults to planning-only. To explicitly allow applying an approved plan:

    sudo env GAMGEE_TOWER_ALLOW_APPLY=true bash scripts/run-control-tower.sh

Only enable apply in a controlled local installation. The guided installer enables apply for the local loopback-bound service so an owner can approve the initial installation in the UI.

## Install Core Cloud

From the Control Tower UI:

1. Review the installation catalogue and dependency plan.
2. Select the Core Cloud foundation.
3. Inspect planned actions and host requirements.
4. Approve only after reviewing the scope.
5. Wait for the installation job to complete.
6. Review job output and cluster status.
7. Confirm the K3s server is Ready before creating workloads.

Core Cloud is the minimum starting point. Add optional bundles deliberately.

## Create a Workspace and deploy an Application

The current Workspace API creates Capsule-backed tenant boundaries with production and staging namespaces and quotas. The Application API accepts a constrained OCI image reference and workload options.

1. Create a Workspace with a unique name.
2. Confirm production and staging namespaces exist.
3. Select an image reachable by the cluster.
4. Submit a typed Application resource with explicit CPU/memory bounds.
5. Keep exposure private unless external access is required.
6. If public TLS ingress is needed, create the required TLS Secret in the expected namespace before referencing it.
7. Watch the Resource Engine operation until ready or an actionable error is reported.

The exact request schema and API paths are versioned in source. Use the running API's OpenAPI document and resource models as the source of truth; do not submit arbitrary Kubernetes YAML.

## Verify installation

    sudo systemctl is-active gamgee-control-tower
    sudo kubectl get nodes -o wide
    sudo kubectl get namespaces
    sudo kubectl get pods -A

Confirm Control Tower is active, the server is Ready, core pods are healthy, workspace namespaces exist and a test Application reaches ready and responds on its intended route.

## Troubleshooting

| Symptom | Checks |
|---|---|
| UI/API unavailable | systemd status, journal logs, port 8091, firewall and SSH tunnel |
| Installation failed | Job detail, Ansible output, DNS, package mirrors, disk and permissions |
| Node NotReady | Node status, K3s logs, CNI and connectivity |
| Application stuck progressing | Deployment/Pod events, image pull, CPU/memory and readiness |
| Ingress unreachable | Traefik, DNS, firewall, Ingress rules and TLS Secret namespace/type |
| Function not scheduled | Spin runtime and required spin=true node label |
| API returns 401 | Bearer token source and supplied token |

## Uninstall and destructive operations

Do not use make clean or scripts/teardown.sh as a routine stop/restart mechanism. The teardown path removes the K3s installation and cluster workloads on the host.

Before teardown, identify affected workloads and data, export configuration and backups, verify persistent data recovery, obtain approval and test on a disposable host.
