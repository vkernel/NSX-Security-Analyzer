from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('inventory', '0046_approved_nsx_deletion')]
    operations = [
        migrations.AddField(model_name='deletionitem', name='verified_at', field=models.DateTimeField(null=True)),
        migrations.AddField(model_name='deletionitem', name='verification', field=models.JSONField(default=dict)),
    ]
