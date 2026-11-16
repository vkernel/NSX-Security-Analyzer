from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('inventory', '0049_execution_confirmation_policy')]
    operations = [
        migrations.AddField(model_name='findingrecalculation', name='paused', field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name='findingrecalculation', name='recalculated', field=models.PositiveIntegerField(default=0)),
    ]
