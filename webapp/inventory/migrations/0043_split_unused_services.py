from django.db import migrations, models
from django.core.validators import MaxValueValidator


def split_services(apps, schema_editor):
    alias = schema_editor.connection.alias
    Policy = apps.get_model('inventory', 'FindingPolicy')
    Policy.objects.using(alias).update(unused_service_days=models.F('unused_days'))
    services = models.Q(path__startswith='/infra/services/') | models.Q(path__startswith='/global-infra/services/')
    for name in ('Finding', 'SnapshotFindingEvidence'):
        apps.get_model('inventory', name).objects.using(alias).filter(services, kind='unused').update(kind='unused_service')
    Assessment = apps.get_model('inventory', 'SnapshotFindingAssessment')
    for row in Assessment.objects.using(alias).filter(services, kind='unused').iterator(chunk_size=500):
        row.kind = 'unused_service'
        row.assessment = dict(row.assessment, kind='unused_service')
        row.save(using=alias, update_fields=['kind', 'assessment'])
    Job = apps.get_model('inventory', 'FindingRecalculation')
    for job in Job.objects.using(alias).all().iterator():
        if 'unused' in job.kinds and 'unused_service' not in job.kinds:
            job.kinds = job.kinds + ['unused_service']
            job.save(using=alias, update_fields=['kinds'])


def merge_services(apps, schema_editor):
    alias = schema_editor.connection.alias
    for name in ('Finding', 'SnapshotFindingEvidence'):
        apps.get_model('inventory', name).objects.using(alias).filter(kind='unused_service').update(kind='unused')
    for row in apps.get_model('inventory', 'SnapshotFindingAssessment').objects.using(alias).filter(kind='unused_service').iterator(chunk_size=500):
        row.kind = 'unused'
        row.assessment = dict(row.assessment, kind='unused')
        row.save(using=alias, update_fields=['kind', 'assessment'])
    for job in apps.get_model('inventory', 'FindingRecalculation').objects.using(alias).all().iterator():
        if 'unused_service' in job.kinds:
            job.kinds = sorted(set('unused' if k == 'unused_service' else k for k in job.kinds))
            job.save(using=alias, update_fields=['kinds'])


class Migration(migrations.Migration):
    dependencies = [('inventory', '0042_alter_finding_qualification_transientemptinesspolicy')]
    operations = [
        migrations.AddField(model_name='findingpolicy', name='unused_service_days',
                            field=models.PositiveIntegerField(default=30, validators=[MaxValueValidator(3650)])),
        migrations.RunPython(split_services, merge_services),
    ]
