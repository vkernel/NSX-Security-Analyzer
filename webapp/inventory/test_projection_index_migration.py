"""PostgreSQL recovery after non-atomic projection-index migration failures."""
import importlib
import threading
import time
from unittest.mock import patch

from django.db import connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase
from django.utils import timezone

from .models import Environment, Snapshot, SnapshotRecord

migration_module = importlib.import_module('inventory.migrations.0063_snapshot_projection_indexes')


class ProjectionIndexMigrationTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Concurrent PostgreSQL index recovery')
        executor = MigrationExecutor(connection)
        self.before = executor.loader.project_state([('inventory', '0062_snapshot_analysis_cache')])
        self.leaves = executor.loader.graph.leaf_nodes()
        self.migration = migration_module.Migration('0063_snapshot_projection_indexes', 'inventory')
        self.drop_indexes()

    def drop_indexes(self):
        with connection.cursor() as cursor:
            for operation in self.migration.operations:
                cursor.execute('DROP INDEX CONCURRENTLY IF EXISTS ' + connection.ops.quote_name(operation.index.name))

    def apply(self):
        with connection.schema_editor(atomic=False) as editor:
            return self.migration.apply(self.before.clone(), editor)

    def index_state(self, name):
        with connection.cursor() as cursor:
            cursor.execute('SELECT indexrelid, indisvalid, indisready FROM pg_index WHERE indexrelid = to_regclass(%s)', [name])
            return cursor.fetchone()

    def tearDown(self):
        if connection.vendor == 'postgresql':
            self.drop_indexes()
            self.apply()
        super().tearDown()

    def test_fresh_creation_and_retry_keep_valid_index_identity(self):
        self.apply()
        original = [self.index_state(op.index.name) for op in self.migration.operations]
        self.assertTrue(all(row and row[1:] == (True, True) for row in original))
        self.apply()
        self.assertEqual(original, [self.index_state(op.index.name) for op in self.migration.operations])

    def test_first_index_survives_second_index_failure_and_rows_are_preserved(self):
        env = Environment.objects.create(name='Migration fixture', slug='index-retry', manager='https://offline.invalid')
        snapshot = Snapshot.objects.create(environment=env, generated_at=timezone.now(), report={})
        record = SnapshotRecord.objects.create(snapshot=snapshot, ordinal=0, view='inventory', name='Keep this group', sort_name='keep this group', compact={}, data={'kind':'group','keep':'unchanged'}, columns=[], sort_values={}, search_basic='', search_evidence='')
        with connection.schema_editor(atomic=False) as editor:
            add = editor.add_index
            def fail_second(model, index, **kwargs):
                if index.name == 'snapshot_rule_projection':
                    raise RuntimeError('Simulated migration interruption')
                return add(model, index, **kwargs)
            with patch.object(editor, 'add_index', side_effect=fail_second):
                with self.assertRaisesRegex(RuntimeError, 'Simulated migration interruption'):
                    self.migration.apply(self.before.clone(), editor)
        first = self.index_state('snapshot_group_projection')
        self.assertIsNotNone(first)
        self.assertIsNone(self.index_state('snapshot_rule_projection'))
        self.apply()
        self.assertEqual(first, self.index_state('snapshot_group_projection'))
        record.refresh_from_db()
        self.assertEqual(record.data, {'kind':'group','keep':'unchanged'})
        self.assertEqual(self.index_state('snapshot_rule_projection')[1:], (True, True))

    def test_real_invalid_index_from_cancelled_concurrent_build_is_rebuilt(self):
        errors = []
        operation = self.migration.operations[0]
        def create_index():
            db = connections['default']
            try:
                with db.schema_editor(atomic=False) as editor:
                    model = self.before.apps.get_model('inventory', 'SnapshotRecord')
                    editor.add_index(model, operation.index, concurrently=True)
            except Exception as error:
                errors.append(error)
            finally:
                db.close()
        # Hold an old writer so PostgreSQL publishes the index catalog entry,
        # then waits before its scan. Cancel only that disposable test backend.
        writer = connection.copy(alias='index-migration-writer')
        writer.ensure_connection()
        writer.set_autocommit(False)
        thread = threading.Thread(target=create_index, daemon=True)
        try:
            with writer.cursor() as cursor:
                cursor.execute('LOCK TABLE inventory_snapshotrecord IN ROW EXCLUSIVE MODE')
            thread.start()
            deadline = time.monotonic() + 10
            pid = None
            while time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pid FROM pg_stat_progress_create_index WHERE index_relid = to_regclass('snapshot_group_projection')")
                    row = cursor.fetchone()
                if row:
                    pid = row[0]
                    break
                time.sleep(0.02)
            self.assertIsNotNone(pid, 'Concurrent index never reached the catalog publication stage')
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_cancel_backend(%s)', [pid])
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        finally:
            writer.rollback()
            writer.close()
            thread.join(timeout=10)
        self.assertTrue(errors)
        invalid = self.index_state(operation.index.name)
        self.assertIsNotNone(invalid)
        self.assertFalse(invalid[1])
        self.apply()
        rebuilt = self.index_state(operation.index.name)
        self.assertNotEqual(invalid[0], rebuilt[0])
        self.assertEqual(rebuilt[1:], (True, True))

    def test_different_predicate_is_not_silently_reused_or_removed(self):
        with connection.cursor() as cursor:
            cursor.execute("CREATE INDEX snapshot_group_projection ON inventory_snapshotrecord (snapshot_id, ordinal) WHERE data->>'kind' = 'not-the-projection'")
        original = self.index_state('snapshot_group_projection')
        with self.assertRaisesRegex(RuntimeError, 'does not match.*migration 0063'):
            self.apply()
        self.assertEqual(original, self.index_state('snapshot_group_projection'))

    def test_relation_on_another_table_is_not_removed(self):
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE projection_index_other_fixture (snapshot_id integer, ordinal integer)')
            cursor.execute('CREATE INDEX snapshot_group_projection ON projection_index_other_fixture (snapshot_id, ordinal)')
        try:
            original = self.index_state('snapshot_group_projection')
            with self.assertRaisesRegex(RuntimeError, 'does not match.*migration 0063'):
                self.apply()
            self.assertEqual(original, self.index_state('snapshot_group_projection'))
        finally:
            with connection.cursor() as cursor:
                cursor.execute('DROP TABLE projection_index_other_fixture')

    def test_reverse_can_resume_after_an_index_was_already_removed(self):
        after = self.apply()
        with connection.cursor() as cursor:
            cursor.execute('DROP INDEX CONCURRENTLY snapshot_rule_projection')
        with connection.schema_editor(atomic=False) as editor:
            self.migration.unapply(after.clone(), editor)
            self.migration.unapply(after.clone(), editor)
        self.assertIsNone(self.index_state('snapshot_group_projection'))
        self.assertIsNone(self.index_state('snapshot_rule_projection'))

    def test_migrate_command_resumes_unrecorded_partial_migration(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('inventory', '0062_snapshot_analysis_cache')])
        try:
            from django.db.backends.postgresql.schema import DatabaseSchemaEditor
            add = DatabaseSchemaEditor.add_index
            def fail_second(editor, model, index, **kwargs):
                if index.name == 'snapshot_rule_projection':
                    raise RuntimeError('Simulated second-index failure')
                return add(editor, model, index, **kwargs)
            with patch.object(DatabaseSchemaEditor, 'add_index', new=fail_second):
                with self.assertRaisesRegex(RuntimeError, 'Simulated second-index failure'):
                    MigrationExecutor(connection).migrate(self.leaves)
            self.assertNotIn(('inventory', '0063_snapshot_projection_indexes'), MigrationRecorder(connection).applied_migrations())
            first = self.index_state('snapshot_group_projection')
            MigrationExecutor(connection).migrate(self.leaves)
            self.assertIn(('inventory', '0063_snapshot_projection_indexes'), MigrationRecorder(connection).applied_migrations())
            self.assertEqual(first, self.index_state('snapshot_group_projection'))
        finally:
            MigrationExecutor(connection).migrate(self.leaves)
