from io import StringIO
from django.core.management import call_command
from django.test import TestCase
from .models import Environment, Snapshot, SnapshotRecord, SnapshotSearchDocument
from .storage import share_search, clean_search_documents
from .tests import sample_report


class StorageTests(TestCase):
    def setUp(self):
        self.env = Environment.objects.create(name='Storage', slug='storage', manager='https://example.test')
        from django.utils import timezone
        self.snapshot = Snapshot.objects.create(environment=self.env, generated_at=timezone.now(), report=sample_report())

    def row(self, ordinal, text):
        return SnapshotRecord(snapshot=self.snapshot, ordinal=ordinal, view='inventory', name='Group', sort_name='group', compact={}, data={'retained':'evidence'}, columns=[], sort_values={}, search_basic='Group', search_evidence=text)

    def test_exact_text_shared_without_changing_search_semantics(self):
        rows = [self.row(1, 'alpha beta\nGamma'), self.row(2, 'alpha beta\nGamma'), self.row(3, 'alpha  beta')]
        share_search(rows)
        SnapshotRecord.objects.bulk_create(rows)
        self.assertEqual(SnapshotSearchDocument.objects.count(), 2)
        self.assertEqual(rows[0].search_document_id, rows[1].search_document_id)
        self.assertEqual(SnapshotRecord.objects.filter(search_document__text__icontains='alpha beta').count(), 2)
        self.assertEqual(rows[0].search_evidence, '')
        self.assertEqual(clean_search_documents(), 0)
        SnapshotRecord.objects.all().delete()
        self.assertEqual(clean_search_documents(), 2)

    def test_compaction_dry_run_bounded_resume_preserves_evidence(self):
        SnapshotRecord.objects.bulk_create([self.row(i, 'same text') for i in range(1, 4)])
        out = StringIO()
        call_command('compact_snapshot_storage', batch_size=2, stdout=out)
        self.assertEqual(SnapshotSearchDocument.objects.count(), 0)
        call_command('compact_snapshot_storage', apply=True, batch_size=2, stdout=out)
        self.assertEqual(SnapshotRecord.objects.filter(search_document__isnull=False).count(), 2)
        call_command('compact_snapshot_storage', apply=True, batch_size=2, stdout=out)
        self.assertEqual(SnapshotRecord.objects.filter(search_document__isnull=False).count(), 3)
        self.assertEqual(SnapshotSearchDocument.objects.count(), 1)
        self.assertTrue(all(r.data == {'retained':'evidence'} for r in SnapshotRecord.objects.all()))

    def test_shared_tag_data_rehydrates_exact_export_evidence(self):
        import json
        from .models import SnapshotPresentation
        from .snapshot_index import tag_context
        from .vm_relationships import ExpandedVMData
        metadata = {'condition_sets': [[0, 1]], 'conditions': [{'name':'first'}, {'name':'second'}],
                    'firewall_rules':[{'path':'/rules/one','name':'One'}], 'firewall_reference_note':'coverage'}
        SnapshotPresentation.objects.create(snapshot=self.snapshot, shell={}, tag_evidence=metadata)
        row = self.row(1, 'first second One')
        row.view = 'tags'
        original = {'name':'Tag', 'condition_evidence_set':0, 'review_condition_set':0,
                    'firewall_references':[{'rule':0}], 'path':'/tags/a'}
        extra = json.loads(json.dumps(tag_context(original, metadata)))
        row.data = dict(original, resolved_tag_evidence=extra)
        row.save()
        call_command('compact_snapshot_storage', apply=True, stdout=StringIO())
        row.refresh_from_db()
        self.assertNotIn('resolved_tag_evidence', row.data)
        expanded = SnapshotRecord.objects.filter(pk=row.pk).annotate(expanded=ExpandedVMData()).values_list('expanded', flat=True).get()
        self.assertEqual(expanded, dict(original, resolved_tag_evidence=extra))

    def test_daily_measurements_are_bounded_and_idempotent(self):
        from .storage import measure_storage, storage_trend
        from .models import StorageMeasurement
        measure_storage()
        measure_storage()
        self.assertEqual(StorageMeasurement.objects.count(), 1)
        self.assertEqual(storage_trend()['environments'][0]['snapshots'], 1)

    def test_compaction_rejects_changed_shared_tag_evidence(self):
        from django.core.management.base import CommandError
        from .models import SnapshotPresentation
        SnapshotPresentation.objects.create(snapshot=self.snapshot, shell={}, tag_evidence={'firewall_reference_note':'new'})
        row = self.row(1, 'original text')
        row.view = 'tags'
        row.data = {'resolved_tag_evidence': {'firewall_reference_note':'old'}}
        row.save()
        with self.assertRaises(CommandError):
            call_command('compact_snapshot_storage', apply=True, stdout=StringIO())
        row.refresh_from_db()
        self.assertEqual(row.search_evidence, 'original text')
        self.assertEqual(row.data['resolved_tag_evidence']['firewall_reference_note'], 'old')

    def test_tag_rehydration_with_inline_conditions_and_no_rules(self):
        from .models import SnapshotPresentation
        from .vm_relationships import ExpandedVMData
        SnapshotPresentation.objects.create(snapshot=self.snapshot, shell={}, tag_evidence={'conditions':[{'name':'condition'}]})
        row = self.row(1, 'condition')
        row.view = 'tags'
        row.data = {'condition_evidence':[0, {'inline':'kept'}], '_shared_tag_evidence':True}
        row.save()
        data = SnapshotRecord.objects.filter(pk=row.pk).annotate(expanded=ExpandedVMData()).values_list('expanded', flat=True).get()
        self.assertEqual(data['resolved_tag_evidence'], {'conditions':{'0':{'name':'condition'}}, 'condition_sets':{}, 'firewall_rules':{}, 'firewall_reference_note':''})
        self.assertEqual(data['condition_evidence'], [0, {'inline':'kept'}])
