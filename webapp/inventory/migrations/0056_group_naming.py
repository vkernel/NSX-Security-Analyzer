from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('inventory', '0055_naming_policies'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.RemoveConstraint(model_name='namingproposal', name='naming_proposal_unique'),
        migrations.RenameField(model_name='namingproposal', old_name='vm_identity', new_name='group_path'),
        # Preserve legacy VM proposals, but never apply them to groups.
        migrations.AddField(model_name='namingproposal', name='object_type', field=models.CharField(default='vm', max_length=12)),
        migrations.AlterField(model_name='namingproposal', name='object_type', field=models.CharField(default='group', max_length=12)),
        migrations.AddField(model_name='namingproposal', name='exception_policy_stamp', field=models.CharField(blank=True, max_length=64)),
        migrations.AddField(model_name='namingproposal', name='reviewed_by', field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
        migrations.AddConstraint(model_name='namingproposal', constraint=models.UniqueConstraint(fields=('environment', 'object_type', 'group_path'), name='naming_proposal_unique')),
    ]
