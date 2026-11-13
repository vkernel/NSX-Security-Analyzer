# Contributing

Discuss substantial changes in an issue before opening a pull request. Contributions intentionally submitted for inclusion are provided under the
[Apache License 2.0](LICENSE), unless explicitly stated otherwise. Only submit work
you have the right to contribute.

For Claude Code, start with [CLAUDE.md](CLAUDE.md) and the
[project handover](docs/claude-code-handover.md). Keep machine-specific context in
ignored `CLAUDE.local.md`.

## Local development

Use Python 3.12 for the web application and Node.js for the collector's JavaScript
checks. From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r webapp/requirements.lock
export DJANGO_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export NSX_SQLITE_PATH="$PWD/webapp/development.sqlite3"
export DJANGO_DEBUG=1
python webapp/manage.py migrate
python webapp/manage.py runserver
```

A fresh database creates `admin` / `NSXSecurityA!` once. Change the password after
signing in. Existing administrators are preserved.

For collections, run `python webapp/manage.py audit_worker` in another terminal
with the same environment. Run `python webapp/manage.py sync_scheduler` for
automatic scheduling. SQLite is for local development with one worker; use the
Docker PostgreSQL setup to validate concurrent collection behavior.

## Validation

```sh
python3 -m unittest discover -s . -p test_collector.py
python webapp/manage.py test inventory
python webapp/manage.py check
python webapp/manage.py makemigrations --check --dry-run
```

Some PostgreSQL concurrency tests are skipped under SQLite, and membership, JSONB
and firewall-reference queries need PostgreSQL. The CI workflow runs against
PostgreSQL in an isolated test database. It does not connect to NSX.

### PostgreSQL test database

Run the Django suite against a disposable PostgreSQL container with synthetic data
only. Django creates and drops its own `test_` database:

```sh
docker run -d --name nsxa-test-pg -e POSTGRES_DB=nsx -e POSTGRES_USER=nsx \
  -e POSTGRES_PASSWORD=local-test-only -p 127.0.0.1:55432:5432 postgres:17-alpine
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 POSTGRES_DB=nsx \
  POSTGRES_USER=nsx POSTGRES_PASSWORD=local-test-only POSTGRES_SSLMODE=disable
unset NSX_SQLITE_PATH
python webapp/manage.py test inventory --noinput
docker rm -f -v nsxa-test-pg
```

Run only one test run at a time against the same server: parallel runs share the
`test_` database and fail with deadlocks or duplicate keys.
Use synthetic fixtures when testing; do not add real snapshots or credentials.

### JavaScript validation

The separate Node tests under `webapp/tests-js` also need `jsdom`. The last verified
setup used Node 24.2.0 and jsdom 29.1.1; that jsdom version requires Node 20.19+,
22.13+ within those major versions, or Node 24+. Install the test dependency outside
the checkout and run from the repository root:

```sh
npm install --prefix /tmp/nsxa-js-tests --no-save --no-package-lock jsdom@29.1.1
NODE_PATH=/tmp/nsxa-js-tests/node_modules node --test webapp/tests-js/*.test.cjs
```

Django tests do not automatically run this separate suite. Browser acceptance
also needs an isolated application, synthetic fixtures and a browser runner such
as Playwright; Node unit tests alone do not verify the complete UI flow.

For UI changes, check light/dark themes, narrow screens, keyboard navigation,
focus behavior and actual navigation to a different item. Confirm collection
and report controls still work after styling changes.

## Pull requests

Describe the problem, resulting behavior, and validation. Include migrations for
model changes. Preserve read-only NSX collection, unknown-state handling, credential
redaction, CSRF protection and access checks. Avoid broad unrelated formatting.
Use `requirements.lock` for reproducible installs and keep dependency ranges in
`requirements.txt` consistent when updating dependencies.

## Release identity

Increment `VERSION` in `webapp/inventory/version.py` and the Dockerfile's
`org.opencontainers.image.version` label for each release. Build from a clean,
committed checkout with `--build-arg APP_REVISION=<full Git commit>` and
`--build-arg APP_BUILD_DATE=<UTC ISO-8601 timestamp>`. Publish a matching immutable
version tag and update installation defaults. Do not reuse a version tag for
different application code. The default `source` build identifier is for local
development; published images must include the actual revision and date.
