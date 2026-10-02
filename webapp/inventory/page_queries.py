"""Small display projections shared by workspace pages."""
from django.db.models import F, OuterRef, Subquery
from django.db.models.functions import JSONObject
from .models import Environment, Snapshot, AuditJob


def environments():
    return Environment.objects.defer('password_ciphertext', 'ca_certificate')


def snapshots():
    return Snapshot.objects.defer('report', 'html', 'summary').annotate(display_summary=JSONObject(
        **{key:F('summary__'+key) for key in ('groups','services','rules','synthetic','new_coverage_issues')}))


def jobs(queryset=None, include_coverage=False):
    queryset = AuditJob.objects.all() if queryset is None else queryset
    queryset = queryset.select_related('environment','snapshot','stop_request').defer(
        'config','diagnostics','environment__password_ciphertext','environment__ca_certificate',
        'snapshot__report','snapshot__html','snapshot__summary')
    if include_coverage:
        queryset = queryset.annotate(new_coverage_issues=F('snapshot__summary__new_coverage_issues'))
    return queryset


def cards(environments, options):
    from .usability import freshness
    latest = Snapshot.objects.filter(environment_id=OuterRef('pk')).values('pk')[:1]
    successful = Snapshot.objects.filter(environment_id=OuterRef('pk'), testing=False,
        imported=False, job__status='succeeded').order_by('-generated_at').values('generated_at')[:1]
    environments = list(environments.annotate(latest_id=Subquery(latest), last_full=Subquery(successful)))
    latest_rows = {s.pk:s for s in snapshots().filter(pk__in=[e.latest_id for e in environments if e.latest_id])}
    active = {j.environment_id:j for j in AuditJob.objects.filter(environment_id__in=[e.pk for e in environments],
        status__in=['queued','running']).defer('config','diagnostics')}
    return [{'environment':e,'latest':latest_rows.get(e.latest_id),'active':active.get(e.pk),
             'freshness':freshness(e,options,last=e.last_full)} for e in environments]
