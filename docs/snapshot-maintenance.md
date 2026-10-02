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

A manually started maintenance Job does **not** lock out collections. There is currently no global
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

## Kubernetes: create a temporary Job manually

No snapshot-refresh Job manifest is included in the deployment. Normal deployments
need only the migration Job and application services. New collections prepare their
own indexes automatically. Use the following procedure only to repair or rebuild
older saved snapshots, after finishing migrations and pausing collections as above.
Do not add this temporary Job to Argo CD, Helm or Kustomize resources.

### Remove a previously managed refresh Job

If Argo CD is waiting for a suspended `nsx-refresh-snapshots` Job, terminate the
current sync first. Remove the Job from the Git resources Argo manages, refresh
Argo CD, then delete the old Job and perform a full sync:

```sh
kubectl -n nsx-security-analyzer delete job nsx-refresh-snapshots --ignore-not-found
```

Deletion does not delete saved snapshots. If the Job is running, save its logs first;
deletion terminates that maintenance run. Removing only the cluster resource while
leaving it in Git allows Argo CD to recreate it.

### Start maintenance when required

Check that no other refresh is running. The command below reads the current web
image so maintenance uses the same build. Verify that this is the intended version
and that the namespace, ConfigMap and Secret references match your installation.
If your application uses extra database TLS mounts, imagePullSecrets or other pod
settings, add the same required settings to this temporary Job before running it.

The example requests 2 CPUs and 8 GiB RAM and limits memory to 16 GiB (medium starting
profile). Adjust for your largest snapshot and cluster capacity; see
[resource sizing](resource-sizing.md). Results are stored in the existing PostgreSQL
database; the maintenance container does not require its own PVC.

**This command starts maintenance immediately.** It is deliberately not a stored
manifest or deployment hook. `kubectl create` fails if the named Job already exists,
helping prevent accidental duplicate runs.

```sh
NSX_MAINTENANCE_IMAGE=$(kubectl -n nsx-security-analyzer get deployment web -o jsonpath='{.spec.template.spec.containers[?(@.name=="web")].image}')
test -n "$NSX_MAINTENANCE_IMAGE" && kubectl create -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: nsx-refresh-snapshots
  namespace: nsx-security-analyzer
spec:
  backoffLimit: 0
  template:
    metadata:
      labels:
        app: nsx-refresh-snapshots
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      terminationGracePeriodSeconds: 60
      containers:
        - name: snapshot-refresh
          image: ${NSX_MAINTENANCE_IMAGE}
          imagePullPolicy: IfNotPresent
          command: [python, manage.py, index_snapshots, --refresh]
          env:
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef: {name: nsx-postgress-app, key: password}
          envFrom:
            - configMapRef: {name: nsx-config}
            - secretRef: {name: nsx-django}
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            runAsGroup: 10001
            allowPrivilegeEscalation: false
            capabilities:
              drop: [ALL]
          resources:
            # Medium starting profile; see docs/resource-sizing.md.
            requests: {cpu: "2", memory: 8Gi}
            limits: {memory: 16Gi}
EOF
```

The example rebuilds all saved indexes with `--refresh`. For missing
presentation/history/coverage data only, remove `--refresh` from `command`. For one
snapshot, append `--snapshot, YOUR-SNAPSHOT-UUID` to the command array (check the
installed version's `index_snapshots --help` for supported arguments).

### Monitor, stop and clean up

```sh
kubectl -n nsx-security-analyzer get pods -l app=nsx-refresh-snapshots
kubectl -n nsx-security-analyzer logs -f job/nsx-refresh-snapshots
kubectl -n nsx-security-analyzer get job nsx-refresh-snapshots
```

Success shows `Complete`. `backoffLimit: 0` prevents automatic retries after failure.
If no pod starts, inspect `kubectl describe job nsx-refresh-snapshots` and the pod's
events in the same namespace for scheduling or image errors.

Save logs before cleanup, then delete the Job:

```sh
kubectl -n nsx-security-analyzer logs job/nsx-refresh-snapshots > snapshot-refresh.log
kubectl -n nsx-security-analyzer delete job nsx-refresh-snapshots
```

Deleting a running Job also stops maintenance. Each snapshot's index rebuild is
transactional: unfinished work rolls back while earlier completed refreshes remain.
Wait for the pod to terminate and database rollback to finish before resuming
collections or retrying. Inspect failures and correct their cause before explicitly
creating another Job. Restore the previous collection schedules after maintenance.
Never run a large refresh inside the web pod, where it competes with page serving.

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

Kubernetes reference: [Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/).
