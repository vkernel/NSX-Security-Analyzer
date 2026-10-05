from django.db import migrations


def enable_retention(apps, schema_editor):
    policy = apps.get_model("inventory", "RetentionPolicy")
    policy.objects.using(schema_editor.connection.alias).filter(pk=1, enabled=False).update(enabled=True)


class Migration(migrations.Migration):
    dependencies = [("inventory", "0027_snapshotvmrule")]

    operations = [migrations.RunPython(enable_retention, migrations.RunPython.noop)]
