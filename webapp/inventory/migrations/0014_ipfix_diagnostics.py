from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('inventory', '0013_ipfix_setup')]
    operations = [
        migrations.AddField(model_name='ipfixreceiver', name='diagnostics', field=models.JSONField(default=list)),
        migrations.AddField(model_name='ipfixreceiver', name='queue_drops', field=models.PositiveBigIntegerField(default=0)),
        migrations.AddField(model_name='ipfixreceiver', name='socket_drops', field=models.PositiveBigIntegerField(default=0)),
        migrations.AddField(model_name='ipfixreceiver', name='socket_drop_monitoring', field=models.BooleanField(default=False)),
    ]
