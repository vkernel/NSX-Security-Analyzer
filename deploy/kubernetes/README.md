# Compose to Kubernetes conversion

Use **[compose.yaml](compose.yaml)** in this directory as input to an online
Compose-to-Kubernetes converter. It is a standalone conversion template, not the
Docker installer configuration. It deliberately uses plain service definitions:
no YAML anchors, extension fields, custom tags, build context, profiles, local
files, variable interpolation, host-IP bindings or Compose startup conditions.

Includes PostgreSQL, migrations, web, collection worker and scheduler. IPFIX is
on hold and omitted from this conversion baseline. Nothing here changes an
existing Docker deployment.

## Convert locally

From the repository root, with Kompose installed:

```sh
mkdir -p test-artifacts/kubernetes
kompose -f deploy/kubernetes/compose.yaml convert --out test-artifacts/kubernetes/generated.yaml
```

Validated using Kompose **1.38.0**. Expected output:

- Services: `db` (5432/TCP), `web` (8000/TCP).
- Deployments: `db`, `web`, `worker`, `scheduler`.
- One-shot migration Pod: `migrate` (`restartPolicy: OnFailure`).
- PersistentVolumeClaim: `postgres-data`.

Messages about no Service being created for migration/worker/scheduler are normal:
these processes have no listening ports. The workload objects must still appear.
Kompose may warn that `version` is obsolete; it remains for older converters.
A volume-directory inspection warning can occur; check that the output still
contains the PVC and that the database mounts it.

## Complete the generated manifests before deploying

Conversion does not make a complete, production-ready Kubernetes deployment.
Review the following in the generated YAML:

1. **Image:** replace `nsx-security-analyzer:local` with a registry image built from
   this branch, or load that exact development image into your local cluster.
   Use the same image version for migration, web, worker and scheduler. Kubernetes
   cannot use an arbitrary image from Docker Desktop's image store automatically.
2. **Secrets:** the template intentionally leaves `DJANGO_SECRET_KEY` and
   `POSTGRES_PASSWORD` empty. Replace them with `valueFrom.secretKeyRef` references
   to a Kubernetes Secret. Every app process needs the same persistent Django key;
   the database password must match its role. Keep existing key/password values
   when migrating existing data. Do not paste your `.env` or resolved production
   Compose configuration into an online converter.
3. **Storage:** select a StorageClass and sufficient PVC capacity. For the bundled
   PostgreSQL container, set `PGDATA=/var/lib/postgresql/data/pgdata` to avoid
   initializing in the root of a provisioned filesystem. Keep one database replica
   and a Recreate strategy, or use an externally managed PostgreSQL instance.
   For external PostgreSQL, remove the database Deployment/Service/PVC and configure
   database host and TLS trust consistently in every app workload.
4. **Startup:** create the database and wait for readiness, then run migrations to
   completion, then start web/worker/scheduler. Prefer changing the generated
   migration Pod into a `batch/v1` Job with a bounded retry policy. Repeat migrations
   for each application upgrade; a previously completed Pod will not rerun just
   because you apply the same manifest. Do not run migrations in every app replica.
5. **Access:** configure allowed hosts, trusted CSRF origins and HTTPS/proxy settings
   for your hostname. Keep the database Service internal. Add a web Ingress or
   port-forward; no public endpoint is created by this template.
6. **Operations:** add resource requests/limits and appropriate startup/readiness/
   liveness probes. Keep one scheduler initially. Create the initial admin account
   inside the deployed web workload using `python manage.py createsuperuser`.

Example secret reference in an app container (create this Secret separately):

```yaml
env:
  - name: DJANGO_SECRET_KEY
    valueFrom:
      secretKeyRef:
        name: nsx-analyzer-secrets
        key: django-secret-key
  - name: POSTGRES_PASSWORD
    valueFrom:
      secretKeyRef:
        name: nsx-analyzer-secrets
        key: postgres-password
```

The database container only needs the password reference, not the Django key.

After preparing the manifests, validate against your chosen cluster with
`kubectl apply --dry-run=server -f <prepared-manifests>` before applying them.
No Kubernetes cluster deployment was performed as part of the conversion change.

## Docker use

Continue using `webapp/compose.yaml` for source-based Docker development and
`deploy/compose.yaml` for the Docker Hub installer. Their shared YAML merge blocks
have been expanded into explicit definitions; startup ordering, optional IPFIX,
local port binding and credential requirements remain unchanged. The Hub and remote
database overrides are Docker-specific and are not online-converter inputs.

References: [Kompose conversion](https://kompose.io/conversion/),
[Kompose user guide](https://kompose.io/user-guide/).

## Isolated Docker test

The Docker-specific `compose.docker-test.yaml` override makes the conversion
baseline runnable locally, with ordered startup and web access on
**http://localhost:8001**. It uses the existing `nsx-security-analyzer:local` image.
The project name below gives it a separate database volume and network. IPFIX
is not started. The existing application on port 8000 is unaffected.

From the repository root:

```sh
docker compose -p nsxa-k8s-test --env-file deploy/kubernetes/.env.docker-test \
  -f deploy/kubernetes/compose.yaml -f deploy/kubernetes/compose.docker-test.yaml up -d
```

The initial test setup generated a private, Git-ignored `.env.docker-test` in this
directory containing independent database and Django secrets. The test admin login
is stored in the private, Git-ignored `.admin-credentials` file alongside it.
Do not replace the test secrets while keeping its database volume.
On another machine, create those secrets and create an admin with Django's
`createsuperuser` command in this test stack's web container.

Use the same project, environment-file and Compose-file options with `ps`, `logs`
or `down`. `down` stops this test stack while retaining its database volume.
Do not add `-v` unless you intend to delete the test database.
This verifies Docker operation only; it is not a Kubernetes cluster validation.
