# Production UX audit and proposal · 3 November 2026

> **Historical record.** This document describes the design or implementation at the time it was written. Current code, tests and the main documentation take precedence.

Status: historical audit/proposal. The approved changes are now [implemented locally](production-ux-implementation-2026-11-03.md), with verification and screenshots recorded separately. Baseline: development commit `6928572de9e38d757c09fe1ddc9de6eac8afb30e`.

The application needs a consistent interaction and page structure before another styling pass. Relay option 04 and Bridge 06 remain the chosen identity. The priorities are to make context switching understandable, bring useful content higher on the page, and stop using generic controls and evidence panels for unrelated tasks.

## Direction

- **Product:** Help administrators inspect NSX configuration, investigate relationships and coordinate reviewed changes.
- **Character:** Calm, exact, accountable.
- **World:** An operations desk with clear records, context and handovers.
- **Moments:** Application relationships and meaningful differences between current and proposed names.
- **Signature:** Environment and evidence time stay attached to every investigation or decision.

The user explicitly requested page reorganization and removal of noise. This proposal therefore includes changes to section order where they help a task, beyond Unslop's usual polish-only instruction to retain section order. No product pages have been changed as part of this audit.

## Scope and evidence

Reviewed the UI architecture across 103 templates, 14 application stylesheets, 33 application JavaScript files, route definitions, context/navigation helpers and the Python report generator. The CSS currently contains 69 `!important` declarations; this is a maintenance signal, not proof that each declaration is wrong. Twelve templates use the shared page-header include; other pages supply their own headers or generated report headings.

An isolated copy of the published image, with synthetic data and no collection workers, was checked in Chrome at 1600 × 1000 light, 1600 × 1000 dark and 390 × 844 light. The final sweep covered **98 page states × 3 layouts = 294 checks**, including report sections, naming, segmentation, findings, deletion, collections, settings, administration and empty states. No HTTP failures, captured JavaScript page errors or document-level horizontal overflow occurred. A collection-result route intentionally redirected to its environment when no report was available. Hash-based report sections were loaded as separate documents for these checks.

Targeted interactions also inspected the environment menu, environment navigation requests, snapshot details and the Appearance icon. The user's screenshots supply populated production examples that the small fixture does not reproduce, especially common naming issues and long inventory tables. Those observations are distinguished from browser measurements below.

This is a UI/code architecture audit, not a security certification, exhaustive screen-reader test or validation of live NSX changes. Passing route checks does not establish production usability.

- [Browser measurements](production-ux-audit-2026-11-03/browser-audit.json)
- [Interaction findings](production-ux-audit-2026-11-03/interaction-audit.json)
- [Interactive proposal](production-ux-proposal/index.html) — illustrative data, no application or NSX connection

Representative current-state captures: [snapshot overview](production-ux-audit-2026-11-03/snapshot-overview-light.png), [VM list](production-ux-audit-2026-11-03/snapshot-all-vms-light.png), [dark naming](production-ux-audit-2026-11-03/naming-compliance-dark.png), [VM investigation](production-ux-audit-2026-11-03/pair-populated-light.png), [mobile segmentation](production-ux-audit-2026-11-03/segmentation-mobile.png), [environment menu](production-ux-audit-2026-11-03/switcher-open.png). Counts in the synthetic fixture do not represent production totals.

## Highest-priority findings

| Priority | Finding and evidence | Proposed change |
| --- | --- | --- |
| P1 | The environment wrapper measures 65.98 px inside a 64 px header, with its top at −1 px. Snapshot controls have the same cramped label/control arrangement. | Replace form-like controls with 40 px context buttons, centered in the 64 px header. Put identifying text inline and keep the current selection readable. |
| P1 | The theme icon is explicitly a `<details>` summary. Clicking it opens a popup and leaves the theme unchanged. | A single button immediately switches the effective light/dark appearance. Keep System in Personal settings. |
| P1 | Environment navigation makes a preflight GET of the destination and then a document GET of the same destination. This was observed in the browser and is implemented in `switchers.js`. | Navigate once to a server-resolved destination. Use loading feedback and a useful destination error state. If a preflight is necessary, make it a lightweight context-resolution endpoint, not a full page render. |
| P1 | Snapshot “View details” opens Collection diagnostics in the generic object drawer. It displays Summary, References, History and Technical details even though several sections have nothing relevant to show. | A labeled Collection details action opens a metadata drawer designed for a collection. Object evidence remains a separate component. |
| P1 | The naming Common issues list is computed from all assessment rows before tab and search filtering. The user's Compliant screenshot therefore still leads with problems outside that view. | Show a compact issue summary only in Needs attention. Label its scope and use issue-specific actions. Keep environment-wide totals explicitly separate from filtered results. |
| P1 | Snapshot overview, VM and firewall headings begin around y=216 in the fixture, versus y=98 for the workspace overview: a repeated notice and breadcrumb push the actual task down. | One page header and one compact evidence-context row. Reserve banners for actionable warnings and historical/imported/sample state. |
| P2 | Three equally sized donut cards compete on snapshot overview, followed by many repeated metric cards and “View details” labels. | Use compact inventory totals, a dominant usage comparison and a ranked findings list with explicit destinations. Preserve assessment definitions and denominators. |
| P2 | Compliant naming rows repeat the current name as a proposal and show Unassigned/owner workflow even when no correction is needed. | Compliant rows emphasize the accepted name and policy. Correction ownership appears when there is actual work. Evidence stays available. |
| P2 | The naming empty state speculates that a new policy may have caused ambiguity, even when the current view simply has zero results. | State the known result: “No compliant groups match these filters.” Offer Clear filters and All groups; show a policy-conflict link only when conflicts exist. |
| P2 | VM names already open evidence, but every row also has a large repeated View details button; paths, raw power-state values and multiple header symbols crowd the table in the user's screenshot. | Make the name the main inspection action, use readable power labels, and move long IDs/paths into evidence. Keep one optional quiet Inspect action when needed for discoverability. |
| P2 | Evidence dates differ between the formatted snapshot header and plain Django datetime output on naming/segmentation. | One user-timezone/date-format component across pages and drawer content; exact UTC on demand. |
| P2 | The mobile application map can crop off nodes even when the document has no horizontal overflow. The graph, its controls and repeated context also take most of the first screen. | Default to a readable application/connection list on narrow screens; offer a focused map with visible pan/zoom/fit controls and an explicit view boundary. Do not treat hidden overflow as successful graph responsiveness. |
| P2 | Relay navigation clones links from page tabs after rendering, while report scripts also replace titles and move controls. Multiple scripts own parts of the same layout. | Server-render shared context, page heading and navigation from one route/state model. Keep a bounded adapter for retained reports. |

P1 means the first implementation phase; these are not claims of a security vulnerability.

## 1. Environment and snapshot context

Keep Relay's rail and section panel. Simplify the top bar to **Environment / Amsterdam ▾**, a compact **Latest snapshot · 03 Nov, 12:21** action when relevant, then appearance, Activity, notifications and the user menu.

**Environment picker:** a small searchable popover with environment names, a clear current selection and All environments only where aggregation makes sense. Recent environments may be shown after the basic behavior is consistent. Search must have an accessible label; Arrow keys, Escape and focus return must work. A single activation switches context; avoid a nested form appearance or a confirmation for ordinary browsing.

Preserve the equivalent module/report section when switching environments. Keep only filters that make sense in the destination; clear snapshot IDs, VM IDs and group selections that belong to the old environment. Explain cleared context briefly. Do not silently map a group path or selected VM into another environment.

**Snapshot picker:** open a right-side collection panel with timestamp, coverage, Latest/Historical/Imported/Sample status and the current selection. Show recent entries first, with a link to full collection history; search/paginate older entries rather than rendering an unlimited list. This panel answers “Which evidence am I looking at?” rather than behaving like a giant date dropdown. Include an Open latest action when a newer collection exists.

Browsing may switch environments. Findings, deletion requests, proposals and other records identify their owning environment and pinned collection; their context must not become an editable global selector. A new collection must not silently replace evidence in an active investigation or approval.

## 2. Direct appearance switching

Replace the top-bar Appearance menu with a real button whose accessible name describes its next action: “Switch to dark mode” or “Switch to light mode.” Use the existing line-icon style, a 40 px target and visible keyboard focus.

Resolve System using `matchMedia('(prefers-color-scheme: dark)')`, then switch to the opposite effective mode on the first click. Update instantly without navigation, preserve entered values, and save through the existing authenticated CSRF-protected theme endpoint. Keep the existing failure rollback, with an unobtrusive status message. Prevent overlapping saves from applying out of order. Personal settings retains Light, Dark and Follow system.

## 3. Snapshot overview and object lists

Recommended overview order:

1. Inventory overview title with Collection details as a quiet, clearly named header action.
2. Evidence context: environment, selected time, coverage and historical/sample/imported state when applicable.
3. Compact inventory totals linked to VMs, Groups, Services and Rules.
4. Usage comparison: horizontal stacked bars or ranked counts for groups/services; rule activity stays distinct because its evidence and denominator differ.
5. Findings requiring review, ranked by useful volume/severity, with labels such as Review unused groups or Review missing statistics.

Do not add finding totals together: unused and empty groups overlap. Preserve Unknown, Not assessed, Not supported and system/built-in exclusions as distinct categories. Zero hits do not establish historical non-use. Percentages must state their denominator. If a unique-object count is shown, use the actual deduplicated count rather than summing categories.

The Collection details drawer should contain Collection summary, Coverage, Run diagnostics and advanced identifiers/export links. It should not inherit object References/History tabs. Open live verification or deletion evidence through their existing task-specific flows.

For VMs/groups/services/rules, place a consistent search/filter toolbar directly above the table. Use one sort affordance per sortable column, plus clear active-filter indicators. Normal rows show the identifying name and useful operational facts; paths and long IDs are available in the evidence drawer and Copy action. Do not remove information necessary to distinguish identically named objects.

Use a document scroll and pagination by default. Retain horizontal scrolling for genuinely wide technical tables, with a cue. Use vertically scrolling tables only for a deliberate, resizable data-grid mode: avoid a small inner scroll area plus page scrolling. Preserve row selection scope, filtering, sorting, exports and counts.

## 4. Naming: replace Common issues with actionable causes

In **Needs attention**, replace the large generic disclosure with a compact “Fix recurring issues” section: issue, affected groups, relevant policy and one next action. Use a short row list rather than another grid of large metric cards. For example:

| Issue | Meaningful next action |
| --- | --- |
| No applicable policy | Review policy coverage; Create policy for administrators |
| Missing required tags/components | Review affected groups, then prepare component values or review the policy's requirements |
| Ambiguous policy | Resolve overlapping policy conditions |
| Invalid value | Inspect formatting/allowed values and the observed scope/value |
| Duplicate proposed name | Compare the colliding groups and the missing identity component |

Missing tags do not automatically mean the policy is wrong. The current blanket instruction “resolve policies before individual groups” is too broad. Pick the remedy from the cause. Read-only users can inspect affected groups but should not be offered inaccessible policy-edit actions.

Scope the issue list explicitly: “Across this environment” or “In these filtered results.” Do not switch between those meanings silently. In Compliant, show compliant groups immediately; keep overall issue counts in navigation or a quiet link to Needs attention. Selecting an issue applies a visible filter and has a clear removal action.

The attention table should focus on Group, Required correction, Outcome and Next action; current/proposed name differences belong together. Put technical paths, definition conditions, supporting evidence and proposal history in a contextual drawer. Compliant rows should not ask the user to assign work unnecessarily. Export/import remain available in a secondary menu; bulk actions appear only after selection.

## 5. Page organization across the application

| Page family | First useful content | Move/reduce | Important behavior to retain |
| --- | --- | --- | --- |
| Workspace overview | Collection exceptions and environment health | Compact zero-state metrics; qualification explanation in context; remove duplicate environment-directory links | Collection freshness, failed versus completed-with-warnings, aggregation scope |
| Environments and environment detail | Searchable list; selected environment health and latest collection | Separate setup/edit from browsing; short status strip instead of repeated notices | Credentials/trust previews, collection permissions, explicit collection actions |
| Snapshot inventory | Totals, usage, ranked findings | Generic diagnostics button, repeated notice, long guide text | Historical provenance, category definitions, unknown states |
| Inventory and firewall lists | Search, filters, readable rows | Repeated row buttons, IDs and multiple sort/filter symbols | Stable identity, server pagination, exports, evidence access |
| Naming compliance | Current queue and relevant groups | Global Common issues on Compliant; duplicate names; irrelevant owner fields | Draft/observed distinction, conflict/collision evidence, exceptions, stale proposals |
| Naming policies | Name/scope, active version and draft state | Disconnected step controls; move history/removal into secondary actions | Scope precedence, preview before activation, revision checks, audited removal/duplication |
| Applications map | Application scope/filter then relationship visualization | Configuration forms and repeated caveats around the graph | Configuration is not observed traffic; bounded map/connection evidence, pinned source |
| VM-to-VM rules | VM pair, service and investigation result | Repeated endpoint cards before any selected rule; three large stat cards; saved/export/history sections below result | Candidate versus unresolved distinction; ordered evidence; group membership qualification |
| Security matrix | Selected groups and matrix with legible legend | Unrelated settings and unselected placeholders | Selection limit, source/destination axes, ANY semantics and omitted endpoints |
| Firewall proposal | Source/destination groups, service and draft preview | Long side material; group evidence opens beside the selection | Local draft only; no deploy/write action; static/dynamic distinction; least-criteria dynamic ordering with evidence, not a “safest group” claim |
| Finding review / My work | Finding reason, readiness and the next eligible action | Repeated instruction paragraphs; raw evidence/history behind tabs | Approval eligibility, disabled-action reason, revision guards, independent reviewers, draft reason preservation |
| Deletion selection / plan / results | Stage, blockers, next actor and clear affected-object list | Repeated per-object prose; advanced configuration in evidence | Explicit destructive confirmation, exact approved plan, expiry, fresh final checks and partial results |
| Collections / Activity / diagnostics | Status, progress/outcome and related record | Full errors and worker internals behind diagnostics | Automatic status updates; retained filters/scroll; failures visibly actionable |
| Coverage / comparison | Changed or incomplete checks and impact | Long methodology before results; details grouped by cause | Incomplete is not empty; explain unavailable comparisons and source age |
| Administration / settings | Clear category, current value and local Save action | Reduce stacked cards, duplicate navigation and raw internals | Role gating, validation, trust/retention previews, independent preference saves |
| Sign-in / errors / empty states | What happened and one next action | Internal terminology, decorative panels, speculative explanations | Accessible forms, first-password protection, permission-aware recovery |

## 6. Shared component and code changes

Fix ownership rather than adding another global override stylesheet:

1. **Tokens:** retain existing Bridge light and Relay dark colors, with one spacing/type scale. Normal text 14–15 px, supporting text 12–13 px; reserve 11 px only for nonessential short metadata. Controls 40 px, mobile touch targets at least 44 px, page padding 24–32 px, table rows sized to actual content. Keep larger-text/high-contrast preferences.
2. **Shell:** render environment context, snapshot context, title and section navigation from explicit server context. The top bar is 64 px, with all normal controls contained inside it. Side-panel section names and counts remain legible; avoid headings wrapping just because of a redundant word.
3. **Page header:** adopt one component for title, optional description, evidence context and actions. Lists get search/filter controls; detail pages get state/next action. Limit each task area to one primary action.
4. **Lists:** share toolbar, selected-row actions, visible filter summary, row inspection and footer: “1–25 of 798 matching groups.” Separate filtered counts from environment totals; selection applies to the current page unless explicitly expanded.
5. **Overlays:** compact popovers for short choices; right drawers for evidence and contextual edits; centered dialogs for confirmations. Use one visible overlay at a time, consistent Close/Escape/backdrop handling, focus restoration and dirty-form protection. Collection metadata is not object evidence. Keep the current full-page escape hatch for complex record forms.
6. **Notices:** show persistent semantic warnings when incomplete/stale evidence affects a decision. Use compact source metadata and contextual help for routine explanation. Removing noise must not hide a blocker or imply a safe deletion/effective access verdict.
7. **Reports:** keep standalone report exports and retained report compatibility. Introduce explicit components for integrated browsing; avoid using the standalone report's entire page shell as the application UI. Retire duplicate CSS and DOM-moving adapters incrementally, with regression checks for historical snapshots.
8. **Loading:** retain the new projection/map caches, bounded searches and lazy connection/membership details. Use task-specific pending states with retry on failure; navigation should not wait for an avoidable full-page preflight. Automatic updates must not replace pinned evidence or unsaved inputs.

### Main implementation locations

| Concern | Current files |
| --- | --- |
| Header / appearance | [`base.html`](../../webapp/templates/base.html), [`relay.js`](../../webapp/static/relay.js), [`relay.css`](../../webapp/static/relay.css), theme-preferences view |
| Context and navigation | [`environment_scope.py`](../../webapp/inventory/templatetags/environment_scope.py), [`workspace_shell.py`](../../webapp/inventory/templatetags/workspace_shell.py), [`switchers.js`](../../webapp/static/switchers.js), [`application.js`](../../webapp/static/application.js), [`workspace.js`](../../webapp/static/workspace.js) |
| Snapshot/diagnostics | [`snapshot.html`](../../webapp/templates/inventory/snapshot.html), [`collector.py`](../../webapp/inventory/collector.py), [`report-workspace.css`](../../webapp/static/report-workspace.css), [`application.js`](../../webapp/static/application.js) |
| Naming issues / rows | [`naming_views.py`](../../webapp/inventory/naming_views.py), [`naming_compliance.html`](../../webapp/templates/inventory/naming_compliance.html), [`naming-compliance.js`](../../webapp/static/naming-compliance.js) |
| Shared page / overlays | [`page_header.html`](../../webapp/templates/components/page_header.html), [`workspace-panels.js`](../../webapp/static/workspace-panels.js), [`dialog-layout.js`](../../webapp/static/dialog-layout.js), [`interface-patterns.js`](../../webapp/static/interface-patterns.js) |
| Visual rules | [`design-system.css`](../../webapp/static/design-system.css), [`workspace.css`](../../webapp/static/workspace.css), [`workspace-components.css`](../../webapp/static/workspace-components.css), component stylesheets |

## Delivery order and acceptance

**Phase 1 — shared interactions and the reported problems:** direct theme button; environment navigation/context; snapshot selector and metadata drawer; common issue scope and actions; snapshot header/noise reduction. Demonstrate all four example pages in both themes before expanding.

**Phase 2 — information hierarchy:** inventory/firewall tables; naming list/policy editor; overview summaries; consistent toolbar/footer/empty states. Preserve established links and report sections through adapters.

**Phase 3 — complex workflows:** segmentation/map/matrix, VM investigations and proposals, finding reviews/deletion, administration and diagnostics. Apply shared patterns with task-specific evidence and actions.

Every phase needs a concrete acceptance record:

- Light and dark, System preference, 1440/1920 desktop, 390/320 mobile and enlarged text. No clipped labels, drawer controls or document-level horizontal overflow; wide tables may scroll intentionally.
- One click switches the effective theme, without a popup/reload or lost form values. Failed saves and session expiry give useful feedback.
- Context controls fit the header. Environment selection makes one normal navigation, preserves the equivalent module and never reuses another environment's object/snapshot IDs. Back/forward and direct historical links remain correct.
- Collection details has a truthful label and collection-specific sections. Opening/closing menus and drawers works with keyboard, Escape, focus return and unsaved changes.
- Common issues has explicit scope, useful affected-group links and correct role-aware next actions. Compliant has no correction panel above its rows. Filters/counts/exports agree.
- Snapshot totals and charts preserve excluded/unknown/not-assessed categories and explain overlap. Shorter copy never changes evidence meaning.
- Reviewer/operator/viewer states, empty results, loading, failures, stale evidence, large lists and multi-selection are included. Existing approval, cleanup and no-NSX-write protections remain authoritative.
- Re-run relevant Django/JavaScript checks and the page-family browser matrix. Automated “HTTP 200/no overflow” checks are necessary but must be accompanied by visual and interaction review.

No claim of production readiness should be made until these acceptance checks and user review of the actual workflows are complete. This audit produces the proposal and evidence only; it does not publish or deploy a redesign.

## Preview review

Open the [interactive proposal](production-ux-proposal/index.html) directly in a browser. The page chooser demonstrates Workspace, Snapshot overview, VM list and Naming. Try the moon/sun button, environment search, snapshot history panel, Collection details, and the Needs attention/Compliant queues. Issue links and text search filter the example rows. Other destinations explain the proposed workflow; they do not perform application actions or generate exports.

The standalone proposal passed JavaScript syntax checking and nine recorded layout states at 1600, 390 and 320 px, with no captured page errors or document-level horizontal overflow. Browser interactions checked immediate theme switching without a popup, hiding issues on Compliant, issue/search filtering, environment search and keyboard selection, snapshot selection and Escape dismissal. A real integrated implementation will also need server persistence, permission/CSRF checks, failure handling and historical-record regression tests; those are not established by this standalone preview.

[Light inventory example](production-ux-proposal/inventory-light.png) · [Dark compliant example](production-ux-proposal/naming-dark.png) · [Mobile VM list](production-ux-proposal/vms-mobile.png) · [Preview measurements](production-ux-proposal/preview-checks.json)

## Attribution

Audit and design method adapted from [Unslop by Damir Sokolovsky (@heydamir)](https://github.com/heydamir/unslop), local methodology revision recorded by this project as `a6f121eee5f29c2f74976b75456feae8b66afb3a`, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Applied locally to the current source and synthetic browser views; no external design service or separate Unslop agent was invoked. Existing credit is retained in `NOTICE`.
