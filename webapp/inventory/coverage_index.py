"""Small, collection-time coverage projection; page reads never open report JSON."""
from django.db import transaction
from .models import SnapshotCoverage, SnapshotCoverageIssue


def report_issues(report, needs_review=False):
    found = False
    for rows, field, area, fallback in (
        (report.get('objects', []), 'membership', 'Group membership', 'Membership is unknown.'),
        (report.get('dfw', {}).get('rules', []), 'hit_status', 'Rule counters', 'Evidence is unknown.'),
        (report.get('dfw', {}).get('policies', []), 'status', 'Policy inventory', 'Evidence is unknown.'),
        (report.get('tags', {}).get('objects', []), 'status', 'Tag usage', 'Evidence is unknown.'),
    ):
        for row in rows:
            if row.get(field) == 'unknown':
                found = True
                yield {'area':area, 'name':row.get('name'), 'detail':'; '.join(row.get('notes', [])) or fallback}
    for area in ('dfw', 'tags'):
        for error in report.get(area, {}).get('errors', []):
            found = True
            yield {'area':area.upper(), 'name':'Collection error', 'detail':error}
    if report.get('tags', {}).get('unsupported_conditions'):
        found = True
        yield {'area':'Tags','name':'Unsupported conditions','detail':'Some tag conditions could not be evaluated.'}
    if report.get('search_coverage', {}).get('mode') != 'all_types':
        found = True
        yield {'area':'Search','name':'Limited or unrecorded coverage',
               'detail':report.get('limitations', 'Search coverage was not recorded.')}
    if needs_review and not found:
        yield {'area':'Audit','name':'Incomplete checks','detail':'This snapshot is flagged for review; consult its report.'}


@transaction.atomic
def build_coverage(snapshot, report):
    # Caller holds the source snapshot lock; publish rows and count together.
    if SnapshotCoverage.objects.filter(snapshot_id=snapshot.pk).exists():
        return
    saved = SnapshotCoverage.objects.create(snapshot=snapshot)
    rows, count = [], 0
    for issue in report_issues(report, snapshot.needs_review):
        rows.append(SnapshotCoverageIssue(coverage=saved, ordinal=count, **issue))
        count += 1
        if len(rows) >= 100:
            SnapshotCoverageIssue.objects.bulk_create(rows, batch_size=100)
            rows.clear()
    if rows:
        SnapshotCoverageIssue.objects.bulk_create(rows, batch_size=100)
    saved.issue_count = count
    saved.save(update_fields=['issue_count'])


class PreparedIssues:
    """Use the persisted count without counting/scanning inventory records."""
    def __init__(self, coverage):
        self.coverage = coverage

    def count(self):
        return self.coverage.issue_count

    def __len__(self):
        return self.count()

    def __getitem__(self, key):
        return self.coverage.issues.values('area', 'name', 'detail')[key]
