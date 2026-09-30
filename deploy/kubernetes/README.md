# Install on Kubernetes

Use the supplied `manifests/` files. You do **not** need an online converter.
These files deploy the database, a one-time migration Job, the website, a collection
worker and a scheduler. They expose the website inside the cluster only.

The manifests use **0.4.0** for migrations, web, worker and scheduler. Database
initialization automatically provisions the initial administrator.

## 1. Before you start

You need:

- A Kubernetes cluster and `kubectl` configured for it. Your platform administrator
  can supply access. Check `kubectl config current-context` before making changes.
- Permission to create a namespace, Secrets, workloads, Services and storage.
- A default StorageClass for the bundled database, or an existing PostgreSQL database.
- Cluster access to Docker Hub, DNS, and your NSX Managers on HTTPS port 443.
- Git and a terminal. Commands below use macOS/Linux/WSL shell syntax.

The memory settings are starting values, not sizing guarantees: allow up to 2 GiB
for each application pod and 1 GiB for PostgreSQL. Large inventories may need more.
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

Create a private file called `secrets.env` **in this directory**, containing:

```dotenv
DJANGO_SECRET_KEY=replace-with-a-long-random-secret
POSTGRES_PASSWORD=replace-with-a-different-long-random-secret
```

Use two independent random strings of at least 32 characters from your password
manager. Do not include quotes. This file is Git-ignored. Keep it with your backups;
changing the Django key prevents decryption of saved NSX credentials.

```sh
chmod 600 secrets.env
kubectl -n nsx-security-analyzer create secret generic nsx-secrets --from-env-file=secrets.env
kubectl -n nsx-security-analyzer apply -f manifests/config.yaml
```

Do not run secret creation again on upgrades or regenerate secrets for an existing database.

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

Use that login's actual password in `secrets.env`. Ensure the database permits
connections from the application pods. `localhost` would refer to the application
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
| Website returns 400 | Browser hostname is included in allowed hosts |
| CSRF failure | Trusted origin matches the HTTPS URL and proxy configuration |
| Collection stays queued | Worker is running and uses the same database and Django key |
| Collection stops at Saving snapshot | Worker logs, memory termination events and database latency/locks |

Do not share Secrets or `.env` files in bug reports. See [operations](../../docs/operations.md)
for the expanded logs available in newer source builds.

## Upgrades

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
Do not delete the PVC or namespace during upgrades. A ConfigMap change requires
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
- `manifests/migrate.yaml`: one-time schema initialization/upgrade Job.
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

References: [Kubernetes workloads](https://kubernetes.io/docs/concepts/workloads/),
[persistent storage example](https://kubernetes.io/docs/tasks/run-application/run-single-instance-stateful-application/).
