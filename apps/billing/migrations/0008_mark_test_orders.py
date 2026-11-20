"""Data only, apart from 0007: PostgreSQL does not build an index on a table in the transaction that has just written to it."""
from django.conf import settings
from django.db import migrations


def carry(apps, schema_editor):
    """The sandbox stays as it was on this installation, and what was bought there is a test."""
    PaymentSettings, Order = apps.get_model("billing", "PaymentSettings"), apps.get_model("billing", "Order")
    PaymentSettings.objects.update(sandbox_enabled=bool(settings.CASSILION["SANDBOX_PAYMENTS"]))
    Order.objects.filter(payment_method="sandbox").update(is_test=True)
    Order.objects.filter(payments__provider="sandbox").update(is_test=True)


class Migration(migrations.Migration):

    dependencies = [("billing", "0007_sandbox_switch_test_orders")]

    operations = [migrations.RunPython(carry, migrations.RunPython.noop)]
