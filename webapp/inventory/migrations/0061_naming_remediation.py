from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("inventory", "0060_deletion_execution_intent")]
    operations = [migrations.AddField(model_name="namingproposal", name="workflow", field=models.JSONField(default=dict, blank=True)), migrations.AddField(model_name="namingproposal", name="expires_at", field=models.DateField(null=True, blank=True))]
