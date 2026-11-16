from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('inventory', '0058_firewall_rule_proposals')]
    operations = [migrations.AddField(model_name='namingpolicy', name='removed_at', field=models.DateTimeField(null=True, blank=True))]
