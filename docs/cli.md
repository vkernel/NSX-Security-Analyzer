# Command-line collector

Run these commands from the repository root. For the web application, see [installation](../webapp/README.md).

`nsx-inventory.py` uses Python 3.9+ with no third-party dependencies. It only
sends GET requests. Run against an NSX-T/NSX Local Manager with credentials
that can read all Policy configuration and inventory:

```sh
python3 nsx-inventory.py --manager nsx.example.com --username admin --json report.json
```

Each run requests fresh data from NSX. Persistent response caching and its CLI
options have been removed; existing cache directories are ignored.

## Multiple NSX Manager environments

Copy `managers.example.json` to `managers.json` and edit each manager's hostname,
display name, username and password environment variable names. Each entry represents
one Manager environment, including all its inventoried Policy domains. Keep its
`id` stable across runs: it determines the output directory and saved hit history.
IDs and manager origins must be unique. Password values are not accepted in this file.

```sh
python3 nsx-inventory.py --managers-file managers.json --output-dir nsx-reports
```

Each manager can use different credentials. Set the environment variables named
by `username_env` and `password_env` for automation. Unset usernames are prompted;
unset passwords are prompted without echo, before collection starts. If omitted,
the names default to `NSX_USERNAME_<ID>` and `NSX_PASSWORD_<ID>`, with the ID
uppercased and hyphens replaced by underscores. For example:

```sh
export NSX_USERNAME_EAST='east-auditor'
export NSX_USERNAME_WEST='west-auditor'
```

Set the corresponding password variables, or let the script prompt for passwords.
Use distinct environment variable names for distinct credentials. Existing files
using a literal `username` remain supported; do not combine it with `username_env`.
`--username` and `nsx_username` apply to single-manager mode; multi-manager mode
uses the per-manager settings. `NSX_MANAGER` is ignored in multi-manager mode.
The TLS, timeout and retry CLI settings apply to all configured managers.

Open `nsx-reports/index.html` to switch reports without a web server. The compact
Environment selector stays above the report; Collection results opens a dialog
with statuses and direct links. Open report opens the selected snapshot in a new
tab. The report fills the remaining window, and the controls adapt to narrow
screens. Displayed audit dates use UTC; the original timestamp remains available
on hover and in JSON. Output is:

```text
nsx-reports/
  index.html
  east/report.html
  east/report.json
  west/report.html
  west/report.json
```

The index loads only the selected report and provides direct links to open reports
in separate tabs. Each report remains standalone and includes an index link.
Share the whole directory to retain switching. Existing single-manager commands
and offline `--from-report` rendering remain available.

Collection runs up to two managers concurrently (`--manager-workers 1–4`). Each
manager independently uses `--workers` for membership/statistics checks; the maximum
concurrent request load is approximately their product. Existing bulk statistics,
pagination, retries and within-run inventory reuse apply independently to each
manager. No inventory or credentials are shared across managers. Use
`--manager-workers 1 --workers 1` for sequential collection.

The index updates as managers finish. A failed manager does not stop the others;
its saved HTML remains selectable with a failure label and saved audit timestamp.
Collection results shows the escaped, credential-redacted error and distinguishes
collection, report-generation and saving failures. A saved report is not a successful
refresh. If no HTML exists, the manager has no selectable report. A companion JSON
identifying a different manager prevents linking that saved report. Completed files
are replaced atomically individually. Each manager's
existing `report.json` supplies its own hit history, with the usual manager/rule
identity checks. Unreadable previous history is logged and skipped. Exit status is
1 if any collection failed, otherwise 2 if any report needs review, otherwise 0.
`--testing` uses one manager and one check worker at a time and defaults to the
separate `nsx-reports-testing` directory. All configured managers are still sampled.
Multi-manager mode uses `--output-dir` instead of `--html`, `--json` or
`--previous-report`, and cannot be combined with `--from-report` or `--manager`.

DFW counters are requested per policy, with individual rule requests as a fallback
when bulk counters are missing, invalid or cannot be mapped unambiguously. Empty
policies need no counter requests. Independent inventory lists, policy statistics,
individual rule fallbacks, tag/VM lists and group membership checks use bounded
workers (four by default), with separate HTTP openers per thread. Individual rule
fallbacks share a pool even when all rules belong to one policy. Complete
configuration lists are still read and reused within the run for reference analysis.

```sh
python3 nsx-inventory.py --manager nsx.example.com --insecure --workers 4
# Reduce load or troubleshoot sequentially
python3 nsx-inventory.py --manager nsx.example.com --workers 1 --retries 0
```

`--workers` accepts 1–16 (default 4). HTTP 429, 502, 503 and 504 responses are
retried with bounded exponential delays; `--retries` accepts 0–5 (default 2).
Authentication and query errors are not retried by this mechanism. Reports show
HTTP attempt/retry totals and elapsed time; JSON also includes phase timings.
Fallback rules include `statistics_fallback_reason` for diagnostics. Internal
realization IDs are not guessed to be Policy rule IDs. Incomplete nested bulk
responses still require individual rule checks.
Missing statistics remain unknown. Fresh requests may still receive counters
cached by NSX itself, and search indexing remains eventually consistent.

The password is prompted without echo. For automation, use `NSX_MANAGER`,
`nsx_username`, and `nsx_password` environment variables. `--username` overrides
`nsx_username`; if neither is set, the username defaults to `admin`. TLS certificates are
verified by default; use `--ca-bundle company-ca.pem` for a private CA, or
explicitly select `--insecure` to disable verification.

Every completed audit saves `nsx-inventory-report.html` and
`nsx-inventory-report.json` in the current directory
(overwriting the previous report). Open it in a browser for summary cards,
findings tables, expandable reference evidence, and the full audited inventory.
The sidebar has expandable Groups, Services, Distributed firewall, and Report
details categories. Distributed firewall contains Policies and Rules submenus.
Categories start collapsed and the active view's parent categories expand
automatically when following summary cards, links, or browser Back/Forward.
Use the category heading to expand/collapse it with a mouse or keyboard.
The section menu shows one view at a time; each populated table has a search box
and pagination (25, 50 or 100 rows). Summary cards open the corresponding view.
Browser Back/Forward preserves section navigation.
The HTML embeds compact JSON object records, rather than prebuilt row markup.
Records are reused across sections. The browser escapes values and creates row markup only
for the current page. No web server or additional data files are required;
the report can still be shared as one file and opened directly in a browser.
The accompanying JSON file contains the full audit data for other tools. The HTML
is self-contained and does not fetch this separate file.
Only the current page
of the active table is mounted in the browser; leaving a section releases its
table rows. Search is debounced and indexed lazily, and pagination reuses the
filtered results. This limits browser rendering work on large inventories.
JavaScript is required to display tables; JSON export remains available for
environments that block JavaScript.
The report is a standalone file with no external dependencies or credentials.
Reports with unknown
membership, unavailable DFW statistics or partial DFW inventory are also saved
and clearly marked as needing review.

Choose a different destination with `--html`; the JSON file uses the same folder
and filename stem by default. Use `--json` to override its location:

```sh
python3 nsx-inventory.py --manager nsx.example.com --insecure --html nsx-review.html --json nsx-review.json
```

Create the destination directory first if specifying a folder. Existing report
files at the selected paths are replaced. Failed audits do not refresh a previous
report, so check its manager and generated timestamp.

The console lists unused group candidates, empty groups, unused custom service
candidates, and groups whose membership could not be determined. JSON contains
all scanned groups/custom services, their paths, reference sources and notes.
An empty group can still be referenced by a rule.
Use `--show-references` to print the source paths that caused objects to be
classified as referenced. References from an object or its own children to
itself do not count as usage.
`GenericPolicyRealizedResource` search results are excluded from usage checks:
their links to configured objects describe realization, not configuration usage.
Groups flagged `is_default` or `_system_owned` are excluded from console and
JSON findings and membership checks. Their references to custom objects still
count toward usage; the report includes the number of excluded groups.
`DefaultMaliciousIpGroup` is also excluded by ID, the final path component, or
display name (case-insensitive, ignoring surrounding whitespace), even when
those flags are absent or false.

Unused candidates have no configuration path references found in the visible
Policy search index. The scan includes disabled rules and nested group/service
references. Built-in (`is_default`) and system-owned services are excluded from
custom-service findings. This is not a traffic/hit-count check. Search is
eventually consistent and does not guarantee visibility of non-indexed or
RBAC-hidden references; review candidates before cleanup.

Empty means all four resolved membership checks (IP addresses, VMs, logical
ports, logical switches) returned no members, with no unsupported expressions.
Explicit IP/MAC members count as nonempty. Failed requests, unsupported member
types, extended expressions, and unresolved path expressions result in unknown
membership unless positive membership evidence exists.

DFW checks list policies under each domain's `/security-policies` endpoint and
retrieve their complete `/rules` lists, including disabled rules. A policy is
empty only if that list is empty after excluding objects marked for deletion.
Failed policy/rule inventory requests appear under Coverage & definitions;
affected policy counts are unknown, not zero. All DFW policies/rules are included,
with system-owned entries labeled in the report.

Each DFW rule's `/statistics` endpoint is queried without an enforcement-point
filter. The parser supports flat counters and enforcement-point wrappers and
sums the returned hit counters. Enabled rules are listed as zero-hit candidates
only when every returned hit counter is explicitly zero and no packet, byte or
session counter indicates activity. Missing/invalid counters and failed requests
are unknown. Disabled rules have a separate section and retain any available
counter evidence. This uses current NSX counter snapshots: the observation start
and last reset time are unknown, and counters may be cached or reset. It does not
establish that a rule has never been used, or measure a chosen number of days.
The script never resets counters. JSON includes all policies, rules, returned
counter samples, check timestamps and DFW inventory errors under `dfw`.

Inventory scope is all `/infra/domains` groups, DFW policies/rules and `/infra/services` on the
specified Local Manager. NSX-V, legacy Manager API objects, Global Manager and
project inventories and gateway firewall rules are not covered. Core group,
service and search failures or incomplete pagination abort the report; DFW
inventory/statistics failures and unknown membership are preserved for review.

Exit codes: `0` completed, `1` failed, `2` membership/DFW checks need review,
`130` interrupted. Findings alone do not cause a nonzero exit code.

Offline checks from the repository root:

```sh
python3 -m unittest discover -s python -v
```

API references: [Policy search](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_QuerySearch.html)
and [group IP membership](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_GetGroupIPMembers.html).
DFW references: [rule statistics](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_GetRuleStatistics.html)
and [enforcement-point statistics](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/types_RuleStatisticsForEnforcementPoint.html).

Search compatibility: if `/search/query` rejects `resource_type:*` with HTTP 400
(as reported on NSX 4.2.4.0.0.25410638), the script retries a plain wildcard,
then an OR query containing explicit configuration types. Each retry starts at
page one. Authentication, authorization and incomplete-pagination errors are
not bypassed. If every variant is rejected, the audit fails with NSX's error
code/message rather than producing an empty inventory.

The explicit-type fallback covers groups/services, DFW and gateway policies,
rules, NAT rules, load-balancer pools/virtual servers, exclusion lists,
redirection and IDS policies/rules, endpoint policies/rules, IPFIX DFW profiles,
and flood/session-timer profile bindings. Other types are outside that fallback
scan. Coverage limits appear in the console and HTML Coverage & definitions;
JSON `search_coverage` records the successful query, types and rejected attempts.
This compatibility path is tested with simulated API responses; live validation
against the specific NSX build is still required.

### Group definitions and sorting

Click a sortable table header to sort ascending; click it again to reverse the
order. Arrows show the active direction, and the Sort by/Order controls stay in
sync. Header buttons also work with the keyboard. Sorting applies to all matching
rows before pagination. Evidence in group/service tables sorts by reference count;
free-form explanations and evidence in other tables have no header sort.

Group rows include membership methods (tag conditions, dynamic conditions,
segment/port paths, nested groups, explicit IP/MAC addresses or object IDs).
Expand **Membership definition** for criteria and the complete expression tree,
which preserves AND/OR logic. These are configured criteria, not resolved members.
Definitions are also included in the JSON report without extra API requests.

Antrea groups and groups containing container member types are included in
reference analysis and group findings. Membership is reported as unknown when
the available checks cannot establish membership for these types.

Use **Sort by** and **Order** above tables. Group/service tables support name,
path, membership status, membership method, reference count and usage. Numeric
counts sort numerically; names use natural ordering (group2 before group10).
Sorting applies to all search matches before pagination.

### Detail popups

Table cells use **View details** and **View evidence** buttons to keep rows
compact. A reusable dialog shows group definitions, references, DFW counters,
and notes. Sidebar categories still expand normally.
Close with **Close**, Escape, or a click outside the dialog; focus returns to the
trigger button.

### Minimal functionality test

```sh
python3 nsx-inventory.py --manager nsx.example.com --insecure --testing
```

`--testing` uses one worker, disables HTTP retries, and never follows pagination.
It samples the first domain, policy and rule, plus one eligible group and custom
service selected from at most 100 objects on each first page. Search processes
one result; compatible query alternatives may still be attempted. Rule statistics
are requested directly for the sampled rule, without a bulk-policy request. The
membership check makes up to four existence requests. A populated sample normally
uses 10–13 HTTP calls (up to two extra search-syntax attempts).

Empty lists or no eligible objects in the bounded sample can leave checks
unexercised. Sampled references never establish non-use; policy emptiness and rule
activity classifications are withheld. Statistics endpoints may return more data
than requested because they do not accept a page-size argument.

Defaults are `nsx-inventory-testing.html` and `nsx-inventory-testing.json`, keeping
normal audit reports separate. Explicit `--html`/`--json` paths override these.
Reports prominently indicate testing mode. Exit code 2 still means checks need
review, including intentionally withheld sample classifications; 1 is a fatal
failure. Run without `--testing` for the full audit.

### Firewall rule IDs

Firewall rule rows and group/service reference popups include
the numeric NSX **Rule ID** and the separate **Policy rule ID** (the object ID
in its Policy path). Both IDs are searchable; rule tables also support sorting
by ID. Missing numeric IDs display “Not returned”.

JSON rule records include `rule_id` and `policy_rule_id`. Group/service records
keep `referenced_by` as paths and add `reference_details` for firewall rule names
and IDs. `--show-references` prints these IDs too. The script uses fields already
retrieved from inventory and search, without extra API calls.

### Tags

The **Tags** sidebar section compares the NSX tag catalog with VM assignments
and all inventoried Local Manager groups (including system and container groups),
plus assignments on visible indexed Policy objects, including Firewall IPFIX
profiles, other profiles, services and policies. It offers **VMs and groups**,
**VM use**, **Group use**, **Other resource use**, and **Needs review**, plus
**All tags** and **Scopes**. Other resource use includes tags also used by VMs or
groups; categories describe observed use, not exclusive assignments.
Evidence popups list VM names/IDs, group paths, original conditions, tags attached
to groups, and other object names, types and paths. Tag values in different scopes remain distinct.

Group use counts both membership conditions and metadata tags attached to groups;
the popup separates them. Nested conditions are inspected regardless of AND/OR
logic: this measures configuration references, not resolved membership. Supported
conditions accept `tag`, `|tag`, and `scope|tag` value formats. They use `EQUALS`, `CONTAINS`, `STARTSWITH`, or `ENDSWITH` with equality on
scope, matching case-insensitively. Empty scope/value components are unrestricted.
Negative, regex, or otherwise unsupported conditions appear under Coverage & review
and prevent missing group usage from being classified only for potentially affected
tags. Known scope and tag constraints limit which tags need review; an unparseable
condition remains conservative. A positive group reference settles group usage
even if another condition is unsupported. Unmatched conditions
are also retained there; a pattern does not create a concrete tag.

There is no unused-tag category. Tags without visible assignments or group references
remain under Needs review: catalog/index timing or incomplete coverage can explain
the discrepancy. Other resource discovery follows Policy search coverage; the
explicit-type compatibility fallback cannot discover every profile type. A tag absent from the catalog, assignments,
and group conditions cannot be discovered. Incomplete/failed reads and testing
samples with missing usage are **Needs review**, never unused. VM and group
coverage are tracked separately. Catalog failures affect discovery coverage but
do not invalidate VM/group evidence already retrieved. Popups show per-tag
uncertainty reasons and relevant unsupported conditions. Catalog counts are
shown as additional evidence rather than treated as VM counts.
The **NSX catalog assignment count** reads `tagged_objects_count` from NSX,
with compatibility for `tagged_objects`, and is stored as `tagged_objects` in
the JSON report. Zero is displayed as zero; missing or invalid counts are
unavailable. This count covers tag assignments, not group condition references.

Two paginated GET lists are added: [tag catalog](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_ListAllTags.html)
and [VM inventory](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_ListAllVirtualMachines.html).
No per-VM or per-tag requests are made. `--testing` reads only the first record of
each new list. JSON includes the complete `tags` analysis and coverage issues.
[Condition semantics](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/types_Condition.html)
are evaluated conservatively; access and API support failures appear in the report.

### Report size and shared evidence

JSON is written without indentation and streamed to disk. The HTML remains
standalone and requires no downloads.

Tag analysis uses `tags.schema_version: 2`. Full group conditions are stored once
in `tags.conditions`. Each tag's `condition_evidence_set` and `review_condition_set`
index `tags.condition_sets`; each set contains indexes into `tags.conditions`.
`unsupported_conditions` and `unmatched_conditions` also contain condition indexes.
This preserves evidence while sharing identical condition lists across tags.
For example, resolve a tag's review evidence in Python with:

```python
conditions = report["tags"]["conditions"]
sets = report["tags"]["condition_sets"]
evidence = [conditions[i] for i in sets[tag["review_condition_set"]]]
```

Tag popups construct full evidence only when opened. Search still includes the
referenced conditions. Existing reports must be regenerated to benefit from these
changes; JSON consumers using the former inline evidence fields need to follow
the indexes above.

Unknown tag usage displays a specific reason (unsupported group condition, failed/invalid inventory, or testing sample). An unsupported condition is not reported as an incomplete group inventory.

Detail popups use compact, collapsible tag assignment tables with counts. JSON and text blocks have line numbers and copy buttons; JSON also has syntax highlighting. Long tables and code blocks scroll within the popup. Copy preserves the original text without line numbers; if browser clipboard access fails, select and copy the text manually.

### Diagnostic logging

By default, the script shows readable phase progress, warnings, and errors on stderr, followed by a compact findings summary and report paths on stdout. Per-object findings remain available in HTML and JSON. `--show-references` explicitly requests the detailed console report with reference evidence.

Use `--debug` for all diagnostic log entries: timestamped phase and per-object messages, request attempts and durations, retries, pagination counts, tag coverage diagnostics, and failure tracebacks. Debug also prints the detailed console findings report. The former `--verbose` / `-v` option has been removed.

```sh
python3 nsx-inventory.py --manager nsx.example.com
python3 nsx-inventory.py --manager nsx.example.com --debug 2> nsx-debug.log
```

Logs go to stderr; HTML and JSON report content is unchanged. Request headers, query parameter values, and successful response bodies are not logged. Passwords and Basic authentication tokens are redacted from diagnostic messages and tracebacks. Logs can still include inventory paths, filenames, and NSX error details, so review them before sharing. Debug mode does not add API requests or change audit classifications.

Tag evidence also lists visible firewall rule references through tag-using groups,
including nested groups and disabled rules. It shows rule names, numeric and
Policy IDs, and the tag-using group. Attached group tags are metadata, so such
references do not prove traffic matches the tag. Existing search coverage limits
apply. Rule records are shared under `tags.firewall_rules`; tag rows reference
these by index in `firewall_references`. No additional API calls are required.

### Positive hit-count history

DFW rule tables show the current count and **Last observed positive count** with
its UTC audit date and value. The latter is a cumulative counter snapshot, not
an exact last-hit timestamp or the number of hits on that date. The documented
Policy statistics API does not expose a last-hit timestamp or pre-reset count.

Each run reads the existing output JSON before replacing it and carries forward
positive rule observations, including when current counters are zero or unknown.
Keep using the same output path, or supply `--previous-report earlier-report.json`
when writing a new report filename. History is matched by manager, rule path and
available rule identity. Reports from a different manager or testing samples are
ignored. Invalid JSON stops the run to avoid overwriting unreadable history.

The first run cannot recover previously reset counters. Traffic and resets between
audits may be missed. A matching name/path cannot guarantee object continuity if
NSX omits unique identity fields. Keep report files for the history you need;
no NSX counters are reset or modified by this feature.

References: [RuleStatistics schema](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/types_RuleStatistics.html)
and [Broadcom statistics guidance](https://knowledge.broadcom.com/external/article/409357).

### Tag scopes

Open **Tags → Scopes** for the observed scopes, tag counts, and unique VM/group
counts per scope. **View tags** lists the tags and their usage in that scope.
Unscoped tags appear under **(empty scope)**. Search, header sorting and pagination
are available. Counts reflect visible evidence; tag coverage limitations apply.

### Full inventory and navigation

Use **Groups → All groups** and **Services → All services** to browse every
non-deleted object returned by those inventories, including default, system,
container groups and built-in services. Excluded objects show **Not assessed**
and their exclusion reasons in the evidence dialog. They do not enter cleanup
findings, and no additional membership requests are made for excluded objects.
Testing mode still shows only the retrieved sample.

The JSON report stores these lists under `inventory.groups` and
`inventory.services`; `objects` retains the eligible audit results. Existing JSON
reports must be regenerated to include previously excluded inventory.

Navigation groups inventory and findings by object type, with full lists first.
Help and coverage follow the inventory sections. The overview
provides direct inventory shortcuts, and tables support search, sortable headers,
pagination and evidence dialogs. The standalone interface adapts to smaller
screens and requires no external fonts or assets.

### Overview charts

The overview includes standalone SVG donut charts for group usage, service usage
and firewall rule activity. Legends show counts and percentages and link to the
relevant report pages. Group/service charts include excluded objects as **Not
assessed**. Disabled rules form a separate category regardless of counters, so
every object is counted once within its chart. Empty membership is kept in the
findings cards because it can overlap usage. Charts label testing samples and
empty inventories, and include text descriptions for screen readers.

### Offline report development

Regenerate the HTML with the current renderer without NSX
requests, a manager or credentials:

```sh
python3 nsx-inventory.py --from-report report.json
python3 nsx-inventory.py --from-report report.json --html preview.html
python3 nsx-inventory.py --from-report report.html
```

The default HTML destination is beside the input, with `.html` as its extension.
An HTML input requires the same-name companion JSON (`report.html` →
`report.json`); HTML alone does not retain the complete audit data. If JSON was
saved under a different name, pass that JSON path directly.

Source JSON is unchanged by default. To also export the report data:

```sh
python3 nsx-inventory.py --from-report report.json --html preview.html --json revised.json
```

Audit timestamps, counters, hit history and collected inventory remain as saved.
Obsolete naming analysis is omitted from new JSON exports. Previously excluded
groups require a fresh audit to collect membership and usage results; offline
regeneration does not reassess saved objects.

`--testing` and `--previous-report` cannot be combined with `--from-report`.
Exit codes retain the normal meaning: 0 complete, 2 checks need review, 1 failure.

Report quantities use comma thousands separators (for example, `1,234,567` hits),
including counters, summaries, charts and pagination. Rule IDs, paths and raw
JSON values retain their original format; table sorting still uses numeric values.

**Distributed firewall → Overview** summarizes policies, enabled/disabled rules,
current rule activity, empty policies, rule actions, policy categories and broad
ALLOW configuration indicators. Its segmentation indicator is the percentage of
enabled ALLOW rules with specific sources and destinations (neither contains ANY).
Missing endpoints stay in the denominator; testing samples and incomplete DFW
inventories withhold the percentage. This is a configuration proxy, not measured
workload microsegmentation coverage or effective isolation. Service indicators
do not evaluate inline service entries. The overview works with saved reports.

**Help & coverage → Report user guide** provides an embedded reader's guide to all
report sections, statuses, charts, search/sort controls, DFW indicators and counter history, tag evidence, popups, and
coverage limits. It includes examples and links to the relevant report pages
and is available offline in every newly generated HTML report.


### Column filters

Click a table column heading to filter it with **Contains** or **Does not contain**.
Text matching ignores case. All active column filters and table search must pass;
filters apply before sorting and pagination. Evidence columns include expandable
details. Use the separate header arrow to sort. Active filter chips can be removed
individually, or together with **Clear column filters**. Filters persist while
navigating the open report and reset on reload. This also applies to the firewall
category summary table. Quantities are matched as displayed, including commas.


Table search provides **Match → Contains / Does not contain** and
**Syntax → Plain text / Regex** next to the search field. Both modes ignore case;
plain text supports multiple conditions:

- `Condition 1 AND Condition 2` — both phrases anywhere in the searched row.
- `prod OR stage` — either condition.
- `prod AND web OR stage` — both prod and web, or stage (AND runs first).
- `"Sales and Marketing"` — a literal phrase containing an operator word.

AND/OR must be separate words and can use any letter case. Spaces within a
condition remain a literal phrase. **Does not contain** negates the whole
expression. The evidence toggle still controls which row data is searched.
Regex mode keeps regular-expression syntax, such as `prod|stage`.
An empty search imposes no restriction. Invalid expressions display an error
and withhold results and CSV export until corrected.
Table search combines with all active column filters before sorting and pagination.


The Report user guide includes a short starting checklist, links to audit coverage
and findings, topic search, and expandable explanations with Expand all / Collapse
all controls. Search opens matching topics and displays a clear no-results message.
The report uses shared typography, control spacing and responsive layouts for
consistent reading across inventory, firewall, tags and help pages.


Header filter dialogs include **Available values**, populated from the column
across all rows in that report table. Each value shows its row count. Typing in
**Text** narrows the suggestions; selecting a suggestion fills the field without
applying it until **Apply filter** is selected. Up to 100 suggestions are shown;
custom text remains supported even when no existing value matches.

Available column values and their counts reflect the current table search and
applied column filters across all matching rows, before pagination.

Turn off **Include membership and evidence** beside the table search to match
only each object’s own name and path. The default includes all row data. This
works with Regex and Contains / Does not contain; column filters remain active.
