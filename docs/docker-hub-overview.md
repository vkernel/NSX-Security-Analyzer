# NSX Security Analyzer

Web-based VMware NSX Policy inventory, configuration review and snapshot history.

**Install the complete stack using Docker Compose. No install.sh or source build required.**

The application image is used by web, migration, collection worker and scheduler
containers. PostgreSQL runs separately with persistent storage. Pulling or running
this image alone does not start the complete product.

- [Installation instructions](https://github.com/vkernel/NSX-Security-Analyzer/blob/v0.3.0/docs/docker-hub.md)
- [Download Compose file](https://raw.githubusercontent.com/vkernel/NSX-Security-Analyzer/v0.3.0/deploy/compose.yaml)
- [Configuration example](https://raw.githubusercontent.com/vkernel/NSX-Security-Analyzer/v0.3.0/deploy/.env.example)
- [Source code](https://github.com/vkernel/NSX-Security-Analyzer)

After downloading Compose and configuring unique secrets in `.env`:

```sh
docker compose pull
docker compose up -d
docker compose exec web python manage.py createsuperuser
```

Open http://localhost:8000. There is no shared administrator password. Keep your
`.env` and PostgreSQL backups; `docker compose down` retains data, whereas `down -v`
deletes the database volume.

Release: **0.3.0**. Architectures: Linux AMD64 and ARM64. License: Apache-2.0.
The UI displays the image version and source build identifier. Use a pinned version
for controlled upgrades. See the installation guide for migration and backup steps.

IPFIX remains experimental and is not started by the default stack. Inventory
collection is read-only; findings are review candidates, not deletion approvals.
