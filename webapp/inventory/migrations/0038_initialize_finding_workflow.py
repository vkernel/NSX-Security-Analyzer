from django.db import migrations


def initialize(apps, schema_editor):
    # Existing acknowledgement is not an independent approval.
    Finding = apps.get_model('inventory', 'Finding')
    Finding.objects.using(schema_editor.connection.alias).filter(owner__isnull=False).update(workflow_state='owner_review')


class Migration(migrations.Migration):
    dependencies = [('inventory', '0037_finding_approvals_finding_change_ticket_and_more')]
    operations = [migrations.RunPython(initialize, migrations.RunPython.noop)]
