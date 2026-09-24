# Install from Docker Hub

Use the published image without building the application locally. Docker selects
Linux AMD64 or ARM64 automatically. The Compose stack runs the web service,
worker, scheduler and migrations using the same image, with PostgreSQL separately.
A single `docker run` command does not start the complete application.

## Complete installation from Docker Hub

Docker Hub / Docker Desktop's **Run** button starts only one container. It does
not install PostgreSQL or the collection services. Use the complete-stack installer
below instead. Docker Desktop will then show all services together as
`nsx-security-analyzer`.

For a **new installation** on macOS, Linux, or Windows with WSL, start Docker and
run these commands in an interactive terminal (Docker Compose v2 and curl required):

```sh
curl -fSL https://raw.githubusercontent.com/vkernel/NSX-Security-Analyzer/main/deploy/install.sh -o install-nsx.sh
sh install-nsx.sh
```

The installer downloads prebuilt application and PostgreSQL images, generates
private secrets, initializes the database, starts the web application, collection
worker and scheduler, waits for the website, and prompts for an administrator
account. No Git, host Python installation, or application build is required.
Open **http://localhost:8000** when it finishes.

Deployment files and secrets are saved in `./nsx-security-analyzer/`. An optional
first argument selects a different directory. Existing installation containers or
volumes are detected and left untouched; use the upgrade procedure for those.
Do not delete the generated `.env`: it protects saved manager credentials.

If setup stops after creating the directory, preserve it and resume there:

```sh
cd nsx-security-analyzer
docker compose up -d
docker compose logs --tail=100 db migrate web worker scheduler
docker compose exec web python manage.py createsuperuser
```

Resume only after `.env` contains both generated secrets; if download or secret
generation failed, resolve that failure first. The installer does not overwrite an
existing directory. A port conflict on 8000 can be resolved by changing `WEB_PORT`
in `.env` and running `docker compose up -d` again.

For native Windows PowerShell, use the manual Compose installation below.

## Manual installation requirements

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
The default pinned image is `e260cc5`. To select another published tag, add
`NSX_IMAGE_TAG=<tag>` to `.env`. `NSX_IMAGE_TAG=latest` follows the mutable latest tag.

```sh
docker compose -f compose.yaml -f compose.hub.yaml pull
docker compose -f compose.yaml -f compose.hub.yaml up -d --no-build
docker compose -f compose.yaml -f compose.hub.yaml exec web python manage.py createsuperuser
```

Open **http://localhost:8000** and sign in. Add a manager under Administration,
enter its credentials, upload its trusted CA if needed, and choose a sync interval.
Use **Environments → Add environment → Create demo environment** to explore a synthetic inventory first.

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

## Upgrading from legacy CLI-based installations

The application no longer includes the standalone collector CLI or manager-file
importer. Existing database snapshots remain readable. Environments using legacy
credential variables must have a username and password saved in **Edit environment**
before collection resumes. Source deployments should use this repository's updated
Compose files; the worker no longer loads `.nsx.env`.

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

- `e260cc5`: image built from Git commit `e260cc5`.
- `latest`: currently published application image; may change on future releases.
- [Docker Hub tags](https://hub.docker.com/r/vkernel/nsx-security-analyzer/tags)
- [Source repository](https://github.com/vkernel/NSX-Security-Analyzer)

Existing source-built deployments use the same Compose project name and volume.
Do not launch a second copy unintentionally. Use the maintenance procedure to
switch the existing deployment to the published image. Changing between build
and image installation does not itself require deleting or importing data.

## Startup failures

`Worker failed to boot` is the final Gunicorn message, not the root cause. Check
the preceding error with `docker compose logs --tail=100 web`. A missing
`DJANGO_SECRET_KEY` means the image was started without required configuration.
The complete installer creates this configuration automatically. Database errors
require checking `db` and `migrate` logs as well. Pulling the image or pressing Run
alone does not create this application's dependencies.

The standalone [deployment Compose file](../deploy/compose.yaml) can also be used
with your own `.env`; it requires neither a source checkout nor the Hub override.
