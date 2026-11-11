# Exporting inventory references

Open an inventory object's drawer, choose **References**, then **Export references**.
The shared dialog is available for VMs, groups, custom services, tags and scopes,
including objects opened through a related-object link.

1. Choose CSV, PDF, JSON, YAML or HTML. CSV is selected initially.
2. Export the selected reference category, or all reference categories.
3. Keep **Saved snapshot**, or, for VMs, select **Cached current direct
   associations + saved links**. This option uses observations already stored
   locally. It never starts an NSX request. If no observation is cached, the
   application explains that a separate current check is required.
4. Choose **Prepare export**, then download when ready. Closing the dialog leaves
   preparation running. Reopen it and expand **Recent exports from this
   snapshot** to download a ready file or check queued work. **Cancel export**
   prevents unfinished work from publishing a file.

Exports include every retained reference page in the selection, regardless of
which table page is visible. A category with no retained rows still has an
explicit coverage entry. Exporting does not repair missing collection evidence.

## Evidence and formats

Every file identifies the environment, object, snapshot UUID, collection time,
request time and preparation time. Categories retain coverage status, notes and
observation timestamps. Current direct observations are frozen when requested;
attachments, IP/subnet matches and firewall configuration remain timestamped
saved evidence. Stale current observations stay labelled stale. Tag relationships
are not presented as confirmed VM membership. Excluded firewall fields remain
labelled as negated references, and IP matches are not an effective-policy or
traffic verdict.

- **CSV:** one reference per row, repeated provenance/coverage columns, firewall
  fields and a structured-reference JSON column. Empty categories have a coverage
  row. UTF-8 with BOM; formula-like cells are neutralized for spreadsheet use.
- **JSON / YAML:** the versioned canonical document, with full retained
  relationship evidence beyond drawer display limits. Existing collection or
  IP-index coverage limits remain visible; exports do not invent omitted data.
- **PDF:** landscape A3, repeated table headers, wrapped paths, embedded fonts,
  coverage notes and numbered pages. Characters outside the embedded font are
  written explicitly as Unicode code points. Other formats retain Unicode text.
- **HTML:** standalone searchable tables, expandable structured evidence and print
  styles. No external assets or network requests. Inventory text is escaped;
  the embedded search script has a fixed Content Security Policy hash.

## Operations

Migration `0069_reference_exports` creates a new job/artifact table. Deploy the
same application version to web and workers, install the locked dependencies,
and run migrations. A new collection is unnecessary for exporting existing data.

The normal `audit_worker` supervisor starts an independent `exports` lane. A
manual installation using explicit lanes must also run:

```sh
python manage.py audit_worker --lane exports
```

Compose/Kubernetes worker health checks include its heartbeat. The lane does not
claim collection jobs. It prepares one export at a time, with a five-minute
process deadline, local database reads and 500-rule batches. Collection API calls
and collection scheduling are unchanged. Exports still consume database and CPU
resources; running them separately does not eliminate shared-host resource use.

Downloads are restricted to the requesting user, use attachment disposition and
expire after 24 hours. Compressed artifacts are stored in PostgreSQL so web and
workers do not need a new shared volume. Expired artifacts and frozen observations
are cleared during queue/API cleanup; expired job metadata is removed after seven
days. Snapshot/user deletion cascades to their exports.

Limits: 100,000 reference rows, 64 MiB uncompressed, 16 MiB compressed per file,
64 MiB retained compressed artifacts per user, three unfinished jobs per user,
20 exports per 24 hours per user and 100 unfinished jobs globally. Exceeding a
limit fails explicitly and offers a narrower export; a partial file is never
silently returned. Failed/interrupted work can be requested again. Export requests
and downloads are recorded in the application audit log without artifact content.

## Verification

Synthetic end-to-end checks exported 1,001 firewall references through the browser
in all five formats, verified the final page's rule, exercised VM/group controls
and mobile sizing, and opened the HTML without network access. Preparation and
download caused zero current-membership/NSX requests. Local observed times were
approximately 2.4–4.4 seconds across JSON, YAML, HTML and PDF, including queue
polling; these are test fixture measurements, not production runtime predictions. PDF pages were rendered
and inspected for path wrapping, repeated headers and numbered footers. Regression
checks also cover owner-only access, CSRF, expiry, cancellation, limits, frozen
current observations and dense provenance beyond drawer display caps.

The [synthetic acceptance record](performance/reference-exports-2026-11-11.json)
contains the browser measurements. Keyboard checks cover focus wrapping,
Escape/focus return, dark theme and compact/mobile sizing. The full 984-test
Django suite and 110-test JavaScript suite passed; targeted export/worker tests
were rerun after the final rendering, metadata and cancellation adjustments.
