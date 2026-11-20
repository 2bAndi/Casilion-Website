import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("licensing", "0003_fill_entitlement_product")]

    operations = [
        migrations.AlterField(
            model_name="entitlement",
            name="product",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="entitlements", to="billing.product"),
        ),
    ]
