# Install the complete Docker Hub stack

> Release **0.5.1** creates the initial administrator automatically during database
> initialization. No manual account-creation command is needed.


The complete stack runs PostgreSQL, database migrations, web, collection worker and
scheduler using Docker Compose. No Git checkout, host Python, local build or
`install.sh` is required. Docker Hub's Run button starts a single container and
cannot provision this stack by itself.

## Required application services

**Pulling the Docker Hub image and clicking Run starts only the web container. Even with all environment variables set, it does not deploy the other containers needed for the application to work.**

Use the provided **`compose.yaml` together with `.env`**. Docker Compose automatically deploys and connects these services; you do not need to install each dependency separately:

| Service | Purpose | Expected state |
| --- | --- | --- |
| `db` | PostgreSQL stores accounts, environments and snapshots | Running |
| `migrate` | Initializes or upgrades the database schema | Exited (0) after success |
| `web` | Serves the application interface | Running |
| `worker` | Processes collection tasks | Running |
| `scheduler` | Queues automatic collections | Running |

## Requirements

- Running Docker Engine or Docker Desktop with Docker Compose v2.
- Linux AMD64 or ARM64 container support.
- HTTPS access to Docker Hub for downloads and to your NSX Managers for collection.
- A free local port (8000 by default), persistent disk space and a database backup plan.

## 1. Download the deployment file (macOS/Linux/WSL)

Use a new directory. Do not overwrite an existing installation's `.env`.

```sh
mkdir nsx-security-analyzer
cd nsx-security-analyzer
curl -fSL https://raw.githubusercontent.com/vkernel/NSX-Security-Analyzer/v0.5.1/deploy/compose.yaml -o compose.yaml
```

## 2. Generate installation secrets

Run these commands in the same directory. They create `.env`; only do this on a
fresh installation. Docker supplies Python, so you do not need Python installed.

```sh
export NSX_IMAGE_TAG=0.5.1
docker pull "vkernel/nsx-security-analyzer:$NSX_IMAGE_TAG"
umask 077
docker run --rm --network none --entrypoint python "vkernel/nsx-security-analyzer:$NSX_IMAGE_TAG" -c 'import secrets; print("DJANGO_SECRET_KEY="+secrets.token_hex(32)); print("POSTGRES_PASSWORD="+secrets.token_hex(32)); print("WEB_PORT=8000")' > .env
printf 'NSX_IMAGE_TAG=%s\n' "$NSX_IMAGE_TAG" >> .env
```

## 3. Start all services

```sh
docker compose pull
docker compose up -d
docker compose ps -a
```

Wait until `web` and `db` show healthy, `worker` and `scheduler` are running,
and `migrate` shows Exited (0). If startup fails, use the troubleshooting section.

## 4. Sign in with the initial administrator

After `migrate` completes successfully and `web` is healthy, open
**http://localhost:8000** and sign in:

- **Username:** `admin`
- **Password:** `NSXSecurityA!`

No manual account-creation command is needed. Change the password after signing in
through **Administration → Users & access**.

Provisioning runs once per database. If an account named `admin` (case insensitive)
or any superuser already exists, it is left unchanged; use its existing login.
Restarts and upgrades never reset passwords, and deleting the initial account does
not cause it to be recreated.

The stack waits for PostgreSQL readiness and successful migrations before starting
web, worker and scheduler. Accounts, configuration and snapshots persist in the
`postgres-data` volume. Preserve `.env` with your backups: the Django secret
protects saved credentials. Recreating containers does not require new secrets.

For native Windows PowerShell, download the same `compose.yaml` and
[configuration example](../deploy/.env.example), save the example as `.env`, and
fill the two secret values using a password manager, and set `NSX_IMAGE_TAG`
to the updated image tag. Run the commands in steps 3 and 4 above. No shell installer is needed.

Add environments through Administration. The default web port is localhost-only.
For shared HTTPS access, configure the reverse proxy, allowed hosts and CSRF
origins described in [application configuration](../webapp/README.md#https-deployment).
Change `WEB_PORT` in `.env` if port 8000 is already used.

## Stop and restart

Run `docker compose stop` to stop the application without removing data.
Run `docker compose up -d` to start it again. Keep the same folder and `.env`.
`docker compose down -v` deletes the database; it is not an ordinary stop command.

## Upgrade an existing Compose installation

Back up PostgreSQL and `.env`, and wait for active collections to finish. Preserve
the project name and existing database volume. In your existing deployment directory,
set `NSX_IMAGE_TAG` in `.env` to your chosen updated image tag, then:

```sh
docker compose pull
docker compose stop web worker scheduler
docker compose run --rm migrate
docker compose up -d
docker compose ps -a
```

Proceed with `up` only if migration succeeds. Review failures before restarting
workers. Never use `down -v` unless intentionally deleting the database. Reverting
an image is not a database rollback; retain the pre-upgrade backup.

For version 0.5.1, after the updated web service starts, prepare existing snapshots:

```sh
docker compose exec web python manage.py index_snapshots
```

This reads saved snapshots from PostgreSQL without contacting NSX. It skips snapshots
already indexed and is safe to rerun after interruption. New collections are indexed
automatically. Until prepared, older reports use the previous viewer path.


For older source/Hub override installations, keep their existing `-f` arguments on
all commands. The updated default release file can be downloaded from the versioned
URL above, but preserve custom ports, volumes and external-database configuration.

## Customer-managed PostgreSQL

Follow the [remote database guide](../webapp/README.md#optional-customer-managed-postgresql)
for provisioning and verified TLS. The source Compose/Hub/remote override combination
remains supported; its custom `!reset` tag requires Compose 2.24.4 or newer. Keep the
same database host, credentials and CA mounts for migration, web, worker and scheduler.

## Diagnose startup

```sh
docker compose ps -a
docker compose logs --tail=100 db migrate web worker scheduler
```

`Worker failed to boot` is a summary: inspect preceding messages. Missing secrets
or failed migrations must be resolved before the application can run. The base
image does not initialize its dependencies when run alone.

The sidebar and Administration show the running image's version, source revision
and build time. Pin an explicit image tag for controlled upgrades; `latest` is mutable.
The supplied Compose file defaults to `0.5.1`; `NSX_IMAGE_TAG` overrides it.

- [Docker Hub](https://hub.docker.com/r/vkernel/nsx-security-analyzer)
- [Release source](https://github.com/vkernel/NSX-Security-Analyzer/tree/v0.5.1)
- [Compose file](../deploy/compose.yaml)
- [Operations and backups](operations.md)
- [Kubernetes installation](../deploy/kubernetes/README.md)

The optional `deploy/install.sh` uses the same release and automatic administrator
provisioning. It is not required for the Compose instructions above.

Compose reference: [Docker Compose quickstart](https://docs.docker.com/compose/gettingstarted/).
