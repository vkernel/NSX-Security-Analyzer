from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0069_reference_exports'),
    ]

    operations = [
        migrations.AddField(
            model_name='auditjob',
            name='not_before',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='auditjob',
            name='retry_of',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='retries', to='inventory.auditjob'),
        ),
        migrations.AddField(
            model_name='auditjob',
            name='worker_slot',
            field=models.CharField(blank=True, default='', max_length=24),
        ),
    ]
