# Install from Docker Hub

Use the published image without building the application locally. Docker selects
Linux AMD64 or ARM64 automatically. The Compose stack runs the web service,
worker, scheduler and migrations using the same image, with PostgreSQL separately.
A single `docker run` command does not start the complete application.

## Requirements

- Docker with Compose **2.24.4 or newer** (the override uses `!reset`).
- HTTPS connectivity from the worker to your NSX Managers.
- Git to obtain the deployment files, or download them from GitHub.

## Install with bundled PostgreSQL

```sh
git clone https://github.com/vkernel/NSX-Security-Analyzer.git
cd NSX-Security-Analyzer/webapp
cp .env.example .env
chmod 600 .env
```

Generate two different random values; run this once for each value:

```sh
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Edit `.env` and set `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD` to those values.
Keep `DJANGO_SECRET_KEY` stable and include it in your secure deployment backups.
The default pinned image is `021a065`. To select another published tag, add
`NSX_IMAGE_TAG=<tag>` to `.env`. `NSX_IMAGE_TAG=latest` follows the mutable latest tag.

```sh
docker compose -f compose.yaml -f compose.hub.yaml pull
docker compose -f compose.yaml -f compose.hub.yaml up -d --no-build
docker compose -f compose.yaml -f compose.hub.yaml exec web python manage.py createsuperuser
```

Open **http://localhost:8000** and sign in. Add a manager under Administration,
enter its credentials, upload its trusted CA if needed, and choose a sync interval.
Use **Environments → Add testing data** to explore a synthetic inventory first.

The default port binds to loopback. For shared access, configure an HTTPS reverse
proxy as described in [application configuration](../webapp/README.md#https-deployment).
Do not expose the default local setup directly to the internet.

## Use an existing PostgreSQL server

Follow the [remote database configuration](../webapp/README.md#optional-customer-managed-postgresql)
for database provisioning, credentials, TLS and CA mounting. Then use all three files:

```sh
docker compose -f compose.yaml -f compose.hub.yaml -f compose.remote.yaml pull
docker compose -f compose.yaml -f compose.hub.yaml -f compose.remote.yaml up -d --no-build
docker compose -f compose.yaml -f compose.hub.yaml -f compose.remote.yaml exec web python manage.py createsuperuser
```

Keep these same file arguments for subsequent commands. The bundled database is
not started in remote mode. Do not enable its `bundled-db` profile.

## Verify and maintain

```sh
docker compose -f compose.yaml -f compose.hub.yaml ps
docker compose -f compose.yaml -f compose.hub.yaml logs --tail=100 web worker scheduler
```

Before upgrading, back up the database and application secret, review migrations,
and wait for active collections to finish. Set the desired published tag in `.env`:

```sh
docker compose -f compose.yaml -f compose.hub.yaml pull
docker compose -f compose.yaml -f compose.hub.yaml stop web worker scheduler
docker compose -f compose.yaml -f compose.hub.yaml run --rm migrate
docker compose -f compose.yaml -f compose.hub.yaml up -d --no-build
```

For remote databases, also add `-f compose.remote.yaml` to each command above.
Never use `down -v` unless intentionally deleting the bundled database volume.
See [operations](operations.md) for backup and recovery guidance.

## Tags and source

- `021a065`: image built from Git commit `021a065`.
- `latest`: currently published application image; may change on future releases.
- [Docker Hub tags](https://hub.docker.com/r/vkernel/nsx-security-analyzer/tags)
- [Source repository](https://github.com/vkernel/NSX-Security-Analyzer)

Existing source-built deployments use the same Compose project name and volume.
Do not launch a second copy unintentionally. Use the maintenance procedure to
switch the existing deployment to the published image. Changing between build
and image installation does not itself require deleting or importing data.
