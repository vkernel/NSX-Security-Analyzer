# Unslop follow-up and acceptance · 3 November 2026

> **Historical record.** This document describes the design or implementation at the time it was written. Current code, tests and the main documentation take precedence.

The outstanding code recommendations and the requested synthetic-inventory acceptance checks are complete locally. The user confirmed that synthetic inventories are the acceptance basis. This extends the [implementation record](production-ux-implementation-2026-11-03.md). This record captures acceptance before publication. Development publication is identified by the Git commit and matching Docker image tag; publishing does not update the running operational deployment.

## Delivered

**Policy context in recurring issues.** Each cause includes affected policy names and a count of filtered groups for each policy. Policy links retain the issue/search filters and narrow the queue; they do not send viewers to an inaccessible editor. Three policies are visible initially, with additional entries in a disclosure. Ambiguous matches explain that counts can overlap. No applicable policy has an explicit empty-policy explanation. The existing cause action still opens all affected groups.

**Recent environments.** The environment popover groups previously visited environments above the remaining choices. It retains at most five environment IDs in the authenticated browser session, excludes the current environment from the recent group, removes unavailable IDs and avoids duplicate buttons. Each target is rebuilt from the current server context, preserving the existing cross-environment clearing rules. Search, Arrow keys, Escape and focus return work across both groups. This is a session convenience, not a new shared or cross-device preference, and requires no migration or extra navigation request.

**Accessibility corrections.** The broader review found and fixed links that relied on colour alone, narrow sort buttons, undersized collection-diagnostics disclosures, a labelled filter container without a role, duplicate environment-field label targets in policy/mapping forms, and map controls/viewport without explicit roles. The naming simulation checkbox also retains a direct visual association with its label at mobile widths. The interactive map is now a labelled group rather than an atomic image. Map keyboard activation opens evidence and returns focus to the activating node when closed.

## Verification

- Full isolated PostgreSQL suite: **787 tests passed**.
- JavaScript suite: **56 tests passed**.
- Existing browser workflows: **14 passed**. New feature/large-inventory workflows: **10 passed**.
- Configuration: no Django issues, no migration changes, 109 templates compiled; local production image build and source whitespace/syntax checks passed.
- Automated accessibility: axe-core 4.10.3, WCAG 2 A/AA, 2.1 A/AA and 2.2 AA tags, **40 representative page states** across both themes and **14 open-menu/drawer/mobile states**. No automated violations in these checked states.
- Six targeted keyboard/contrast checks passed: skip-link focus, map keyboard activation/focus return and SVG text contrast, each in both themes. Minimum computed map text contrast was **5.81:1 light** and **8.19:1 dark** against node surfaces.

Axe's incomplete/manual-review entries remain in the evidence. Native hidden-dialog references were checked against the actual labelled dialogs; symbolic controls have accessible names; SVG text contrast was calculated against its node surface. Automated scanning does not certify the complete application or replace screen-reader/user acceptance.

[Page accessibility evidence](production-ux-acceptance-2026-11-03/accessibility-audit.json) · [Overlay and keyboard evidence](production-ux-acceptance-2026-11-03/accessibility-overlays.json) · [Workflow and large-inventory evidence](production-ux-acceptance-2026-11-03/followup-workflows.json) · [Validation summary](production-ux-acceptance-2026-11-03/validation.json)

## Inventory-size acceptance

An offline fixture approximated the supplied operational counts: **10,839 groups, 4,075 VMs, 1,528 services, 3,239 rules and 133 firewall policies**. It included compliant/noncompliant/invalid naming values, application buckets, configuration references and enabled/disabled rules. No NSX endpoints or workers were used.

| View | Rendered items | First browser visit | Repeat visit |
| --- | ---: | ---: | ---: |
| Naming attention | 20 rows | 899 ms | 245 ms |
| VMs | 25 rows | 298 ms | 269 ms |
| Groups | 25 rows | 281 ms | 268 ms |
| Rules | 25 rows | 265 ms | 273 ms |
| Applications map | 12 displayed buckets | 369 ms | 165 ms |
| Matrix group picker | 50 options | 311 ms | 378 ms |

These are observed loopback Chrome timings against a disposable SQLite-backed development preview with prepared indexes/caches. They include navigation and waiting for the relevant rendered items. They are not a production SLA, cold-cache benchmark, PostgreSQL deployment benchmark or proof of live NSX collection performance. The full regression suite separately used PostgreSQL.

Naming search and page-two counts were checked against the large fixture. Recent-environment menus and policy summaries stayed within 320px in light/dark mode with larger text and high contrast.

## Accepted validation scope

The user confirmed: “just use a synthetic inventories.” The completed large synthetic fixture, functional checks and accessibility review above therefore satisfy the requested acceptance scope. An operational deployment or real dataset is not an outstanding requirement for this task.

No live-data validation or exhaustive screen-reader certification is claimed. The recorded checks describe the synthetic inventory, browsers and states actually tested. Publishing and deployment remain separate from these acceptance results; use the matching development commit/image tag to identify a published build.

## Captures

[Policy context and filtered queue](production-ux-acceptance-2026-11-03/policy-issue-context.png) · [Recent-environment picker](production-ux-acceptance-2026-11-03/recent-environments.png)

Method: [Unslop by Damir Sokolovsky (@heydamir)](https://github.com/heydamir/unslop), revision `a6f121eee5f29c2f74976b75456feae8b66afb3a`, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Applied locally; existing `NOTICE` credit is retained.
