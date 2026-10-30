# Workload segmentation

Open **Segmentation** in the sidebar and choose an environment. The page pins the latest full, non-imported collection in its URL. A new collection produces a notification; it never replaces evidence while you are reviewing it. Archived browsing data must be restored from the snapshot page first. Environment switching opens that environment's latest full collection.

## Explore

- **Matrix** lists direct source/destination group references, including same-group rules. Select a cell and then a rule for its saved evidence. ANY is a symbolic endpoint, not an internet node or an automatically expanded set of workloads.
- **Map** provides directed connections and accessible equivalent connection lists. Group cards distinguish static, dynamic and mixed definitions and link to saved object evidence. Explicit members come from definitions; dynamic resolved member lists are not inferred.
- Search names, paths, VM details/IP addresses or the configured classification. VM-to-group links are potential configuration relationships, not verified membership. Service search includes directly referenced saved service definitions and inline entries.
- Administrators can configure a classification tag scope per environment. Only supported positive definition tags supply classifications. Missing or ambiguous classifications remain Unclassified; visual containers are not enforcement boundaries.
- The Reduce clutter filter can hide comma-separated group paths, for example shared infrastructure. A visible notice reports hidden matching groups. Those groups are outside the map and matrix. Rules involving other visible endpoints can still appear in the rule evidence.
- Save personal filters for repeated use, share the current URL for an exact snapshot, or export rule evidence to CSV. Saved personal views open a fresh latest snapshot when used.

## Review

Review priorities identify broad ANY endpoints/services and unsupported endpoint definitions. Administrators can record intended source/destination separation boundaries. Direct positive enabled allow rules, including ANY references, are candidates for review. No matching candidate does **not** prove that a boundary is enforced.

Operators can assign a rule review to an active operator, add a note, or record an accepted exception with a reason and future expiry. Reviews are bound to the saved rule configuration fingerprint. Changed rule evidence and expired exceptions are marked stale. Audit events retain review metadata. These reviews are separate from finding approvals and NSX deletion authorization; none of these actions changes NSX.

The Changes tab compares rule configuration in the selected view with the preceding browsable full snapshot, and compares definition/membership status for groups present in both bounded views. Added/removed means entering/leaving the selected view, not necessarily object creation/deletion. Resolved member-by-member changes are not available here.

## Limits and interpretation

This is a configuration explorer, not an effective-access simulator. Rule order, policy category, applied-to scope, nested groups, exclusions, unsupported conditions, distributed enforcement and complete collection evidence must be considered before concluding whether traffic can pass. Negated endpoint rules are shown as review candidates, never converted into ordinary matrix edges. Rule hits cannot identify communicating workload pairs. Actual flow visualization requires separately collected flow telemetry.

Views are limited to 12 matching groups plus a symbolic ANY node, 10,000 rule records and 50 matching VMs. A connection shows at most 25 rule links; review priorities are paginated. Limit notices remain visible. Filter more narrowly or use the firewall inventory for the complete collected rule list. Counts are for the selected view, not a segmentation security score. Complex/nested service resolution and effective policy evaluation are not performed.

Snapshot records are reused without duplicating inventory payloads. Migration `0057_segmentation_workspace` adds only environment grouping settings, intended boundaries, review metadata and personal saved views. Apply migrations before deploying matching web/worker code. No new NSX permissions or write API calls are introduced.

## Between two workloads

The sidebar now opens **Between workloads**, with **Application map** and **Review priorities** alongside it. Select source and destination using VM name, IP or tag search. The pickers are limited to the chosen environment and snapshot. Swap reverses the pair; TCP/UDP/ICMP and destination-port filters narrow service evidence. ICMP does not accept a destination port.

The investigation scans up to 10,000 collected rules independently of the application map's 12-group limit. It explains group references, explicit saved IP/CIDR matches, ANY endpoints and supported direct/nested service definitions. Group candidates include explicit VM identities, saved IP definitions, positive tag relationships and bounded nested-group relationships. These are configuration relationships, not automatically verified realized membership. Unsupported/negated endpoints, missing service definitions, recursion limits and unresolved membership are kept in **Needs assessment**, not silently treated as a deny or proof of no rule.

Results are ordered by saved policy category and policy/rule sequence, without evaluating first-match effective access. Select a rule to highlight its source → rule → destination explanation. Details include direction, Applied to, full saved configuration and links to the original snapshot. An enabled allow rule and drop rule do not by themselves establish a conflict. Unknown scope remains explicit. Disabled rules are excluded unless requested.

The group lists offer operators **Check live VM membership**. This performs one read-only NSX GET of up to 1,000 VM membership rows for the selected group. Matching immutable VM identity is reported with the check time. No match does not establish absence: more pages or other membership types may apply. Timeouts, missing credentials and permission failures are visible and audited. The check never changes the saved rule assessment or authorizes an operation. No NSX write is performed.

Save a personal investigation to reopen stable VM identities against the latest full snapshot, or copy the current URL to share exact historical evidence. Missing VMs in a later snapshot prompt reselection. Candidate and unresolved rule explanations can be exported to CSV. Saved investigations can be removed by their owner. New-collection polling never replaces a selected snapshot, collapses evidence or resets a form.

## Local firewall rule proposals

After investigating a pair, choose **Draft a firewall rule**. Review similar collected candidate rules first. A zero count means no candidate was found in available evidence, not that no applicable rule exists.

Choose the source and destination explicitly from related groups or saved VM addresses. The application does not automatically choose among multiple groups; a group can affect more workloads and change membership later. Specify a proposed rule name, protocol/port, action, existing destination policy, intended placement, logging, owner and business justification. The form previews the proposal before saving.

**Save rule proposal**, edit, **Discard** and JSON export only manage a local planning artifact. Drafts are never passed to a deletion or NSX execution workflow. There is no deploy button and exported JSON is labelled as a non-deployed proposal, not an API request body. New snapshots do not rewrite existing drafts; stale evidence and removed snapshots are flagged. Snapshot provenance and endpoint selections survive normal snapshot deletion. Discarded drafts remain historical. Revision checks reject concurrent edits/discards.

Drafts are available to the creator, assigned owner and administrators. Audit records retain saved revisions and discards. Apply migration **0058_firewall_rule_proposals** when deploying matching application images. This adds only compact proposal metadata and does not duplicate snapshot inventories.

### Reading workload investigations

The result summary shows candidate counts, unresolved counts and the age of the pinned collection. Open a rule to inspect its evidence in a side drawer without leaving the VM pair. Close or press Escape to return to the same rule. Without dialog support, evidence expands inline. Share/export and technical configuration remain under expandable sections. These summaries do not establish effective connectivity.
