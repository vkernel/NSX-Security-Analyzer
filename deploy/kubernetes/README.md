# Install on Kubernetes

Use the supplied `manifests/` files. You do **not** need an online converter.
These files deploy the database, a migration Job, the website, a collection
worker and a scheduler. They expose the website inside the cluster only.

The manifests use **0.5.2** for migrations, web, worker and scheduler. Database
initialization automatically provisions the initial administrator.

Using Argo CD? Follow the configuration and Secret prerequisites below, then use
[Argo CD deployment and upgrades](#argo-cd-deployment-and-upgrades) for synchronization.
The numbered `kubectl apply` steps are for manual deployments.

## 1. Before you start

You need:

- A Kubernetes cluster and `kubectl` configured for it. Your platform administrator
  can supply access. Check `kubectl config current-context` before making changes.
- Permission to create a namespace, Secrets, workloads, Services and storage.
- A default StorageClass for the bundled database, or an existing PostgreSQL database.
- Cluster access to Docker Hub, DNS, and your NSX Managers on HTTPS port 443.
- Git and a terminal. Commands below use macOS/Linux/WSL shell syntax.

The default manifests use the **medium** resource profile, not a production sizing guarantee.
With bundled PostgreSQL and one replica per application component, normal memory
requests total 10.25Gi; migration and optional refresh Jobs require additional capacity.
Use the [small, medium and large sizing guide](../../docs/resource-sizing.md) to choose
per-container CPU/memory allocations and validate them with real collections and refreshes.
The bundled database is a single instance, not a highly available database service.
Ask your platform administrator to review storage, backups and resources for production.

Download the source and enter this folder:

```sh
git clone https://github.com/vkernel/NSX-Security-Analyzer.git
cd NSX-Security-Analyzer/deploy/kubernetes
kubectl config current-context
kubectl create namespace nsx-security-analyzer
```

If you already have a checkout, use its `deploy/kubernetes` folder. For a fresh
installation the namespace should be empty. Stop and review if it already exists.
All commands below explicitly use this namespace; no default-context change is needed.

## 2. Configure the database and secrets

Open `manifests/config.yaml` in your editor. For the bundled database, keep
`POSTGRES_HOST: "db"`, database `nsx` and user `nsx`.

The application imports settings from `nsx-config` and the Django key from the
dedicated `nsx-django` Secret using `envFrom`. The existing database Secret uses
an explicit mapping because its key is named `password`, not `POSTGRES_PASSWORD`:

| Application variable | Secret name | Key |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | `nsx-django` | `DJANGO_SECRET_KEY` |
| `POSTGRES_PASSWORD` | `nsx-postgress-app` | `password` |

These settings appear in `manifests/migrate.yaml` and all three
Deployments in `manifests/application.yaml`. The optional bundled database uses
the same password mapping. Change the Secret name/key there if yours differ.
The spelling `nsx-postgress-app` matches the example existing Secret exactly.

**Existing database Secret:** reuse it. Do not overwrite it, regenerate its password,
or copy its password into a new file. The Secret must exist in the **same namespace
as the application pods**, even if the database Service is in another namespace.
Have your platform administrator synchronize it into the application namespace if needed.

For a **new installation**, generate the Django key and create its dedicated Secret
with OpenSSL and kubectl:

```sh
kubectl create secret generic nsx-django \
  --from-literal=DJANGO_SECRET_KEY="$(openssl rand -base64 50)" \
  -n nsx-security-analyzer
```

This generates a random key without needing a local `secrets.env` file. The key
name must be exactly `DJANGO_SECRET_KEY` for the `envFrom` import to work.
Keep this Secret dedicated to the Django key; unrelated database keys are not
imported from the database Secret.

Skip creation if `nsx-django` already exists. Preserve the key with your database
backups and **do not regenerate it on upgrades**: changing it prevents decryption
of saved NSX credentials. If an existing installation used a differently named
Secret, copy its original Django key through your platform's secret-management
process or retain that Secret name in all four workload references.

Apply the application settings:

```sh
kubectl -n nsx-security-analyzer apply -f manifests/config.yaml
```

### New bundled database only: create its password Secret

Skip this section when using an existing database Secret. For a new bundled
PostgreSQL installation, create a private `database-secret.env` file containing:

```dotenv
password=replace-with-a-different-long-random-secret
```

Then create the Secret referenced by the manifests:

```sh
chmod 600 database-secret.env
kubectl -n nsx-security-analyzer create secret generic nsx-postgress-app --from-env-file=database-secret.env
```

Use a different random value from the Django key. Keep this file with your backups.
Changing this Secret later does not change the password inside PostgreSQL.

### If PostgreSQL already exists

Ask your database administrator to create an **empty, dedicated database** and a
login that owns its schema and can create/alter tables and indexes. The application
does not create the database or PostgreSQL login. It does not need database superuser rights.

Before applying `config.yaml`, set:

| Field | Example / meaning |
| --- | --- |
| `POSTGRES_HOST` | `postgres.database.svc.cluster.local` — the database Service DNS name |
| `POSTGRES_PORT` | `5432` |
| `POSTGRES_DB` | Name of the dedicated database |
| `POSTGRES_USER` | Its database login |
| `POSTGRES_SSLMODE` | `verify-full` for a TLS-enabled server |

Reference that login's existing password Secret in all four application containers:

```yaml
env:
  - name: POSTGRES_PASSWORD
    valueFrom:
      secretKeyRef: {name: nsx-postgress-app, key: password}
envFrom:
  - configMapRef: {name: nsx-config}
  - secretRef: {name: nsx-django}
```

`envFrom` imports keys under their original names; it does not rename `password`
to `POSTGRES_PASSWORD`. A Secret with multiple keys works, but explicit mappings
select only the database values this application needs. Use `envFrom` for the
ConfigMap and the dedicated Django Secret, not for the database Secret. If the database username is also stored in
that Secret, add an explicit `POSTGRES_USER` mapping to its actual username key.
Explicit `env` entries take precedence over ConfigMap values imported by `envFrom`.

Ensure the database permits connections from the application pods. `localhost` would refer to the application
pod, not your PostgreSQL service. Network policies must allow DNS and the database port.

For a private database CA, create a trust ConfigMap:

```sh
kubectl -n nsx-security-analyzer create configmap postgres-ca --from-file=ca.crt=/path/to/ca.pem
```

Add `POSTGRES_SSLROOTCERT: "/etc/postgres-ca/ca.crt"` to `config.yaml`. In **both**
`migrate.yaml` and **each** Deployment in `application.yaml`, add this under
`spec.template.spec` (beside `containers`):

```yaml
volumes:
  - name: postgres-ca
    configMap:
      name: postgres-ca
```

Add this to each corresponding container (beside `envFrom`):

```yaml
volumeMounts:
  - name: postgres-ca
    mountPath: /etc/postgres-ca
    readOnly: true
```

The hostname must match the server certificate. Apply the edited connection settings
with `kubectl -n nsx-security-analyzer apply -f manifests/config.yaml` before
continuing. Skip step 3 for external PostgreSQL.
Selecting a different database does not copy existing snapshots; moving data requires
backup/restore and the original Django key. Migrations in step 4 create or update
application tables in the selected database.

## 3. Start bundled PostgreSQL (skip for an existing database)

```sh
kubectl -n nsx-security-analyzer apply -f manifests/database.yaml
kubectl -n nsx-security-analyzer rollout status deployment/db --timeout=180s
```

Wait for success before continuing. If the pod is Pending, check
`kubectl -n nsx-security-analyzer get pvc`. The requested disk is 10 GiB. If your
cluster has no default StorageClass, add `storageClassName: YOUR_CLASS` under the
PVC's `spec` in `database.yaml`, using the class your administrator provides.

## 4. Initialize the application database

```sh
kubectl -n nsx-security-analyzer apply -f manifests/migrate.yaml
kubectl -n nsx-security-analyzer wait --for=condition=complete job/migrate --timeout=900s
kubectl -n nsx-security-analyzer logs job/migrate
```

Continue only if the Job completes successfully. A Completed migration pod is normal;
it initializes the database and then exits. On failure, see troubleshooting below.

## 5. Start the application

```sh
kubectl -n nsx-security-analyzer apply -f manifests/application.yaml
kubectl -n nsx-security-analyzer rollout status deployment/web --timeout=180s
kubectl -n nsx-security-analyzer rollout status deployment/worker --timeout=180s
kubectl -n nsx-security-analyzer rollout status deployment/scheduler --timeout=180s
kubectl -n nsx-security-analyzer get pods
```

Database initialization creates the initial administrator automatically:

- **Username:** `admin`
- **Password:** `NSXSecurityA!`

No manual account-creation command is needed. Change the password through
**Administration → Users & access** after signing in. Provisioning runs once per
database and skips existing `admin` accounts or superusers. Existing passwords are
never replaced, and deleting the initial account does not recreate it.

## 6. Open the website

```sh
kubectl -n nsx-security-analyzer port-forward service/web 8000:8000
```

Keep this terminal open and browse to **http://localhost:8000**. If local port 8000
is in use, use `8001:8000` and browse to port 8001. Press Ctrl+C to stop forwarding;
the application keeps running in Kubernetes.

Add your first environment through Administration, enter its NSX credentials and
retrieve/review the certificate if needed. Choose a sync interval or start a collection.
The worker needs network access to NSX; browser access alone does not establish that.

For shared access, ask your platform administrator to expose Service `web:8000`
through an HTTPS Ingress. Set the public hostname in `DJANGO_ALLOWED_HOSTS`, its full
`https://` origin in `DJANGO_CSRF_TRUSTED_ORIGINS`, and `DJANGO_HTTPS` to `1`.
Set `DJANGO_TRUST_PROXY` to `1` only with a trusted proxy that sets/overwrites
`X-Forwarded-Proto`. Reapply the ConfigMap and restart all application Deployments
so they read the new settings. Do not expose PostgreSQL publicly.

## Troubleshooting

```sh
kubectl -n nsx-security-analyzer get pods,pvc,jobs
kubectl -n nsx-security-analyzer get events --sort-by=.metadata.creationTimestamp
kubectl -n nsx-security-analyzer logs deployment/worker --tail=100 -f
kubectl -n nsx-security-analyzer logs deployment/web --tail=100
kubectl -n nsx-security-analyzer logs deployment/scheduler --tail=100
kubectl -n nsx-security-analyzer logs job/migrate --all-containers=true
```

Use `kubectl -n nsx-security-analyzer describe pod POD_NAME` for startup, scheduling
or memory issues; `logs POD_NAME --previous` retrieves the previous container's logs
if it restarted. Replace `POD_NAME` with a name from `get pods`.

| Symptom | Check |
| --- | --- |
| ImagePullBackOff | Image tag exists, registry credentials if private, outbound registry access |
| Pending | PVC StorageClass/capacity, cluster resources and scheduling events |
| Migration failed | Database host, credentials, schema permissions and TLS trust |
| Argo CD: `field is immutable` on `migrate` | Adopt the [migration hook and recovery steps](#argo-cd-deployment-and-upgrades), then perform a full application sync |
| Website returns 400 | Browser hostname is included in allowed hosts |
| CSRF failure | Trusted origin matches the HTTPS URL and proxy configuration |
| Collection stays queued | Worker is running and uses the same database and Django key |
| Collection stops at Saving snapshot | Worker logs, memory termination events and database latency/locks |

Do not share Secrets or `.env` files in bug reports. See [operations](../../docs/operations.md)
for the expanded logs available in newer source builds.

## Argo CD deployment and upgrades

Kubernetes does not allow an existing Job's Pod template to change, including its
container image. Updating the image tag in an ordinary fixed-name Job therefore
causes `field is immutable`. The supplied migration hook recreates the Job instead.

For each version upgrade:

1. Back up PostgreSQL, retain the existing `DJANGO_SECRET_KEY`, and let active
   collections finish.
2. Update the image tag in `manifests/migrate.yaml` and all three Deployments in
   `manifests/application.yaml` to the same published version. Keep your database
   connection settings and Secret references.
3. Commit these changes to the repository and branch watched by your Argo CD
   Application. Ensure it includes the hook and wave annotations shown below.
   An Application pinned to an older release tag will not receive newer manifests.
4. Refresh the Application in Argo CD and perform a **full Sync**. Leave selective
   resource synchronization disabled so the migration hook runs.
5. Verify that `migrate` succeeds, then that web, worker and scheduler become
   Healthy. If migrations fail, inspect `kubectl -n nsx-security-analyzer logs
   job/migrate`, fix the cause in Git, and perform another full sync.
6. When upgrading from before 0.5.2, follow
   [Preparing older snapshots](#preparing-older-snapshots-for-faster-report-pages)
   after the rollout completes.

No Docker image rebuild is required to adopt these annotations. They must be in
the manifests Argo CD actually reads; changing only a local checkout has no effect.

The manifests support a **full application sync** with this order:

| Sync wave | Resources | Purpose |
| --- | --- | --- |
| `-2` | ConfigMap and optional bundled database resources | Prepare configuration and wait for database readiness |
| `-1` | `migrate` Sync hook | Apply database migrations before rolling out new application pods |
| `0` (default) | Web, worker, scheduler and application Service | Roll out the application after migrations succeed |

Create the `nsx-django` and `nsx-postgress-app` Secrets in the target namespace
before syncing. If Argo CD manages these Secrets in the same Application, give them
wave `-2` or earlier. For an existing PostgreSQL server, omit `database.yaml` and
ensure that the database is reachable before syncing. Waves do not order resources
belonging to separate Argo CD Applications.

The migration Job has these annotations under its top-level `metadata`:

```yaml
annotations:
  argocd.argoproj.io/hook: Sync
  argocd.argoproj.io/hook-delete-policy: BeforeHookCreation
  argocd.argoproj.io/sync-wave: "-1"
```

`BeforeHookCreation` deletes the previous migration Job before creating its
replacement, avoiding `field is immutable` when the image tag changes. Only the
Job and its pods are replaced; the PostgreSQL database and PVC are preserved.
Successful and failed Jobs remain available for logs until the next full sync.
Django applies only migrations that have not already been applied.

Use **Sync** for the whole Application when updating the version, with the same
image tag in `migrate.yaml` and every application Deployment. Do not selectively
sync only the Deployments: selective sync skips hooks. A migration failure blocks
the later application rollout. These waves do not stop already-running application
pods; schema changes incompatible with the old application require a maintenance
window with collections paused and the application scaled down through Git first.

If an older ordinary `migrate` Job still blocks the first sync after adopting these
annotations, check its status and save any logs. Once it is no longer running,
delete **only that Job**, then perform a full Argo CD sync:

```sh
kubectl -n nsx-security-analyzer get job migrate
kubectl -n nsx-security-analyzer logs job/migrate
kubectl -n nsx-security-analyzer delete job migrate --ignore-not-found
```

Do not force-replace all application resources or delete the database/PVC to fix
this error. No new application image is needed for this manifest-only change.
Plain `kubectl apply` ignores the Argo CD annotations; follow the manual upgrade
steps below when not using Argo CD.

Reference: [Argo CD sync phases, hooks and waves](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-waves/).

## Manual kubectl upgrades

Back up the database and keep the original secret. Wait for collections to finish.
Set the **same published image tag** in `migrate.yaml` and all three containers in
`application.yaml`. Then, from this directory:

```sh
kubectl -n nsx-security-analyzer scale deployment/web deployment/worker deployment/scheduler --replicas=0
kubectl -n nsx-security-analyzer delete job migrate --ignore-not-found
kubectl -n nsx-security-analyzer apply -f manifests/config.yaml
kubectl -n nsx-security-analyzer apply -f manifests/migrate.yaml
kubectl -n nsx-security-analyzer wait --for=condition=complete job/migrate --timeout=900s
```

Only after success:

```sh
kubectl -n nsx-security-analyzer apply -f manifests/application.yaml
kubectl -n nsx-security-analyzer scale deployment/web deployment/worker deployment/scheduler --replicas=1
kubectl -n nsx-security-analyzer rollout status deployment/web --timeout=180s
```

A completed Job does not rerun automatically, so deleting/recreating it is intentional.
Do not delete the PVC or namespace during upgrades. A ConfigMap or Secret change requires
pod replacement; scale-to-zero above ensures new pods read it. Reverting an image
does not reverse database migrations; keep the pre-upgrade backup.

## Use a newer source build

Build the checkout and publish a unique tag to a registry your cluster can reach:

```sh
# From the repository root; replace YOUR_ACCOUNT and YOUR_TAG.
docker build -f webapp/Dockerfile -t YOUR_ACCOUNT/nsx-security-analyzer:YOUR_TAG .
docker push YOUR_ACCOUNT/nsx-security-analyzer:YOUR_TAG
```

Use that image in all four application/migration containers. For mixed CPU clusters,
publish a multi-platform image or target the node architecture. A local Docker image
is not automatically available in a remote cluster. Private registries need an
`imagePullSecrets` entry supplied by your platform administrator.

## File reference and validation limits

- `manifests/config.yaml`: non-secret connection and application settings.
- `manifests/database.yaml`: optional bundled PostgreSQL with persistent storage.
- `manifests/migrate.yaml`: schema initialization/upgrade Job; recreated as a hook on each full Argo CD sync.
- `manifests/application.yaml`: web, worker, scheduler and internal web Service.
- `compose.yaml`: optional converter input for users who still need Kompose or an
  online converter. It is not the installation path above; converted output needs
  Secret references, storage, startup ordering and probes added manually.

The nine resources were checked against the Kubernetes 1.32 schema with
`kubeconform -strict -summary -kubernetes-version 1.32.0 manifests/`.

These manifests are a single-instance starting point, not a managed production
platform. Validate them with your cluster's policies before use. No live Kubernetes
cluster validation has been performed for this change. Docker operation alone does
not validate Kubernetes storage, admission policies or networking.

References: [Secret environment mappings](https://kubernetes.io/docs/tasks/inject-data-application/distribute-credentials-secure/),
[Kubernetes workloads](https://kubernetes.io/docs/concepts/workloads/),
[persistent storage example](https://kubernetes.io/docs/tasks/run-application/run-single-instance-stateful-application/).

### Preparing older snapshots for faster report pages

Snapshot indexing prepares report sections and table records from snapshots already
stored in PostgreSQL. It makes report browsing faster and does not contact NSX or
collect new inventory. New collections perform this preparation automatically in
the collection worker.

For older snapshots, finish schema migration and application rollout first. The
management commands are:

```sh
# Prepare snapshots missing presentation or history data.
python manage.py index_snapshots

# Rebuild prepared data for every saved snapshot, including existing indexes.
python manage.py index_snapshots --refresh
```

These are optional manual maintenance commands, not deployment steps or an
always-running service. No refresh Job manifest is shipped with the deployment.
When needed, pause collections and create a separate temporary Job using the
[snapshot maintenance instructions](../../docs/snapshot-maintenance.md#kubernetes-create-a-temporary-job-manually).
That guide covers resources, database settings, logs, stopping, cleanup and removing
an older suspended Job from Argo CD. Do not include maintenance Jobs in normal syncs.
Do not execute a large refresh inside the web pod: it shares that container's memory
limit and can interrupt the website if the container is OOMKilled. See the
[resource sizing guide](../../docs/resource-sizing.md) for initial allocations.
Keep this optional maintenance Job outside the normal Argo CD installation/sync path.

The refresh rebuilds derived data; it preserves the original saved snapshot.
It can be rerun after interruption, but `--refresh` rebuilds already-prepared
snapshots too. In development builds supporting `--snapshot`, add
`--snapshot <snapshot-uuid>` to limit the work to one snapshot; check
`python manage.py index_snapshots --help` in the deployed image first.
See [report performance](../../docs/collection-performance.md#report-navigation)
for search, pagination, storage and export behavior.

Version 0.5.2 adds **Inventory → VMs**. Refresh existing prepared reports with the command above, then run a new collection for complete returned VM inventory, including untagged VMs. Older snapshots only contain VMs recoverable from their saved tag assignments. Group and service links describe configuration relationships, not confirmed membership or observed traffic.

For optional single sign-on, follow [Keycloak authentication setup](../../docs/keycloak.md).
