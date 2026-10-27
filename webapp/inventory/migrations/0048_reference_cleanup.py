from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('inventory', '0047_deletion_verification')]
    operations = [migrations.AddField(model_name='deletionitem', name=name, field=models.JSONField(default=list))
                  for name in ('cleanup_plan', 'cleanup_approvals', 'cleanup_results')]
