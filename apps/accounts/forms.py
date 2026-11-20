from django import forms
from django_countries import countries

from .models import Account, Address, TaxProfile
from .services import create_account


class SignupDetailsForm(forms.Form):
    """Extra signup fields. django-allauth mixes these into its own signup form."""

    full_name = forms.CharField(label="Your name", max_length=200)
    kind = forms.ChoiceField(
        label="You register as",
        choices=Account.Kind.choices,
        initial=Account.Kind.INDIVIDUAL,
        widget=forms.RadioSelect,
    )
    account_name = forms.CharField(
        label="Company name",
        max_length=200,
        required=False,
        help_text="Leave empty if you register as a private person.",
    )
    country = forms.ChoiceField(label="Country", choices=[("", "Select a country")] + list(countries))

    field_order = ["full_name", "email", "kind", "account_name", "country", "password1", "password2"]

    def clean(self):
        data = super().clean()
        if data.get("kind") == Account.Kind.COMPANY and not (data.get("account_name") or "").strip():
            self.add_error("account_name", "Enter the company name, or register as a private person.")
        return data

    def signup(self, request, user):
        user.full_name = self.cleaned_data["full_name"].strip()
        user.save(update_fields=["full_name"])
        kind = self.cleaned_data["kind"]
        name = self.cleaned_data["account_name"] if kind == Account.Kind.COMPANY else user.full_name
        create_account(user, kind=kind, name=name, country=self.cleaned_data["country"])


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ["name", "country"]


class AddressForm(forms.ModelForm):
    class Meta:
        model = Address
        fields = ["line1", "line2", "postal_code", "city", "region"]


class TaxProfileForm(forms.ModelForm):
    class Meta:
        model = TaxProfile
        fields = ["vat_id", "billing_email"]
