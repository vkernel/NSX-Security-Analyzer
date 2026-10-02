# Snapshot refresh and stopping collections

Normal collections run in the worker, save a snapshot and prepare its report indexes
automatically. A snapshot refresh is optional maintenance: it rebuilds report indexes
from saved PostgreSQL data, without contacting NSX. Use it when an upgrade requires
older reports to be rebuilt. It is not a scheduled collection or schema migration.

## Avoid overlapping refresh with collections

Before starting maintenance, pause automatic collection for the affected environments
in the web GUI. Wait for running collections to finish (or use Stop collection and
wait for acknowledgement), and stop queued collections. For a full refresh, do this
for all environments. Avoid starting manual collections until maintenance completes,
then restore the environments' previous scheduling settings.

Suspending the maintenance Job prevents automatic startup; it is **not** a database
lock against collections once you manually start it. There is currently no global
mutual-exclusion mechanism between maintenance and collection workers. Running both
can compete for database, CPU and memory resources. Normal collections must continue
to index their own new snapshot; that is separate from refreshing saved history.

## Docker Compose: temporary refresh container

First finish migrations and start your normal application stack. From the directory
containing your Compose file and `.env`, run:

```sh
docker compose run --rm --no-deps --name nsx-snapshot-refresh snapshot-refresh
```

The `snapshot-refresh` service uses the application image and database configuration,
runs `python manage.py index_snapshots --refresh`, then exits. `--rm` removes the
container after it exits. The `maintenance` profile excludes it from normal
`docker compose up -d`; explicitly targeting it with `run` makes it available.
Do not enable the maintenance profile for normal startup. `--no-deps` assumes that
the database is already reachable and migrations have finished.

Use the same Compose override files as your installation. For example, for a source
installation using an external database, run from `webapp/`:

```sh
docker compose -f compose.yaml -f compose.remote.yaml run --rm --no-deps --name nsx-snapshot-refresh snapshot-refresh
```

The initial container limit is 16 GiB RAM and two CPUs (the medium starting profile). Adjust `snapshot-refresh.mem_limit`
and `cpus` for your data and host capacity using the [sizing guide](resource-sizing.md).
For a targeted refresh in a build supporting `--snapshot`:

```sh
docker compose run --rm --no-deps --name nsx-snapshot-refresh snapshot-refresh python manage.py index_snapshots --refresh --snapshot YOUR-SNAPSHOT-UUID
```

To prepare only missing presentation/history/coverage data, override the command with
`python manage.py index_snapshots` (omit `--refresh`). To stop a refresh, use
`docker stop nsx-snapshot-refresh` from another terminal. The explicit container
name also prevents accidentally starting two of these named refreshes at once. Do not stop the normal collection worker to stop this maintenance task.

## Kubernetes: separate maintenance Job

The optional manifest is `deploy/kubernetes/maintenance/refresh-snapshots.yaml`,
**outside the normal `manifests/` directory**. Keep it outside Argo CD's normal sync
path so deployments do not automatically trigger a full reindex. It now includes
`spec.suspend: true`: applying it creates a suspended Job without starting a pod.
Start it only with the explicit unsuspend command below, after pausing collections.

Argo CD discovers resources by its configured source path, recursion, Kustomize or
Helm inclusion—not by whether a folder is named `maintenance`. Point the normal
application at `deploy/kubernetes/manifests`, or otherwise exclude this maintenance
manifest from the resources it renders. Do not add migration-style Sync hooks to it.
If Argo owns the Job, self-healing can undo a manual unsuspend; keep the manual Job
outside that application's managed resources. Removing it with pruning enabled can
terminate an existing run, so preserve logs first.

Before applying it:

1. Set its image to the exact application build you deployed. Development fixes
   require the corresponding development image, not the stable default tag.
2. Check the namespace, `nsx-config` ConfigMap and Secret names/key mappings.
3. Finish the migration Job first. Set resources for the largest saved snapshot.
   The medium example requests 2 CPUs and 8 GiB RAM, with a 16 GiB memory limit;
   this is not a guarantee
   that every snapshot fits.
4. Optionally append `--snapshot` and a snapshot UUID to `command` when supported by
   the image. Check `python manage.py index_snapshots --help` if unsure.

From the repository root:

```sh
kubectl apply -f deploy/kubernetes/maintenance/refresh-snapshots.yaml
# Explicit manual start, after completing the maintenance-window checks above:
kubectl -n nsx-security-analyzer patch job nsx-refresh-snapshots --type=merge -p '{"spec":{"suspend":false}}'
kubectl -n nsx-security-analyzer get pods -l app=nsx-refresh-snapshots
kubectl -n nsx-security-analyzer logs -f job/nsx-refresh-snapshots
```

Success shows `Completed`. `backoffLimit: 0` prevents automatic retries of a large,
failed refresh. Inspect logs and pod termination details before retrying. No new PVC
is required: results are stored in the application's existing PostgreSQL database.

Before another run, save any logs you need, delete the old Job and apply it again (it will be suspended):

```sh
kubectl -n nsx-security-analyzer delete job nsx-refresh-snapshots
kubectl apply -f deploy/kubernetes/maintenance/refresh-snapshots.yaml
```

Repeat the explicit unsuspend command when ready for the new run. Reapplying the
manifest to an active Job restores `suspend: true` and stops its running pod.

To stop an unintended active refresh without deleting the Job:

```sh
kubectl -n nsx-security-analyzer patch job nsx-refresh-snapshots --type=merge -p '{"spec":{"suspend":true}}'
```

Save logs first if possible. If Argo still manages an unsuspended version, correct
its Git source or exclude the Job so reconciliation does not restart it.
Deleting a running refresh Job also stops it. Each snapshot's index rebuild is
transactional: unfinished work rolls back, while earlier completed refreshes remain.
Database rollback/cleanup may take time. Do not run overlapping refresh Jobs for the
same snapshots. A refresh is never run inside the web pod.

## Stop a queued or running collection

Staff users can select **Stop collection** in collection history, recent collections,
the environment's active collection or collection diagnostics.

- A queued collection is stopped immediately when it has not already been claimed.
- For a running collection, the page shows **Stop requested**. The worker checks for
  requests every two seconds while it supervises the collector process, including
  during snapshot saving. It kills that process and marks the collection **Stopped**.
- The stop request is stored separately so a long snapshot transaction does not block
  the website's request. Unfinished snapshot writes roll back. Earlier completed
  snapshots remain available.
- A collection that commits just before the stop takes effect remains **Completed**;
  stopping does not remove a successfully saved snapshot.
- The environment remains occupied until the worker acknowledges the stop. A missing
  or database-disconnected worker cannot acknowledge promptly; inspect worker logs
  and restore worker/database connectivity. The normal stale-job timeout remains a
  fallback for an absent worker.
- Stop requests and acknowledgements are recorded in the audit log. A stopped
  collection is not reported as a collection failure and follows collection-history
  retention. Stopping one collection does not disable future automatic syncs; pause
  the environment if you also want to prevent scheduled collections.

Apply migration `0021` and deploy the updated web **and worker** image before using
this feature. An older worker does not poll for stop requests. Collection stop controls
do not stop the separate snapshot-refresh maintenance Job; use the commands above.

References: [Docker Compose profiles](https://docs.docker.com/compose/how-tos/profiles/),
[one-off containers](https://docs.docker.com/reference/cli/docker/compose/run/).

Kubernetes reference: [suspending Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/#suspending-a-job).
