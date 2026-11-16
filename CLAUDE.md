# NSX Security Analyzer project guidance

Read [the handover](docs/claude-code-handover.md) at the start of work, then the
relevant source and tests. Use [CONTRIBUTING.md](CONTRIBUTING.md) for setup and
[docs/architecture.md](docs/architecture.md) for the runtime layout.
User instructions take precedence over this guidance.

## Working practices

- Check `git status`, branch and the requested scope before editing. Preserve
  unrelated user changes. The current development branch is `development`.
- Use synthetic inventories and isolated test databases. The user has explicitly
  requested that diagnosis and regression tests work without production data.
- For review requests, identify causes and proposals before changing code. For
  implementation requests, complete the authorized work and relevant verification.
- Share concise progress, concrete results and limitations. Distinguish
  implemented, locally verified, published and deployed states.
- Publish or deploy only within the user's current authorization. A previous
  publishing request is not authorization to publish every later change.
- Keep local credentials, `.env` files, database dumps, browser session cookies,
  real inventory and private configuration out of Git, logs and Docker contexts.
  Do not print authentication configuration or copy it into handover material.
- Keep machine-specific notes in ignored `CLAUDE.local.md`. Recheck their paths
  and processes before use; temporary test tools can disappear.

## Application invariants

- Collection uses read-only NSX requests. Preserve the separate administrator
  approval and live-revalidation workflow for deletion. Never turn a review
  candidate into an automatic NSX change.
- Unknown, unavailable, partial and complete-empty evidence are different states.
  Retain partial positive observations and their coverage limitations.
- VM names, shared tags and shared IPs do not establish VM group membership.
  Keep direct VM associations, verified interface attachments and saved IP/subnet
  matches distinguishable, including in firewall references and exports.
- A current membership check does not repair historical snapshots. Current direct
  associations can be combined with separately timestamped saved attachments,
  IP matches and rule configuration only when the distinction remains visible.
- Avoid per-VM scans of every group or per-group redundant inventory requests.
  Reuse shared inventory, exact identities and indexed saved evidence. Preserve
  collection pacing, concurrency bounds, pagination and collection priority.
- References describe saved configuration, not effective policy or traffic.
  Excluded Source/Destination fields are negated references.
- Reference exports use local evidence and must never initiate an NSX refresh.
  Preserve requesting-user access, expiry, quotas and the independent export lane.
- Use shared drawer/table renderers and theme tokens. Check related-object
  navigation, long names, narrow layouts, light/dark themes and keyboard focus.
- Preserve CSRF, authorization, credential redaction and safe rendering/CSV cells.
- Include migrations for model changes. Keep historical migrations intact;
  diagnose failed concurrent index builds rather than recommending blind fakes.

## Validation and publication

Use Python 3.12 and the locked web dependencies. Use PostgreSQL for membership,
JSONB and concurrency tests; SQLite is only a limited local smoke-test option.
Follow [the validation commands](CONTRIBUTING.md#validation), including the separate
JavaScript suite after UI changes. Run focused regressions first and the full
relevant suites before publishing runtime changes.

When asked to publish development builds, commit the intended changes, verify the
remote branch, build with the full `APP_REVISION` and UTC `APP_BUILD_DATE`, check
both AMD64 and ARM64 images, and verify the registry manifest. Use a unique
`development-<short-commit>` tag before updating `development`; keep release tags
separate. Web, worker, scheduler and migration job must use the same image.
A documentation-only handover commit does not require a new runtime image.

Keep the handover's baseline and open checks current when significant work changes.
