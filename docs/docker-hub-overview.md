# NSX Security Analyzer

A read-only NSX Policy inventory and security review workspace with scheduled
collections, PostgreSQL snapshot history and firewall activity analysis.

**[Source code and documentation on GitHub](https://github.com/vkernel/NSX-Security-Analyzer)**

## Features

- Groups, services, distributed firewall policies, rules, tags and scopes.
- Empty-group and unused-object candidates with reference evidence.
- Firewall counters and historical activity review.
- Scheduled multi-environment collection with progress and readable errors.
- Search, column filters, CSV exports, retention policies and display preferences.
- Synthetic demo data for exploring the application without NSX connectivity.

Findings are review candidates, not deletion approvals. Collection only sends GET
requests to NSX; it does not modify rules or reset counters. IPFIX traffic analysis
is planned in the [roadmap](https://github.com/vkernel/NSX-Security-Analyzer/blob/main/roadmap.md),
not part of this image.

## Supported platforms and tags

Linux **AMD64** and **ARM64** are included in each published multi-platform tag.

- `597cbcb`: pinned application image built from Git commit `597cbcb`.
- `latest`: mutable tag for the currently published application image.

```sh
docker pull vkernel/nsx-security-analyzer:597cbcb
```

## Quick start with Docker Compose

Docker Compose **2.24.4+** is required. Use the repository's deployment files to
start the web application, worker, scheduler and PostgreSQL together. Pulling or
running the application image alone does not start the complete stack.

```sh
git clone https://github.com/vkernel/NSX-Security-Analyzer.git
cd NSX-Security-Analyzer/webapp
cp .env.example .env
chmod 600 .env
```

Set `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD` in `.env` to different random values.
Generate each value with:

```sh
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Then start the prebuilt images and create your administrator account:

```sh
docker compose -f compose.yaml -f compose.hub.yaml pull
docker compose -f compose.yaml -f compose.hub.yaml up -d --no-build
docker compose -f compose.yaml -f compose.hub.yaml exec web python manage.py createsuperuser
```

Open **http://localhost:8000**. Add an environment in Administration or explore
**Environments → Add testing data**. The Compose override defaults to `597cbcb`;
set `NSX_IMAGE_TAG=latest` in `.env` to follow the latest tag instead.

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
