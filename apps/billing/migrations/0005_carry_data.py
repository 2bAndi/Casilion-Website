"""Carry existing rows over: the plans go to the product Cassilion, every order gets one line,
paid sandbox orders get their payment, and the webhook log becomes the payment history."""
from decimal import Decimal

from django.db import migrations

PRODUCTS = [
    dict(code="cassilion", name="Cassilion", tagline="The C++ database.", license_prefix="CAS", is_public=True, rank=0),
    dict(code="zuri-ai", name="Zuri-AI", tagline="The Cassandra variant.", license_prefix="ZUR", is_public=False, rank=1),
    dict(code="vakaluja", name="Vakaluja", tagline="AI workspace: the integration layer.", license_prefix="VAK", is_public=False, rank=2),
]
# The two new products start hidden, each with a free license; paid plans are added in the backoffice.
FREE_PLANS = [
    dict(product="zuri-ai", code="zuri-ai-community", name="Community", tagline="Free to use, without support."),
    dict(product="vakaluja", code="vakaluja-community", name="Community", tagline="Free to use, without support."),
]
KINDS = {"payment.succeeded": "payment_succeeded", "payment.failed": "payment_failed"}


def seed_products(apps, schema_editor):
    Product, Plan = apps.get_model("billing", "Product"), apps.get_model("billing", "Plan")
    by_code = {}
    for product in PRODUCTS:
        by_code[product["code"]], _ = Product.objects.update_or_create(code=product["code"], defaults=product)
    Plan.objects.filter(product__isnull=True).update(product=by_code["cassilion"])
    for plan in FREE_PLANS:
        values = {**plan, "product": by_code[plan["product"]], "price_eur_year": 0, "support_level": "none", "self_service": True, "rank": 0}
        Plan.objects.update_or_create(code=plan["code"], defaults=values)


def carry_orders(apps, schema_editor):
    Order, OrderLine, Payment = (apps.get_model("billing", name) for name in ("Order", "OrderLine", "Payment"))
    for order in Order.objects.select_related("old_plan", "old_subscription"):
        plan = order.old_plan
        OrderLine.objects.create(
            order=order,
            plan=plan,
            subscription=order.old_subscription,
            description=f"{plan.name} support plan, twelve months",
            quantity=1,
            unit_price=order.total,
            tax_rate=Decimal("19"),
            total=order.total,
            period_from=order.paid_at.date() if order.paid_at else None,
            period_to=order.old_subscription.period_end if order.paid_at else None,
        )
        if order.status == "paid":
            provider = order.old_subscription.provider or "sandbox"
            Payment.objects.create(order=order, provider=provider, provider_id=f"legacy-{order.ref}", amount=order.total, status="succeeded")
            order.payment_method = provider
            order.save(update_fields=["payment_method"])


def carry_events(apps, schema_editor):
    PaymentEvent = apps.get_model("billing", "PaymentEvent")
    for event in PaymentEvent.objects.order_by("pk"):
        kind = KINDS.get(event.provider_type, "ignored")
        PaymentEvent.objects.filter(pk=event.pk).update(source="webhook", kind=kind, outcome=event.old_error or "applied")



class Migration(migrations.Migration):

    dependencies = [("billing", "0004_products_orders_payments")]

    operations = [
        migrations.RunPython(seed_products, migrations.RunPython.noop),
        migrations.RunPython(carry_orders, migrations.RunPython.noop),
        migrations.RunPython(carry_events, migrations.RunPython.noop),
    ]
