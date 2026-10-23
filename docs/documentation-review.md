# Documentation update — 23 October 2026

Updated the user workflow, GUI Help & coverage, operations guide, security guidance,
application overview and changelog for category approval modes and confirmed NSX
deletion. Corrected manual-only completion language and documented deletion-record
retention, worker sharing, migration requirements and uncertain outcomes. The gallery
identifies screenshots that predate these features. The older review below is a
historical record, not a statement about current release pins.

# Documentation review — 13 October 2026

Reviewed all 22 tracked Markdown files present at the start of this task, including
GitHub issue/PR templates, against development commit `c90842f` and the corresponding
source, templates and deployment configuration. This review updates documentation
and in-app help; it does not change collection verification or publish a release.
Historical changelog entries describe their release-time behavior. Release examples
remain pinned to 0.5.2; development features are explicitly distinguished by build.

| File | Result |
| --- | --- |
| README.md | Added development/release distinction and current workflow/deployment links. |
| CHANGELOG.md | Added recent controls, independent lanes, search fallback and inventory verification; corrected development migration guidance. |
| CONTRIBUTING.md | Reviewed; development, validation and release identity instructions remain applicable. |
| SECURITY.md | Reviewed; read-only collection, shared visibility and credential boundaries remain applicable. |
| SUPPORT.md | Added counts, endpoint, job ID and actual worker build for inventory diagnostics. |
| roadmap.md | Marked authentication/audit features implemented; retained environment isolation and traffic features as future scope. |
| .github/ISSUE_TEMPLATE/bug_report.md | Added deployment mode, separate web/worker identity and inventory warning details. |
| .github/ISSUE_TEMPLATE/feature_request.md | Reviewed; no stale product-specific instructions. |
| .github/pull_request_template.md | Reviewed; validation and documentation requirements remain applicable. |
| deploy/kubernetes/README.md | Distinguished development images, corrected authentication navigation and added source build identity arguments. |
| webapp/README.md | Corrected global worker limits, statistics cooldowns, retention defaults and evidence tabs; distinguished development features. |
| docs/architecture.md | Documented three independent worker lanes and shared resource limits. |
| docs/collection-performance.md | Corrected legacy review terminology and the VM runtime exception in conflict guidance. |
| docs/docker-hub-overview.md | Distinguished development features and moved maintenance guidance out of the web container. |
| docs/docker-hub.md | Clarified refresh versus missing-index preparation and separate maintenance execution. |
| docs/finding-review-workflow.md | Updated Actions dialogs, contextual bulk release, selection limits, preserved approvals and global inventory warning guard. |
| docs/history-and-review.md | Replaced obsolete acknowledgement/due-date workflow, documented comparison shortcut and corrected upgrade ordering. |
| docs/keycloak.md | Updated finding assignment navigation to Actions. Authentication integration behavior reviewed against the existing implementation. |
| docs/ldaps.md | Reviewed; primary/secondary failover, trust and direct group mapping guidance remains applicable. |
| docs/operations.md | Replaced outdated mismatch-failure guidance with warning versus search-coverage failure behavior; added development rollout and queue troubleshooting. |
| docs/resource-sizing.md | Removed advice that extra replicas increase collection concurrency; documented combined three-lane memory requirements. |
| docs/snapshot-maintenance.md | Reviewed; separate optional maintenance lifecycle and no mutual exclusion with normal workers remain applicable. |

## In-app guidance

Help & coverage now explains the compact controls, release permissions and retained
approvals, snapshot selection, diagnostic collection, independent task queues and
inventory verification. Collection coverage includes an expandable explanation of
paused reviews and the `1237/1238` duplicate-row example. Indexed snapshots receive
current help without reindexing; historical evidence and warnings are not rewritten.

## Validation boundaries

This is a source/documentation consistency review. It does not certify NSX versions,
third-party administration console labels, live Kubernetes behavior or capacity
estimates. External authentication and infrastructure references remain supporting
links, not evidence of new live integration testing. Run the documented maintenance
or migration steps only for the selected deployment/build; UI documentation updates
alone do not require snapshot refresh or schema migrations.

Validation completed: 45 help, coverage, recalculation and inventory-verification
tests passed against isolated PostgreSQL. Local Markdown file links and
`git diff --check` passed. The initial SQLite run exposed an existing
PostgreSQL-specific comparison projection assertion; the PostgreSQL run passed
without changing application behavior to accommodate that test.
