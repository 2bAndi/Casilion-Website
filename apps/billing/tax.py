"""Which VAT rate applies to a sale.

One rule for now: every customer is charged German VAT at the rate set in the backoffice
(or none, if the company is a small business under § 19 UStG). Prices are final prices that
include it.

Cases this does not cover yet, to be decided with the tax adviser before selling abroad:
companies in other EU countries with a valid VAT ID (reverse charge), private customers in
other EU countries (VAT of their country above the EU-wide threshold, one-stop shop), and
customers outside the EU. They all get one more branch in :func:`rate_for`, and
``TaxProfile.vat_id_valid`` is where a validated VAT ID is kept.
"""
from decimal import Decimal


def rate_for(account, config=None) -> Decimal:
    from apps.billing.providers import config as load

    config = config or load()
    return Decimal("0") if config.small_business else Decimal(config.vat_rate)
