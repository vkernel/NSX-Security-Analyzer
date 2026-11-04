# Workspace performance

Segmentation, VM-to-VM investigations and naming reviews reuse derived snapshot data. They do not contact NSX when opened. Read-only live membership checks still require an explicit user action.

## What changed

- A versioned `SnapshotAnalysisIndex` stores group evidence, projected rule configuration/fingerprints and service definitions in separate columns. New collections prepare it transactionally alongside the browsing index. Older ready snapshots prepare missing components on first use. Web workers share the stored result.
- `SnapshotApplicationMap` caches the assembled topology, coordinates, counts and display labels for the exact application scope/search/service/disabled/hidden-group selection. A cache hit reads compact map data without loading group/rule projections or rebuilding the graph. At most 12 recently created variants are retained per projection. New collections and the preparation command warm the default map for the configured scope.
- Connection summaries contain no inline rule evidence. Opening one fetches up to 25 rule links from the same snapshot and filters; successful results are reused while the page stays open. Failures offer retry. Changed source evidence or an incompatible map version asks the reader to reopen the map. Cache eviction can rebuild a matching view without changing the selected snapshot.
- Grouping settings and an unselected security matrix request groups only. Boundary comparisons run on the priorities view. URLs and review metadata are prepared for displayed rules, rather than every retained rule or group.
- The matrix initially renders at most 50 choices plus selected groups. Search returns at most 50 matches from the pinned snapshot. Selected groups survive subsequent searches; the matrix still accepts at most 12 groups.
- VM membership calculations are reused per snapshot and VM record. Rule matching builds membership lookup dictionaries once per VM, instead of once per rule. Membership drawers load 25 relationships at a time and retain the same explicit live-check controls.
- Naming assessment results are reused separately for observed results and draft simulations. Their signature includes the snapshot, algorithm version, policy configurations/revisions, scope bindings, proposal fields, coverage warning, and date. Policy previews bypass assessment reuse. Exceptions expire and stale proposals still require review.
- Finding, activity, comparison, finding-policy and deletion-plan status requests render only their status regions. My work status requests skip the finding-table query and readiness calculation. Drawer-close refreshes use the same fragment contract. Existing refresh intervals, selection preservation and error handling continue to apply.
- Group/rule projection queries have partial indexes on snapshot and ordinal. Migration 0063 creates these concurrently on PostgreSQL so normal collection inserts can continue.

## Cache lifecycle

The caches are disposable. Archiving/rebuilding the browsing index deletes them; restoring it prepares a fresh projection. Deleting the snapshot cascades to its derived rows, including assembled maps. A projection version change discards dependent maps/membership/naming caches. Saving changed record data also invalidates them; changes to unrelated index metadata do not. Grouping-scope changes select a different map-cache key.

Snapshot records are published immutably using `bulk_create`. Code that changes record configuration with raw SQL or `QuerySet.update` must call `inventory.analysis_index.invalidate(snapshot_id)` in the same transaction. The normal browsing-index rebuild already does this. Request-specific URLs and review models are never stored in the shared configuration projection.

## Applying the update

Run the normal deployment migration step (`python manage.py migrate`) before starting the updated web/worker processes. This adds migrations 0062–0064. Concurrent index creation in 0063 can take time on a large existing database. Migration 0064 adds the assembled-map cache table without scanning existing source records.

Optionally warm the latest full snapshot per environment after migration:

```sh
python manage.py prepare_analysis
```

To warm one retained ready snapshot instead:

```sh
python manage.py prepare_analysis --snapshot <snapshot-uuid>
```

These commands prepare derived data only. They do not rebuild the full report or make NSX requests. Archived/unfinished snapshots are skipped. New collections prepare the projection automatically.

### Recovering an interrupted migration 0063

An earlier version of 0063 could fail on retry with `relation "snapshot_group_projection" already exists` (or `snapshot_rule_projection`). Its concurrent index operations run outside a transaction, so an index can remain even when Django has not recorded the migration as applied. A cancelled concurrent build can also leave an invalid index.

Use the corrected development image for the migration job and all application components, then rerun the normal migration step inside the updated image:

```sh
python manage.py migrate --noinput
```

The corrected 0063 checks the existing index's table, columns, method and predicate. It reuses a matching valid index and concurrently rebuilds a matching invalid one. Snapshot rows are preserved. A conflicting relation definition stops the migration with an explanatory error and is left untouched. Run one deployment migration job at a time; a second migrator now stops with a clear error instead of waiting for the first migrator's advisory lock. If another index build is still active, let it finish before retrying. Existing databases that already recorded 0063 as applied need no additional migration.

Do not fake the migration or remove snapshot tables to bypass this error: an existing index may be invalid, or the other required index may still be missing.

### Diagnosing a migration that appears stuck

Three minutes of `Running migrations:` output alone does not identify the cause. Django writes its `Applying ...` status without a newline until the operation finishes; a log viewer may not display an unfinished line. Corrected 0063 logs a separate line when checking, reusing, removing or building each index, and another when a build finishes. Confirm the migration pod uses the corrected image revision, rather than relying on a cached mutable `development` tag.

Migration 0063 also emits a structured `progress` log every **15 seconds** while an index operation runs. Each line includes the index name, migration backend PID, operation stage, elapsed seconds, PostgreSQL index-build phase, tuple/block/locker counters, wait type/event (for example `IO` / `WalWrite`), and blocking session IDs with their state and transaction age. An `input_size` event reports estimated source rows and heap/total bytes using catalog estimates and relation sizes, without counting or reading snapshot rows. `started`, `finished` and `failed` events bracket each operation; database failures include SQLSTATE.

`phase_observed_seconds` measures time since the observer first saw the current stage/phase. `progress_changed: false` means its phase/counters were unchanged between samples; it is not a verdict that the build is stuck. Counters can be zero or unavailable in some phases, so logs do not invent an overall percentage or completion estimate. A `WalWrite` sample records the current wait, not the cumulative time spent waiting for storage.

The observer uses a separate autocommit, read-only connection, with configured connection/query/lock timeouts capped at 3/2/1 seconds. It cannot hold a long transaction snapshot or change migration connection timeouts. Observation errors produce `observer_unavailable` warnings and leave index work running. No SQL text, snapshot contents or credentials are logged. Normal diagnostics add one activity query per sample, an extra bounded blocker query when needed, and one source-size lookup per index operation. PostgreSQL permissions may limit visible activity; logs expose `activity_visible` and report unavailable diagnostics explicitly.

Follow these logs with:

```sh
kubectl -n nsx-security-analyzer logs -f job/migrate --timestamps
```

The logging update leaves the **900-second Kubernetes deadline unchanged**. Logged elapsed time is per index operation; the Job deadline applies to the entire Job. If Kubernetes terminates the pod, it may not emit a final application `failed` event; retain the last progress lines and the Job's `DeadlineExceeded` status. The existing retry recovery still handles matching invalid indexes left by interrupted builds. Recreate a failed migration Job through the normal deployment process using the updated image, after checking that the previous pod has stopped and that no earlier migration backend is still building an index. A running earlier backend must be investigated before retrying.

Concurrent index creation scans the source table and can wait for older transactions. A running Kubernetes pod only confirms that its process is alive. Inspect these read-only queries in the application's PostgreSQL database (use its existing database login):

```sql
SELECT pid, phase, current_locker_pid, blocks_done, blocks_total
FROM pg_stat_progress_create_index
WHERE datname = current_database();

SELECT pid, state, wait_event_type, wait_event,
       clock_timestamp() - query_start AS query_age,
       clock_timestamp() - xact_start AS transaction_age,
       pg_blocking_pids(pid) AS blocked_by
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND state <> 'idle';
```

Repeat after a short interval to distinguish advancing build work from a wait. `waiting for writers ...` or `waiting for old snapshots` identifies a transaction wait; `current_locker_pid` identifies the session being waited on. An `advisory` wait can indicate another migrator using an earlier image. Investigate the identified session before changing application replicas or cancelling anything. Empty results do not prove the migration finished: also inspect pod logs, restarts, CPU/memory and the actual image revision.

PostgreSQL documents [index-build progress phases](https://www.postgresql.org/docs/17/progress-reporting.html#CREATE-INDEX-PROGRESS-REPORTING) and [concurrent index-build waits](https://www.postgresql.org/docs/17/sql-createindex.html#SQL-CREATEINDEX-CONCURRENTLY). The bundled Kubernetes migration Job has a 900-second deadline; assess the observed build progress and transaction waits before increasing that deployment-specific limit. Repeatedly restarting a healthy large build can prevent it from finishing.

## Measuring a deployed slowdown

Response `Server-Timing` headers expose total, SQL execution, analysis and template-render durations in milliseconds. Structured request logs also include `database_queries`, `response_bytes`, `database_seconds`, `analysis_seconds` and `render_seconds`. Analysis/render timings apply to the instrumented workspaces; other routes may report zero for those stages. SQL timings can overlap analysis/render, so these values should not be added together. Streamed responses omit the response-size measurement.

`Snapshot analysis projection` logs include a cache-hit flag, selected components and load/build duration. `Assembled application map` records map-cache hits and load/build durations. Timings record no SQL text or query parameters. Use these measurements and PostgreSQL query plans to distinguish database delays, computation, rendering and browser/network delays before changing worker counts or timeouts.

## Verification

The initial synthetic comparison used 5,200 groups and 3,240 simple firewall rules on disposable SQLite data. Approximate repeat-request results:

| View | Before | After | HTML before → after |
| --- | ---: | ---: | ---: |
| Application map | 0.33 s | 0.11 s | 184 → 184 KiB |
| Grouping settings | 0.15 s | 0.05 s | 20 → 20 KiB |
| Matrix picker | 0.22 s | 0.05 s | 1,025 → 27 KiB |
| VM-to-VM rules | 0.51 s | 0.09 s | 740 → 53 KiB |
| Naming compliance | 0.18 s | 0.09 s | 47 → 47 KiB |

After assembled-map caching and lazy connection details, a further run of the same fixture returned the map in approximately **0.013 seconds** on repeat requests, with **144 KiB** of HTML. Its first visit, using an already-prepared source projection, took about 0.106 seconds. These timings include server-side work only; Chrome separately verified that no connection-evidence request occurs until a connection opens.

These are development measurements, not a production latency promise. The naming fixture has no applicable policy; regression tests separately exercise active policies, proposal changes, simulations and exception expiry. First-use preparation adds work for existing snapshots; the warming command avoids that work on the first user visit.

PostgreSQL 17 migration and query-plan checks used separate disposable data. The group query selected `snapshot_group_projection`; for the single-snapshot rule fixture PostgreSQL preferred a sequential scan. Production plans depend on snapshot count, row sizes and statistics. Normal cache hits avoid the source-record scans entirely.

The migration-retry, duplicate-session and progress-logging fixes were verified on PostgreSQL 17 with all 799 Django tests, including 12 recovery/observation tests. These exercise a fresh install, an unrecorded partial migration, a real cancelled concurrent build leaving an invalid index, conflicting definitions, retained snapshot data, repeated rollback/retry, and a duplicate migrator stopping without waiting or disturbing the session holding the lock. Observer checks cover repeated samples from a real writer-blocked build, blocker IDs, cancellation SQLSTATE, observer session cleanup, read-only/autocommit/timeouts, observation failure isolation and SQL-plan generation without a monitor. Fresh SQLite migrations and both backends' model checks also pass.

Chrome checks cover remote group search, retained selections, matrix generation, both appearance modes, paginated membership drawers and automatic fragment refresh. Additional map checks cover no eager connection requests, fetching on expansion, local reuse on reopening, pinned service filters and stale-evidence recovery.
