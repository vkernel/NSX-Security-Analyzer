# NSX Security Analyzer workspace

A Django frontend and backend for the existing read-only NSX collector. PostgreSQL
stores environments, audit jobs and snapshots. A separate worker collects inventory;
web requests never wait for an NSX audit to finish. The CLI remains available and
uses the same collector and report renderer.

## Run with Docker Desktop

From this directory:

```sh
cp .env.example .env
chmod 600 .env
```

Set `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD` in `.env` to **different** random
values. Generate each with:

```sh
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Enter each NSX Manager's username and password in the workspace when adding an
environment. The password field masks input. Passwords are encrypted in PostgreSQL
using authenticated encryption with a key derived from `DJANGO_SECRET_KEY`.
Keep that application secret stable and retain it with your deployment backups.
The local `.env` file is excluded from Git and the Docker build context.

```sh
docker compose up --build -d
docker compose exec web python manage.py createsuperuser
```

Open **http://localhost:8000** and sign in. The service binds to loopback by default.
Compose waits for PostgreSQL health checks and successful database migrations
before starting the web process and worker. Static assets are built into the image.

If Docker Desktop's command is not on your PATH on macOS, use
`/Applications/Docker.app/Contents/Resources/bin/docker`, or add
`$HOME/.docker/bin` to your shell PATH.

## Optional customer-managed PostgreSQL

The default `docker compose up --build -d` still starts the bundled PostgreSQL
container. To use an existing PostgreSQL server instead, configure `.env`:

```dotenv
POSTGRES_HOST=postgres.customer.example
POSTGRES_PORT=5432
POSTGRES_DB=nsx_analyzer
POSTGRES_USER=nsx_analyzer
POSTGRES_PASSWORD='your-database-password'
POSTGRES_SSLMODE=verify-full
POSTGRES_SSLROOTCERT=system
POSTGRES_CONNECT_TIMEOUT=10
```

Use PostgreSQL 17 or a version supported by Django 5.2. The customer must provision
an empty, dedicated database and a role that can connect and create/alter tables,
indexes and sequences in its schema. The application runs Django migrations; it
does not create the remote database or role and does not require a superuser.
The PostgreSQL server must accept connections from the Docker host/network.
`localhost` inside a container refers to that container; on Docker Desktop use
`host.docker.internal` to reach a database running on the host machine.

Start with the remote override (Docker Compose 2.24.4 or later):

```sh
docker compose -f compose.yaml -f compose.remote.yaml up --build -d
docker compose -f compose.yaml -f compose.remote.yaml exec web python manage.py createsuperuser
```

Use both `-f` flags for subsequent commands in remote mode. The override disables
normal startup of the bundled `db` service and removes its migration dependency.
Web, worker, scheduler and migrations share the external database settings. Web, scheduler and worker
still wait for successful migrations; if the remote database is unavailable or
rejects credentials, fix connectivity and rerun the command. Do not enable the
`bundled-db` profile in remote mode.

Remote mode defaults to `verify-full`, which validates the database certificate
and hostname. `POSTGRES_SSLROOTCERT=system` uses the image's system trust store.
For a customer/private CA, set `POSTGRES_SSLROOTCERT=/certificates/postgres-ca.pem`
and add a third Compose file such as `compose.database-ca.yaml`:

```yaml
services:
  migrate:
    volumes:
      - ./certificates/postgres-ca.pem:/certificates/postgres-ca.pem:ro
  web:
    volumes:
      - ./certificates/postgres-ca.pem:/certificates/postgres-ca.pem:ro
  worker:
    volumes:
      - ./certificates/postgres-ca.pem:/certificates/postgres-ca.pem:ro
  scheduler:
    volumes:
      - ./certificates/postgres-ca.pem:/certificates/postgres-ca.pem:ro
```

Include `-f compose.database-ca.yaml` after the other two files. The CA file must
be readable by container user 10001. Other libpq SSL modes can be explicitly set
through `POSTGRES_SSLMODE` when required by the customer's database configuration.
See [PostgreSQL TLS configuration](https://www.postgresql.org/docs/17/libpq-ssl.html).
These database TLS settings are separate from each NSX Manager's TLS settings.

**Switching an existing installation:** stop its web, worker and scheduler services before
changing `.env`. Back up the old database. Changing the connection settings does
not move accounts, jobs or snapshots; restore a PostgreSQL dump into the new
server first if you want to keep that history. Preserve `DJANGO_SECRET_KEY`.
If switching from bundled to remote, stop the old `db` service as well; its volume
is retained. Do not use `down -v`. For remote databases, use the customer's backup
and restore process rather than `docker compose exec db pg_dump`.

To return to bundled PostgreSQL, stop the remote web/worker services using both
Compose files, restore the bundled database credentials in `.env`, remove the
remote host/SSL settings, and start with only `compose.yaml`. Existing bundled
volumes retain their original database/user/password; changing `.env` alone does
not update those credentials. Remote support does not provide automatic data
migration between databases.

## First audit

1. Add an environment with its name, stable ID and HTTPS manager origin.
2. Enter the NSX Manager username and password directly. Password input is masked.
   When editing, leave Password blank to keep the saved password, or enter a new
   password to replace it. Saved passwords are never displayed.
3. Run a testing sample to check connectivity, or run a full audit. Status updates
   automatically while you keep the dashboard or environment page open.
4. Open the saved snapshot. All existing report functions remain available:
   AND/OR and regex search, evidence search toggle, column filters, sorting,
   pagination, evidence dialogs, copying and CSV exports.

Use **Add testing data** on the Environments page to create a separate,
paused demo environment with synthetic groups, services and firewall rules. No
credentials or NSX connection are required. Demo snapshots are labeled as testing
data and excluded from operational history analysis. JSON import is no longer available.

Snapshot history is ordered by audit timestamp. Report pages read the saved PostgreSQL JSONB snapshot
and render it inside the workspace, sharing its navigation and page scroll.
There is no iframe or saved HTML dependency. HTML and JSON download routes have
been removed; table CSV exports remain available. The legacy HTML column is retained
for existing installations, but is never read by the viewer and remains empty for
new snapshots. All existing data and history are preserved.
The standalone CLI continues to support its own file reports independently.


## Import existing manager configuration

The original `managers.example.json` format is supported by a management command:

```sh
docker compose cp ../managers.example.json web:/tmp/managers.json
docker compose exec web python manage.py import_managers /tmp/managers.json
```

Use your actual managers file instead of the example. This command adds new
environments atomically and refuses to overwrite existing IDs. It does not
contact NSX or import passwords. Open each imported environment and enter its
username and password in the workspace. Existing environments are edited there.
A manager origin cannot change after snapshots exist; add a new environment for
a different manager. Configuration is frozen into each queued job.

## Users and access

The initial superuser manages users through Administration. Active users can read
all environments and snapshots in this shared workspace. Staff users
can also configure environments, import reports and request audits. Per-environment
tenant isolation is not implemented. Mutations require login, staff access and CSRF
validation. Logout uses POST. There is no public registration.

Audit responses and imports are rendered by the existing escaping renderer.
Integrated report pages use a content security policy. NSX passwords and Basic authorization tokens are redacted from collection
errors and saved audit values. Inventory reports themselves can contain sensitive
configuration, so keep database backups and CSV exports appropriately protected.

## Worker operation

One queued/running audit is permitted per environment, enforced in PostgreSQL.
Workers claim jobs using row locks with `skip_locked`. Each worker runs one audit
at a time; request concurrency adapts automatically within that audit.
Requests start at two in flight and may grow to eight when work is waiting.
HTTP throttling, server errors and network failures reduce concurrency immediately.
Successful requests use separate latency baselines for inventory, search,
membership, policy statistics and rule statistics; three consecutive responses
above twice the learned baseline (and at least two seconds above it) signal
sustained degradation. A slow successful statistics request alone does not reduce
concurrency. Recovery waits at least 30 seconds after a reduction.
Saved `performance.concurrency.endpoints` metrics include successful request counts,
cumulative request seconds (overlapping requests are summed), and latency baselines.
Bulk-counter fallback reasons are retained on the affected rules for troubleshooting.
When a policy statistics request fails, its individual rules still get fresh
counter checks. Subsequent collections for the same manager bypass that failing
bulk endpoint until a fixed 30-minute retry time, derived from the previous
snapshot. Skipped attempts do not extend the deadline. Bulk retrieval is tried
again after expiry. No old counters, membership results or classifications are
reused. Missing/unmapped counters in a successful bulk response do not trigger
this cooldown.
To process multiple environments concurrently:

```sh
docker compose up -d --scale worker=2
```

The default whole-audit timeout is 3,600 seconds (`NSX_AUDIT_TIMEOUT` in `.env`).
Each audit runs in a child process. Timeout or graceful shutdown kills that process
and marks the job failed. After a hard crash, a surviving/restarted worker marks
running jobs older than the timeout plus five minutes as failed. Failed jobs are
not rerun as the same job; automatic sync creates a new job at the next interval,
or you can start one manually. Earlier snapshots remain
available, and a late result cannot publish after a job has expired.

Successful full audits carry positive-hit history from the latest non-testing
snapshot through the original engine's manager/rule identity checks. Testing
snapshots never become the source of historical evidence.

```sh
docker compose ps
docker compose logs --tail=100 worker
docker compose logs --tail=100 web
docker compose restart worker
```

Older configurations using credential variable names and already queued jobs remain
compatible. Their optional `.nsx.env` file is still read by workers. Saving direct
credentials in Edit environment switches that environment to encrypted storage;
new installations do not need `.nsx.env`. After source changes, use
`docker compose up --build -d` to rebuild the image.

For a custom NSX certificate authority, use **CA certificate file** in Add/Edit
environment to upload a PEM certificate or bundle (up to 1 MB; `.pem`, `.crt` or
`.cer` containing PEM data). Files containing private keys or invalid certificate
data are rejected. The certificate and its filename are stored in PostgreSQL;
workers load the saved certificate directly into their TLS context without file
mounts. Each queued job retains the certificate selected when it was queued.

Leave the upload empty to keep the current certificate, upload a new file to
replace it, or select **Remove saved CA certificate** to return to the system
trust store. Legacy worker paths continue working until replaced or removed.
TLS verification is enabled by default; leave **Disable TLS certificate validation**
unchecked to use certificate verification. Database server CA configuration remains
separate from the NSX environment certificate.

## Persistent data and backups

The `postgres-data` volume holds accounts, configuration, jobs, audit JSON and uploaded CA certificates (plus legacy rendered HTML). A regular `docker compose down` keeps it. `down -v` deletes that volume.
Manager passwords and the copies in queued job configurations are encrypted in
PostgreSQL; they are not stored as plaintext. Preserve `DJANGO_SECRET_KEY` when
moving or restoring databases. Replacing that secret makes saved passwords
unreadable; re-enter the passwords in Edit environment if the old key is lost.

```sh
docker compose exec -T db pg_dump -U nsx -d nsx -Fc > nsx-workspace.dump
chmod 600 nsx-workspace.dump
```

For a planned restore into an empty database, stop web, worker and scheduler first, restore
with `pg_restore`, and run migrations before resuming them. Keep `.env` (including the credential encryption secret) separately; database
backups do not include it. Retain `.nsx.env` only if using legacy variable-based credentials. Automatic retention cleanup is disabled by default; administrators can enable it
under Administration → Data retention policies.

## HTTPS deployment

For access beyond this machine, place the web service behind your HTTPS reverse
proxy. Set `DJANGO_ALLOWED_HOSTS` to the actual hostname and
`DJANGO_CSRF_TRUSTED_ORIGINS` to its HTTPS origin. Enable `DJANGO_HTTPS=1`; set
`DJANGO_TRUST_PROXY=1` only when the proxy strips incoming forwarded headers and
sets `X-Forwarded-Proto` itself. The database has no published host port.
Apply your normal login rate limiting at the proxy.

## Collection progress

Collection history, active environment cards and the environment page show a live
progress bar and the current collection phase, refreshed every five seconds.
The seven phases cover inventory, references, firewall counters, group membership,
tags, report preparation and snapshot saving. Percentages describe completed
phases, not elapsed time; large inventories can spend longer in a single phase.
Only a successfully saved snapshot reaches 100%. Failed collections retain the
last phase reached and their error details.

## Tests

The application tests can run against the real PostgreSQL container; Django uses
a separate temporary test database:

```sh
docker compose exec web python manage.py test inventory
docker compose exec web python manage.py check
docker compose exec web python manage.py makemigrations --check --dry-run
python3 -m unittest discover -s .. -p test_nsx_inventory.py
```

Tests cover authorization, CSRF, duplicate jobs, demo data, database-backed report rendering,
manager identity, credential redaction, failed collections and worker recovery.
Collector tests remain offline. The collector suite's JavaScript tests require Node.

For local development without Docker, use Python 3.12 and install `requirements.lock`
in a virtual environment. Set a random `DJANGO_SECRET_KEY` and explicitly set
`NSX_SQLITE_PATH` to a local database file, run migrations, create a user, and run
`manage.py runserver` and `manage.py audit_worker` in separate terminals. Set
`DJANGO_DEBUG=1` for development asset serving. SQLite is a single-worker development
option; use PostgreSQL for concurrent workers. `requirements.txt` lists dependency
ranges and `requirements.lock` records the exact tested versions.

## Reference

- [Django 5.2 release notes](https://docs.djangoproject.com/en/5.2/releases/5.2/)
- [Docker Compose startup ordering](https://docs.docker.com/compose/how-tos/startup-order/)

## Administration and automatic sync

Open Administration to add/edit environments and set **Automatic sync** per manager.
The default is hourly, including existing environments after this upgrade. The
first scheduled run is one interval after saving or scheduler initialization.
Choose 15/30 minutes, hourly, 6/12 hours, daily, weekly, or Manual only. Pausing an
environment disables scheduling. Superusers also manage users and permission groups
from the same workspace design.

The Compose `scheduler` service checks persisted PostgreSQL schedules every 15
seconds independently of the collection workers. Concurrent schedulers serialize
on each environment. Queued/running jobs prevent overlap; missed intervals produce
at most one catch-up job. Failures retry at the next interval, retaining prior
snapshots. Collection history labels these jobs Automatic sync. Queue congestion
can delay collection beyond the displayed due time. Keep both scheduler and worker
running. Local development can run `python manage.py sync_scheduler` separately.
Remote database deployments also configure the scheduler's database and CA mount.

## Firewall activity history

Open an environment and select **Review rule history** for 7-, 30- or 90-day
views. A report also links to history ending at that particular snapshot. History
is computed from PostgreSQL DFW snapshot data; testing samples are excluded.
Rule names, paths and IDs are searchable, assessments are filterable, and tables
are paginated. Deleted rules are outside this view: it follows the latest full
snapshot's rules and stops each series at an identity/configuration change or
missing rule.

A consistently-zero review candidate needs a stable rule identity, a recorded
configuration fingerprint, at least two distinct valid counter observations, a
baseline at/before the window start, a check within 24 hours of its end, and no
observation gap above 24 hours, incomplete evidence, enforcement-scope changes,
possible resets or failed collections. The 24-hour threshold is a stated coverage
criterion, not a reconstruction of historical schedule settings. Counts include
one baseline where available. Re-imported observations at the same timestamp
count once; conflicting copies are incomplete evidence.

Older snapshots without fingerprints remain limited evidence. Future collections
record fingerprints of rule configuration and policy context. Referenced group
and service contents and effective rule ordering are not evaluated for historical
changes. Positive counters take precedence over zero hits; disabled rules are
separate. Saved counter snapshots cannot prove there was no traffic between
checks, especially around resets or caching. These results support review, not
automatic deletion.

## Automatic collection concurrency

Environment settings no longer expose a worker count. Full web collections start
with two concurrent NSX requests and can grow gradually to eight when requests
complete quickly and checks are waiting. Responses taking five seconds or more,
HTTP 429/502/503/504, and network failures reduce the limit. Growth resumes only
after 30 seconds of recovery and sufficient fast responses. Existing in-flight
requests finish when the limit is reduced; subsequent requests wait for capacity.
Retry backoff holds no request slot. Small inventories naturally use fewer slots.

Saved performance metadata records initial, peak and final limits and adjustments.
Testing collections still use one worker. Legacy environment worker values are
retained but ignored by web collections; standalone CLI `--workers` is unchanged.
This adjusts request concurrency per audit, not the number of Docker containers.

## Website preferences and history pagination

The Settings sidebar page stores each user's display density, history page size
(10/20/50/100), initial report table row count (25/50/100), default rule-history
period (7/30/90 days), and collection-progress refresh interval (5/10/30/60 seconds)
in PostgreSQL. Settings apply across devices and do not change other users' views
or the environment's automatic collection schedule.

Snapshot history is paginated (20 rows by default). Recent collections link to a
complete, separately paginated collection history. The report switcher lists 50
recent snapshots plus the selected snapshot if older; it is not a retention limit.
Changing display settings never removes historical data. Storage grows until an
administrator enables age-based retention; there is no fixed maximum stored count.

## Data retention

Superusers can open **Administration → Data retention policies** to configure
full snapshot, testing snapshot, and completed collection-history retention.
Cleanup is disabled by default. Suggested values are 180 days for full snapshots
and collections and 30 days for testing snapshots. Each category also supports
Keep forever. Preview changes shows eligible counts without saving or deleting;
Save policy persists the settings in PostgreSQL.

When enabled, the scheduler cleans up hourly, in batches of up to 1,000 snapshots
and 1,000 collection records per environment per run. Deletion is permanent.
The latest full and latest testing snapshot in each environment are always kept,
as are source collection records for retained snapshots. Environments with queued
or running collections are skipped. Snapshot expiry requires both the audit date
and the database save date to be older than the cutoff, so newly imported history
is not immediately removed. Completed jobs require both creation and completion
to be older than their cutoff; failed jobs can expire without a snapshot.

Full snapshots and collection history have a minimum retention of 91 days to
support the 90-day activity view and its baseline. Retention cannot create missing
observations or recover deleted evidence, and views of older snapshots may have
less historical coverage. Testing snapshots can use shorter retention periods.
A retained successful collection whose snapshot expired is labeled accordingly.
The settings page displays the last cleanup time and deletion counts. Database
backups remain separate from these policies.

## Personal usability settings

Settings now includes timezone and date format (UTC remains available on timestamp
hover), Light/Dark/Follow system themes, larger text, high contrast, reduced motion,
and a preferred landing page. Landing applies after login unless a specific page
was requested; Overview always remains accessible. Environment and firewall-activity
landing pages require a preferred environment.

Remember tables stores report search expressions, column filters, sorting, page
size, evidence-search preference, and column layout in PostgreSQL per account,
environment and report section. Use **Table columns** above a report table to hide
or reorder columns, or **Reset table preferences** to clear that table's choices.
At least one column stays visible. CSV export continues to include all columns.
Remember menus saves sidebar expansion state; the current page's ancestors open
automatically. Turning either preference off stops restoring and saving its state.
The most recently changed 150 table/menu records are retained per account.

## Freshness and in-app notifications

Superusers can configure **Administration → Freshness & notifications**. The
stale-data threshold defaults to 24 hours. Warnings on Overview and environment
pages use the audit timestamp of the most recent successful full collection;
imports and testing snapshots do not refresh it. Paused environments are labeled
separately, and environments with no full collection are identified explicitly.

The sidebar notification center shows failed collections, completed audits, and
new coverage issues, with each category controlled by the shared policy. It checks
once per minute and on opening, lists up to 50 matching results from the last 30
days, and stores each user's Mark all read timestamp in PostgreSQL. Notifications
link to the report or collection history. These are in-app messages, not email or
browser push notifications.

Coverage comparisons use unknown membership/statistics/tag evidence and collection
coverage errors in consecutive full collected snapshots. A legacy snapshot without
coverage keys establishes a baseline on the next collection; imported and testing
snapshots are excluded from new-coverage comparisons. Existing issues do not count
as new, though an issue that resolves and reappears does. Saved audit data and
retention policies are unchanged by display preferences.

## Application navigation and redesigned workspace

The primary sidebar stays consistent across pages: Overview, Environments,
Inventory, Firewall, Collections, and Administration (operators only). Help is
at the bottom. The top bar contains the environment switcher, notifications and
account menu; **Personal settings** is in the account menu. On phones, the sidebar
opens as a keyboard-accessible drawer, and snapshot selection moves into the page.

Inventory and Firewall use local page tabs, with finding views selected from a
View filter. Switching environments retains the current report section when a
snapshot is available and opens that environment's latest snapshot. Missing
snapshots have an explicit empty state. The snapshot heading distinguishes latest
from historical data. The Environments directory supports search, pagination and
table/card views; Collections provides a paginated workspace-wide history with
status filtering. Overview emphasizes freshness, failures, active jobs, and newly
observed coverage issues, with links to the relevant records.

Report tables use a compact toolbar: search, search options, columns, filters and
export. Sorting is available beside column headings; pagination and row count
are below the table. Object names open an evidence side panel with Summary,
References, Definition, Statistics and Advanced tabs as applicable. Raw JSON
remains copyable under Advanced. Mobile tables prioritize essential columns;
inspect an object for its remaining details. User-hidden columns and export data
remain governed by the existing saved table preferences and export rules.

Personal settings has focused Appearance, Dates & time, Tables & navigation and
Live updates pages. Each saves or resets only its own fields. Appearance changes
preview before saving; unsaved edits prompt before leaving. Administration uses
local tabs for environments, access, retention and collection policies. Remember
menus now preserves report search-option and column-popover expansion state;
primary navigation no longer requires nested menus. Existing snapshots, retention
settings, account permissions and collection behavior remain unchanged.
