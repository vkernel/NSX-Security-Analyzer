# Install the complete Docker Hub stack

Release **0.3.0** runs PostgreSQL, database migrations, web, collection worker and
scheduler using Docker Compose. No Git checkout, host Python, local build or
`install.sh` is required. Docker Hub's Run button starts a single container and
cannot provision this stack by itself. IPFIX is experimental and not started by
the release Compose file.

## Requirements

- Running Docker Engine or Docker Desktop with Docker Compose v2.
- Linux AMD64 or ARM64 container support.
- HTTPS access to Docker Hub for downloads and to your NSX Managers for collection.
- A free local port (8000 by default), persistent disk space and a database backup plan.

## New installation (macOS/Linux/WSL)

Use a new directory. Do not overwrite an existing installation's `.env`.

```sh
mkdir nsx-security-analyzer
cd nsx-security-analyzer
curl -fSL https://raw.githubusercontent.com/vkernel/NSX-Security-Analyzer/v0.3.0/deploy/compose.yaml -o compose.yaml

docker pull vkernel/nsx-security-analyzer:0.3.0
umask 077
docker run --rm --network none --entrypoint python vkernel/nsx-security-analyzer:0.3.0 -c 'import secrets; print("DJANGO_SECRET_KEY="+secrets.token_hex(32)); print("POSTGRES_PASSWORD="+secrets.token_hex(32)); print("WEB_PORT=8000")' > .env

docker compose pull
docker compose up -d
docker compose ps -a
docker compose exec web python manage.py createsuperuser
```

Wait for the web container to become healthy before creating the administrator.
Migration exits with code **0** when successful; this is expected. Open
**http://localhost:8000** and sign in with the account you created. There is no
shared/default administrator password.

The stack waits for PostgreSQL readiness and successful migrations before starting
web, worker and scheduler. Accounts, configuration and snapshots persist in the
`postgres-data` volume. Preserve `.env` with your backups: the Django secret
protects saved credentials. Recreating containers does not require new secrets.

For native Windows PowerShell, download the same `compose.yaml` and
[configuration example](../deploy/.env.example), save the example as `.env`, and
fill the two secret values using a password manager. Run the four `docker compose`
commands above. No shell installer is needed.

Add environments through Administration. The default web port is localhost-only.
For shared HTTPS access, configure the reverse proxy, allowed hosts and CSRF
origins described in [application configuration](../webapp/README.md#https-deployment).
Change `WEB_PORT` in `.env` if port 8000 is already used.

## Upgrade an existing Compose installation

Back up PostgreSQL and `.env`, and wait for active collections to finish. Preserve
the project name and existing database volume. In your existing deployment directory,
set `NSX_IMAGE_TAG=0.3.0` in `.env`, then:

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

The sidebar and Administration show version **0.3.0**, source revision and build
time. The `0.3.0` tag pins this release; `latest` is mutable. Source and Compose
files are pinned by Git tag `v0.3.0`.

- [Docker Hub](https://hub.docker.com/r/vkernel/nsx-security-analyzer)
- [Release source](https://github.com/vkernel/NSX-Security-Analyzer/tree/v0.3.0)
- [Compose file](../deploy/compose.yaml)
- [Operations and backups](operations.md)
- [Kubernetes conversion](../deploy/kubernetes/README.md)

The optional `deploy/install.sh` convenience script is retained; it is no longer
required for the documented installation path.
