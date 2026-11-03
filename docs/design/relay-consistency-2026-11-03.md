# Relay / Bridge · Application-wide consistency audit

The selected Relay option 04 layout and Bridge 06 light palette remain the design direction. This is a polish and integration pass over the current application, including populated results and historical presentations.

## Direction

- **Product:** NSX operators inspect configuration evidence and coordinate reviewed infrastructure changes.
- **Character:** Calm, precise, readable.
- **World:** An infrastructure routing desk, with quiet controls around configuration evidence.
- **Moments:** Application relationships and the source → rule → destination explanation.
- **Signature:** The Relay route mark and dark module rail, paired with a white/silver light workspace or graphite dark workspace.

## Findings and corrections

| Finding | Where it appeared | Correction |
| --- | --- | --- |
| Report-wide `section`, heading, body and control styles escaped into the application shell | Retained inventory and firewall snapshots; the section panel gained margins and rounded corners | Put retained report CSS in a lower-priority cascade layer, scoped to report content and its native dialogs |
| Component stylesheets loaded from page content, after the shared theme | Segmentation, populated VM-pair results, proposals and naming-policy editing | Load component styles in `extra_head`, before the authoritative design system and Relay geometry |
| Fixed blue surfaces, borders and labels remained in component CSS | Rule paths, proposals, membership pickers, evidence, settings, progress and administration | Replace component paint literals with shared surface, text, accent and semantic tokens; remove redundant dark-theme definitions |
| Technical viewers and some administrator actions used inappropriate text colors after theme changes | JSON evidence, add/save/search controls | Use readable code tokens and `--action-text` on filled primary controls |
| Colored shadows, inconsistent small labels and alternate fonts remained | Drawers, cards, code controls and access-management forms | Use centralized neutral depth, the selected interface family and restrained label styling |
| Tables and long paths widened the document | Transient policies, deletion selection, impact previews, callbacks and historical help | Add missing table scroll containers, wrap paths and contain legacy inline report artwork |
| A large non-numeric metric exceeded its mobile grid | Collection coverage, “Not prepared” | Let the label track shrink, retain readable values and use bounded mobile metric columns |

The nine Unslop passes kept the selected brand: spacing and containment, token-based paint, inherited icon colors, shorter VM-investigation labels, shared typography, consistent control/card geometry, readable technical details, unchanged evidence visuals and neutral depth with existing reduced-motion behavior. Features and section order remain intact.

## Coverage and verification

[Page-state inventory](relay-consistency-2026-11-03/coverage.json) · [Browser results](relay-consistency-2026-11-03/report.json) · [Interaction results](relay-consistency-2026-11-03/interaction-report.json)

- Reviewed every application stylesheet and top-level template family. Page coverage includes overview, environments, inventory/firewall report panels, naming, segmentation, populated VM-pair investigations, proposals, finding evidence, deletion/cleanup, collections, history, settings, authentication and administration.
- **99 authenticated page states × 4 theme/viewport configurations = 396 checks:** light/dark desktop, 390 px light and 320 px dark. All returned HTTP 200 with no captured page-script errors, document-level horizontal overflow, content-loaded stylesheets or sampled legacy blue paint. The metric-layout correction was also rechecked on environment, coverage and rule-history pages in both themes at both mobile widths.
- **30 anonymous checks:** two sign-in forms and the 403/404/500 templates, in both themes at 1440/390/320 px. Sign-in displays exactly one theme-appropriate wordmark.
- Opened saved-report evidence and technical tabs, report filters, populated rule drawers and naming-component panels in both themes. Checked fixed report navigation, mobile focus restoration and viewer-only navigation permissions.
- **102 Django tests passed; one PostgreSQL-specific check skipped** in the isolated SQLite fixture. **47 JavaScript tests passed.** Django configuration, migration drift, production manifest static-asset processing and whitespace checks passed.

All examples use a disposable synthetic database and no NSX connections. The browser checks use Chrome; they are not a claim of exhaustive screen-reader or cross-browser certification.

## Screenshots

| Screen | Light | Dark |
| --- | --- | --- |
| Firewall report | [Light](relay-consistency-2026-11-03/snapshot-dfw-overview-light-1440.png) | [Dark](relay-consistency-2026-11-03/snapshot-dfw-overview-dark-1440.png) |
| Populated VM investigation | [Light](relay-consistency-2026-11-03/pair-populated-light-1440.png) | [Dark](relay-consistency-2026-11-03/pair-populated-dark-1440.png) |
| Sign-in | [Light](relay-consistency-2026-11-03/login-light.png) | [Dark](relay-consistency-2026-11-03/login-dark.png) |

[Mobile investigation](relay-consistency-2026-11-03/pair-populated-light-390.png) · [Cleanup review](relay-consistency-2026-11-03/deletion-reference-cleanup-dark-1440.png) · [Access-management form](relay-consistency-2026-11-03/admin-user-edit-dark-1440.png)

## Implementation notes

The snapshot isolation uses native CSS [`@scope`](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@scope) inside a cascade layer. Use current browsers supporting the application's modern CSS. Retained snapshot presentations acquire the current application styling at display time; no recollection, reindexing or stored-report rewrite is needed. Standalone exported reports remain self-contained.

No database migration or NSX permission/workflow change is included. Follow the [shared style contract](../branding.md#shared-style-contract) for future pages rather than introducing component-specific palettes.

Design methodology adapted from [Unslop by Damir Sokolovsky (@heydamir)](https://github.com/heydamir/unslop), revision `a6f121eee5f29c2f74976b75456feae8b66afb3a`, licensed under CC BY 4.0. See `NOTICE`.
