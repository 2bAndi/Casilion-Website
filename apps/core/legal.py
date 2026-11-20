"""Legal pages, and the two functions German law asks of a shop that sells to consumers.

- The texts (imprint, privacy notice, terms, withdrawal policy) are templates in
  templates/legal/<slug>.<language>.html. The German version is the binding one; the English
  version is a translation for readers of the English site.
- "Withdraw from contract" is the electronic withdrawal function of § 356a BGB (in force since
  19 June 2026): a clearly labelled link on every page, a form that asks for nothing but name,
  contract and an address for the receipt, a confirm button, and an immediate confirmation of
  receipt with date and time.
- "Cancel contracts here" is the cancellation button of § 312k BGB for running plans.

Both functions work without a login, because a customer who cannot sign in must still be able
to use them.
"""
from datetime import timedelta

from django import forms
from django.conf import settings
from django.core.mail import EmailMessage
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.audit.log import audit

from .models import ContractNotice

LANGUAGES = ("de", "en")

LEGAL_PAGES = {
    "imprint": {"de": "Impressum", "en": "Legal notice"},
    "privacy": {"de": "Datenschutzerklärung", "en": "Privacy notice"},
    "terms": {"de": "Allgemeine Geschäftsbedingungen", "en": "Terms and conditions"},
    "withdrawal": {"de": "Widerrufsbelehrung", "en": "Right of withdrawal"},
}

# At most this many declarations per e-mail address and hour, and in total per hour. The forms are
# public and send a mail to the address entered, so they must not become a way to mail strangers.
MAX_PER_ADDRESS_PER_HOUR = 3
MAX_PER_HOUR = 60


def language_of(request) -> str:
    """?lang=de|en wins, then the language of the visit, then English like the rest of the site."""
    asked = (request.GET.get("lang") or request.POST.get("lang") or "").lower()
    if asked in LANGUAGES:
        return asked
    current = (getattr(request, "LANGUAGE_CODE", "") or "")[:2].lower()
    return current if current in LANGUAGES else "en"


def page(request, slug):
    if slug not in LEGAL_PAGES:
        raise Http404
    lang = language_of(request)
    return render(
        request,
        "core/legal.html",
        {
            "slug": slug,
            "lang": lang,
            "title": LEGAL_PAGES[slug][lang],
            "body_template": f"legal/{slug}.{lang}.html",
            "other_lang": "en" if lang == "de" else "de",
        },
    )


def documents(lang: str) -> list[tuple[str, str, str]]:
    """Terms and withdrawal policy as stand-alone HTML files, to attach to the order mail.

    The law wants the customer to receive both on a durable medium; a link is not enough.
    """
    lang = lang if lang in LANGUAGES else "en"
    names = {"terms": {"de": "AGB", "en": "Terms"}, "withdrawal": {"de": "Widerrufsbelehrung", "en": "Right-of-withdrawal"}}
    files = []
    for slug in ("terms", "withdrawal"):
        html = render_to_string(
            "legal/_document.html",
            {
                "lang": lang,
                "title": LEGAL_PAGES[slug][lang],
                "body_template": f"legal/{slug}.{lang}.html",
                "company": settings.COMPANY,
                "SITE_URL": settings.CASSILION["SITE_URL"],
                "base": settings.CASSILION["SITE_URL"],
            },
        )
        files.append((f"Cassilion-{names[slug][lang]}.html", html, "text/html"))
    return files


# --- the two forms -------------------------------------------------------------------------

TEXT = {
    "withdrawal": {
        "de": {
            "title": "Vertrag widerrufen",
            "intro": "Hier widerrufen Sie als Verbraucherin oder Verbraucher einen Vertrag, den Sie auf dieser Website geschlossen haben. Sie müssen keinen Grund nennen.",
            "name": "Ihr Name",
            "contract": "Vertrag, den Sie widerrufen",
            "contract_help": "Bestellnummer (beginnt mit ord_) oder Tarif und Bestelldatum. Wenn Sie nur einen Teil widerrufen, nennen Sie den Teil.",
            "email": "E-Mail-Adresse für die Eingangsbestätigung",
            "submit": "Widerruf bestätigen",
            "after": "Nach dem Absenden erhalten Sie sofort eine Eingangsbestätigung per E-Mail mit dem Inhalt Ihrer Erklärung sowie Datum und Uhrzeit des Eingangs.",
            "done_title": "Ihr Widerruf ist eingegangen",
            "done_lead": "Wir haben Ihre Widerrufserklärung erhalten. Diese Seite und die E-Mail an Sie bestätigen den Eingang.",
            "declaration": "Hiermit widerrufe ich den von mir abgeschlossenen Vertrag.",
            "mail_subject": "Eingang Ihres Widerrufs",
        },
        "en": {
            "title": "Withdraw from contract",
            "intro": "As a consumer, you can withdraw here from a contract you concluded on this website. You do not need to give a reason.",
            "name": "Your name",
            "contract": "Contract you withdraw from",
            "contract_help": "Order number (starts with ord_), or plan and order date. If you withdraw from only a part, name the part.",
            "email": "E-mail address for the confirmation of receipt",
            "submit": "Confirm withdrawal",
            "after": "After sending you receive a confirmation of receipt by e-mail at once, with the content of your declaration and the date and time we received it.",
            "done_title": "We have received your withdrawal",
            "done_lead": "Your declaration of withdrawal has reached us. This page and the e-mail to you confirm its receipt.",
            "declaration": "I hereby withdraw from the contract I concluded.",
            "mail_subject": "Receipt of your withdrawal",
        },
    },
    "cancellation": {
        "de": {
            "title": "Vertrag kündigen",
            "intro": "Hier kündigen Sie einen laufenden Tarif. Eine Anmeldung ist dafür nicht nötig.",
            "name": "Ihr Name",
            "contract": "Vertrag, den Sie kündigen",
            "contract_help": "Bestellnummer (beginnt mit ord_) oder Tarif und der Name des Kundenkontos.",
            "email": "E-Mail-Adresse für die Bestätigung",
            "cancel_type": "Art der Kündigung",
            "ordinary": "Ordentliche Kündigung",
            "extraordinary": "Außerordentliche Kündigung aus wichtigem Grund",
            "cancel_reason": "Grund der außerordentlichen Kündigung",
            "cancel_reason_help": "Nur bei einer außerordentlichen Kündigung.",
            "cancel_at": "Zu wann soll der Vertrag enden?",
            "cancel_at_help": "Leer lassen für den frühestmöglichen Zeitpunkt, sonst ein Datum.",
            "earliest": "zum frühestmöglichen Zeitpunkt",
            "submit": "Jetzt kündigen",
            "after": "Nach dem Absenden erhalten Sie sofort eine Bestätigung per E-Mail mit dem Inhalt Ihrer Kündigung sowie Datum und Uhrzeit des Eingangs.",
            "done_title": "Ihre Kündigung ist eingegangen",
            "done_lead": "Wir haben Ihre Kündigungserklärung erhalten. Diese Seite und die E-Mail an Sie bestätigen den Eingang.",
            "declaration": "Hiermit kündige ich den genannten Vertrag.",
            "mail_subject": "Eingang Ihrer Kündigung",
        },
        "en": {
            "title": "Cancel a contract",
            "intro": "Cancel a running plan here. You do not need to sign in.",
            "name": "Your name",
            "contract": "Contract you cancel",
            "contract_help": "Order number (starts with ord_), or the plan and the name of the customer account.",
            "email": "E-mail address for the confirmation",
            "cancel_type": "Kind of cancellation",
            "ordinary": "Ordinary cancellation",
            "extraordinary": "Cancellation for good cause, without notice",
            "cancel_reason": "Reason for the cancellation for good cause",
            "cancel_reason_help": "Only for a cancellation for good cause.",
            "cancel_at": "When should the contract end?",
            "cancel_at_help": "Leave empty for the earliest possible date, or enter a date.",
            "earliest": "at the earliest possible date",
            "submit": "Cancel now",
            "after": "After sending you receive a confirmation by e-mail at once, with the content of your cancellation and the date and time we received it.",
            "done_title": "We have received your cancellation",
            "done_lead": "Your notice of cancellation has reached us. This page and the e-mail to you confirm its receipt.",
            "declaration": "I hereby cancel the contract named above.",
            "mail_subject": "Receipt of your cancellation",
        },
    },
}

COMMON = {
    "de": {
        "received": "Eingegangen am",
        "reference": "Vorgangsnummer",
        "kind_label": "Erklärung",
        "note": "Diese Bestätigung belegt den Eingang Ihrer Erklärung. Sie erhalten eine weitere Nachricht, sobald wir sie bearbeitet haben.",
        "print": "Diese Seite können Sie drucken oder speichern.",
        "throttled": "Über dieses Formular sind gerade zu viele Erklärungen eingegangen. Bitte senden Sie Ihre Erklärung per E-Mail an {email}; sie ist damit ebenso wirksam abgegeben.",
        "mail_failed": "Die Bestätigung per E-Mail konnte nicht gesendet werden. Bitte speichern oder drucken Sie diese Seite; sie belegt den Eingang.",
        "required": "Bitte ausfüllen.",
        "greeting": "Guten Tag",
        "regards": "Mit freundlichen Grüßen",
    },
    "en": {
        "received": "Received on",
        "reference": "Reference",
        "kind_label": "Declaration",
        "note": "This confirmation shows that your declaration has reached us. You will get a further message once we have processed it.",
        "print": "You can print or save this page.",
        "throttled": "Too many declarations have come in through this form just now. Please send yours by e-mail to {email}; it is just as valid that way.",
        "mail_failed": "The confirmation e-mail could not be sent. Please save or print this page; it shows that we received your declaration.",
        "required": "Please fill this in.",
        "greeting": "Hello",
        "regards": "Kind regards",
    },
}


def _one_line(value: str) -> str:
    return " ".join((value or "").split())


class NoticeForm(forms.Form):
    """Only what the law allows the form to ask: who, which contract, where the receipt goes."""

    name = forms.CharField(max_length=200)
    contract = forms.CharField(max_length=300)
    email = forms.EmailField(max_length=254)
    # Cancellation only
    cancel_type = forms.ChoiceField(choices=ContractNotice.CancelType.choices, required=False)
    cancel_reason = forms.CharField(max_length=2000, required=False, widget=forms.Textarea)
    cancel_at = forms.CharField(max_length=60, required=False)
    # Honeypot: people do not see this field, form robots fill it.
    website = forms.CharField(required=False)

    def __init__(self, *args, kind, lang, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind, self.lang = kind, lang
        for field in self.fields.values():
            field.error_messages["required"] = COMMON[lang]["required"]
        if kind == ContractNotice.Kind.CANCELLATION:
            self.fields["cancel_type"].required = True

    def clean_name(self):
        return _one_line(self.cleaned_data["name"])

    def clean_contract(self):
        return _one_line(self.cleaned_data["contract"])

    def clean_cancel_at(self):
        return _one_line(self.cleaned_data["cancel_at"])


def _throttled(email: str) -> bool:
    since = timezone.now() - timedelta(hours=1)
    recent = ContractNotice.objects.filter(received_at__gte=since)
    return recent.filter(email__iexact=email).count() >= MAX_PER_ADDRESS_PER_HOUR or recent.count() >= MAX_PER_HOUR


def _lines(notice: ContractNotice, lang: str) -> list[tuple[str, str]]:
    """The declaration as label/value pairs: the same on the receipt page and in the mail."""
    text, common = TEXT[notice.kind][lang], COMMON[lang]
    rows = [
        (common["kind_label"], text["declaration"]),
        (text["name"], notice.name),
        (text["contract"], notice.contract),
    ]
    if notice.kind == ContractNotice.Kind.CANCELLATION:
        rows.append((text["cancel_type"], text[notice.cancel_type or "ordinary"]))
        if notice.cancel_reason:
            rows.append((text["cancel_reason"], notice.cancel_reason))
        rows.append((text["cancel_at"], notice.cancel_at or text["earliest"]))
    received = timezone.localtime(notice.received_at)
    rows.append((common["received"], received.strftime("%d.%m.%Y, %H:%M:%S ") + (received.tzname() or "")))
    rows.append((common["reference"], notice.reference))
    return rows


def _send_receipt(notice: ContractNotice) -> bool:
    lang = notice.language
    text, common, company = TEXT[notice.kind][lang], COMMON[lang], settings.COMPANY
    body = "\n".join(
        [f"{common['greeting']} {notice.name},", "", text["done_lead"], ""]
        + [f"{label}: {value}" for label, value in _lines(notice, lang)]
        + ["", common["note"], "", common["regards"], company["NAME"], f"{company['STREET']}, {company['POSTAL_CODE']} {company['CITY']}", company["EMAIL"]]
    )
    operators = [address for address in settings.CASSILION["OPERATOR_EMAILS"] if address]
    try:
        # The customer's copy first: it is the receipt the law asks for. The team gets the same text.
        EmailMessage(f"[Cassilion] {text['mail_subject']} ({notice.reference})", body, settings.DEFAULT_FROM_EMAIL, [notice.email], reply_to=[company["EMAIL"]]).send()
    except Exception:  # noqa: BLE001 - a mail failure must not lose the declaration
        sent = False
    else:
        sent = True
    if operators:
        EmailMessage(
            f"[Cassilion] {notice.get_kind_display()} received ({notice.reference})",
            body + f"\n\n--\nOpen in the backoffice: {settings.CASSILION['SITE_URL']}{reverse('backoffice:notice', args=[notice.reference])}",
            settings.DEFAULT_FROM_EMAIL,
            operators,
            reply_to=[notice.email],
        ).send(fail_silently=True)
    return sent


def _notice_view(request, kind):
    lang = language_of(request)
    text, common = TEXT[kind][lang], COMMON[lang]
    initial = {"cancel_type": ContractNotice.CancelType.ORDINARY}
    if request.user.is_authenticated:
        initial.update(name=request.user.full_name, email=request.user.email)
    form = NoticeForm(request.POST or None, kind=kind, lang=lang, initial=initial)
    error = ""
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        if data["website"]:
            # A robot. Answer as if it had worked and keep nothing.
            return redirect("core:home")
        if _throttled(data["email"]):
            error = common["throttled"].format(email=settings.COMPANY["EMAIL"])
        else:
            is_cancellation = kind == ContractNotice.Kind.CANCELLATION
            notice = ContractNotice.objects.create(
                kind=kind,
                name=data["name"],
                email=data["email"],
                contract=data["contract"],
                cancel_type=data["cancel_type"] if is_cancellation else "",
                cancel_reason=data["cancel_reason"].strip() if is_cancellation else "",
                cancel_at=data["cancel_at"] if is_cancellation else "",
                language=lang,
                user=request.user if request.user.is_authenticated else None,
            )
            notice.confirmation_sent = _send_receipt(notice)
            notice.save(update_fields=["confirmation_sent"])
            audit(f"legal.{kind}_received", actor=notice.user, obj=notice.reference)
            return redirect(f"{reverse('core:notice_receipt', args=[notice.token])}?lang={lang}")
    return render(
        request,
        "core/notice_form.html",
        {
            "form": form,
            "kind": kind,
            "lang": lang,
            "other_lang": "en" if lang == "de" else "de",
            "t": text,
            "c": common,
            "error": error,
            "title": text["title"],
        },
    )


def withdraw(request):
    return _notice_view(request, ContractNotice.Kind.WITHDRAWAL)


def cancel_contract(request):
    return _notice_view(request, ContractNotice.Kind.CANCELLATION)


def notice_receipt(request, token):
    """The receipt on screen. The address holds a random token, so only the sender can open it."""
    notice = get_object_or_404(ContractNotice, token=token)
    lang = language_of(request) if request.GET.get("lang") else notice.language
    return render(
        request,
        "core/notice_receipt.html",
        {
            "notice": notice,
            "lang": lang,
            "t": TEXT[notice.kind][lang],
            "c": COMMON[lang],
            "rows": _lines(notice, lang),
            "title": TEXT[notice.kind][lang]["done_title"],
        },
    )
