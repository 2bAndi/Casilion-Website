import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("licensing", "0001_initial"),
        ("billing", "0004_products_orders_payments"),
    ]

    operations = [
        migrations.AddField(
            model_name="entitlement",
            name="product",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="entitlements", to="billing.product"),
        ),
    ]
