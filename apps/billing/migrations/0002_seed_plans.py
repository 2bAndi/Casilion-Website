from django.db import migrations

# Working names and numbers. They are placeholders for decision D3 and can be edited in the admin.
PLANS = [
    dict(code="community", name="Community", price_eur_year=0, support_level="none", ticket_quota_month=0, bug_report_quota_month=2, self_service=True, rank=0,
         tagline="Free to use, without support."),
    dict(code="supporter", name="Supporter", price_eur_year=10, support_level="ai", ticket_quota_month=2, bug_report_quota_month=5, self_service=True, rank=1,
         tagline="Answers from Cassilion's support AI."),
    dict(code="professional", name="Professional", price_eur_year=100, support_level="ai_human", ticket_quota_month=5, bug_report_quota_month=10, self_service=True, rank=2,
         tagline="The support AI, and a person when you ask."),
    dict(code="business", name="Business", price_eur_year=1000, support_level="priority", ticket_quota_month=20, bug_report_quota_month=20, self_service=True, rank=3,
         tagline="Priority handling by the Cassilion team."),
    dict(code="enterprise", name="Enterprise", price_eur_year=0, support_level="priority", ticket_quota_month=100, bug_report_quota_month=100, self_service=False, rank=4,
         tagline="By agreement: contract, service levels, private channel."),
]


def seed(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for plan in PLANS:
        Plan.objects.update_or_create(code=plan["code"], defaults=plan)


def unseed(apps, schema_editor):
    apps.get_model("billing", "Plan").objects.filter(code__in=[p["code"] for p in PLANS]).delete()


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
