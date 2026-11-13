"""Reference rows show saved rule configuration and mark matched entries in place."""
from django.test import TestCase
from django.utils import timezone
from .models import Environment, Snapshot, SnapshotRecord
from .firewall_reference_context import enrich, context_profiles_in_use, FIELD_LIMIT
from .reference_export_formats import COLUMNS, row
from . import confirmed_references, object_references, tag_relationships

GP = '/infra/domains/default/groups/'
SP = '/infra/services/'
PP = '/infra/context-profiles/'


class FirewallReferenceFieldTests(TestCase):
    def setUp(self):
        context_profiles_in_use.cache_clear()
        env = Environment.objects.create(name='Synthetic fields', slug='synthetic-fields', manager='https://fields.invalid')
        self.snapshot = Snapshot.objects.create(environment=env, generated_at=timezone.now(), report={})
        self.ordinal = 0
        for name in ('web', 'db', 'admins'):
            self.record('inventory', name.title(), {'kind': 'group', 'path': GP + name})
        self.record('inventory', 'Custom HTTPS', {'kind': 'custom_service', 'path': SP + 'custom-https'})

    def record(self, view, name, data):
        self.ordinal += 1
        return SnapshotRecord.objects.create(snapshot=self.snapshot, ordinal=self.ordinal, view=view, name=name, data=data,
                                             compact={'path': data.get('path')}, columns=[], sort_values={})

    def rule(self, name, *, sources, destinations, services=('ANY',), scope=('ANY',), profiles=None,
             policy_scope=None, sources_excluded=False, category='Application', policy='App', policy_sequence=1,
             sequence=10):
        policy_path = '/infra/domains/default/security-policies/' + policy.lower()
        path = policy_path + '/rules/' + name
        rule = {'sources_excluded': sources_excluded}
        if sequence is not None:
            rule['sequence_number'] = sequence
        if profiles is not None:
            rule['profiles'] = list(profiles)
        saved_policy = {'scope': list(policy_scope or ['ANY'])}
        if policy_sequence is not None:
            saved_policy['sequence_number'] = policy_sequence
        self.record('dfw', name, {'name': name, 'path': path, 'policy_name': policy, 'policy_path': policy_path,
            'category': category, 'rule_id': 7, 'action': 'ALLOW', 'disabled': False, 'source_groups': list(sources),
            'destination_groups': list(destinations), 'services': list(services), 'scope': list(scope),
            'configuration': {'rule': rule, 'policy': saved_policy}})
        return path

    def test_fields_resolve_names_mark_matches_and_keep_any_and_negation(self):
        path = self.rule('web-to-db', sources=[GP + 'admins', GP + 'web'], destinations=[GP + 'db'],
                         services=[SP + 'HTTPS', SP + 'custom-https'], profiles=[PP + 'SSL'], sources_excluded=True)
        item, = enrich(self.snapshot.pk, [{'path': path, 'via_groups': [GP + 'web']}], vm=True)
        fields = item['rule_fields']
        sources = fields['sources']
        self.assertTrue(sources['excluded'])
        self.assertEqual([entry['name'] for entry in sources['items']], ['Web', 'Admins'])  # matched first
        self.assertEqual([entry['matched'] for entry in sources['items']], [True, False])
        self.assertEqual(fields['destinations']['items'][0], {'path': GP + 'db', 'name': 'Db', 'known': True, 'matched': False})
        services = {entry['name']: entry['known'] for entry in fields['services']['items']}
        self.assertEqual(services, {'HTTPS': False, 'Custom HTTPS': True})  # built-ins are not inventoried
        self.assertEqual(fields['context_profiles']['items'][0]['name'], 'SSL')
        self.assertTrue(fields['applied_to']['any'])
        self.assertEqual(item['matched_fields'], ['Source (excluded)'])
        self.assertTrue(item['show_context_profiles'])

    def test_policy_applied_to_takes_precedence_and_profiles_flag_is_snapshot_wide(self):
        path = self.rule('scoped', sources=['ANY'], destinations=[GP + 'db'], scope=[GP + 'web'], policy_scope=[GP + 'admins'])
        item, = enrich(self.snapshot.pk, [{'path': path, 'via_groups': [GP + 'db']}], vm=True)
        applied = item['rule_fields']['applied_to']
        self.assertEqual((applied['origin'], applied['items'][0]['name']), ('policy', 'Admins'))
        self.assertTrue(item['rule_fields']['sources']['any'])
        self.assertFalse(item['rule_fields']['context_profiles']['recorded'])
        self.assertFalse(item['show_context_profiles'])
        policy_match, = enrich(self.snapshot.pk, [{'path': path, 'via_groups': [GP + 'admins']}], vm=True)
        self.assertEqual(policy_match['matched_fields'], ['Applied to (policy)'])
        self.assertTrue(policy_match['rule_fields']['applied_to']['items'][0]['matched'])
        context_profiles_in_use.cache_clear()
        self.rule('other', sources=['ANY'], destinations=['ANY'], profiles=['ANY'])
        self.assertFalse(enrich(self.snapshot.pk, [{'path': path, 'via_groups': []}], vm=True)[0]['show_context_profiles'])

    def test_page_rows_are_bounded_but_exports_keep_every_entry(self):
        many = [GP + 'member-%03d' % i for i in range(60)]
        path = self.rule('wide', sources=many + [GP + 'web'], destinations=['ANY'])
        page, = enrich(self.snapshot.pk, [{'path': path, 'via_groups': [GP + 'web']}], vm=True)
        self.assertEqual(len(page['rule_fields']['sources']['items']), FIELD_LIMIT)
        self.assertEqual(page['rule_fields']['sources']['total'], 61)
        self.assertTrue(page['rule_fields']['sources']['items'][0]['matched'])
        full, = enrich(self.snapshot.pk, [{'path': path, 'via_groups': [GP + 'web']}], vm=True, full=True)
        self.assertEqual(len(full['rule_fields']['sources']['items']), 61)

    def test_export_row_uses_the_drawer_columns(self):
        path = self.rule('export', sources=[GP + 'web'], destinations=['ANY'], profiles=[PP + 'SSL'], sources_excluded=True,
                         policy_scope=[GP + 'admins'])
        item, = enrich(self.snapshot.pk, [{'path': path, 'via_groups': [GP + 'web', GP + 'nested']}], vm=True, full=True)
        cells = dict(zip(COLUMNS, row(item)))
        self.assertEqual(len(row(item)), len(COLUMNS))
        self.assertEqual(cells['Sources'], 'NOT Web')
        self.assertEqual(cells['Destinations'], 'Any')
        self.assertEqual(cells['Context profiles'], 'SSL')
        self.assertEqual(cells['Applied to'], 'From policy: Admins')
        self.assertEqual(cells['Matched in'], 'Source (excluded): Web; Field not retained: nested')
        self.assertEqual((cells['Action'], cells['State']), ('ALLOW', 'Enabled'))


class FirewallReferenceOrderTests(TestCase):
    """Configured NSX order: category (Ethernet first), policy sequence, rule sequence, before paging."""
    setUp = FirewallReferenceFieldTests.setUp
    record = FirewallReferenceFieldTests.record
    rule = FirewallReferenceFieldTests.rule

    def ordered_rules(self):
        web = GP + 'web'
        # Inserted out of order; names would sort differently from NSX order.
        specs = [('a-app-late', 'Application', 'Late', 900, 5), ('b-env', 'Environment', 'Isolation', 10, 1),
                 ('c-app-early-2', 'Application', 'Early', 100, 20), ('d-ethernet', 'Ethernet', 'L2', 50, 1),
                 ('e-infra', 'Infrastructure', 'Shared', 1, 1), ('f-app-early-1', 'Application', 'Early', 100, 10),
                 ('g-emergency', 'Emergency', 'Quarantine', 5, 1), ('h-custom', 'Custom', 'Other', 1, 1),
                 ('i-app-no-order', 'Application', 'Unordered', None, None)]
        for name, category, policy, policy_sequence, sequence in specs:
            self.rule(name, sources=[web], destinations=['ANY'], category=category, policy=policy,
                      policy_sequence=policy_sequence, sequence=sequence)
        return ['d-ethernet', 'g-emergency', 'e-infra', 'b-env', 'f-app-early-1', 'c-app-early-2', 'a-app-late',
                'i-app-no-order', 'h-custom']

    def test_vm_references_follow_nsx_order_with_group_totals(self):
        expected = self.ordered_rules()
        result = confirmed_references.read(self.snapshot.pk, [GP + 'web'])
        self.assertEqual([item['name'] for item in result['items']], expected)
        early = result['items'][4]['nsx_order']
        self.assertEqual((early['category'], early['policy'], early['policy_sequence'], early['rule_sequence']),
                         ('Application', 'Early', 100, 10))
        self.assertEqual((early['category_count'], early['policy_count']), (4, 2))
        self.assertTrue(result['items'][5]['nsx_order']['policy_continued'])
        unordered = result['items'][7]['nsx_order']
        self.assertIsNone(unordered['policy_sequence'])
        self.assertFalse(result['items'][8]['nsx_order']['category_known'])

    def test_order_is_applied_before_paging(self):
        self.ordered_rules()
        page = confirmed_references.read(self.snapshot.pk, [GP + 'web'], page=0)
        self.assertEqual(len(page['items']), 9)
        for index in range(30):
            self.rule('zz-app-%02d' % index, sources=[GP + 'web'], destinations=['ANY'], policy='Early',
                      policy_sequence=100, sequence=1000 + index)
        first = confirmed_references.read(self.snapshot.pk, [GP + 'web'], page=0)
        second = confirmed_references.read(self.snapshot.pk, [GP + 'web'], page=1)
        self.assertEqual([item['name'] for item in first['items'][:4]], ['d-ethernet', 'g-emergency', 'e-infra', 'b-env'])
        head = second['items'][0]['nsx_order']
        self.assertEqual(head['category'], 'Application')
        self.assertTrue(head['category_continued'])
        self.assertTrue(head['policy_continued'])
        self.assertEqual(head['policy_count'], 32)
        self.assertEqual(second['items'][-1]['name'], 'h-custom')

    def test_group_and_tag_references_use_the_same_order(self):
        expected = self.ordered_rules()
        paths = [record['data__path'] for record in SnapshotRecord.objects.filter(snapshot=self.snapshot, view='dfw')
                 .order_by('ordinal').values('data__path')]
        group = SnapshotRecord.objects.get(snapshot=self.snapshot, view='inventory', compact__path=GP + 'web')
        group.data = dict(group.data, referenced_by=paths + ['/infra/domains/default/security-policies/gone/rules/missing'])
        group.save(update_fields=['data'])
        names = [item['name'] or item['path'].rsplit('/', 1)[-1] for item in object_references.read(self.snapshot.pk, GP + 'web')['items']]
        self.assertEqual(names, expected + ['missing'])  # rules absent from the snapshot sort last
        tag = self.record('tags', 'tier|web', {'firewall_references': []})
        from .models import SnapshotRelationship
        for position, path in enumerate(reversed(paths)):
            SnapshotRelationship.objects.create(record=tag, section='firewall_references', position=position,
                                                data={'name': path.rsplit('/', 1)[-1], 'path': path, 'via_group': GP + 'web'})
        SnapshotRecord.objects.filter(pk=tag.pk).update(relationships_ready=True)
        items = tag_relationships.read(self.snapshot.pk, tag.ordinal, 'firewall_references', context=True)['items']
        self.assertEqual([item['name'] for item in items], expected)

    def test_export_row_starts_with_category_and_sequences(self):
        self.ordered_rules()
        item = confirmed_references.read(self.snapshot.pk, [GP + 'web'], full=True)['items'][4]
        cells = dict(zip(COLUMNS, row(item)))
        self.assertEqual(COLUMNS[0], 'Rule category')
        self.assertEqual((cells['Rule category'], cells['Policy sequence'], cells['Rule sequence']), ('Application', '100', '10'))


class PolicyAppliedToReferenceTests(TestCase):
    """A policy's Applied To covers every rule in it; references must not miss those rules."""
    setUp = FirewallReferenceFieldTests.setUp
    record = FirewallReferenceFieldTests.record
    rule = FirewallReferenceFieldTests.rule

    def test_vm_references_include_rules_selected_only_by_policy_applied_to(self):
        scoped = self.rule('scoped', sources=['ANY'], destinations=['ANY'], policy='Scoped', policy_scope=[GP + 'admins'])
        self.rule('elsewhere', sources=['ANY'], destinations=['ANY'], policy='Elsewhere', policy_scope=[GP + 'db'])
        result = confirmed_references.read(self.snapshot.pk, [GP + 'admins'])
        item, = result['items']
        self.assertEqual((item['path'], item['via_groups'], item['matched_fields']), (scoped, [GP + 'admins'], ['Applied to (policy)']))
        self.assertEqual(item['rule_fields']['applied_to']['origin'], 'policy')
        detail = confirmed_references.read(self.snapshot.pk, [GP + 'admins'], path=scoped, detail=True)
        self.assertIn(GP + 'admins', confirmed_references.rule_groups(detail))

    def test_group_references_include_policy_applied_to_rules_once(self):
        both = self.rule('both', sources=[GP + 'admins'], destinations=['ANY'], policy='Scoped', policy_scope=[GP + 'admins'])
        only = self.rule('only-policy', sources=['ANY'], destinations=['ANY'], policy='Scoped', policy_scope=[GP + 'admins'],
                         sequence=20)
        self.rule('unrelated', sources=['ANY'], destinations=['ANY'], policy='Other', policy_scope=['ANY'])
        group = SnapshotRecord.objects.get(snapshot=self.snapshot, view='inventory', compact__path=GP + 'admins')
        policy_path = both.split('/rules/')[0]
        group.data = dict(group.data, referenced_by=[both, policy_path])  # as collected: the policy, not its rules
        group.save(update_fields=['data'])
        items = object_references.read(self.snapshot.pk, GP + 'admins')['items']
        self.assertEqual([item['path'] for item in items], [both, only])
        self.assertEqual(items[0]['matched_fields'], ['Source', 'Applied to (policy)'])
        self.assertEqual(items[1]['matched_fields'], ['Applied to (policy)'])
        self.assertIn('every rule in the policy', items[1]['reference_reason'])
        others = object_references.read(self.snapshot.pk, GP + 'admins', 'objects')['items']
        self.assertEqual([item['path'] for item in others], [policy_path])


class TagReferenceMergeTests(TestCase):
    """A rule reached through several tag-using groups is one row listing every group."""
    setUp = FirewallReferenceFieldTests.setUp
    record = FirewallReferenceFieldTests.record
    rule = FirewallReferenceFieldTests.rule

    def test_tag_rows_merge_edges_per_rule_and_keep_every_group_and_use(self):
        from .models import SnapshotRelationship
        both = self.rule('both', sources=[GP + 'web'], destinations=[GP + 'db'], category='Environment')
        single = self.rule('single', sources=[GP + 'web'], destinations=['ANY'], category='Ethernet')
        tag = self.record('tags', 'tier|web', {'firewall_references': []})
        edges = [(both, GP + 'web', 'condition'), (single, GP + 'web', 'condition'), (both, GP + 'db', 'assignment')]
        for position, (path, group, use) in enumerate(edges):
            SnapshotRelationship.objects.create(record=tag, section='firewall_references', position=position,
                                                data={'name': path.rsplit('/', 1)[-1], 'path': path, 'via_group': group, 'tag_use': use})
        SnapshotRecord.objects.filter(pk=tag.pk).update(relationships_ready=True)
        result = tag_relationships.read(self.snapshot.pk, tag.ordinal, 'firewall_references', context=True)
        self.assertEqual([item['name'] for item in result['items']], ['single', 'both'])
        merged = result['items'][1]
        self.assertEqual(merged['via_groups'], [GP + 'db', GP + 'web'])
        self.assertEqual(merged['matched_fields'], ['Source', 'Destination'])
        self.assertEqual(merged['reference_reason'], 'Group membership condition; tag also attached to a group (metadata)')
        self.assertEqual(merged['nsx_order']['category_count'], 1)
        self.assertNotIn('via_group', merged)
        self.assertEqual(result['items'][0]['reference_reason'], 'Group membership condition')
        cells = dict(zip(COLUMNS, row(merged)))
        self.assertEqual(cells['Matched in'], 'Source: Web; Destination: Db')
