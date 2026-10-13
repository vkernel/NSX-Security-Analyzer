# Support

Use [GitHub issues](https://github.com/vkernel/NSX-Security-Analyzer/issues) for bugs
and feature requests. Check the installation guide and existing issues first.
No commercial support or response-time commitment is implied.

For a bug, include the commit, browser, deployment mode, relevant NSX version,
steps to reproduce, expected behavior and a sanitized error summary. Expand the
collection error to view technical details. Remove hostnames, account names,
addresses, tokens and configuration details that should remain private.

Do not attach `.env`, `.admin-credentials`, raw production snapshots or database
backups. Security vulnerabilities belong in the private reporting process described
in [SECURITY.md](SECURITY.md).

For inventory-verification warnings, include the affected endpoint, reported and
unique counts, duplicate count, attempt number and job ID. Include the actual
worker image/build as well as the web build; they can differ after an incomplete
rollout. Sanitize object paths before sharing. See
[inventory troubleshooting](docs/operations.md#inventory-changes-during-collection).
