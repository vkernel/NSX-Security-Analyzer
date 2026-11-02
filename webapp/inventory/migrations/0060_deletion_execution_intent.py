from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('inventory', '0059_naming_policy_removal')]
    operations = [migrations.AddField(model_name='deletionbatch', name='execution_intent', field=models.JSONField(default=dict, blank=True))]
