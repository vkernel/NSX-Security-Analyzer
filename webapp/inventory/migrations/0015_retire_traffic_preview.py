from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0014_ipfix_diagnostics")]
    # Retire ORM access without deleting existing setup audit records. Historical
    # migrations and tables remain available for a future supported reintroduction.
    operations = [migrations.SeparateDatabaseAndState(
        database_operations=[
            # Archive identifiers without live foreign keys: retired records must
            # not prevent deletion of environments or users.
            migrations.AlterField(model_name="ipfixexporter", name="environment",
                                  field=models.BigIntegerField(db_column="environment_id")),
            migrations.AlterField(model_name="ipfixsetup", name="environment",
                                  field=models.BigIntegerField(db_column="environment_id")),
            migrations.AlterField(model_name="ipfixsetup", name="actor",
                                  field=models.IntegerField(db_column="actor_id", null=True)),
        ],
        state_operations=[
            migrations.DeleteModel(name="IPFIXSetup"),
            migrations.DeleteModel(name="IPFIXExporter"),
            migrations.DeleteModel(name="IPFIXReceiver"),
        ],
    )]
