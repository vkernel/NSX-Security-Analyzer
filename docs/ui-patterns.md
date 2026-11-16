# Shared interface patterns

The application uses shared page headers, status badges, action menus, table containers,
empty states and theme-aware surfaces across the main workspace pages. These patterns
also apply to collection, review, deletion, cleanup and settings workflows.

## Visual language (coss ui)

The workspace follows the look of [coss ui](https://coss.com/ui): neutral surfaces,
hairline borders with a soft bottom shadow, 10px controls and 14px framed cards.
- **Tokens:** colours, focus ring, radius and shadows are defined once in
  `static/design-system.css` for light, dark and system themes.
- **Component layer:** `static/coss.css` loads last and restyles the shared components
  without markup changes: light navigation rail, outline default buttons with a solid
  neutral primary, 36px controls (44px on phones), framed cards, quiet table headers,
  underline page tabs, segmented in-panel tabs, rounded drawers and `kbd` hints.
- **Fonts:** the stack starts with Inter and Geist Mono and falls back to the system UI
  font, because pages load fonts only from the application itself.
- **Colour use:** status colour is reserved for outcomes (allow, drop, warnings);
  primary actions are neutral, and destructive actions stay red.

## Workspace navigation

The sidebar is one grouped, server-rendered menu (`workspace_shell.relay_sections`,
`inventory/workspace_navigation.html`, `static/nav-shell.js`, `static/nav-shell.css`).
- **Groups:** Overview and Activity, then **Explore** (Environments, Inventory, Firewall,
  Segmentation, Naming compliance) and **Work** (Reviews, Collections, Administration).
  Every module lists its pages; only the current module starts expanded. Permission
  filtering stays on the server (viewers get no owner/second-approval queues or
  administration).
- **Collapsed rail:** *Collapse* narrows the sidebar to icons and is remembered with
  the existing `menu:workspace:section-panel` preference. Hovering, focusing or clicking a
  module icon opens its pages as a flyout; Escape closes it and returns focus.
- **Environment context** sits at the top of the sidebar; the top bar shows a breadcrumb
  (scope / module / page).
- **Search (Ctrl K / ⌘K):** pages, review queues, environments and actions are filtered
  in the browser. Object names come from `api/search/`, a prefix search over the latest
  browsable full snapshot per environment. It never contacts NSX. A result opens the
  matching report table with its search filled in (`?browse=1&find=<name>#<panel>`).
- Page-level tabs (review queues, segmentation views, settings) stay in the page.
- Phones use the same grouped menu as an off-canvas drawer.

## Actions and navigation

Use a descriptive page title, a short purpose and environment context where relevant.
Give the next workflow action priority; put supporting links in a secondary button or
More menu. Keep approval and execution separate. Existing server-side authorization,
review requirements and live verification remain authoritative.

Preserve the originating queue and filters in return links. Returning to the same list
also restores its reading position within the browser session. Escape closes action
menus and returns focus to their summary. Technical evidence stays in expandable
sections, with object names and outcomes visible outside those sections.

## Status and refresh

Use readable status badges with text as well as colour. Completed describes a finished
request, not necessarily a successful deletion; individual object results remain visible.
Show actual phase or operation counts when available, never simulated completion.

Collections and live task regions expose Refresh now and Pause updates. Deletion and
cleanup retain their own refresh controls. Pausing only affects browser updates.
Requests time out and report refresh failures without stopping background work.
Expanded evidence and draft values survive region refreshes; focused inputs and open
dialogs postpone replacement. Validation errors marked `data-preserve-error` remain
visible until explicit refresh. No refresh submits an approval or deletion form.

## Layout and accessibility

Shared styles support light/dark themes, display density, reduced motion, focus
indicators and narrow screens. Wide tables stay inside scroll regions, which become
keyboard-focusable when they overflow. Search and filter controls wrap on narrow
screens. Empty results explain what to do next.

Use `components/page_header.html`, `components/empty_state.html`, and the
`ui_components.status_badge` inclusion tag for new pages. Load the shared assets from
`base.html`; keep page-specific styles narrowly scoped. Test workflow permissions and
refresh state preservation when changing these components.


### Table counts and owner filtering

Paginated lists show the displayed range, matching result count, rows per page and previous/next links beneath the table, including single-page and empty results. Size changes preserve filters and reset pagination. Server lists remember the selected size for the browser session; existing preference sizes remain supported alongside 25, 50 and 100. Inventory tables retain their existing page-size preference. Unfiltered totals appear only where the data source provides them.

Small unpaginated tables show rows currently displayed, not an inferred database total. Bulk selection refers to the current page and remains subject to action limits.

The finding Owner filter uses the shared searchable reviewer selector, with Anyone, Assigned to me and Unassigned shortcuts. Changing the search text retains the previous filter until a result is chosen. Apply submits the selected filter.

## Reviews list and side panel

Reviews use one table styled as a two-line list (`.review-board`), with the
selected task's summary beside it. The server renders one `[data-review-detail]`
panel per row on the current page and shows the one named by `?selected=` (or
the first row). Row links work without scripts; `reviews.js` swaps panels in
place, keeps `?selected=` in the URL and restores the choice after live polling.
The panel only links to the review page: assignment, approval, rejection and
deletion preview stay there with their own authorization and revalidation.

## Inventory overview summary

`inventory/overview_summary.py` builds the overview's counts, evidence coverage
and change totals from saved projections only: individual `Snapshot.summary`
keys, the collection-time coverage index and an already calculated snapshot
comparison. It never reads report JSON or the record index on first load and
never starts a comparison. A missing coverage projection is shown as
"Not recorded", never as complete; snapshots collected before the VM count was
added to the summary show the VM count as not recorded. `application.js` places
the summary at the top of the report's overview and reuses the report's finding
categories as the "Needs attention" list.

## Groups and VMs side panel

On screens at least 1280px wide, the shared evidence drawer (`#detail-dialog`)
docks beside the Groups and VMs lists (`all-groups`, `empty-groups`,
`unused-groups`, `unknown-membership`, `all-vms`) instead of opening as a modal.
`inventory-dock.js` only changes how the drawer is shown: the evidence renderer,
its requests, tabs (Summary, Members, References, History, Technical details)
and export controls are unchanged. The row that opened the panel is marked with
`aria-current`; Escape, Close or leaving the list closes it and returns focus.
Narrow screens and other report pages keep the modal drawer. Report sub-views
are shown as chip links, and the sidebar maps a sub-view to its list page.

The docked panel has three widths: Docked (resizable with the left-edge handle
or the arrow keys, 360px to 70% of the window, remembered), Wide (about 62% of
the window, list still visible) and Full screen (Escape returns to the previous
width; a second Escape closes). Opening Firewall rules switches a docked panel
to Wide unless the user picked a width for that object; the choice is
remembered per browser. In the docked width, firewall references show one card
per rule (container query on the panel, labels from each cell's `data-label`);
wide and full screen keep the table with a sticky header and rule name. The
Category column is hidden visually where category heading rows exist. Coverage
notes collapse into "About this evidence", category tabs wrap beside Export,
and "Open in new tab" links to `?object=<path>&view=…&name=…`, which the
snapshot page resolves to the exact saved record.

## Firewall rules and policies in NSX order

The Rules and Policies pages open in an "NSX order" layout; "Sortable table"
switches back to the existing server table with search, filters, export and
bulk selection (the choice is remembered per browser). `rule-order.js` reads
`op=rule-order` and `op=policy-order` from the snapshot data endpoint
(`inventory/rule_order.py`, `inventory/policy_order.py`). Both read saved records
only and use the same `nsx_order_sql` ordering and `enrich` field values as the
firewall reference drawers: category (Ethernet, Emergency, Infrastructure,
Environment, Application), policy sequence, rule sequence, with heading rows and
totals over the filtered set. Policy records in the `dfw` view are excluded from
the rule list. A missing policy scope is "Not recorded"; an empty one is the
whole DFW. Selecting a policy shows its facts and rules beside the list; "Open in
Rules" filters the rule list to that policy. Rule and object names open the
shared evidence drawer.

## Collection page

`collections/<id>/` (`inventory/collection_detail.py`) shows one collection: the
seven collector phases as cards (done, running, waiting, stopped here, not run),
request pacing, coverage and the environment's ten most recent collections.
While a collection is queued or running, only the `#collection-status` region is
polled. Pacing figures (requests, retries, throttled attempts, average rate,
longest phases) come from the published snapshot's saved performance record;
from the saved collection configuration only the worker and concurrency limits
are read, because it also holds encrypted credentials. Coverage comes from the
coverage index and is "unknown, not complete" until a snapshot is published.
Stop and Diagnostics stay staff-only through the shared controls. The
collection tables link each row's request time to this page; the existing
`collection-result` redirect is unchanged.

A completed collection shows the result instead of the phase cards: duration,
requests (with throttled attempts), objects saved (from summary counts) and
changes against the previous snapshot (only from an already calculated
comparison, otherwise a link to compare), plus "What happens next": snapshot
publication and browsing status, findings evaluated from the snapshot, a
coverage note when the snapshot needs review, and the next scheduled collection.
Administrator diagnostics stay on the separate staff-only diagnostics page.

## Deletion request page

The deletion request page shows a stage title (Deletion preview, Cleanup
approvals, Confirm deletion, Deleting objects, Deletion result, or Deletion
request cancelled) and five step cards marked done, current or not started.
`deletion.detail` derives them from the same state the page already used for
its step markers; forms, permissions, deadlines, live revalidation and the
confirmation dialog are unchanged. A cancelled preview has no current step.

## Services and Tags & scopes overviews

The Services and Scopes pages open in an "Overview" layout (`catalog-views.js`);
"Sortable table" switches back to the server table and the choice is remembered.
`op=service-list` and `op=tag-scopes` (`inventory/catalog_views.py`) read saved
records only. Services show entries from the saved definition, Custom or
Predefined, saved rule references and observations: Not referenced (saved usage
candidate), Broad port range (no port or more than 1,000 ports), Nested service.
Built-in services are not assessed and show no rule count. Scopes list their
values with VM, group-definition and other counts; values that differ only in
case, values with group definitions but no VMs, and VM-only values are flagged.
Names open the shared evidence drawer.

## References page

Firewall › References is a section of the snapshot report (`#object-references`,
`object-references.js`), so the shared export control posts to the snapshot's
own `reference-exports/` endpoint with its owner, quota, expiry and export lane.
Pick Groups or Services, find an object (`op=reference-objects`, name or path),
and the page lists one row per rule and field where the object is named, using
`op=object-references` with `full=1` (up to 500 rules; the export includes every
page). Excluded fields read NOT, a policy Applied To is labelled as applying to
every rule in the policy, and nested references name the containing group.
Field chips filter the rows; other referencing objects are listed below. VM and
tag references stay in their evidence drawers.

## Segmentation and Naming

Segmentation shows application cards (groups and configured connections per
application) above the applications list and a sticky "Configured connections"
panel. A card selects that application in place with the same focus logic as
the map; pressing it again shows every connection. The map, VM-to-VM rules and
security matrix are unchanged. Naming compliance shows its outcomes as count
cards with a short explanation each; "Review needed" is drawn as a separate,
dashed queue because it may include compliant names.

## Account pages and sign-in

`get-started/` (`inventory/getting_started.py`) is a five-step checklist whose
states come from the workspace: an environment exists, a non-testing collection
succeeded, an application scope is set, a naming policy is enabled, a finding
has an owner. Actions only appear for users who may take them.
`notifications/` lists the same feed as the header menu (`notification_feed`)
with the active notification settings; "Mark all as read" posts to the shared
endpoint, which redirects for a plain form post and keeps its JSON contract for
the header menu. Both are linked from the account menu. Sign-in, directory
sign-in and password change use one centered card; providers, forms and
validation are unchanged.

## Rule proposal

A saved firewall rule proposal shows a "Proposed rule" field list (action,
source, destination, service, policy, placement, logging) beside a Notes card,
with the statement that the analyzer never creates or changes rules in NSX. The
similar-rules check, VM context and edit form are unchanged.

## Reference export choices

The export dialog lists the object's reference categories as checkboxes (the
open category is pre-selected) with a column preset per category: Essential,
All columns or Choose columns. The column lists come from the export endpoint
(`reference_export_columns.py`), never from the page. "Evidence basis" is
always included, so direct VM associations, interface attachments, IP/subnet
matches and saved configuration stay distinguishable in any narrowed export;
coverage, timestamps and notes are always kept. Context (environment, snapshot,
coverage) goes into a separate about file or sheet, or onto every row; raw JSON
evidence is opt-in. CSV is one file per category (a ZIP with `about.csv`, or a
single CSV when one category has context on every row); Excel is a workbook
with an About sheet and one sheet per category, written without extra
libraries; PDF and HTML use the chosen columns; JSON and YAML use schema
version 2 with the chosen columns. The dialog remembers choices per object type
in the browser. Requests without `sections` keep the original single-schema
output. Exports still read saved evidence only and keep ownership, quota,
expiry and the export lane.
