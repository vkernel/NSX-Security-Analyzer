"""Provision the initial administrator once, tracked by migration history."""
import logging

from django.contrib.auth.hashers import make_password
from django.db import migrations


def provision_administrator(apps, schema_editor):
    alias = schema_editor.connection.alias
    users = apps.get_model('auth', 'User').objects.using(alias)
    # Never reset, reactivate or promote an existing account, even if disabled.
    if users.filter(username__iexact='admin').exists() or users.filter(is_superuser=True).exists():
        logging.getLogger('inventory.bootstrap').info(
            'Initial administrator provisioning skipped: an administrator already exists')
        return
    user = users.create(
        username='admin', password=make_password('NSXSecurityA!'),
        is_active=True, is_staff=True, is_superuser=True,
    )
    apps.get_model('inventory', 'AuditEvent').objects.using(alias).create(
        action='administrator.provisioned', target_type='User', target_id=str(user.pk),
        details={'source': 'initial_database_migration'},
    )
    logging.getLogger('inventory.bootstrap').info(
        'Initial administrator provisioned; change its password after signing in')


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0016_auditevent_auditjob_debug_until_auditjob_diagnostics'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]
    operations = [migrations.RunPython(provision_administrator, migrations.RunPython.noop)]
