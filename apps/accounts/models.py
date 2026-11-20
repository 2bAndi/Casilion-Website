"""Who the customer is.

A *user* is a login. An *account* is the customer: a private person or a company.
Licenses, subscriptions and tickets belong to the account, never to the login, so
a company can add or remove staff without losing anything.
"""
import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone
from django_countries.fields import CountryField


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("An e-mail address is required.")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra["is_staff"] = True
        extra["is_superuser"] = True
        return self._create(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=200, blank=True)
    locale = models.CharField(max_length=8, default="en")
    is_staff = models.BooleanField(default=False, help_text="Member of the Cassilion team (backoffice access).")
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    def __str__(self):
        return self.email

    def get_full_name(self):
        return self.full_name or self.email

    def get_short_name(self):
        return (self.full_name or self.email).split(" ")[0]


class Account(models.Model):
    class Kind(models.TextChoices):
        INDIVIDUAL = "individual", "Private person"
        COMPANY = "company", "Company"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.INDIVIDUAL)
    name = models.CharField(max_length=200, help_text="Company name, or the person's name for a private customer.")
    country = CountryField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    members = models.ManyToManyField(User, through="Membership", related_name="accounts")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        BILLING = "billing", "Billing"
        TECHNICAL = "technical", "Technical"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.OWNER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "account"], name="unique_membership")]
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.user} in {self.account} ({self.role})"


class Address(models.Model):
    """Billing address. Stored as entered; countries differ too much to force one shape."""

    account = models.OneToOneField(Account, on_delete=models.CASCADE, related_name="address")
    line1 = models.CharField("Address line 1", max_length=200, blank=True)
    line2 = models.CharField("Address line 2", max_length=200, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    city = models.CharField(max_length=120, blank=True)
    region = models.CharField("State, province or region", max_length=120, blank=True)

    def __str__(self):
        return ", ".join(p for p in [self.line1, self.postal_code, self.city] if p)


class TaxProfile(models.Model):
    account = models.OneToOneField(Account, on_delete=models.CASCADE, related_name="tax_profile")
    vat_id = models.CharField("VAT ID", max_length=32, blank=True)
    vat_id_valid = models.BooleanField(null=True, blank=True, help_text="Result of the last validation; empty = not checked.")
    billing_email = models.EmailField(blank=True, help_text="Where invoices go, if different from the login.")

    def __str__(self):
        return self.vat_id or "no VAT ID"
