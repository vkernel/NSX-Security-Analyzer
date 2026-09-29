# Collection performance

Development builds from 0.2.2-dev retain fresh inventory and counter checks while reducing transport and fallback overhead.

- HTTPS connections are pooled per collection (up to 16 connections); the existing adaptive limiter still caps concurrent requests at eight. Uploaded CA certificates and hostname validation remain supported. Redirects are rejected, and the pool closes when collection finishes. System HTTPS proxy settings and proxy bypass rules are honored.
- Optional policy statistics use an eight-second connect/read timeout, or the environment timeout if shorter. Each bulk request has one attempt. Individual rule requests retain the environment timeout and retry policy.
- Failed policy endpoints use a cooldown saved in PostgreSQL snapshot data. Transient failures start at one hour; HTTP 400/404/405/501 start at six hours. Repeated failures double the delay up to 24 hours. After expiry, collection probes the endpoint again. A successful complete response clears its failure history. Manager mismatches and testing snapshots are ignored. These are endpoint failure records, never cached counters.
- Rule fallback requests become eligible as soon as their policy request finishes; they do not wait for all bulk requests. Both use one bounded executor and the existing shared adaptive request limiter.
- Incomplete, ambiguous, or malformed bulk evidence still falls back to individual rules. Search scope, pagination checks, membership checks and enforcement-point coverage are unchanged.

## Diagnostics

New snapshots save DFW strategy counts in `dfw.collection_diagnostics`: policies evaluated, fully successful bulk policies, policies skipped during cooldown, fallback rules and categorized reasons (counted per rule). Empty policies are excluded.

`performance.concurrency.endpoints` includes successful and failed attempts, failure counts by HTTP status, total request time, maximum latency, and p95 latency from the most recent 512 attempts per endpoint family. Timing excludes waiting for the adaptive limiter but includes transport time. Request durations can overlap; their sum is not collection elapsed time. Existing phase timings remain the wall-clock reference.

Cooldown history comes from the latest successful non-testing snapshot. If collection fails before saving a snapshot, new cooldown information from that run is not persisted. Retention or deleting snapshots may also remove failure history; the next collection then probes normally.

## Validation

Compare several collections of equivalent inventories using total elapsed time, DFW phase time, bulk success/skip counts, fallback counts and unknown statistics. A faster run is not an improvement if coverage deteriorates. Offline regression tests check fallback equivalence, early fallback scheduling, increasing cooldown and recovery, bounded diagnostics, redirects, uploaded-CA validation and actual TLS connection reuse.

References: [NSX policy statistics](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_GetSecurityPolicyStatistics.html), [urllib3 connection pooling and TLS](https://urllib3.readthedocs.io/en/stable/advanced-usage.html).

## Unsupported statistics and timeout recovery

Ethernet policy rules are retained in inventory with **Not supported** activity and no hit count. Their statistics endpoints are not queried. An explicit NSX HTTP 400 / error 500209 response is also recognized as unsupported. This does not establish zero traffic. Unsupported rules remain in inventory totals, are shown separately from unknown statistics, and cannot qualify as historical zero-activity evidence.

After the first statistics pass completes, transport timeouts get one recovery pass, one rule at a time, following a two-second delay. HTTP errors and malformed results do not trigger this recovery. The recovery uses the existing request timeout and HTTP retry policy. Initial timeout notes and timestamps are retained in `statistics_retry`; a failed recovery stays unknown. Diagnostics count timeout retries, recoveries, and unsupported rules. A collection can take longer when recovering missing evidence.

Statistics tasks are capped at two concurrent workers in addition to the shared adaptive request limiter. Inventory concurrency is unchanged. Sequential recovery runs after that pool completes. Compare one versus two using equivalent read-only rule requests before raising this cap; a short comparison does not establish capacity for every manager.

## Overlapping phases and optional probes

Search and DFW collection run concurrently after initial inventory, through the same adaptive request limiter. Testing and single-worker calls stay sequential. Search completeness failures still discard the audit; no partial snapshot is accepted. Individual `search` and `dfw` phase times overlap and must not be added together. `search_and_dfw` records their combined wall time.

Only one optional policy-statistics task is scheduled at a time. Remaining statistics capacity can serve rule fallbacks. A bulk read timeout is recorded and enters cooldown without reducing the manager-wide concurrency limit. Rule timeouts, connection failures, and explicit HTTP backpressure (including 429/503) still reduce the limit. No NSX-side configuration or service restart is performed.
