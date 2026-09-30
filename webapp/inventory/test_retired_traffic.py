from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class RetiredTrafficMigrationTests(TransactionTestCase):
    def test_archive_survives_and_does_not_block_environment_deletion(self):
        executor = MigrationExecutor(connection)
        before = [('inventory', '0014_ipfix_diagnostics')]
        after = [('inventory', '0015_retire_traffic_preview')]
        executor.migrate(before)
        try:
            apps = executor.loader.project_state(before).apps
            env = apps.get_model('inventory', 'Environment').objects.create(
                name='Synthetic migration test', manager='https://manager.example.invalid')
            exporter = apps.get_model('inventory', 'IPFIXExporter').objects.create(
                environment=env, address='192.0.2.10')
            setup = apps.get_model('inventory', 'IPFIXSetup').objects.create(environment=env)
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            new_apps = executor.loader.project_state(after).apps
            with self.assertRaises(LookupError):
                new_apps.get_model('inventory', 'IPFIXExporter')
            new_apps.get_model('inventory', 'Environment').objects.get(pk=env.pk).delete()
            with connection.cursor() as cursor:
                for table, pk in [('inventory_ipfixexporter', exporter.pk), ('inventory_ipfixsetup', setup.pk)]:
                    cursor.execute('SELECT environment_id FROM ' + table + ' WHERE id = %s', [pk])
                    self.assertEqual(cursor.fetchone()[0], env.pk)
                    cursor.execute('DELETE FROM ' + table + ' WHERE id = %s', [pk])
        finally:
            final = MigrationExecutor(connection)
            final.migrate(final.loader.graph.leaf_nodes())
