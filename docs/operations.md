# Operations

Run Compose commands from `webapp/`. For external PostgreSQL, include
`-f compose.yaml -f compose.remote.yaml` on every Compose command.

## Routine checks

```sh
docker compose ps
docker compose logs --tail=100 web worker scheduler
docker compose exec web python manage.py check
```

`/health/` is used by the container health check. Collection failures appear in the
workspace with expandable details. Check DNS/VPN connectivity, HTTPS reachability,
certificate trust and account permissions from the worker's network context.
A healthy web container does not establish that every NSX Manager is reachable.

## Upgrade

Back up the database and application secret first. Review changes and migrations.
For a maintenance-window upgrade, stop application processes before migration:

```sh
docker compose stop web worker scheduler
git pull --ff-only
docker compose build
docker compose run --rm migrate
docker compose up -d
```

Wait for active collections to finish before stopping workers where practical.
Review service health and collection history afterward. Do not roll back code across
schema changes without reviewing migration compatibility; restore a matching backup
if necessary. Avoid `docker compose down -v` unless deliberately deleting all data.

## Backups

For bundled PostgreSQL with default database/user names:

```sh
docker compose exec -T db pg_dump -U nsx -d nsx -Fc > nsx-workspace.dump
chmod 600 nsx-workspace.dump
```

Use your configured names when different. For a customer-managed database, use its
approved backup procedure. Preserve the deployment's `.env` securely and separately:
`DJANGO_SECRET_KEY` is required to decrypt saved manager credentials. A database dump
alone does not contain this secret.

Test recovery into an isolated database. Stop web, worker and scheduler before a
planned restore, restore with `pg_restore`, run migrations for the matching revision,
and verify configuration before restarting scheduling. If the application secret is
lost, saved manager passwords must be entered again.

## Retention

Retention is configured in Administration and is disabled by default. Review the
policy and its preview before enabling it. Pruning old observations affects the
available history window; deleted evidence cannot be reconstructed by later audits.
Retention is not a replacement for backups.

## Moving to this standalone checkout

If upgrading from the earlier `vmware-scripts/python/nsx-inventory` location, keep
that deployment running until this checkout is validated. Both Compose files use
the same explicit project name, `nsx-security-analyzer`. Do not start a second copy
against the same live database unintentionally.

Transfer deployment secrets through your secure local process (never Git), preserve
the existing database volume or remote connection settings, and use the maintenance
steps above to recreate services from this checkout. Existing source files and
running services are not moved merely by cloning this repository.

## Inventory changes during collection

If NSX search returns a different number of objects from its advertised total,
the collector retries the search from page one after 2 and then 4 seconds. Each
failed attempt is discarded. After three unsuccessful attempts the collection
fails rather than publishing a partial snapshot; previous reports remain intact.
Authentication errors and unrelated pagination failures are not retried by this
mechanism. Search metadata records `inventory_retries` for successful collections.
Concurrent policy/object changes and eventual search indexing can cause this
condition. If it persists, allow changes and indexing to settle before collecting
again. Successful pagination does not guarantee a transactional point-in-time
snapshot of NSX. No counters or configuration are modified.
