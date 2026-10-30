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
