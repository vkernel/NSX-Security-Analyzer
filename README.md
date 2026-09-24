# NSX Security Analyzer

A read-only NSX Policy inventory and security review workspace. Collect inventory
from multiple NSX Managers, explore firewall rules and reference evidence, and
compare saved snapshots in a PostgreSQL-backed web application.

**Findings are review candidates, not deletion approvals or proof of historical non-use.**
The collector sends GET requests to NSX; it does not change rules or reset counters.

## What it does

- Inventory groups, services, distributed firewall policies, rules, tags and scopes.
- Identify empty groups, unreferenced custom objects, disabled rules and zero recorded counters.
- Inspect membership definitions, configuration references and collection coverage.
- Schedule collections per environment, with progress tracking and readable errors.
- Retain snapshots in PostgreSQL and review firewall activity across observations.
- Search tables with AND/OR or regex, apply column filters, and export matching rows as CSV.
- Manage users, retention, sync policies, display preferences and notifications.
- Explore synthetic data in a separate demo environment without connecting to NSX.

The web application supports bundled or customer-managed PostgreSQL. Configure
environments, schedule collections and explore reports through the web interface.

## Installation

**Recommended: [install the prebuilt Docker Hub image](docs/docker-hub.md)** — no local build required.
Images are available for AMD64 and ARM64 at [Docker Hub](https://hub.docker.com/r/vkernel/nsx-security-analyzer).

The instructions below build the application from source.

## Install the complete product

With Docker running (macOS, Linux or WSL):

```sh
curl -fSL https://raw.githubusercontent.com/vkernel/NSX-Security-Analyzer/main/deploy/install.sh -o install-nsx.sh
sh install-nsx.sh
```

This pulls the Docker Hub images, generates secrets, starts PostgreSQL, migrations,
the web application, worker and scheduler, then prompts for an administrator.
Open **http://localhost:8000**. Docker Hub's single-image Run button does not install
these dependencies. See the [installation guide](docs/docker-hub.md) for manual
setup, native Windows, recovery and upgrades. Existing installations are preserved.

## Quick start from source

Requirements: Docker with Docker Compose and access to your NSX Manager's HTTPS
Policy API. The remote-database override requires Compose 2.24.4 or later.

```sh
git clone https://github.com/vkernel/NSX-Security-Analyzer.git
cd NSX-Security-Analyzer/webapp
cp .env.example .env
chmod 600 .env
```

Generate two independent secrets, running this command once for each:

```sh
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Set `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD` in `.env`, then start:

```sh
docker compose up --build -d
docker compose exec web python manage.py createsuperuser
```

Open **http://localhost:8000** and sign in. In **Administration**, add an
environment with its NSX Manager address, username and password. Upload a CA
certificate if required and select a sync interval. You can also run a collection
from the environment page. For a preview, choose **Environments → Add testing data**.

Keep `DJANGO_SECRET_KEY` stable: it is used to protect saved manager credentials.
The default service listens on loopback. Use an HTTPS reverse proxy for shared access.

## Documentation

| Guide | Contents |
| --- | --- |
| [Docker Hub installation](docs/docker-hub.md) | Prebuilt images, Compose setup and upgrades |
| [Installation and configuration](webapp/README.md) | Docker, external PostgreSQL, TLS, users, scheduling and settings |
| [Operations](docs/operations.md) | Upgrades, backups, recovery and troubleshooting |
| [Architecture](docs/architecture.md) | Components, storage and collection flow |
| [Contributing](CONTRIBUTING.md) | Development setup, tests and pull requests |
| [Security](SECURITY.md) | Reporting vulnerabilities and deployment boundaries |
| [Support](SUPPORT.md) | Bug reports and useful diagnostics |
| [Roadmap](roadmap.md) | Planned history, IPFIX traffic analysis and enterprise capabilities |
| [Changelog](CHANGELOG.md) | Project changes |

## Scope and interpretation

The collector targets **NSX Local Manager `/infra` Policy inventory**. It does not
provide NSX-V, legacy Manager, Global Manager or project inventory coverage.
Use an account with read access across Policy inventory and search.

Search indexing delays and account visibility affect reference findings. Missing
or failed counter checks are unknown, not zero. Historical analysis compares
saved observations; collection gaps, counter resets and activity between snapshots
limit what can be concluded. A segmentation indicator describes rule configuration,
not effective workload isolation. Read coverage and evidence before acting.

All signed-in workspace users can read all environments; this is not tenant
isolation. Local user authentication is implemented. LDAP/LDAPS and Keycloak
integration are not included in the current codebase.

## Project status and license

This is an initial standalone repository; no versioned release or support SLA is
currently declared. Validate the application against your NSX deployment before
operational adoption.

NSX Security Analyzer is licensed under the [Apache License 2.0](LICENSE).
See [NOTICE](NOTICE) for project attribution. Third-party dependencies retain their
own licenses; the project license does not replace their terms.

This is an independent project and is not affiliated with or endorsed by VMware
or Broadcom.
