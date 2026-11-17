# 05 — Compute, Servers and Scaling

## The GAMGEE cell

A current GAMGEE cell consists of one Control Tower installation and one K3s cluster. The cluster has one server/control-plane node and zero or more agent/worker nodes.

The server hosts the Kubernetes control plane and platform services. Worker nodes contribute schedulable CPU and memory for workloads. This is horizontal workload capacity, not high availability of the Control Tower or Kubernetes control plane.

## Deployment sizes: minimum to larger footprints

These are logical deployment patterns, not certified hardware sizing tiers. Select actual CPU, RAM, storage and network capacity after measuring workload requirements.

| Pattern | Topology | Intended use | Current support |
|---|---|---|---|
| S0 — Lab | One Linux host, one K3s server | Learning, development, disposable tests | Core install path |
| S1 — Small cell | One server with modest local workloads | Small internal services | Supported single-server shape |
| S2 — Expanded cell | One server plus one or more K3s agents | More replicas and aggregate compute | Worker join path implemented |
| S3 — Dedicated workload pools | Server plus workers labelled by workload class | General, memory-heavy or Spin/WASM scheduling | Requires labels/affinity discipline; no complete pool manager |
| S4 — Resilient cell | Three control-plane servers, resilient storage and network | Control-plane availability | Target design; HA workflow not delivered |
| S5 — Multi-cell estate | Multiple independent cells | Site/region/environment separation | Planned |

A larger worker count does not make the control plane resilient. A single server remains a single point of failure in the current topology.

## Scale up: make a node larger

Scale-up means increasing capacity of an existing server or worker: CPU, memory, disk throughput or network bandwidth.

1. Measure utilisation and workload requests/limits.
2. Confirm host/hypervisor capacity.
3. Schedule maintenance if reboot is required.
4. Increase VM or physical host allocation.
5. Verify Linux sees new CPU, memory and disk capacity.
6. Confirm K3s and kubelet health.
7. Re-evaluate scheduling and storage capacity.
8. Record the change and compare latency/error rates.

GAMGEE does not currently provide a general Machine resource lifecycle to resize arbitrary VMs. This is performed at the underlying host/hypervisor layer today.

## Scale out: add worker nodes

Scale-out adds agent nodes to the existing K3s cluster. The repository provides scripts/join-compute-node.sh.

Prepare the joining host and retrieve the cluster join details, including server endpoint and node token, securely from the server. Never commit the token or share it in logs.

Review the exact script interface in the checked-out version:

    sed -n '1,240p' scripts/join-compute-node.sh

Run the script on the joining host with its documented arguments/environment:

    sudo bash scripts/join-compute-node.sh

After joining, verify from the server:

    sudo kubectl get nodes -o wide
    sudo kubectl describe node <worker-name>

Wait until the node is Ready. Confirm labels, taints, runtime availability, storage mounts and network reachability before scheduling workloads.

## Scheduling and workload capacity

Kubernetes schedules pods based on resource requests, node allocatable capacity, taints/tolerations, affinity and other constraints. Adding a node does not guarantee even workload spread or automatic replica growth.

For Applications, the current driver supports lifecycle and scale operations through its constrained API/UI. For Functions, KEDA HTTP Add-on scales Spin workloads based on HTTP concurrency and can scale to zero when configured.

Capacity planning should include CPU/memory requests and limits, system pods, image cache and registry bandwidth, persistent storage IOPS/throughput, east-west and north-south network, pod/IP density, GPU resources if used, and observability retention.

## Target compute resource model

The long-term direction is to make compute an outcome-oriented resource:

1. **Application** — stateless OCI workload on the cell.
2. **Function** — immutable WASM artifact with HTTP-driven scaling.
3. **Machine** — VM managed through a future Incus/QEMU/KVM driver.
4. **Service** — managed database, queue, object or AI service through a future adapter.
5. **Placement** — select an eligible cell or workload pool using policy and capacity.
6. **Elasticity** — scale replicas or compute capacity according to approved policies and observed demand.

Only Application and the first HTTP/WASM Function slices are currently registered Resource Engine drivers. Machine and Service lifecycle operations are planned.

## Servers and control-plane availability

For resilient K3s, the target is an odd-numbered embedded-etcd control-plane quorum (commonly three servers), stable API endpoint, resilient storage, backups and tested recovery. This is a target architecture, not an instruction to add two servers to the current single-server install and assume HA is complete.

A complete HA design needs server bootstrap/join automation, API endpoint failover, etcd backup/restore, certificate lifecycle, quorum recovery, storage replication, Control Tower state durability, distributed worker coordination and tested upgrade sequencing.

## Storage and stateful workloads

The current Application schema is intentionally stateless. Persistent volumes, database attachment, arbitrary volume mounts and durable state bindings are not part of that API. Do not place important state in an ephemeral container filesystem. Until a supported GAMGEE storage/service lifecycle is delivered, provision and back up persistent data independently.

## Autoscaling boundaries

- Application replica changes are explicit through current lifecycle controls; do not assume CPU-based HPA is automatically configured.
- Function HTTP concurrency scaling is implemented through KEDA HTTP Add-on for the supported Spin/WASM path.
- Node autoscaling and cloud-provider capacity acquisition are not delivered as a general GAMGEE feature.
- Multi-cell placement, workload migration and automatic VM resizing are planned.

## Scale validation checklist

Confirm nodes are Ready and correctly labelled, workloads schedule as intended, replica count matches desired state, readiness checks pass, requests are served, storage/network are not bottlenecks, metrics show headroom, tenant policy remains intact and rollback/node-drain procedures are understood.
