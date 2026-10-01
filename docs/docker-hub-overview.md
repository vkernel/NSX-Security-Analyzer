# NSX Security Analyzer

> Release **0.5.1** improves Inventory and Firewall page loading, historical activity,
> tag evidence and table filtering. Existing installations should apply migrations
> and run `python manage.py index_snapshots --refresh` after updating.


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

- [Installation instructions](https://github.com/vkernel/NSX-Security-Analyzer/blob/v0.5.1/docs/docker-hub.md)
- [Download Compose file](https://raw.githubusercontent.com/vkernel/NSX-Security-Analyzer/v0.5.1/deploy/compose.yaml)
- [Configuration example](https://raw.githubusercontent.com/vkernel/NSX-Security-Analyzer/v0.5.1/deploy/.env.example)
- [Source code](https://github.com/vkernel/NSX-Security-Analyzer)

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

From the directory containing `compose.yaml` and your configured `.env`, run:

```sh
docker compose pull
docker compose up -d
docker compose ps -a
```

Docker Desktop will show the services grouped under `nsx-security-analyzer`. A single randomly named container created with Run is not the complete deployment. Adding environment variables or restarting that container will not create the missing services. No `install.sh` is required for the Compose deployment.

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
| `NSX_IMAGE_TAG` | Compose-only image selection; defaults to `0.5.1`. |

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
curl -fSL https://raw.githubusercontent.com/vkernel/NSX-Security-Analyzer/v0.5.1/deploy/compose.yaml -o compose.yaml

export NSX_IMAGE_TAG=0.5.1
docker pull "vkernel/nsx-security-analyzer:$NSX_IMAGE_TAG"
umask 077
docker run --rm --network none --entrypoint python "vkernel/nsx-security-analyzer:$NSX_IMAGE_TAG" -c 'import secrets; print("DJANGO_SECRET_KEY="+secrets.token_hex(32)); print("POSTGRES_PASSWORD="+secrets.token_hex(32)); print("WEB_PORT=8000")' > .env
printf 'NSX_IMAGE_TAG=%s\n' "$NSX_IMAGE_TAG" >> .env
```

This generates two different secrets locally; no host Python installation is needed.
For native Windows PowerShell, download the configuration example, save it as `.env`,
and fill both secrets with independently generated password-manager values.

Start the stack:

```sh
docker compose pull
docker compose up -d
```

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

Keep your `.env` and PostgreSQL backups; `docker compose down` retains data,
whereas `down -v` deletes the database volume.

Release **0.5.1** supports Linux AMD64 and ARM64. It adds indexed snapshot reports,
server-side pagination/search/sorting, on-demand evidence, and full-result CSV exports.
It retains structured logs, audit history, automatic initial administrator provisioning,
and Kubernetes deployment support. Experimental IPFIX functionality is deferred.

After upgrading and running migrations, prepare existing snapshots with:

```sh
docker compose exec web python manage.py index_snapshots
```

This uses saved PostgreSQL data without contacting NSX and can be restarted safely.
New collections prepare their report indexes automatically. Indexes require additional
database storage; original reports are retained.
The UI displays the image version and source build identifier. Use a pinned version
for controlled upgrades. See the installation guide for migration and backup steps.

Inventory collection is read-only; findings are review candidates, not deletion approvals.


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
