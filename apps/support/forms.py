from django import forms

from apps.guard.input_guard import MAX_DIAGNOSTICS_CHARS, MAX_TEXT_CHARS


class TicketForm(forms.Form):
    subject = forms.CharField(label="Subject", max_length=200)
    product_version = forms.CharField(
        label="Cassilion version", max_length=40, required=False, help_text="Output of cassilion --version."
    )
    body = forms.CharField(
        label="What happened?",
        max_length=MAX_TEXT_CHARS,
        widget=forms.Textarea(attrs={"rows": 8}),
        help_text="What you did, what you expected, what happened instead.",
    )
    diagnostics = forms.CharField(
        label="Diagnostics or log lines",
        max_length=MAX_DIAGNOSTICS_CHARS,
        required=False,
        widget=forms.Textarea(attrs={"rows": 6, "class": "mono"}),
        help_text="Output of cassilion diag --json, or the log lines around the problem. Passwords and tokens are removed before analysis.",
    )


class ReplyForm(forms.Form):
    body = forms.CharField(label="Your reply", max_length=MAX_TEXT_CHARS, widget=forms.Textarea(attrs={"rows": 5}))
    diagnostics = forms.CharField(
        label="Diagnostics or log lines",
        max_length=MAX_DIAGNOSTICS_CHARS,
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "class": "mono"}),
    )
