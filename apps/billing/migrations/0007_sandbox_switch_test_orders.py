from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("billing", "0006_finish_products_orders_payments")]

    operations = [
        migrations.AddField(
            model_name="order",
            name="is_test",
            field=models.BooleanField(
                db_index=True, default=False, help_text="Paid through the sandbox: no money moved. Test orders can be deleted in the backoffice."
            ),
        ),
        migrations.AddField(
            model_name="paymentsettings",
            name="sandbox_enabled",
            field=models.BooleanField(
                default=False, help_text='Offers the payment method "Sandbox" on the order page. No money moves.', verbose_name="Sandbox switched on"
            ),
        ),
        migrations.AddField(
            model_name="paymentsettings",
            name="sandbox_testers",
            field=models.TextField(
                blank=True,
                help_text="E-mail addresses of the logins that see the sandbox, one per line. Empty = every customer, who can then give themselves any plan for free.",
                verbose_name="Who may use the sandbox",
            ),
        ),
    ]
