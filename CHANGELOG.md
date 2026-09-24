# Changelog

## 0.2.1

- Restart paginated NSX search up to twice when returned totals are inconsistent.
- Discard incomplete attempts and preserve the previous snapshot if retries fail.
- Explain inventory changes and search indexing delays in collection error guidance.

## 0.2.0

- Add snapshot comparison for rules, groups, services and membership evidence.
- Add persistent finding ownership, acknowledgement, notes and review dates.
- Reopen findings when relevant evidence changes; retain notes across snapshot retention.
- Add collection coverage, incomplete checks, stale evidence and observation gap views.
- Save fuller NSX configuration without additional API requests.
- Add migration 0011 for finding review tables; existing snapshots remain unchanged.

## 0.1.0

- Make the web GUI the only product interface; remove standalone CLI, file reports and manager-file import.
- Move collection into the application package; require saved GUI credentials for collection.

- Adopt Apache License 2.0 with project attribution and container license metadata.

Initial standalone repository for NSX Security Analyzer.

- Read-only NSX Policy collection and database-backed report viewing.
- Multi-environment web workspace with PostgreSQL snapshots and scheduled collection.
- Inventory, firewall activity history, reference evidence and coverage review.
- Retention policies, user preferences, progress indicators and readable errors.
- Synthetic demo environments and searchable environment/snapshot navigation.
- Product documentation, contribution guidance and continuous integration.
