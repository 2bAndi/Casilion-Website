"""Settings for the Cassilion web platform.

Local-first: with no environment at all this runs on SQLite with development
secrets. Everything that matters in production comes from environment variables
(see .env.example).
"""
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

import django
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Read KEY=VALUE lines from .env without overriding real environment variables."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in (os.environ.get(name) or default).split(",") if item.strip()]


DEBUG = env_bool("DEBUG", True)

VAR_DIR = Path(env("CASSILION_VAR_DIR") or BASE_DIR / "var")
VAR_DIR.mkdir(parents=True, exist_ok=True)

SECRET_KEY = env("SECRET_KEY") or ("dev-only-secret-key-do-not-use-in-production" if DEBUG else None)
if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is off.")

SITE_URL = (env("SITE_URL") or "http://127.0.0.1:8000").rstrip("/")
# Keep search engines out (header, meta tag, closed robots.txt). On by default, as on
# Tango-Kalender: set SITE_NOINDEX=0 only when the site is meant to be found.
SITE_NOINDEX = env_bool("SITE_NOINDEX", True)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "127.0.0.1,localhost,[::1],web")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django_countries",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "apps.core",
    "apps.accounts",
    "apps.audit",
    "apps.billing",
    "apps.invoicing",
    "apps.mailing",
    "apps.licensing",
    "apps.downloads",
    "apps.support",
    "apps.mcp_server",
    "apps.backoffice",
]

MIDDLEWARE = [
    "apps.core.middleware.HealthzMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.RobotsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "apps.accounts.middleware.CurrentAccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": not DEBUG,
        "OPTIONS": {
            # In development templates are read from disk on every request, so an edit shows on
            # reload without restarting the server. (Django caches them otherwise, and only its own
            # runserver knows how to drop that cache; this project runs under uvicorn.)
            **({"loaders": ["django.template.loaders.filesystem.Loader", "django.template.loaders.app_directories.Loader"]} if DEBUG else {}),
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context.site",
            ],
        },
    },
]


def _database_from_url(url: str) -> dict:
    parts = urlparse(url)
    if parts.scheme in {"postgres", "postgresql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parts.path.lstrip("/"),
            "USER": unquote(parts.username or ""),
            "PASSWORD": unquote(parts.password or ""),
            "HOST": parts.hostname or "",
            "PORT": str(parts.port or ""),
        }
    if parts.scheme == "sqlite":
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": parts.path or str(VAR_DIR / "db.sqlite3")}
    raise ImproperlyConfigured(f"Unsupported DATABASE_URL scheme: {parts.scheme}")


if env("DATABASE_URL"):
    DATABASES = {"default": _database_from_url(env("DATABASE_URL"))}
elif env("POSTGRES_DB"):
    # The Docker Compose stack: the same three values configure the database container.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB"),
            "USER": env("POSTGRES_USER") or "",
            "PASSWORD": env("POSTGRES_PASSWORD") or "",
            "HOST": env("POSTGRES_HOST") or "db",
            "PORT": env("POSTGRES_PORT") or "5432",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": VAR_DIR / "db.sqlite3",
            # The web process and the job API write concurrently; wait instead of failing.
            "OPTIONS": {"timeout": 20},
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# --- Accounts (django-allauth): e-mail login, optional TOTP second factor ---------------
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_EMAIL_VERIFICATION = env("ACCOUNT_EMAIL_VERIFICATION") or ("optional" if DEBUG else "mandatory")
ACCOUNT_SIGNUP_FORM_CLASS = "apps.accounts.forms.SignupDetailsForm"
ACCOUNT_EMAIL_SUBJECT_PREFIX = "[Cassilion] "
LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "/account/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/"
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]
MFA_TOTP_ISSUER = "Cassilion"

# --- Internationalisation ---------------------------------------------------------------
LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("de", "Deutsch")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static files -----------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
# In the Docker image the files are collected at build time into a folder of the image
# (STATIC_ROOT=/app/staticfiles), because var/ is a mounted volume there.
STATIC_ROOT = Path(env("STATIC_ROOT") or VAR_DIR / "static")
STATIC_ROOT.mkdir(parents=True, exist_ok=True)
WHITENOISE_USE_FINDERS = DEBUG
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# --- E-mail -----------------------------------------------------------------------------
if env("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("EMAIL_HOST")
    EMAIL_PORT = int(env("EMAIL_PORT") or 25)
    EMAIL_HOST_USER = env("EMAIL_HOST_USER") or ""
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD") or ""
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
    EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
# Mails are delivered by the backend chosen above and written to the mail log on the way,
# so the backoffice can show a customer everything that was sent to them.
MAIL_DELIVERY_BACKEND = EMAIL_BACKEND
EMAIL_BACKEND = "apps.mailing.backend.LoggingBackend"
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL") or "Cassilion <noreply@cassilion.example>"
SERVER_EMAIL = env("SERVER_EMAIL") or DEFAULT_FROM_EMAIL
# Server errors (HTTP 500) are mailed to the operators when DEBUG is off.
ADMINS = [("Cassilion operator", address) for address in env_list("OPERATOR_EMAILS", "")]

# --- Security headers -------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SAMESITE = "Lax"
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    # Browsers then refuse plain HTTP for that long: set it only once the certificate is in place for good.
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS") or 0)

if django.VERSION >= (6, 0):
    # Built-in Content Security Policy: no third-party origins, no inline scripts.
    from django.utils.csp import CSP

    MIDDLEWARE.append("django.middleware.csp.ContentSecurityPolicyMiddleware")
    SECURE_CSP = {
        "default-src": [CSP.SELF],
        "script-src": [CSP.SELF],
        "style-src": [CSP.SELF],
        "img-src": [CSP.SELF, "data:"],
        "font-src": [CSP.SELF],
        "form-action": [CSP.SELF],
        "base-uri": [CSP.SELF],
        "frame-ancestors": [CSP.NONE],
    }

# --- Provider identification (imprint, privacy notice, terms, withdrawal policy) -----------
# The legal pages, the footer and the order mails read the company data from here, so a change
# of address or a new VAT ID is one line in .env and not an edit in eight texts.
COMPANY = {
    "NAME": env("COMPANY_NAME") or "Zuri-AI GmbH",
    "STREET": env("COMPANY_STREET") or "Lautzenhardtweg 10",
    "POSTAL_CODE": env("COMPANY_POSTAL_CODE") or "63069",
    "CITY": env("COMPANY_CITY") or "Offenbach am Main",
    "DIRECTOR": env("COMPANY_DIRECTOR") or "Jörn Henning Wunderlich",
    "REGISTER_COURT": env("COMPANY_REGISTER_COURT") or "Amtsgericht Offenbach am Main",
    "REGISTER_NO": env("COMPANY_REGISTER_NO") or "HRB 57727",
    # Shown in the imprint only when set. Either one satisfies § 5 (1) no. 6 DDG.
    "VAT_ID": env("COMPANY_VAT_ID") or "",
    "ECON_ID": env("COMPANY_ECON_ID") or "",
    "PHONE": env("COMPANY_PHONE") or "+49 151 11572555",
    "EMAIL": env("COMPANY_EMAIL") or "contact@zuri-ai.com",
    # Date shown as "last updated" on the legal pages; change it when a text changes.
    "LEGAL_AS_OF": env("LEGAL_AS_OF") or "2026-11-20",
}

# --- Cassilion ---------------------------------------------------------------------------
CASSILION = {
    "SITE_URL": SITE_URL,
    # Shared secret of the AI worker for the job API (the only door into the application zone).
    "AI_WORKER_TOKEN": env("AI_WORKER_TOKEN") or ("dev-worker-token" if DEBUG else ""),
    "JOB_LEASE_SECONDS": int(env("JOB_LEASE_SECONDS") or 600),
    # Products: the one every account gets its free license for at registration, and that support works for.
    "DEFAULT_PRODUCT": env("DEFAULT_PRODUCT") or "cassilion",
    # Payments. Stripe, PayPal and the bank account are entered in the backoffice (Payments -> Settings)
    # and stored encrypted with FIELD_ENCRYPTION_KEY. The sandbox (test purchases, no money moves) is
    # switched on and off there too; this value is only what a fresh installation starts with.
    "SANDBOX_PAYMENTS": env_bool("ALLOW_SANDBOX_PAYMENTS", DEBUG),
    "SANDBOX_WEBHOOK_SECRET": env("SANDBOX_WEBHOOK_SECRET") or ("dev-sandbox-secret" if DEBUG else ""),
    "GRACE_DAYS": int(env("GRACE_DAYS") or 14),
    # A renewal invoice goes out this many days before a paid term ends.
    "RENEWAL_NOTICE_DAYS": int(env("RENEWAL_NOTICE_DAYS") or 30),
    # A bank-transfer order waits this many days for the money before it is cancelled.
    "BANK_TRANSFER_DAYS": int(env("BANK_TRANSFER_DAYS") or 14),
    # Invoices and credit notes as issued (PDF with the e-invoice inside). Back this directory up.
    "INVOICE_DIR": Path(env("INVOICE_DIR") or VAR_DIR / "invoices"),
    # Licenses
    "LICENSE_KEY_DIR": Path(env("LICENSE_KEY_DIR") or VAR_DIR / "keys"),
    "LICENSE_KEY_ID": env("LICENSE_KEY_ID") or "cas-lic-dev",
    "LICENSE_AUTOCREATE_KEY": env_bool("LICENSE_AUTOCREATE_KEY", DEBUG),
    # Downloads and knowledge
    "RELEASES_DIR": Path(env("RELEASES_DIR") or VAR_DIR / "releases"),
    "DOWNLOAD_LINK_SECONDS": int(env("DOWNLOAD_LINK_SECONDS") or 600),
    "KNOWLEDGE_PUBLIC_DIR": Path(env("KNOWLEDGE_PUBLIC_DIR") or BASE_DIR / "knowledge" / "public"),
    "KNOWN_ISSUES_FILE": Path(env("KNOWN_ISSUES_FILE") or BASE_DIR / "knowledge" / "known_issues.json"),
    # Support
    "OPERATOR_EMAILS": env_list("OPERATOR_EMAILS", "ops@cassilion.example"),
    "REQUIRE_MFA_FOR_AGENT_TOKENS": env_bool("REQUIRE_MFA_FOR_AGENT_TOKENS", not DEBUG),
    # MCP transport: Host and Origin values the MCP endpoints accept.
    "MCP_ALLOWED_HOSTS": env_list("MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*,[::1]:*,testserver"),
    "MCP_ALLOWED_ORIGINS": env_list("MCP_ALLOWED_ORIGINS", "http://127.0.0.1:*,http://localhost:*,http://[::1]:*"),
}
if not DEBUG and not CASSILION["AI_WORKER_TOKEN"]:
    raise ImproperlyConfigured("AI_WORKER_TOKEN must be set when DEBUG is off.")
# Key for secrets stored in the database (payment keys). Falls back to SECRET_KEY; once keys
# are stored, changing either means entering the payment keys again.
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY") or ""

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL") or "INFO"},
    "loggers": {"mcp": {"level": "WARNING"}},
}
