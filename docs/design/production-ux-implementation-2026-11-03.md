# Production UX implementation · 3 November 2026

Status at validation: implementation and follow-up code changes complete, before publication. Publishing this source does not deploy it to an operational environment. [Completed follow-up and synthetic-inventory acceptance](production-ux-acceptance-2026-11-03.md). This delivers the implementation phases in the [approved audit and proposal](production-ux-audit-2026-11-03.md). Relay option 04 navigation, Bridge 06 light and Relay dark remain the product identity.

## Implemented changes

| Area | Result |
| --- | --- |
| Environment context | Compact searchable picker, selected state, Arrow keys, Escape and focus return. One document navigation replaces the full-page preflight. Browsing targets the destination environment's latest saved collection directly; environment-specific VM/group/snapshot/policy/owner selections are cleared with an explanation. Empty environments retain a useful destination and equivalent report section. A bounded, session-scoped Recently visited group provides shortcuts without duplicate choices. Record contexts remain fixed. |
| Snapshot context | Server-rendered metadata with Latest/Historical and source time. A searchable history drawer shows up to 50 recent collections plus the selected older snapshot, coverage, sample/imported state, selection and full history. Report section and browsing scope survive a switch. Findings, proposals and reviews retain their pinned evidence. |
| Appearance | One click switches the effective light/dark mode immediately, including when the preference is System. The authenticated CSRF-protected save is serialized; failed saves restore the previous preference and show a status message. Forms retain entered values. Follow system remains in Personal settings. |
| Collection details | A named header action opens a compact collection-specific reading drawer with source, coverage, advanced identifiers, diagnostics and scope. Generic object References/History tabs no longer appear for collection metadata. Escape returns focus; coverage navigation closes the drawer. |
| Snapshot overview | The title and useful data move above repeated explanation. Compact linked totals precede horizontal usage comparisons and a ranked findings list. Zero-count categories live in a secondary disclosure. Unknown, not assessed, excluded and activity categories remain distinct; overlap and the unique-object total remain explicit. |
| Inventory / firewall lists | Readable power-state labels, name-based inspection and a quiet Inspect action replace repeated large buttons. Long VM identities are retained in evidence and shown in the list when duplicate visible names need disambiguation. Header symbols are reduced. Normal paginated tables no longer impose an inner vertical scroll region. Counts distinguish matching results from the total before filtering. Exports and stored values retain their original semantics. |
| Naming compliance | Fix recurring issues appears only in Needs attention and is calculated from the filtered rows. Each cause shows affected policies with per-policy group counts and links to the filtered queue; overlap is explained for ambiguous matches. Compliant rows emphasize the accepted name and policy, without duplicate proposed names or irrelevant correction ownership. Empty states state what is known and provide filter recovery. Import/export is secondary; selection and correction actions remain contextual and role-aware. |
| Naming policies | Shared page structure, explicit draft/source state and ordered steps remain. Removal and version history move into secondary policy actions. Existing duplication, preview, activation, revision and audit safeguards are preserved. |
| Applications / matrix | Shared context and section navigation replace duplicated selectors/navigation. Filters are compact and visible. Mobile starts with readable applications/connections and an explicit Show map action; zoom, fit, scroll and drag support map exploration. Group limits, source/destination axes, evidence and configuration-versus-traffic qualifications remain. |
| VM investigations / rule proposals | The VM pair, service and result lead. Compact evidence counts replace three large cards. Endpoint/flow cards appear when a rule is selected. Saved proposals and sharing remain secondary. Group membership, static/dynamic evidence, unresolved candidates and local-draft-only restrictions remain authoritative. |
| Workspace / environments / coverage / comparison | Shared headers, compact operational status and server navigation establish the same hierarchy. Finding methodology follows health and recent collection results. Coverage and comparison retain incomplete/unknown qualifications and contextual evidence. |
| Findings / deletion / collections / activity | Canonical server-rendered navigation and shared typography, spacing, tables, controls and drawers apply to these page families. Existing stage, blocker, reviewer and next-action patterns are retained. No approval, exact-plan, expiry, destructive-confirmation, cleanup or execution guards were weakened. |
| Administration / personal settings | Canonical navigation replaces client-side cloning. Shared forms and typography apply throughout. System health separates worker identity, database tables and optional storage calculations into diagnostics disclosures. Existing validation, retention/trust previews and preference saves remain. Sign-in and recovery continue using the established Relay identity and shared controls. |

## Code ownership and compatibility

Shared page navigation is rendered from route/context and canonical template includes. The broad navigation MutationObserver and copied normal-page tabs are removed. Retained hash-based reports use a bounded adapter and an explicit navigation-ready event; their first-load navigation and section changes are covered by regression and browser checks.

Environment destinations use a metadata subquery rather than downloading a report. Snapshot history is bounded and reads metadata only. Existing projection caches, lazy map connections/membership details, bounded search, pinned evidence and loading/retry behavior remain in use. Integrated report comparisons adapt existing report values; standalone report rendering and exports remain supported.

The existing stylesheets own the changes; no additional global override stylesheet was introduced. Normal body/list text is 14px, supporting text 12–13px, desktop controls 40px and mobile controls at least 44px where applicable. Larger-text, high-contrast and reduced-motion preferences remain available. Native selectors/history links provide fallback where enhancements cannot run.

No model or migration changes are required by this implementation. No live NSX configuration, approval records or production databases were changed. Synthetic browser checks saved only disposable test-account preferences.

## Verification

| Check | Result |
| --- | --- |
| Full Django suite on isolated PostgreSQL | 787 tests passed |
| JavaScript interaction/regression suite | 56 tests passed |
| Collector / standalone report suite | 112 tests completed; 9 optional integrations skipped |
| Django configuration, migrations, templates | No configuration issues, no pending model changes; 109 templates compiled |
| Local production Docker build | Passed, including compressed/manifest static asset collection |
| Page-family browser sweep | 93 route states × light/dark/390px mobile = 279 checks; no HTTP errors, captured page errors or document-level horizontal overflow; report section navigation populated |
| Additional layout checks | 36 checks at 1440/1920 desktop in both themes, and 320px mobile in both themes with larger text/high contrast; no overflow or clipped tested header controls |
| Focused browser workflows | 14 passed: direct theme persistence/rollback/form preservation, first-load report navigation, collection drawer/focus, historical selection, one-request environment switching, naming issue filters/compliant rows/selection, VM inspection, deferred pair flow, pinned proposals, System setting, viewer/operator permissions, mobile map controls and keyboard navigation |
| Source hygiene | JavaScript syntax and `git diff --check` passed |

[Page measurements](production-ux-implementation-2026-11-03/browser-audit.json) · [Layout measurements](production-ux-implementation-2026-11-03/layout-checks.json) · [Workflow checks](production-ux-implementation-2026-11-03/interaction-checks.json)

The preview used a new offline synthetic database, test accounts and a loopback-only web server, with no collection workers or NSX calls. The initial page sweep used small fixtures; follow-up checks also used 10,839 synthetic groups, 4,075 VMs, 1,528 services and 3,239 rules. Neither fixture represents actual production data. Browser checks include empty, historical, populated naming, VM/rule and role-specific states. The tests protect large-list bounds, caches and workflow guards; this is not a production-scale browser benchmark or exhaustive screen-reader certification. The user selected synthetic inventories as the acceptance basis; the completed follow-up checks satisfy that scope. No live-data acceptance is claimed.

## Visual review

- [Snapshot overview, light](production-ux-implementation-2026-11-03/snapshot-overview-light.png)
- [VM inventory, light](production-ux-implementation-2026-11-03/snapshot-all-vms-light.png)
- [Naming issues, dark](production-ux-implementation-2026-11-03/naming-compliance-dark.png)
- [Compliant naming, dark](production-ux-implementation-2026-11-03/naming-compliant-dark.png)
- [Mobile segmentation](production-ux-implementation-2026-11-03/segmentation-mobile.png)
- [VM pair result](production-ux-implementation-2026-11-03/pair-populated-light.png)
- [Collection details](production-ux-implementation-2026-11-03/collection-details-light.png)
- [Snapshot history](production-ux-implementation-2026-11-03/snapshot-history-light.png)

The original audit/prototype remain historical design evidence; these captures show the integrated application.

## Navigation and inventory follow-up · 4 November 2026

Applied the same Unslop methodology to the user's inventory screenshot, shared shell, report renderer, retained-report adapter, table preferences and representative page families. This follow-up preserves Relay 04, Bridge 06 light and Relay dark. Publishing the source and development image does not roll out these changes to an operational deployment.

### Findings and corrections

| Finding | Correction |
| --- | --- |
| Inventory review paragraphs preceded the object name and enlarged every row. | Put identity first and exact qualification/history under Usage or Status. The retained-report adapter handles older saved snapshots; the renderer emits the new structure for future reports. Eligibility remains secondary text rather than another large badge. |
| Retained report theme selectors included an ID inside `:is()`, unintentionally overpowering shared component styles. | Lower scope specificity for toolbar and evidence-button paint. Apply shared theme surfaces, typography, padding, borders, status colors and density. |
| Checkboxes appeared last and service Membership repeated “Not applicable.” | Make selection first by default and service Membership optional through Columns. Original filter/sort indices remain stable; custom saved layouts are preserved. Untouched old default layouts adopt the new defaults. |
| Long work queues clipped inside the section panel. | Style canonical navigation as the same vertical list as report navigation; use Second approval and Ready for cleanup. Keep counts readable and routes/permissions unchanged. |
| Abbreviated primary labels concealed the page names. | Show Environments, Naming compliance, Segmentation, Collections and Administration. Widen the rail enough to display full words; Naming compliance wraps naturally. |
| The section menu consumed space permanently. | Add Hide/Show section menu beside environment context. Save the state when Remember menus is enabled. Keep the primary rail available; mobile navigation remains a complete drawer with keyboard focus handling. |
| Laptop context controls and inventory columns became crowded. | Give snapshot context a separate row at 801–1200px. Let search use a full toolbar row. Keep object and usage visible while secondary Type and duplicate Inspect columns yield; clicking the name still opens evidence. Remove old positional column-width assumptions after reordering. |

Within Inventory, Overview replaces Snapshot overview; the view picker uses Unused services while the full page title remains Unused custom services. Within Segmentation, Applications, Priorities and Settings replace longer redundant labels. These are presentation changes, with no changes to assessment, review or cleanup rules.

### Follow-up validation

- 802 Django tests passed against a disposable PostgreSQL database; system checks and migration checks passed.
- 63 JavaScript tests passed, including collapse persistence, preference failure handling, mobile restoration, retained-row inspection and saved column-layout compatibility.
- Collector suite: 112 tests completed, with 9 optional integrations skipped.
- 18 representative routes × light/dark × 320/390/1024/1680px = 144 browser states. All returned successfully, with no captured page errors, document overflow, sidebar overflow or clipped sidebar links. Laptop context/action rectangles were also checked separately for overlap.
- Browser workflows verified saved collapse after reload, mobile submenu access, inspection, selection, search and direct theme switching without a popup. The populated synthetic service row measured 93px high at both 1024px and 1680px after correction.
- JavaScript syntax and `git diff --check` passed.

[Layout measurements](navigation-inventory-review-2026-11-04/layout-checks.json) · [Workflow measurements](navigation-inventory-review-2026-11-04/interaction-checks.json)

The offline fixture includes populated groups, custom services and firewall data; VM and some work queues exercise empty states. This pass is a representative page-family review, not every populated workflow, role combination, accessibility certification or production performance benchmark. No live NSX or production database was accessed.

### Further suggestions

1. Consolidate the retained-report and native-list column definitions into one shared table model. This pass fixes the visible drift; reducing duplicate rendering and CSS ownership would prevent future drift.
2. Keep navigation labels short within their section, with fuller workflow descriptions in the page heading or introduction. Use the same terms in help and future menus; do not abbreviate product areas to fit the rail.
3. If more evidence categories are added, keep the list focused on identity, outcome and next action, with detailed qualification available in inspection. Preserve visible unknown/incomplete states so a quieter list does not imply certainty.

### Review captures

- [Inventory, light](navigation-inventory-review-2026-11-04/after-inventory-light-1680.png) · [Inventory, dark](navigation-inventory-review-2026-11-04/after-inventory-dark-1680.png)
- [Laptop inventory](navigation-inventory-review-2026-11-04/after-inventory-light-1024.png) · [My work navigation](navigation-inventory-review-2026-11-04/after-work-light-1680.png)
- [Collapsed submenu](navigation-inventory-review-2026-11-04/after-collapsed.png) · [Mobile navigation](navigation-inventory-review-2026-11-04/after-mobile-menu.png)

## Naming count and static-example correction · 4 November 2026

The reported screenshot's global totals reconcile (4,811 compliant + 501 needing attention = 5,312), but its issue panel shows only a subset of attention outcomes. Code review also confirmed that sidebar counts ignored policy/search filters, queue links dropped those filters, and each policy's impact summary counted the whole environment. These views could therefore present different scopes without saying so. No production-data classification has been inferred for the 18 attention groups outside the displayed issue categories.

Queue counts and destinations now share the selected filters and matching criteria. Every attention outcome is included in the issue breakdown, including name differences, incomplete definitions/coverage and changed reviews. Per-issue policy links retain the same scope; ambiguous-policy attribution remains explicitly overlapping. A policy preview shows that draft policy's matching groups, compliant/attention/exception counts, with environment totals and transitions in separate disclosures. Collision/conflict analysis still evaluates the full environment.

The draft example previously ignored reviewed local component proposals. It now uses the same baseline-checked component helper as assessment. Draft, rejected, stale and other-environment proposals stay excluded. Missing static values have actionable scope-specific guidance, and obsolete inline feedback clears when the sample changes. Custom scope names alone still do not provide values or extract an IP address.

Validation: full Django suite on disposable PostgreSQL passed 809 tests; after the final conflict-link correction, all 66 focused naming/UX tests passed on PostgreSQL. All 64 JavaScript tests passed. Synthetic browser verification used five groups across two policies: environment 3 compliant/2 attention, host policy 2 compliant/1 attention, with policy filtering retained across queues. The reviewed live example agrees with assessment, and missing-value guidance names the required scope. No page errors were captured. No live NSX or operational database was accessed; these corrections are included with the automatic host naming feature below. Publishing source and images does not roll out an operational deployment.

[Browser checks](navigation-inventory-review-2026-11-04/naming-browser-checks.json) · [Per-policy counts](navigation-inventory-review-2026-11-04/fixed-policy-counts.png) · [Draft policy preview](navigation-inventory-review-2026-11-04/fixed-preview-counts.png) · [Reviewed static example](navigation-inventory-review-2026-11-04/fixed-reviewed-example.png) · [Missing-value guidance](navigation-inventory-review-2026-11-04/fixed-missing-value-help.png)

## Automatic static-host naming · 4 November 2026

Added a Static policy match condition, Exactly one explicit host IP, and an Explicit single host IP component. The component reads the saved membership definition directly, without manual scope proposals or NSX requests. It accepts one bare IPv4/IPv6 address or a host /32 or /128 and emits its canonical address. It rejects ranges, broader CIDRs, duplicate/multiple entries, multiple expressions, nested/dynamic/extended membership, malformed values and scoped IPv6 literals. The editor sets Required, Keep case, IP address format and an adequate length; value mappings cannot replace the actual address.

The condition takes precedence over general static fallbacks. Equally specific host or exact-category policies remain ambiguous rather than silently overriding each other. Full environment collision/coverage checks and review/execution guards remain. Existing policies are not automatically converted or activated. A new blank group-policy draft no longer displays the incorrect legacy-VM warning. No schema migration is required; the naming assessment cache version changes to refresh retained evaluations.

Validation: 822 Django tests passed on disposable PostgreSQL, then 78 naming/UX tests passed after the final warning correction, and all 13 automatic-host tests passed with the additional exact-category overlap case. All 65 JavaScript tests passed. Browser acceptance created and activated a policy over 155 synthetic groups: 150 eligible hosts (149 compliant, one deliberately wrong name), five excluded definitions and zero component proposals. Light/dark desktop/mobile checks had no captured page errors or document overflow. [Updated illustrated guide](../guides/automatic-host-naming-policy.html) · [Browser measurements](../guides/automatic-host-naming-images/browser-checks.json).

## Automatic static-subnet naming and duplicate drafts · 4 November 2026

Added Exactly one explicit subnet for Static policies and the Explicit single subnet component. One saved IPAddressExpression with one CIDR is canonicalized to network_prefix (for example, 10.128.196.150/24 → 10.128.196.0_24). IPv4/IPv6 and /0 networks are supported. Host /32 and /128 entries remain with the separate host condition. Multiple entries (including duplicates), ranges, malformed/scoped addresses and nested/dynamic/extended membership do not qualify. Nothing is combined or inferred from a group's name, and reviewed values cannot replace the actual subnet. Editor defaults set Required, Keep case, Subnet format and length 49. No schema migration is required; cached naming assessments refresh automatically.

Subnet rules retain general-fallback precedence, explicit policy ambiguity, canonical-name collision checks and existing coverage/review guards. Existing host policies can stay active because their membership condition is disjoint. Existing policies are not automatically converted or activated. Duplicate drafts retain the exact source policy name without adding a copy suffix. Duplicate policy is an action in its own table column, outside the name cell.

Validation: all 834 Django tests passed against disposable PostgreSQL and all 65 JavaScript tests passed. Browser acceptance created and activated a policy over 155 synthetic groups: 150 eligible subnets (149 compliant and one deliberately incorrect name), five excluded definitions and zero manual proposals. Desktop/mobile light/dark views had no captured page errors or document overflow. Duplicate-name preservation and Actions placement were also checked in the browser. The six guide images load and the guide has no desktop/mobile document overflow. No operational database or NSX deployment was accessed. [Illustrated subnet guide](../guides/automatic-subnet-naming-policy.html) · [Browser measurements](../guides/automatic-subnet-naming-images/browser-checks.json).

## Shared inventory reference tables · 4 November 2026

Applied the local Unslop methodology in polish mode, retaining the Relay palette, type and drawer navigation. The user explicitly requested tables in place of nested reference disclosures, so implementation proceeded within that direction.

- Product: let NSX administrators compare saved VM and object references without losing inventory context.
- Character: calm, exact, accountable.
- World: a network operations desk with aligned, readable records.
- Moments: configured rule action and recorded enabled/disabled state, using labelled semantic colors.
- Signature: saved environment, snapshot context and membership limitations stay alongside the references.

Audit findings: `report-data.js` used three disclosure levels for VM references; actions and services were hidden inside individual rules; tag/scope drawers used loosely aligned paragraphs; retained group/service references used a different list surface; generic report section/header CSS leaked borders and a gradient into new drawer content. These are corrected by shared table components and scoped styles in `evidence.js` and `workspace-panels.css`.

References now offer category tabs with a visible table: VM Tags, Groups, and Rules & services; tag/scope categories use the same renderer, headers, spacing, empty/error states and pagination. Existing group/service reference lists inherit the table surface too. Rule tables expose name/policy, configured action and recorded state. Allow uses a restrained green, Drop/Reject red and Disabled amber; missing state says Not recorded instead of implying Enabled. Color never replaces text or establishes effective connectivity. Paths are quieter metadata, with full identities retained in the DOM and titles.

Inspect replaces the rule table with configured-service and group-provenance tables within the same drawer. Back to rules restores the page, scroll position and triggering control. Related object navigation retains the existing Back behavior. Raw technical definitions remain separate, lazy and paged; their optional JSON disclosures are retained.

Opening VM Summary loads no reference lists. Each selected category fetches at most 25 rows. Rule display metadata is joined from the same snapshot after limiting the deduplicated page; services and provenance are requested only for the inspected rule. Unknown metadata stays unknown. No index rebuild, schema migration or NSX access is required.

Validation: all 836 Django tests passed on isolated PostgreSQL, plus all 9 VM relationship tests after strengthening the cross-snapshot metadata regression. All 72 JavaScript tests passed, including lazy loading, keyboard tabs, pagination, safe text rendering, unknown states, retries, stale responses, inspection/focus restoration and shared tag/scope tables. Browser acceptance used 63 synthetic shared rules, three tags and two groups. Real VM-row selection, page 2 inspection, related group/tag navigation and Back preservation passed. Light/dark desktop and 390px mobile checks recorded no page errors, document overflow or drawer overflow; wide tables scroll locally. This is synthetic acceptance, not a full screen-reader certification.

[Before](reference-drawer-review-2026-11-04/before.png) · [Rule table](reference-drawer-review-2026-11-04/after-rules.png) · [Rule inspection](reference-drawer-review-2026-11-04/after-inspect.png) · [Group references](reference-drawer-review-2026-11-04/after-group.png) · [Tag references](reference-drawer-review-2026-11-04/after-tag.png) · [Dark drawer](reference-drawer-review-2026-11-04/after-dark-1440.png) · [Mobile drawer](reference-drawer-review-2026-11-04/after-light-390.png) · [Browser checks](reference-drawer-review-2026-11-04/browser-checks.json).

Design suggestion for a later pass: if reference navigation needs filtering across thousands of entries, add server-side name/action filters to this shared table API. Avoid filtering only the currently loaded page, which could conceal matches on other pages. Retain full paths and unknown/coverage states when adding denser layouts.

## Attribution

Design review and implementation use [Unslop by Damir Sokolovsky (@heydamir)](https://github.com/heydamir/unslop), methodology revision `a6f121eee5f29c2f74976b75456feae8b66afb3a`, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Applied locally to the repository and synthetic browser views; no external design service or separate Unslop agent was invoked. Existing credit remains in `NOTICE`.
