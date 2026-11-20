import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("billing", "0005_carry_data")]

    operations = [
        migrations.AlterField(
            model_name="plan",
            name="product",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="plans", to="billing.product"),
        ),
        migrations.RemoveField(model_name="order", name="old_plan"),
        migrations.RemoveField(model_name="order", name="old_subscription"),
        migrations.RemoveField(model_name="paymentevent", name="old_error"),
    ]
