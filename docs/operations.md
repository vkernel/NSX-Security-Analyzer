# Operations

Run Compose commands from the folder containing your deployment's `compose.yaml`
and `.env`: the downloaded installation folder for Docker Hub, or `webapp/` for
a source build. Keep any override `-f` arguments on every command. Kubernetes
commands and ordered upgrades are in the [Kubernetes guide](../deploy/kubernetes/README.md).

## Initial administrator

Database migration `0017_initial_administrator` provisions `admin` with password
`NSXSecurityA!` when no superuser or account named admin (case insensitive) exists.
Change the initial password in Administration → Users & access after signing in.
The password is stored as a hash, never in audit events or startup logs.

This runs once per database, using migration history. Restarts and upgrades do
not reset passwords; deleting the initial administrator does not recreate it.
Existing accounts, including disabled administrators, remain unchanged. Restoring
a database also restores its accounts and migration history. Keep one migration
job per deployment and wait for it before starting application pods. This works
with bundled and external PostgreSQL; it does not provision a PostgreSQL login.

No manual account-creation command is needed with release **0.4.0**.

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

## Upgrade a source build

For prebuilt images, use the [Docker Hub upgrade steps](docker-hub.md#upgrade-an-existing-compose-installation).

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

## Logging, audit trail and collection diagnostics

These features are included in release **0.4.0**. Pull and recreate application
containers when upgrading; restarting an older image does not update its code.

Application and Gunicorn logs use JSON on stdout/stderr with UTC timestamps,
severity, process/pod identity and application build. Web requests include a
server-generated request ID (also returned as `X-Request-ID`); collector logs
include job and environment IDs. Request routes omit query strings and raw URLs.
Successful health/poll requests are suppressed in application logs, while failures
are retained. Gunicorn logs method/status/duration without request payloads.

```sh
kubectl logs -n <namespace> <worker-pod> --since=1h --timestamps -f
kubectl logs -n <namespace> <worker-pod> --previous --timestamps
# From the local deployment directory:
docker compose logs --since=1h -f worker scheduler web
```

Set `NSX_LOG_FORMAT=text` for a readable single-line format; default is `json`.
Forward container output to your platform's centralized logging service with
restricted access, appropriate retention and alerts on ERROR events. Application
changes cannot recover logs from evicted pods or guarantee delivery when the
container runtime/logging service fails. No log files or sidecars are required
inside this application image. Configure your platform's log collection separately.

### Troubleshoot a collection

Staff can open **Diagnostics** in collection history. The page shows the job ID,
saved stage durations, diagnostic expiry and sanitized exception chain. Live stage
starts, ends and 30-second heartbeats appear in terminal logs. Heartbeats indicate
a live process, not confirmed database progress. Nested stage messages are normal.

Database stages distinguish job-lock acquisition, snapshot write, finding
synchronization, completion update and transaction commit. Finding counters report
progress every 500 candidates. Only `collection committed` confirms successful
commit. A saved timeline is supplemental and is written after completion or a
handled failure; a killed process or database outage may leave none. Terminal logs
are the primary diagnostic source in that case.

Errors have stable codes and sanitized chained exceptions with file/function/line
locations, without local variables or source lines. PostgreSQL failures include
SQLSTATE where available but suppress driver messages that could contain SQL or
report contents. Timeout/exit/stale-job events do not claim an underlying cause;
correlate exit signals with Kubernetes termination reasons before concluding OOM.

Superusers can queue a **Diagnostic collection** in **Administration → Audit &
diagnostics**. It enables NSX DEBUG requests only for that job, for up to 15 minutes
from submission. It does not restart a running collection or enable Django DEBUG.
Warnings and errors remain enabled after expiry. Detailed logs can contain Policy
object paths; restrict access. Credentials, authorization values, connection secrets,
request bodies and snapshots are not intentionally logged. Redaction is defense in
depth; avoid putting sensitive free text in names or paths.

### Durable audit events

**Administration → Audit & diagnostics** provides superuser-only search, outcome
filters, pagination and JSON export (maximum 5,000 newest matching events).
Recorded actions include logins/logout/failed logins, access denials, environment
changes/deletion, credential or trust changes (boolean only), user/group/permission
changes, manual and scheduled job creation, review changes, collection outcomes and
retention cleanup. Actor/object identities are scalar IDs so records survive deletion.
Historical activity is not reconstructed. Direct SQL and bulk ORM writes outside
instrumented application paths are not covered; use database auditing if required.

HTTP `processed` events mean a mutation request was handled, not that its requested
change succeeded. Domain change records reflect committed transactions. Failed
requests/authentication are recorded separately; an unavailable database falls back
to console audit diagnostics and cannot provide a durable database record until a
logging backend captures it. Audit logs have no edit/delete UI, but a database
administrator can still modify them: use independent protected storage for stronger
tamper resistance. No tamper-proof or exactly-once delivery guarantee is made.

Audit events default to indefinite retention independently of snapshot cleanup.
Set `NSX_AUDIT_LOG_RETENTION_DAYS` to a positive number to remove older events in
batches of 1,000 through the scheduler. Cleanup itself is audited. Backups follow
your database backup policy; external log retention is configured separately.
