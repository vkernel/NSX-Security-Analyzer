# Contributing

Discuss substantial changes in an issue before opening a pull request. Contributions intentionally submitted for inclusion are provided under the
[Apache License 2.0](LICENSE), unless explicitly stated otherwise. Only submit work
you have the right to contribute.

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
python webapp/manage.py createsuperuser
python webapp/manage.py runserver
```

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

Some PostgreSQL concurrency tests are skipped under SQLite. The CI workflow runs
against PostgreSQL in an isolated test database. It does not connect to NSX.
Use synthetic fixtures when testing; do not add real snapshots or credentials.

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
