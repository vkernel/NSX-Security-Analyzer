# NSX Security Analyzer

Web-based VMware NSX Policy inventory, configuration review and snapshot history.

**Docker Compose and configuration are required. This is not an all-in-one image.**

Docker Hub / Docker Desktop's **Run** button starts only the application container.
It does not supply secrets, start PostgreSQL, apply migrations, or start the
collection worker and scheduler. Running the image without configuration causes
`KeyError: 'DJANGO_SECRET_KEY'` followed by `Worker failed to boot`.

Use the supplied Compose file and a `.env` file to install the complete stack.
No `install.sh` or source build is required.

The application image is used by web, migration, collection worker and scheduler
containers. PostgreSQL runs separately with persistent storage. Pulling or running
this image alone does not start the complete product.

- [Installation instructions](https://github.com/vkernel/NSX-Security-Analyzer/blob/v0.3.1/docs/docker-hub.md)
- [Download Compose file](https://raw.githubusercontent.com/vkernel/NSX-Security-Analyzer/v0.3.1/deploy/compose.yaml)
- [Configuration example](https://raw.githubusercontent.com/vkernel/NSX-Security-Analyzer/v0.3.1/deploy/.env.example)
- [Source code](https://github.com/vkernel/NSX-Security-Analyzer)

## Required configuration

Create `.env` **in the same directory as the downloaded `compose.yaml`**.
Compose reads this file and passes the configured values into the containers.
Docker Desktop's Run dialog and plain `docker run` do not automatically load it.

| Variable | Requirement / purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Required, unique random secret. Use the same value for web, migrations, worker and scheduler. It also protects saved NSX credentials; preserve it across upgrades and restarts. |
| `POSTGRES_PASSWORD` | Required for the supplied stack. A separate random password, matching the PostgreSQL user. Keep it stable when reusing the database volume. |
| `POSTGRES_HOST` | Database hostname reachable from the application containers. The supplied Compose file defaults to `db`, its PostgreSQL service. For standalone containers, provide an actual reachable database host; `localhost` refers to that container itself. |
| `POSTGRES_DB` | Database name; defaults to `nsx`. |
| `POSTGRES_USER` | Database user; defaults to `nsx`. |
| `POSTGRES_PORT` | Database port; defaults to `5432`. |
| `DJANGO_ALLOWED_HOSTS` | Hostnames/IPs used to access the application, comma-separated and without schemes or ports. Local Compose defaults: `localhost,127.0.0.1,[::1]`. Configure your hostname for remote access. |
| `WEB_PORT` | Compose-only host port; defaults to `8000`. The web process listens on container port `8000`. |
| `NSX_IMAGE_TAG` | Compose-only image selection; defaults to `0.3.1`. |

For an HTTPS reverse proxy, also configure `DJANGO_CSRF_TRUSTED_ORIGINS`
(full HTTPS origins), `DJANGO_HTTPS` and, only behind a trusted proxy,
`DJANGO_TRUST_PROXY`. For remote PostgreSQL, configure verified database TLS;
see the linked installation guide. NSX Manager credentials are entered in the
web GUI, not in these deployment variables.

## New installation without install.sh

The commands below are for macOS/Linux/WSL and require Docker Compose v2 and curl.
Start in a **new directory**; do not overwrite an existing installation's `.env`.

```sh
mkdir nsx-security-analyzer
cd nsx-security-analyzer
curl -fSL https://raw.githubusercontent.com/vkernel/NSX-Security-Analyzer/v0.3.1/deploy/compose.yaml -o compose.yaml

docker pull vkernel/nsx-security-analyzer:0.3.1
umask 077
docker run --rm --network none --entrypoint python vkernel/nsx-security-analyzer:0.3.1 -c 'import secrets; print("DJANGO_SECRET_KEY="+secrets.token_hex(32)); print("POSTGRES_PASSWORD="+secrets.token_hex(32)); print("WEB_PORT=8000")' > .env
```

This generates two different secrets locally; no host Python installation is needed.
For native Windows PowerShell, download the configuration example, save it as `.env`,
and fill both secrets with independently generated password-manager values.

Start the stack:

```sh
docker compose pull
docker compose up -d
docker compose exec web python manage.py createsuperuser
```

Wait until the web container is healthy before running `createsuperuser`.
The migration container exiting with code 0 is normal.

Open http://localhost:8000. There is no shared administrator password. Keep your
`.env` and PostgreSQL backups; `docker compose down` retains data, whereas `down -v`
deletes the database volume.

Release: **0.3.1**. Architectures: Linux AMD64 and ARM64. License: Apache-2.0.
The UI displays the image version and source build identifier. Use a pinned version
for controlled upgrades. See the installation guide for migration and backup steps.

IPFIX remains experimental and is not started by the default stack. Inventory
collection is read-only; findings are review candidates, not deletion approvals.


## Troubleshooting missing configuration

If you see `KeyError: 'DJANGO_SECRET_KEY'`, the web container has not received the
required secret. Adding only that variable is **not** a complete installation:
a reachable, initialized database and the collection services are still required.
Use the Compose deployment above instead of Docker Desktop's standalone Run action.

For an existing Compose installation, keep its current `.env` and run from that
directory:

```sh
docker compose pull
docker compose up -d
docker compose ps -a
docker compose logs --tail=100 db migrate web worker scheduler
```

Do not regenerate the Django secret to resolve a startup error. Ensure the existing
secret is passed to every app service. Changing only `POSTGRES_PASSWORD` in `.env`
does not change the password of an already initialized PostgreSQL database.
