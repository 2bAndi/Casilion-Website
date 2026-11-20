from django.db import migrations


def fill(apps, schema_editor):
    Entitlement = apps.get_model("licensing", "Entitlement")
    for entitlement in Entitlement.objects.select_related("plan").order_by("pk"):
        Entitlement.objects.filter(pk=entitlement.pk).update(product_id=entitlement.plan.product_id)


class Migration(migrations.Migration):

    dependencies = [("licensing", "0002_entitlement_product"), ("billing", "0005_carry_data")]

    operations = [migrations.RunPython(fill, migrations.RunPython.noop)]
