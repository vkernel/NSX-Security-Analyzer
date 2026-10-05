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
available as an explicitly invoked Compose service or a manually created temporary
Kubernetes Job. It is excluded from normal startup/sync and uses its own memory allocation. See
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

The default Kubernetes manifests and documented manual refresh Job example use the **medium** profile
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

## Database disk sizing

Only PostgreSQL needs persistent storage for snapshots and indexes. Web, worker,
scheduler and refresh containers do not need a shared report PVC. For an external
or operator-managed database, change storage on that service; changing the bundled
`database.yaml` does not resize an external database.

These are **illustrative initial disk budgets per PostgreSQL instance**, not tested
capacity guarantees. Choose the larger of this starting budget and the measured
retention requirement below. Even a small environment can exceed the large budget
with frequent collections, long retention or dense relationships.

| Profile | Data budget (tables, indexes, free space) | Additional WAL budget | Single combined volume | Three instances, combined volumes |
| --- | --- | --- | --- | --- |
| Small | 50Gi | 20Gi | 70Gi | 210Gi |
| Medium | 200Gi | 50Gi | 250Gi | 750Gi |
| Large | 500Gi | 100Gi | 600Gi | 1,800Gi |

Each physical PostgreSQL replica holds a full copy, not one third of the database.
The totals exclude backups, storage snapshots and any additional replication performed
by the storage platform. WAL (write-ahead log) supports crash recovery and replication;
its required space depends on write rate, refreshes, archiving and replica lag, not
just the retained database size. The WAL budgets above are not configured WAL limits.

With a single data PVC, tables and WAL normally share capacity. If your database
operator uses a separate WAL PVC, size and monitor **both** filesystems. CloudNativePG
supports separate WAL storage and PVC expansion when supported by the StorageClass;
follow its version-specific procedure for existing clusters. Do not assume adding
`walStorage` will migrate an existing installation automatically. See
[CloudNativePG storage](https://cloudnative-pg.io/docs/1.26/storage/).

The bundled **10Gi PVC is a demonstration default**, not a medium production allocation.
CPU and memory defaults in the manifests do not automatically resize it. For an
existing volume, request an increase through your database/operator configuration in
Git and verify that the StorageClass permits expansion; Kubernetes does not support
shrinking PVCs. Never delete a database PVC or files inside `pg_wal` to free space.

### Calculate the retention requirement

Measure complete, indexed snapshots from representative environments. Raw report JSON
size is insufficient: derived records, search evidence, relationships and database
indexes add storage.

```text
retained snapshot bytes = sum, across environments, of:
    average indexed snapshot footprint × collections per day × retention days

required combined capacity =
    (baseline data + retained snapshot bytes + audit/history growth
     + maintenance allowance + peak retained WAL) / 0.70
```

This calculation targets approximately **30% free space** as an initial operational
margin; adjust it for measured write bursts and recovery time. Do not count the same
indexes twice if already included in the measured snapshot footprint. Backups stored
on the same filesystem need an additional allowance; preferably budget them separately.

For example, **100MiB per indexed snapshot × 24 collections/day × 180 days is about
422Gi per environment**, before WAL and headroom. Six-hourly collection would retain
about 70Gi at the same retention, but reduces observation frequency. Choose collection
frequency and retention to match the history you need, not just available disk.

Measure database size before and after several completed collections during a quiet
period. Existing free pages, concurrent cleanup and autovacuum can make net growth
understate the snapshot footprint. Combine those measurements with table sizes and
longer-term growth monitoring. Refresh deletes and rebuilds derived records inside a
transaction: old versions and replacement data can coexist until cleanup is possible.
Allow room for the largest rebuild and its WAL, not only steady-state retained data.

### Check usage and alert before exhaustion

Administrators can open **Administration → System health** for current web-container
CPU, memory and filesystem usage, application database size and its ten largest tables.
Use **Refresh measurements** for a new sample. Container metrics require readable Linux
cgroups; unavailable measurements are labeled explicitly. The page does not measure
other pods, PostgreSQL free disk, WAL or replica storage. Database queries have short
timeouts and do not read snapshot payloads. The existing `/health/` readiness endpoint
remains separate from this authenticated diagnostic page.

Run these read-only SQL queries against the application database:

```sql
-- Database size includes tables/indexes/TOAST, but not the cluster's WAL directory.
SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;

-- Largest relations, including their indexes and out-of-line JSON/text (TOAST).
SELECT relname,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
       pg_size_pretty(pg_table_size(relid)) AS table_and_toast,
       pg_size_pretty(pg_indexes_size(relid)) AS indexes
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 20;

-- Row counts are estimates; look for cleanup falling behind.
SELECT relname, n_live_tup, n_dead_tup, last_autovacuum, last_autoanalyze
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC
LIMIT 20;
```

Ask the DBA to inspect WAL usage, inactive replication slots, replica lag and failed
archiving as well. A database-size query cannot tell you how much free space remains
on its filesystem. Check **every database instance**, including replicas. Inside a
running PostgreSQL container with `PGDATA` set:

```sh
df -h "$PGDATA" "$PGDATA/pg_wal"
```

For Kubernetes volume configuration:

```sh
kubectl -n nsx-security-analyzer get pvc
kubectl get storageclass
```

Start with alerts at **70% used (warning)** and **85% used (critical)** on data and WAL
filesystems, then tune for growth rate. Also alert when projected time-to-full is less
than your expansion/recovery lead time. Monitor volume latency/IOPS, WAL generation,
replication lag, failed archiving and database-container memory. A replica reporting
`no free disk space for WALs` needs its storage investigated even if the primary is healthy.

## PostgreSQL resource efficiency

### Operational settings to review first

- **Retention:** verify it is enabled on the deployed installation. New installations
  default to 180 days of full snapshots, 7 days of testing snapshots and 180 days of
  collection jobs; existing settings are preserved. Shorten retention or reduce
  collection frequency only when acceptable for historical analysis. Audit-event
  retention is separate. See [operations](operations.md).
- **Maintenance scheduling:** run refresh only when needed, with collection activity
  paused, following [snapshot maintenance](snapshot-maintenance.md). New collections
  already build their indexes. Repeated full refreshes add substantial writes and WAL.
- **Autovacuum and statistics:** keep them enabled and monitor large snapshot tables
  after retention cleanup. Normal VACUUM makes dead-row space reusable; it generally
  does not return that space to the filesystem. `VACUUM FULL` requires an exclusive
  lock and additional disk for a rewrite; it is not an emergency low-space remedy.
  See [PostgreSQL vacuuming](https://www.postgresql.org/docs/current/routine-vacuuming.html).
- **Memory and connections:** allocate requests/limits to every database instance,
  including operator-managed replicas. Keep connection counts bounded as application
  replicas grow. Tune `shared_buffers` against the database container's memory budget,
  not the Kubernetes node's RAM. `work_mem` can be consumed by multiple operations
  and parallel workers in each connection; a large global value can cause OOMs.
  See [PostgreSQL memory settings](https://www.postgresql.org/docs/current/runtime-config-resource.html).
- **WAL:** investigate lagging replicas, replication slots and archive failures before
  merely adding capacity. `max_wal_size` is a soft checkpoint target, not a disk cap.
  WAL compression can reduce full-page-image traffic at a CPU cost; benchmark it
  with your DBA. Keep durability settings enabled. See
  [WAL settings](https://www.postgresql.org/docs/current/runtime-config-wal.html).

### Application storage improvements and follow-up

The current implementation has opportunities to reduce writes without losing evidence:

| Priority | Current behavior | Proposed improvement |
| --- | --- | --- |
| Implemented | New snapshot indexes omit duplicated export JSON from `columns`. | Exports reconstruct evidence on demand; structured data and searchable evidence remain available. Existing indexes remain readable. |
| Implemented | VM relationship records reference shared rule definitions, including configured services, per snapshot. | New indexes avoid repeating those structured details per VM; provenance and search text remain on VM records. Existing snapshots remain compatible. |
| 3 | Retention can delete up to 1,000 snapshots per environment inside one transaction spanning environments. | Use smaller, bounded cleanup transactions, preserving latest-snapshot protection and coordination with collection/refresh. Measure cascade size and WAL per batch. |
| 4 | Full index refresh rewrites derived records in a long transaction. | Investigate versioned, staged index builds with bounded writes and atomic publication; preserve the old index until the replacement is complete, then clean it up in batches. This needs temporary storage too. |

Rows marked Implemented apply to new indexes in the updated build. The other rows are proposed follow-up work.
Measure table/TOAST sizes and WAL generation first to prioritize the largest saving.
Any schema/storage change needs tests for evidence completeness, failure recovery,
concurrent browsing and upgrade behavior; reducing memory or disk must not silently
remove historical evidence.
