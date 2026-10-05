from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('inventory', '0026_retention_defaults')]
    operations = [migrations.CreateModel(
        name='SnapshotVMRule',
        fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('path', models.TextField()), ('data', models.JSONField()),
                ('snapshot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='inventory.snapshot'))],
        options={'constraints': [models.UniqueConstraint(fields=('snapshot', 'path'), name='snapshot_vm_rule_unique')]},
    )]
