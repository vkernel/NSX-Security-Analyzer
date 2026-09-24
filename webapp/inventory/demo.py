"""Synthetic inventory for exploring the UI without contacting NSX."""
from uuid import uuid4
from django.db import transaction
from django.utils import timezone
from .models import Environment
from .services import prepare_snapshot


@transaction.atomic
def create_demo_environment():
    suffix = uuid4().hex[:12]
    environment = Environment.objects.create(
        slug='demo-' + suffix, name='Demo environment · ' + suffix[:6],
        manager='https://demo-' + suffix + '.invalid', enabled=False,
        sync_interval_minutes=0)
    stamp = timezone.now().isoformat()
    group_root = '/infra/domains/default/groups/'
    policy_path = '/infra/domains/default/security-policies/demo'
    rows = []
    for name, key, membership, usage in [
        ('Demo web servers', 'web', 'nonempty', 'referenced'),
        ('Demo database servers', 'database', 'nonempty', 'referenced'),
        ('Demo empty group', 'empty', 'empty', 'unused_candidate')]:
        rows.append(dict(name=name, path=group_root+key, kind='group',
            membership=membership, usage=usage, referenced_by=[], notes=['Synthetic demonstration data.']))
    rows.append(dict(name='Demo HTTPS', path='/infra/services/demo-https', kind='custom_service',
        membership='not_applicable', usage='referenced', referenced_by=[], notes=['Synthetic demonstration data.']))
    rules = []
    for index, (name, status, hits, disabled) in enumerate([
        ('Demo web to database', 'traffic_recorded', 1250, False),
        ('Demo zero-hit rule', 'zero_hits', 0, False),
        ('Demo disabled rule', 'zero_hits', 0, True)], 1):
        path = policy_path + '/rules/' + str(index)
        rules.append(dict(name=name, path=path, policy_rule_id=str(index), rule_id=index,
            policy_name='Demo application policy', policy_path=policy_path, category='Application',
            action='ALLOW', disabled=disabled, system_owned=False, hit_status=status, hit_count=hits,
            source_groups=[group_root+'web'], destination_groups=[group_root+'database'],
            services=['/infra/services/demo-https'], scope=['ANY'],
            statistics=[{'enforcement_point':'Synthetic', 'hit_count':hits}],
            statistics_checked_at=stamp, notes=['Synthetic counters; not evidence of real traffic.']))
    for row in [rows[0], rows[1], rows[3]]:
        row['referenced_by'] = [rule['path'] for rule in rules]
    report = dict(manager=environment.manager, generated_at=stamp, testing=True,
        objects=rows, groups_scanned=3, custom_services_scanned=1, system_groups_excluded=0,
        indexed_objects_scanned=8, scope='Synthetic demo inventory — no NSX data collected',
        usage_definition='Illustrative findings only', limitations='Synthetic testing data. Do not use for operational decisions.',
        dfw={'policies':[dict(name='Demo application policy', path=policy_path,
            domain='/infra/domains/default', category='Application', rule_count=3,
            status='has_rules', system_owned=False, notes=[])], 'rules':rules, 'errors':[]})
    snapshot = prepare_snapshot(environment, report)
    snapshot.summary['synthetic'] = True
    snapshot.save()
    return environment
