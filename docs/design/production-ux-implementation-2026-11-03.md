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

## Attribution

Design review and implementation use [Unslop by Damir Sokolovsky (@heydamir)](https://github.com/heydamir/unslop), methodology revision `a6f121eee5f29c2f74976b75456feae8b66afb3a`, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Applied locally to the repository and synthetic browser views; no external design service or separate Unslop agent was invoked. Existing credit remains in `NOTICE`.
