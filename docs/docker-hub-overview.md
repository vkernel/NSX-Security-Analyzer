# NSX Security Analyzer

A read-only NSX Policy inventory and security review workspace with scheduled
collections, PostgreSQL snapshot history and firewall activity analysis.

**[Source code and documentation on GitHub](https://github.com/vkernel/NSX-Security-Analyzer)**

The web GUI is the only product interface. Configure environments, schedule
collections and review database-backed reports in the browser. No standalone CLI
or file-based report import/export workflow is included; table CSV export remains available.

## Features

- Groups, services, distributed firewall policies, rules, tags and scopes.
- Empty-group and unused-object candidates with reference evidence.
- Firewall counters and historical activity review.
- Scheduled multi-environment collection with progress and readable errors.
- Search, column filters, CSV exports, retention policies and display preferences.
- Synthetic demo data for exploring the application without NSX connectivity.
- Snapshot comparison, finding ownership and review notes, and collection coverage with observation gaps.

Findings are review candidates, not deletion approvals. Collection only sends GET
requests to NSX; it does not modify rules or reset counters. IPFIX traffic analysis
is planned in the [roadmap](https://github.com/vkernel/NSX-Security-Analyzer/blob/main/roadmap.md),
not part of this image.

## Supported platforms and tags

Linux **AMD64** and **ARM64** are included in each published multi-platform tag.

- `0.2.1`: pinned application image built from Git commit `60f3de6`.
- `latest`: mutable tag for the currently published application image.

```sh
docker pull vkernel/nsx-security-analyzer:0.2.1
```

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

For native Windows PowerShell, remote PostgreSQL or upgrades, follow the
[manual installation guide](https://github.com/vkernel/NSX-Security-Analyzer/blob/main/docs/docker-hub.md).

Create synthetic test data from **Environments → Add environment → Create demo environment**.
Delete an environment from **Edit environment → Delete environment**, with explicit confirmation.

The sidebar displays **v0.2.1** and the build identifier. Administration also shows the build date.

## Upgrading to 0.2.1

Back up your database and `.env`, and let active collections finish. Set
`NSX_IMAGE_TAG=0.2.1` in `.env`, then run from the deployment directory:

```sh
docker compose pull
docker compose stop web worker scheduler
docker compose run --rm migrate
docker compose up -d
```

Migration 0011 creates review tables; existing snapshots are preserved.
Use your usual extra Compose file arguments for remote-database installations.
[History and review guide](https://github.com/vkernel/NSX-Security-Analyzer/blob/main/docs/history-and-review.md)

## Configuration and data

- PostgreSQL is separate from this image. Bundled and customer-managed PostgreSQL are supported.
- Uploaded CA certificates and snapshot data are stored in the database.
- Saved manager passwords are encrypted using a key derived from `DJANGO_SECRET_KEY`; preserve that secret with your backups.
- The default web port binds to loopback. Use an HTTPS reverse proxy for shared access.
- All signed-in users can read all environments; the current application does not provide tenant isolation.
- NSX Local Manager `/infra` Policy inventory is supported; NSX-V, Global Manager and project inventories are outside scope.

[Full Docker Hub installation, remote PostgreSQL and upgrade instructions](https://github.com/vkernel/NSX-Security-Analyzer/blob/main/docs/docker-hub.md)

## Project links

- [GitHub repository](https://github.com/vkernel/NSX-Security-Analyzer)
- [Configuration guide](https://github.com/vkernel/NSX-Security-Analyzer/blob/main/webapp/README.md)
- [Operations and backups](https://github.com/vkernel/NSX-Security-Analyzer/blob/main/docs/operations.md)
- [Issues and support](https://github.com/vkernel/NSX-Security-Analyzer/issues)
- [Security reporting](https://github.com/vkernel/NSX-Security-Analyzer/blob/main/SECURITY.md)

Licensed under the [Apache License 2.0](https://github.com/vkernel/NSX-Security-Analyzer/blob/main/LICENSE).
[Project attribution](https://github.com/vkernel/NSX-Security-Analyzer/blob/main/NOTICE).
Third-party dependencies retain their own licenses. The application image includes
its license and attribution at `/app/LICENSE` and `/app/NOTICE`.

This independent project is not affiliated with or endorsed by VMware or Broadcom.
