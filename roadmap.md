# NSX Security Analyzer roadmap

The proposed direction is explainable security recommendations backed by
configuration history and observed traffic. Priority 1 is implemented in version 0.2.0; later phases describe planned work,
not currently available functionality or committed release dates. Priorities may
change after compatibility testing and feedback.

## Priorities

| Priority | Capability | Practical value |
| --- | --- | --- |
| 1 | Snapshot comparison | Show rules, groups, services and memberships added, removed or changed between collections. |
| 1 | Finding review workflow | Assign owners, add notes, acknowledge findings, set review dates and reopen findings when evidence changes. |
| 1 | Collection coverage dashboard | Show gaps, failed checks, stale data and observation windows before drawing conclusions. |
| 2 | IPFIX collection and traffic explorer | Search observed communications by source, destination, protocol, port and time. |
| 2 | Traffic-to-rule correlation | Inspect a rule's observed communications alongside its configuration history. |
| 2 | Application dependency map | Show which workloads communicate and which services they use. |
| 3 | Policy optimization recommendations | Suggest narrower endpoints/services and flag potentially redundant rules with supporting evidence. |
| 3 | Change and traffic alerts | Notify on new communication paths, broad ALLOW rules, unexpected policy changes or missing exporters. |
| Parallel | Enterprise access and accountability | Keycloak/OIDC, environment-scoped permissions and an audit trail of application changes. |

## Phase 1: History and review foundations — delivered in 0.2.0

- Compare two saved snapshots and explain configuration changes.
- Track findings through ownership, notes, review dates and acknowledgement.
- Reopen reviewed findings when relevant evidence changes.
- Make incomplete collection, stale data and observation gaps visible.
- Preserve unknown states rather than interpreting missing evidence as zero activity.

These capabilities establish the context needed to interpret future traffic data.
See [History and review](docs/history-and-review.md) for usage and limitations.
Membership comparison covers definitions and checked status, not resolved member
list changes. Legacy fields and incomplete coverage remain explicitly unknown.

## Phase 2: DFW IPFIX proof of concept

Start with NSX Distributed Firewall IPFIX rather than attempting broad NetFlow
support immediately. NSX provides firewall IPFIX profiles and collector
configuration. Validate the actual export templates, available fields and behavior
against supported NSX versions before committing to correlation features.

Reference: [Broadcom NSX firewall IPFIX API](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/policy_monitoring_ipfix_firewall_ipfix_profiles.html).

The proof of concept should establish:

- Which templates and fields the target NSX versions export.
- Whether exported rule identity and action support reliable correlation.
- Sampling behavior, template refresh, exporter restarts and loss indicators.
- Correct separation of environments with overlapping IP addresses.
- Sustainable ingestion rates and query performance under representative load.

Keep export configuration separate from read-only analysis. Adding a collector
does not authorize the application to change NSX configuration automatically.

## Phase 3: Traffic explorer and rule correlation

The first traffic release should provide:

- An optional collector service, deployed separately from the web application.
- Time-filtered source/destination, protocol and port searches.
- Bytes and packets where provided by exported records.
- First and last **observed** activity.
- Exporter health: last received record, template availability, suspected loss and known sampling.
- Environment-aware mapping that handles overlapping addresses.
- Configurable retention and aggregated daily statistics.
- Rule identity/action correlation only when exported fields provide sufficient evidence.

An IP address alone must not identify a workload across all environments.
Correlation requires timestamps and historical inventory because addresses, group
membership and rule definitions change. Ambiguous mappings must remain visible.

### Improve zero-hit rule evidence

Combine snapshot counters, exported traffic observations and collection coverage.
A future rule view could present this illustrative example:

> No positive counters across 42 successful snapshots over 30 days.
> No matching exported flows observed.
> Traffic collection had a six-hour gap; sampling was enabled.

This is more useful than a single zero counter, but it is not a declaration that a
rule is safe to delete. Sampling, lost exports and incomplete coverage can hide
activity. Counter resets and traffic between snapshots also limit conclusions.
Flow records describe communications; they do not provide packet payload inspection.

## Phase 4: Dependencies and recommendations

Once traffic evidence is dependable:

- Build application dependency maps with time and environment filters.
- Explain new or changed communication paths.
- Suggest narrower rule endpoints and services with observation-window context.
- Flag potentially redundant rules without treating observed traffic as a complete policy model.
- Alert on relevant configuration changes and missing traffic exporters.
- Link every recommendation to its supporting evidence and limitations.

## Storage and deployment

Keep PostgreSQL for configuration, snapshots, findings and aggregated traffic
summaries. Partitioned PostgreSQL flow tables may be sufficient for a small pilot.
Benchmark ingestion, retention and queries before deciding whether larger
installations need a dedicated analytics store such as ClickHouse.

Do not store one database row per packet or run continuous flow processing inside
the Django web process. Define independent flow retention and aggregation policies
so that traffic volume does not overwhelm snapshot storage or the application UI.

## Enterprise capabilities

Develop these alongside the analysis roadmap:

- Keycloak/OpenID Connect authentication.
- Environment-scoped access permissions.
- An audit trail for application configuration and review actions.

These are planned enhancements; the current workspace's shared environment
visibility must not be presented as tenant isolation.

## Deferred scope

Automatic rule deletion, automatic policy changes and broad AI threat-detection
claims are outside the initial roadmap. Focus first on recommendations that a
reviewer can understand and verify.

## Suggested delivery order

1. Snapshot comparison, review tracking and coverage visibility.
2. IPFIX proof of concept using actual NSX export templates.
3. Traffic explorer and evidence-based rule correlation.
4. Dependency maps, optimization recommendations and targeted alerts.

No implementation timelines are committed at this stage.
