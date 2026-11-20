"""The case store: tickets, their messages, and everything the AI did with them.

Deliberately small. It borrows from helpdesk systems only what this use case
needs: public reply versus internal note, a complete history in which the AI
appears like any other actor, a few fixed states, draft-and-release as the rule.
"""
import secrets

from django.conf import settings
from django.db import models

_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _ticket_id() -> str:
    return "T-" + "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))


class Ticket(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued for analysis"
        ANALYSING = "analysing", "Being analysed"
        NEEDS_INFO = "needs_info", "Waiting for you"
        IN_REVIEW = "in_review", "With the Cassilion team"
        BUG_CANDIDATE = "bug_candidate", "Possible product bug"
        CONFIRMED = "confirmed", "Confirmed product bug"
        ANSWERED = "answered", "Answered"
        CLOSED = "closed", "Closed"
        REJECTED = "rejected", "Not accepted"

    class Channel(models.TextChoices):
        WEB = "web", "Customer area"
        MCP = "mcp", "AI agent (MCP)"

    class Priority(models.TextChoices):
        NORMAL = "normal", "Normal"
        CRITICAL = "critical", "Critical"

    OPEN_STATES = [Status.QUEUED, Status.ANALYSING, Status.NEEDS_INFO, Status.IN_REVIEW, Status.BUG_CANDIDATE, Status.CONFIRMED, Status.ANSWERED]

    public_id = models.CharField(max_length=12, unique=True, default=_ticket_id)
    account = models.ForeignKey("accounts.Account", on_delete=models.CASCADE, related_name="tickets")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    channel = models.CharField(max_length=6, choices=Channel.choices, default=Channel.WEB)
    subject = models.CharField(max_length=200)
    product_version = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.QUEUED, db_index=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    classification = models.CharField(max_length=24, blank=True)
    confidence = models.CharField(max_length=8, blank=True)
    bug_report = models.BooleanField(default=False, help_text="One-way bug report: no reply is promised.")
    flagged = models.BooleanField(default=False, help_text="A guard raised a flag; a person has to look.")
    needs_human = models.BooleanField(default=False)
    rounds = models.PositiveIntegerField(default=0, help_text="Customer follow-ups after the first message.")
    tokens_used = models.PositiveIntegerField(default=0)
    issue_url = models.URLField(blank=True, help_text="Linked issue in the product tracker.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.public_id} {self.subject}"

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATES


class Message(models.Model):
    class Author(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        CUSTOMER_AGENT = "customer_agent", "Customer's AI agent"
        AI = "ai", "Cassilion support AI"
        OPERATOR = "operator", "Cassilion team"
        SYSTEM = "system", "Cassilion"

    class Visibility(models.TextChoices):
        PUBLIC = "public", "Visible to the customer"
        INTERNAL = "internal", "Internal note"

    class State(models.TextChoices):
        DRAFT = "draft", "Draft, not sent"
        SENT = "sent", "Sent"
        REJECTED = "rejected", "Rejected"
        DISCARDED = "discarded", "Not needed"

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="messages")
    author_kind = models.CharField(max_length=16, choices=Author.choices)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.PUBLIC)
    state = models.CharField(max_length=10, choices=State.choices, default=State.SENT)
    body = models.TextField()
    body_redacted = models.TextField(blank=True, help_text="Customer text as the AI sees it: secrets and personal data removed.")
    diagnostics = models.TextField(blank=True, help_text="Diagnostics bundle or logs, already redacted.")
    ai_generated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.ticket.public_id} {self.author_kind} {self.state}"

    @property
    def from_customer(self) -> bool:
        return self.author_kind in {self.Author.CUSTOMER, self.Author.CUSTOMER_AGENT}


class TicketEvent(models.Model):
    """The ticket's history. Every actor, including the AI and the guards, writes here."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="events")
    at = models.DateTimeField(auto_now_add=True)
    actor_kind = models.CharField(max_length=16, default="system")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    type = models.CharField(max_length=40)
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["at", "id"]


class AIJob(models.Model):
    """One unit of work for the AI enclave. Leased, not handed over: an unanswered job is offered again."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        CLAIMED = "claimed", "Claimed"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="jobs")
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.QUEUED, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    worker_id = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["created_at", "id"]


class AIRun(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="runs")
    job = models.OneToOneField(AIJob, null=True, blank=True, on_delete=models.SET_NULL, related_name="run")
    backend = models.CharField(max_length=40, blank=True)
    model = models.CharField(max_length=120, blank=True)
    prompt_version = models.CharField(max_length=40, blank=True)
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Finding(models.Model):
    """What the analyst concluded. Operators only. Pointers name places in the source; they never carry code."""

    run = models.OneToOneField(AIRun, on_delete=models.CASCADE, related_name="finding")
    classification = models.CharField(max_length=24)
    confidence = models.CharField(max_length=8)
    component = models.CharField(max_length=24, blank=True)
    known_issue_id = models.CharField(max_length=40, blank=True)
    public_refs = models.JSONField(default=list, blank=True)
    missing_info = models.JSONField(default=list, blank=True)
    source_pointers = models.JSONField(default=list, blank=True)
    summary = models.TextField(blank=True)


class GuardVerdict(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="verdicts")
    run = models.ForeignKey(AIRun, null=True, blank=True, on_delete=models.CASCADE, related_name="verdicts")
    stage = models.CharField(max_length=2)  # G1..G5
    check_name = models.CharField(max_length=60)
    verdict = models.CharField(max_length=6)  # pass | flag | block
    detail = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class Review(models.Model):
    """How an outgoing message was released. Automatic sending leaves a record too."""

    class Action(models.TextChoices):
        APPROVE = "approve", "Approved as drafted"
        EDIT = "edit", "Edited and approved"
        REJECT = "reject", "Rejected"
        AUTO = "auto", "Sent automatically"
        TEMPLATE = "template", "Fixed template"

    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name="review")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=10, choices=Action.choices)
    note = models.CharField(max_length=300, blank=True)
    decided_at = models.DateTimeField(auto_now_add=True)
