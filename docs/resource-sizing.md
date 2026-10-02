# Resource sizing: small, medium and large deployments

These are initial planning profiles, **not benchmarked capacity guarantees**. Validate
with your own largest full collection and historical snapshot refresh before setting
production limits. Relationship density, snapshot size, retention and simultaneous
users can matter more than VM count. An OOM means the configured allocation was
insufficient for that run; a PVC does not add RAM.

## Choose a starting profile

Use the highest applicable tier. Inventory counts below refer to the largest individual
NSX environment; Manager counts refer to the whole application deployment. The object
count is the combined number of groups, services and DFW rules, not resolved members.

| Profile | Illustrative deployment | Collection concurrency to start with |
| --- | --- | --- |
| Small | 1–2 Managers; up to 1,000 VMs and 5,000 objects per environment; a few users | 1 worker replica |
| Medium | 3–5 Managers, or up to 10,000 VMs and 25,000 objects per environment | 1 worker; consider 2 if queues persist |
| Large | More than 5 Managers, over 10,000 VMs or 25,000 objects, or dense tag/group/rule relationships | Size one worker for the largest environment first; add replicas only after measuring |

Move up a tier if actual peak memory approaches the limit, even if object counts are
small. Many shared tags linking thousands of VMs to many groups/rules can create large
amounts of relationship evidence. Retaining many snapshots increases database storage
and history-analysis work; it does not mean a refresh loads every snapshot at once.

## Per-container starting allocations

Each cell gives **CPU request; memory request / memory limit**. CPU `1` is one vCPU;
`500m` is half a vCPU. Memory uses binary GiB/MiB. Values apply per replica, not to the
whole namespace. The snapshot refresh Job below is an **optional maintenance recommendation**,
available as a Compose service and a separate Kubernetes maintenance manifest. It
is excluded from normal startup/sync and uses its own memory allocation. See
[snapshot maintenance](snapshot-maintenance.md) for commands. New collections prepare
their snapshot indexes automatically in the worker.

| Workload | Small | Medium | Large |
| --- | --- | --- | --- |
| Web | 250m; 1Gi / 2Gi | 500m; 2Gi / 4Gi | 1; 4Gi / 8Gi |
| Collection worker | 500m; 2Gi / 4Gi | 1; 4Gi / 8Gi | 2; 8Gi / 16Gi |
| Scheduler | 100m; 256Mi / 512Mi | 100m; 256Mi / 512Mi | 250m; 512Mi / 1Gi |
| Migration Job | 250m; 512Mi / 2Gi | 500m; 1Gi / 4Gi | 1; 2Gi / 8Gi |
| Optional snapshot refresh Job | 1; 4Gi / 8Gi | 2; 8Gi / 16Gi | 2; 16Gi / 32Gi |
| Dedicated PostgreSQL instance | 500m; 2Gi / 4Gi | 1; 4Gi / 8Gi | 2; 8Gi / 16Gi |

The large tier is not an upper bound: a particularly large snapshot may require more
memory or further application optimization. For an existing/shared PostgreSQL service,
ask the DBA to size total workload, connection counts, cache and maintenance memory;
do not apply this dedicated-instance table to an entire shared database blindly.

A worker processes one collection at a time and launches a collector subprocess in
its container. Both processes share the same limit. Additional worker replicas permit
more concurrent environments; they do not provide extra memory to a single collection.
Keep one scheduler replica initially. Adding Gunicorn processes or web replicas also
increases total memory and database connections; measure before increasing either.

CPU limits are intentionally not prescribed here. If your platform requires them,
start with burst capacity above the request and monitor throttling. Kubernetes schedules
using requests; a memory limit can result in OOM termination and a CPU limit can throttle
work. See [Kubernetes resource management](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/).

## Apply settings in Kubernetes / Argo CD

Edit the container's `resources` block in your deployment repository. For example,
this is the medium collection worker allocation:

```yaml
resources:
  requests:
    cpu: "1"
    memory: 4Gi
  limits:
    memory: 8Gi
```

- `deploy/kubernetes/manifests/application.yaml`: separate blocks for web, worker and scheduler.
- `deploy/kubernetes/manifests/migrate.yaml`: migration Job.
- Optional refresh: allocate resources in the temporary Job created with the [manual maintenance procedure](snapshot-maintenance.md#kubernetes-create-a-temporary-job-manually). No refresh Job is included in deployment manifests.
- `deploy/kubernetes/manifests/database.yaml`: bundled PostgreSQL only.

Commit changes to the repository watched by Argo CD and sync. Deployment resource
changes replace application pods; schedule worker changes between collections where
possible. A completed/failed maintenance Job must be deleted and recreated to change
its pod resources. Preserve its logs first. This does not mean deleting the database,
PVC or namespace. Argo CD migration-hook handling is covered in the
[Kubernetes guide](../deploy/kubernetes/README.md#argo-cd-deployment-and-upgrades).

The default Kubernetes manifests and optional refresh Job use the **medium** profile
above. Profiles are not selected automatically; adjust the manifests for your workload.
Check the actual running pod after sync; namespace defaults or policy can alter resources.

## Plan cluster capacity

Add requests across all replicas for scheduling, and separately plan for simultaneous
memory peaks. Include the refresh and migration Jobs, monitoring sidecars and other
workloads on the same nodes. A maintenance Job's request must fit on one eligible node.

For medium with one web, one worker, one scheduler and dedicated PostgreSQL:
normal memory requests total 10.25Gi and limits total 20.5Gi. One refresh Job adds an
8Gi request and a 16Gi limit. These are workload totals, not recommended node sizes;
allow room for Kubernetes/system processes and other applications. Avoid launching
multiple refresh Jobs or overlapping a large refresh with peak collection activity.

## Docker Compose / Docker Desktop

The same per-container memory starting points apply. Kubernetes YAML is not interpreted
by Docker Compose. Set a Compose override for the relevant service, for example:

```yaml
services:
  worker:
    mem_limit: 8g
    cpus: "2"
```

Here `cpus` is a CPU cap, not the Kubernetes CPU request. Include the override on every
Compose command. Make sure Docker Desktop's VM memory budget or the Docker host has
enough RAM for the full stack plus maintenance activity. A larger container limit
cannot supply RAM the host does not have.

## Measure, then adjust

1. Run a full collection for the largest environment, including saving/indexing the snapshot.
2. Test report browsing, historical activity and comparison with realistic concurrent users.
3. Run a targeted refresh in a separate Job using the same image version as the tested application.
4. Record peak container memory, CPU/throttling, run duration, restarts and database growth.
5. As an initial heuristic, set memory limits about 30–50% above the highest measured
   representative peak. Set requests to realistic expected concurrent usage, and higher
   for predictable memory-heavy Jobs. Repeat measurements after growth or feature changes.

```sh
kubectl -n nsx-security-analyzer top pods --containers
kubectl -n nsx-security-analyzer describe pod <pod-name>
kubectl -n nsx-security-analyzer logs <pod-name> --all-containers --tail=100
```

`kubectl top` requires Metrics Server and shows sampled current usage; it can miss short
peaks. Use your monitoring system's historical container-memory data as well. Indexing
`peak_rss_mib` checkpoints, where supported by the build, measure process lifetime peak
RSS, not all processes in the pod. After `OOMKilled`, the observed surviving samples
can understate the required memory. Do not treat a failed run as a measured safe peak.

## Database storage is separate from RAM

Only the database needs persistent storage for snapshots and indexes. Application,
worker, scheduler and refresh containers do not need a shared report PVC. For an
external database, manage storage on that database service.

Estimate capacity from **measured database growth per completed indexed snapshot**,
multiplied by collections per day, retention days and environments; then include
existing data, WAL, indexes, backup policy and maintenance headroom. Do not size from
raw report JSON alone. Refresh can temporarily retain old rows while inserting new
ones in a transaction. Leave substantial free space and monitor growth rather than
using a fixed VM-to-disk ratio. The bundled 10Gi PVC is an example, not a production
storage recommendation. See [operations](operations.md) for backup/retention guidance.
