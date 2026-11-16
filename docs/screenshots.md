# Visual walkthrough

These screenshots show the current **development interface** rendered locally with
the built-in synthetic demo (`load_demo_data`): fictional environments, VMs, groups,
rules and users on `*.example.invalid` hosts. No production inventory, credentials or
live NSX connection was used.

Images use a 1440 × 900 desktop viewport and the light theme unless noted. Your role,
theme, screen size, saved filters and review state can change the available controls.
Click an image on GitHub to view it at full size. Screenshots illustrate appearance,
not collection completeness or approval of any real object.

## Sign in

![Sign-in card with the NSX Security Analyzer logo, username and password fields](images/login.png)

Enter your local credentials, or choose Keycloak or LDAPS when your administrator has
enabled them. The directory sign-in page uses the same card.

## Workspace overview and navigation

![Workspace overview with collection health tiles, environment health, recent collections and finding qualification](images/overview.png)

The sidebar groups pages under **Explore** and **Work**, with an environment switcher,
search (Ctrl/⌘ K) and a collapsible rail. The overview shows failed and running
collections, environment freshness and coverage, recent collections and how present
findings qualify for review.

![The same overview in the dark theme](images/overview-dark.png)

![Phone layout with the navigation drawer open](images/mobile-navigation.png)

On phones the navigation opens as a drawer.

## Inventory

![Inventory overview with a coverage warning, object counts, evidence coverage and needs-attention list](images/inventory-overview.png)

The inventory overview summarises the latest saved snapshot from saved projections
only: counts, evidence coverage per area (unknown coverage is never shown as
complete) and the finding categories that need attention.

![VM list with the evidence panel widened on firewall references](images/vm-evidence.png)

Selecting a VM or group opens its evidence beside the list. The panel has **Docked**,
**Wide** and **Full screen** widths and widens automatically for firewall references.
Use **Open in new tab** to bookmark or share one object's evidence.

![Docked evidence panel showing firewall references as one card per rule](images/vm-evidence-docked.png)

In the docked width, firewall references show one card per rule.

![Groups list with the evidence panel docked beside it](images/groups.png)

![Services overview with filter cards, entries, rule counts and observations](images/services.png)

![Tags and scopes overview with a scope list and values flagged for case differences or missing VMs](images/tags-scopes.png)

## Firewall

![Firewall rules in NSX processing order grouped by category and policy](images/firewall-rules.png)

Rules open in configured NSX order (category, policy sequence, rule sequence) with
Source, Destination, Services, Applied To, Action and State. **Sortable table** keeps
the original searchable table with export and bulk selection.

![Firewall policies in NSX order with the selected policy's rules beside the list](images/firewall-policies.png)

![References page listing every saved rule that names a group, with field filters and export](images/references.png)

**References** lists every saved rule that names a group or custom service, one row
per field, with NOT for excluded fields. References describe saved configuration,
not effective policy or traffic.

![Export dialog with categories, column presets and custom columns](images/export-references.png)

Exports let you choose categories and columns per category. The evidence basis is
always included, so direct VM associations, interface attachments and IP/subnet
matches stay distinguishable. Exports never start an NSX refresh.

## Find and review work

![Reviews list with the selected finding's summary and decommissioning steps beside it](images/finding-reviews.png)

**Reviews** lists tasks by queue with the selected task's summary beside the list.
Decisions stay on the review page itself.

![Reviews with one finding selected and the bulk actions bar](images/finding-selection.png)

![Assign owner page listing the selected finding with reviewer choice and confirmation](images/assign-owner.png)

![Finding detail with review stages, evidence and review progress](images/finding-detail.png)

Open a finding to inspect evidence, readiness and review progress. Follow the
[finding review guide](finding-review-workflow.md) for assignment, required checks,
independent approvals, manual completion and administrator-confirmed NSX deletion.

![Return to unassigned page with a required reason and confirmation](images/return-to-unassigned.png)

Select eligible rows → **Actions → Return to unassigned** → enter a reason → confirm.
Valid approvals and history are preserved; the server rechecks permissions and revisions.

![Unassigned queue](images/unassigned-reviews.png)

![Assigned to me queue](images/my-work.png)

## Collections, history and coverage

![Collection result with duration, requests, objects saved, next steps, pacing and coverage](images/collection-result.png)

Each collection has its own page: progress phases while it runs, and a result view
with request pacing, coverage and what happens next once it completes.

![Environment page with collection controls and saved snapshot totals](images/environment.png)

![Snapshot history with two snapshots selected as Before and After](images/snapshot-selection.png)

Choose two snapshots and select **Compare selected**. The earlier timestamp is Before;
the later is After. See [comparison and coverage](history-and-review.md).

![Snapshot comparison selectors](images/compare-snapshots.png)

![Collection coverage with observation totals, gaps and checks needing attention](images/collection-coverage.png)

![Activity with collections, recalculations and comparisons](images/activity.png)

## Segmentation and naming

![Segmentation with application cards and the selected application's configured connections](images/segmentation.png)

![Naming compliance with outcome cards and recurring issues](images/naming.png)

## Administration and account

![Administration directory](images/administration.png)

![Review approval settings](images/review-approval-settings.png)

![Authentication settings](images/authentication-settings.png)

![Audit log](images/diagnostic-collection.png)

![Get started checklist with steps derived from the workspace](images/getting-started.png)

## Screenshot maintenance

Captured on 16 November 2026 from the `development` branch with the synthetic demo
data, using headless Chrome at 1440 × 900 (phone: 390 × 844 at 2×). Assets are in
`docs/images/` and shared across guides. The step-by-step naming guides under
`docs/guides/` use older captures. Recapture a page when its controls or navigation
change. Use an isolated synthetic database and a separate browser session; never
capture a production account, secret field, token, real Manager hostname or private
inventory.

### Drawer interaction checks

- Reviews: select a task and verify the side panel follows; the primary action opens the review page, where decisions are made.
- Evidence panel: switch Docked, Wide and Full screen, resize with the handle and arrow keys, and check that Escape leaves full screen before closing.
- Naming policy, deletion verification and reference cleanup: inspect details without expanding the table vertically. Close restores focus to the opener.
- Check at desktop and 390px mobile width. Without native dialog support, ordinary links and expandable details remain available.

### Consistent tabs and accessibility

Object evidence uses Summary, Members when applicable, References, History and Technical details. Check Arrow Left/Right, Home and End in tab lists; exactly one tab should participate in the Tab sequence, and each tab must reference its panel. Switching tabs must retain draft field values. These checks are regression coverage, not a claim of formal WCAG certification.
