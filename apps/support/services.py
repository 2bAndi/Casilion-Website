"""Everything that happens to a ticket.

The customer area, the customer MCP, the operator MCP, the backoffice and the job
API all call these functions and nothing else, so entitlement checks, guardrails
and the audit trail cannot be bypassed through another door.

Guardrails by number (see docs/architecture):
    G1  entitlement gate          gate()
    G2  input guard               apps.guard.input_guard.inspect()
    G3  bridge                    in the AI enclave (ai_worker)
    G4  output guard              in the AI enclave (ai_worker)
    G5  release gate              submit_result() + release_gate() + release()
"""
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.audit.log import audit
from apps.backoffice.models import SupportSettings
from apps.core import knowledge
from apps.guard import input_guard
from apps.licensing.services import current_entitlement
from shared.redaction import find_secrets, redact
from shared.schemas import ANSWER_CLASSES, BUG_CLASSES, CLASSES, MISSING_INFO, GuardCheck, validate_result

from .models import AIJob, AIRun, Finding, GuardVerdict, Message, Review, Ticket, TicketEvent

MAX_JOB_ATTEMPTS = 3

AI_NOTICE = "\n\n—\nThis reply was written by Cassilion's support AI."
AI_NOTICE_REVIEWED = "\n\n—\nThis reply was drafted by Cassilion's support AI and released by a member of the Cassilion team."

TEMPLATES = {
    "ack_bug": (
        "Thank you for the report. Our first analysis points to a possible defect in Cassilion itself, "
        "so we have passed it to the engineering team. A person will look at it and reply here."
    ),
    "ack_security": (
        "Thank you for the report. We treat it as a possible security matter and have passed it to the "
        "responsible person at once. You will hear from a member of the team here. Please do not share "
        "further details in public channels."
    ),
    "ack_bug_report": "Thank you. Your bug report has been recorded and will be reviewed. Bug reports on the Community plan do not receive an individual reply.",
    "needs_info": "To look into this we need a little more information:\n\n{items}\n\nReply to this ticket with it and the analysis continues.",
    "out_of_scope": "This request is outside what Cassilion support covers. Support answers questions about installing, configuring and running Cassilion.",
    "abuse": "This request was not accepted.",
    "human": "A member of the Cassilion team has been asked to look at this ticket and will reply here.",
}


class SupportError(Exception):
    code = "error"


class NotEntitled(SupportError):
    code = "not_entitled"


class QuotaExceeded(SupportError):
    code = "quota_exceeded"


class InputRejected(SupportError):
    code = "input_rejected"


class NotAllowed(SupportError):
    code = "not_allowed"


# --------------------------------------------------------------------------- helpers


def _event(ticket, type_, *, actor_kind="system", actor=None, **data) -> None:
    TicketEvent.objects.create(ticket=ticket, type=type_, actor_kind=actor_kind, actor=actor, data=data)


def _store_checks(ticket, checks, run=None) -> None:
    GuardVerdict.objects.bulk_create(
        [
            GuardVerdict(ticket=ticket, run=run, stage=c.stage, check_name=c.check, verdict=c.verdict, detail=c.detail[:300])
            for c in checks
        ]
    )


def _set_status(ticket, status) -> None:
    if ticket.status != status:
        ticket.status = status
        if status in {Ticket.Status.CLOSED, Ticket.Status.REJECTED}:
            ticket.closed_at = timezone.now()
    ticket.save()


def _ticket_url(ticket) -> str:
    return settings.CASSILION["SITE_URL"] + reverse("support:detail", args=[ticket.public_id])


def _ops_url(ticket) -> str:
    return settings.CASSILION["SITE_URL"] + reverse("backoffice:ticket", args=[ticket.public_id])


def notify_customer(ticket) -> None:
    """Mail is a notification with a link, never a channel: the content stays in the portal."""
    if ticket.author and ticket.author.email:
        send_mail(
            f"[Cassilion] New reply to ticket {ticket.public_id}",
            f"There is a new reply to your ticket \"{ticket.subject}\".\n\nRead it here: {_ticket_url(ticket)}\n",
            settings.DEFAULT_FROM_EMAIL,
            [ticket.author.email],
            fail_silently=True,
        )


def alert_operators(ticket, reason: str, *, urgent: bool = False) -> None:
    """No customer text and no source code in alerts: only what is needed to find the ticket."""
    recipients = settings.CASSILION["OPERATOR_EMAILS"]
    _event(ticket, "operators_alerted", reason=reason, urgent=urgent)
    if recipients:
        plan = current_entitlement(ticket.account).plan
        send_mail(
            f"[Cassilion ops]{' URGENT' if urgent else ''} {ticket.public_id}: {reason}",
            f"Ticket:   {ticket.public_id}\nPlan:     {plan.name}\nVersion:  {ticket.product_version or 'not given'}\n"
            f"Class:    {ticket.classification or 'not analysed yet'}\nReason:   {reason}\n\n{_ops_url(ticket)}\n",
            settings.DEFAULT_FROM_EMAIL,
            recipients,
            fail_silently=True,
        )


# --------------------------------------------------------------------------- G1: entitlement gate


def gate(account, *, bug_report: bool = False):
    """Does the plan allow this, and is there quota left this month? Returns the entitlement."""
    entitlement = current_entitlement(account)
    plan = entitlement.plan
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = account.tickets.filter(bug_report=bug_report, created_at__gte=month_start).count()
    if bug_report:
        if used >= plan.bug_report_quota_month:
            raise QuotaExceeded(f"The {plan.name} plan allows {plan.bug_report_quota_month} bug reports a month.")
        return entitlement
    if not plan.has_support:
        raise NotEntitled(f"The {plan.name} plan does not include support. You can still send a one-way bug report, or choose a support plan.")
    if used >= plan.ticket_quota_month:
        raise QuotaExceeded(f"The {plan.name} plan includes {plan.ticket_quota_month} tickets a month, and they are used up.")
    return entitlement


# --------------------------------------------------------------------------- customers


@transaction.atomic
def open_ticket(account, user, *, channel, subject, body, product_version="", diagnostics="", bug_report=False) -> Ticket:
    gate(account, bug_report=bug_report)
    verdict = input_guard.inspect(body, diagnostics)
    if verdict.decision == "reject":
        raise InputRejected(" ".join(verdict.reasons))

    ticket = Ticket.objects.create(
        account=account,
        author=user,
        channel=channel,
        subject=input_guard.normalise(subject)[:200] or "No subject",
        product_version=input_guard.normalise(product_version)[:40],
        bug_report=bug_report,
        flagged=verdict.flagged,
        priority=Ticket.Priority.CRITICAL if verdict.critical else Ticket.Priority.NORMAL,
    )
    author_kind = Message.Author.CUSTOMER_AGENT if channel == Ticket.Channel.MCP else Message.Author.CUSTOMER
    Message.objects.create(
        ticket=ticket,
        author_kind=author_kind,
        author=user,
        body=verdict.text,
        body_redacted=verdict.redacted,
        diagnostics=verdict.diagnostics,
        sent_at=timezone.now(),
    )
    _store_checks(ticket, [GuardCheck("G1", "entitlement", "pass", "plan and quota allow this")] + verdict.checks)
    _event(ticket, "opened", actor_kind=author_kind, actor=user, channel=channel, bug_report=bug_report)
    audit("ticket.opened", actor=user, actor_kind="agent" if channel == Ticket.Channel.MCP else "user", account=account, obj=ticket.public_id)

    if bug_report:
        send_template(ticket, "ack_bug_report", notify=False)
    if verdict.critical:
        # Data loss, corruption and security go to a person at once, whatever the AI later says.
        ticket.needs_human = True
        ticket.save(update_fields=["needs_human"])
        alert_operators(ticket, f"customer reports a {verdict.critical} problem", urgent=True)
    _enqueue(ticket)
    return ticket


@transaction.atomic
def add_customer_message(ticket, user, *, channel, body, diagnostics="") -> Message:
    if not ticket.is_open:
        raise NotAllowed("This ticket is closed. Open a new one and mention this ticket's number.")
    if ticket.bug_report:
        raise NotAllowed("One-way bug reports cannot be continued. Open a new report if you have more details.")
    verdict = input_guard.inspect(body, diagnostics)
    if verdict.decision == "reject":
        raise InputRejected(" ".join(verdict.reasons))

    author_kind = Message.Author.CUSTOMER_AGENT if channel == Ticket.Channel.MCP else Message.Author.CUSTOMER
    message = Message.objects.create(
        ticket=ticket,
        author_kind=author_kind,
        author=user,
        body=verdict.text,
        body_redacted=verdict.redacted,
        diagnostics=verdict.diagnostics,
        sent_at=timezone.now(),
    )
    _store_checks(ticket, verdict.checks)
    ticket.rounds += 1
    ticket.flagged = ticket.flagged or verdict.flagged
    _event(ticket, "customer_replied", actor_kind=author_kind, actor=user, round=ticket.rounds)

    limits = SupportSettings.load()
    over_limit = ticket.rounds >= limits.max_rounds or ticket.tokens_used >= limits.max_tokens_per_ticket
    if (over_limit or verdict.critical) and not ticket.needs_human:
        # Two AIs can talk each other into a plausible, wrong diagnosis. Cap it and bring in a person.
        ticket.needs_human = True
        reason = f"customer reports a {verdict.critical} problem" if verdict.critical else "round or budget limit reached"
        alert_operators(ticket, reason, urgent=bool(verdict.critical))

    if ticket.status in {Ticket.Status.IN_REVIEW, Ticket.Status.BUG_CANDIDATE, Ticket.Status.CONFIRMED}:
        ticket.save()  # the team already has it; keep the state, the new message is in the thread
    elif over_limit:
        _set_status(ticket, Ticket.Status.IN_REVIEW)
    else:
        _set_status(ticket, Ticket.Status.QUEUED)
        _enqueue(ticket)
    return message


@transaction.atomic
def request_human(ticket, user, *, channel="web", reason="") -> None:
    plan = current_entitlement(ticket.account).plan
    if not plan.human_on_request:
        raise NotEntitled(f"The {plan.name} plan answers through the support AI. A person steps in when a product bug is suspected.")
    if not ticket.is_open:
        raise NotAllowed("This ticket is closed.")
    if not ticket.needs_human:
        ticket.needs_human = True
        _event(ticket, "human_requested", actor_kind="customer", actor=user, channel=channel, reason=input_guard.normalise(reason)[:300])
        send_template(ticket, "human", notify=False)
        alert_operators(ticket, "customer asks for a person")
    if ticket.status not in {Ticket.Status.BUG_CANDIDATE, Ticket.Status.CONFIRMED}:
        _set_status(ticket, Ticket.Status.IN_REVIEW)
    else:
        ticket.save()


@transaction.atomic
def close_ticket(ticket, actor=None, *, actor_kind="customer") -> None:
    if ticket.is_open:
        ticket.messages.filter(state=Message.State.DRAFT).update(state=Message.State.DISCARDED)
        ticket.jobs.filter(status__in=[AIJob.Status.QUEUED, AIJob.Status.CLAIMED]).update(status=AIJob.Status.FAILED, error="ticket closed")
        _event(ticket, "closed", actor_kind=actor_kind, actor=actor)
        _set_status(ticket, Ticket.Status.CLOSED)


def customer_view(ticket) -> dict:
    """What the customer, and the customer's AI agent, may know about a ticket."""
    messages = [
        {
            "from": m.get_author_kind_display(),
            "at": (m.sent_at or m.created_at).isoformat(timespec="seconds"),
            "text": m.body,
            "written_by_ai": m.ai_generated,
        }
        for m in ticket.messages.filter(visibility=Message.Visibility.PUBLIC, state=Message.State.SENT)
    ]
    needs = []
    if ticket.status == Ticket.Status.NEEDS_INFO:
        finding = Finding.objects.filter(run__ticket=ticket).order_by("-run__created_at").first()
        needs = [{"code": c, "what": MISSING_INFO[c]} for c in (finding.missing_info if finding else []) if c in MISSING_INFO]
    next_step = {
        Ticket.Status.QUEUED: "Analysis has not started yet. Ask again in a minute.",
        Ticket.Status.ANALYSING: "The support AI is analysing the ticket. Ask again in a minute.",
        Ticket.Status.NEEDS_INFO: "Send the missing information with reply_ticket; the analysis then continues.",
        Ticket.Status.IN_REVIEW: "A member of the Cassilion team is reviewing this ticket. No action needed.",
        Ticket.Status.BUG_CANDIDATE: "This may be a product bug. The engineering team has it. No action needed.",
        Ticket.Status.CONFIRMED: "Confirmed as a product bug. Updates will appear in this ticket.",
        Ticket.Status.ANSWERED: "There is an answer. Reply if it does not solve the problem; the ticket closes by itself otherwise.",
        Ticket.Status.CLOSED: "Closed.",
        Ticket.Status.REJECTED: "Not accepted.",
    }[ticket.status]
    return {
        "ticket_id": ticket.public_id,
        "subject": ticket.subject,
        "status": ticket.status,
        "status_text": ticket.get_status_display(),
        "product_version": ticket.product_version,
        "opened_at": ticket.created_at.isoformat(timespec="seconds"),
        "needs": needs,
        "next_step": next_step,
        "messages": messages,
        "url": _ticket_url(ticket),
    }


# --------------------------------------------------------------------------- job queue (called by the job API)


def _enqueue(ticket) -> None:
    if not ticket.jobs.filter(status__in=[AIJob.Status.QUEUED, AIJob.Status.CLAIMED]).exists():
        AIJob.objects.create(ticket=ticket)


def worker_seen(worker_id: str) -> None:
    SupportSettings.objects.update_or_create(pk=1, defaults={"worker_last_seen": timezone.now(), "worker_last_id": worker_id[:80]})


@transaction.atomic
def claim_job(worker_id: str) -> AIJob | None:
    """Lease the oldest waiting job. A job whose lease ran out is offered again, up to a limit."""
    now = timezone.now()
    lease = timedelta(seconds=settings.CASSILION["JOB_LEASE_SECONDS"])
    while True:
        job = (
            AIJob.objects.select_related("ticket")
            .filter(Q(status=AIJob.Status.QUEUED) | Q(status=AIJob.Status.CLAIMED, lease_until__lt=now))
            .order_by("created_at", "id")
            .first()
        )
        if job is None:
            return None
        if job.attempts >= MAX_JOB_ATTEMPTS:
            _fail(job, "no result after several attempts")
            continue
        job.status, job.worker_id = AIJob.Status.CLAIMED, worker_id[:80]
        job.attempts += 1
        job.claimed_at, job.lease_until = now, now + lease
        job.save()
        if job.ticket.status == Ticket.Status.QUEUED:
            _set_status(job.ticket, Ticket.Status.ANALYSING)
        return job


def heartbeat(job: AIJob) -> None:
    job.lease_until = timezone.now() + timedelta(seconds=settings.CASSILION["JOB_LEASE_SECONDS"])
    job.save(update_fields=["lease_until"])


def _fail(job: AIJob, reason: str) -> None:
    job.status, job.error, job.finished_at = AIJob.Status.FAILED, reason[:300], timezone.now()
    job.save()
    ticket = job.ticket
    _event(ticket, "analysis_failed", actor_kind="ai", reason=reason[:300])
    if ticket.status in {Ticket.Status.QUEUED, Ticket.Status.ANALYSING}:
        _set_status(ticket, Ticket.Status.IN_REVIEW)


@transaction.atomic
def fail_job(job: AIJob, reason: str) -> None:
    if job.status == AIJob.Status.CLAIMED:
        _fail(job, reason or "the worker gave up")


def build_payload(job: AIJob) -> dict:
    """What crosses into the enclave: redacted text, version, plan. No identity, no contact data, no other tickets."""
    ticket = job.ticket
    messages = []
    for m in ticket.messages.filter(state=Message.State.SENT, visibility=Message.Visibility.PUBLIC):
        if m.from_customer:
            messages.append({"role": "customer", "text": m.body_redacted or m.body, "diagnostics": m.diagnostics})
        else:
            messages.append({"role": "support", "text": m.body, "diagnostics": ""})
    return {
        "job_id": job.pk,
        "ticket": {
            "id": ticket.public_id,
            "subject": redact(ticket.subject)[0],
            "product_version": ticket.product_version,
            "plan": current_entitlement(ticket.account).plan.code,
            "language": (ticket.author.locale if ticket.author else "en") or "en",
            "channel": ticket.channel,
            "priority": ticket.priority,
            "bug_report": ticket.bug_report,
            "round": ticket.rounds,
            "messages": messages,
        },
        "docs_base_url": settings.CASSILION["SITE_URL"] + "/docs",
    }


# --------------------------------------------------------------------------- G5: result, release gate, release


@transaction.atomic
def submit_result(job: AIJob, data: dict) -> AIRun:
    """Take a result from the enclave. Validate it again here; the enclave is trusted to try, not to be right."""
    if job.status != AIJob.Status.CLAIMED:
        raise NotAllowed("This job is not open.")
    ticket = job.ticket
    corpus = knowledge.corpus()
    issue_ids = {i["id"] for i in knowledge.known_issues()}
    clean, problems = validate_result(data, corpus.passage_ids, issue_ids)
    usage, f = clean["usage"], clean["finding"]

    run = AIRun.objects.create(
        ticket=ticket,
        job=job,
        backend=usage["backend"],
        model=usage["model"],
        prompt_version=usage["prompt_version"],
        tokens_in=usage["tokens_in"],
        tokens_out=usage["tokens_out"],
        duration_ms=usage["duration_ms"],
    )
    if problems:
        # A malformed result is never repaired into something sendable.
        f["classification"] = f["classification"] if f["classification"] in CLASSES else "doc_gap"
        f["confidence"] = "low"
    Finding.objects.create(
        run=run,
        classification=f["classification"],
        confidence=f["confidence"],
        component=f["component"],
        known_issue_id=f["known_issue_id"] or "",
        public_refs=f["public_refs"],
        missing_info=f["missing_info"],
        source_pointers=f["source_pointers"],
        summary=f["summary"],
    )
    checks = [GuardCheck(c["stage"], c["check"], c["verdict"], c["detail"]) for c in clean["guard"]]
    checks.append(GuardCheck("G5", "result_schema", "block" if problems else "pass", "; ".join(problems)[:300]))
    leaked = find_secrets(clean["draft"])
    checks.append(GuardCheck("G5", "outbound_secret_scan", "block" if leaked else "pass", ", ".join(leaked)))
    _store_checks(ticket, checks, run=run)

    draft = None
    if clean["draft"].strip():
        draft = Message.objects.create(
            ticket=ticket,
            author_kind=Message.Author.AI,
            state=Message.State.DRAFT,
            body=clean["draft"].strip(),
            ai_generated=True,
        )
    ticket.classification, ticket.confidence = f["classification"], f["confidence"]
    ticket.tokens_used += usage["tokens_in"] + usage["tokens_out"]
    ticket.save()
    job.status, job.finished_at = AIJob.Status.DONE, timezone.now()
    job.save()
    _event(ticket, "analysed", actor_kind="ai", classification=f["classification"], confidence=f["confidence"], model=usage["model"])

    release_gate(ticket, run, draft)
    return run


def release_gate(ticket, run, draft) -> str:
    """Decide what happens to the AI's result. Returns the decision for the history."""
    finding = run.finding
    cls, limits = finding.classification, SupportSettings.load()
    verdicts = list(run.verdicts.all())
    blocked = any(v.verdict == "block" for v in verdicts)
    flagged = ticket.flagged or any(v.verdict == "flag" for v in verdicts)

    def discard():
        if draft:
            draft.state = Message.State.DISCARDED
            draft.save(update_fields=["state"])

    if ticket.bug_report:
        discard()
        if cls in BUG_CLASSES:
            decision = "bug_candidate"
            _set_status(ticket, Ticket.Status.BUG_CANDIDATE)
            alert_operators(ticket, f"bug report classified as {cls}", urgent=cls == "security_suspected")
        else:
            decision = "bug_report_filed"
            _set_status(ticket, Ticket.Status.CLOSED)
    elif cls in BUG_CLASSES:
        # The analysis is never sent automatically. The customer gets a fixed acknowledgement;
        # the draft, if any, waits for an operator (it may hold a workaround from the public docs).
        decision = "bug_candidate"
        ticket.needs_human = True
        send_template(ticket, "ack_security" if cls == "security_suspected" else "ack_bug")
        _set_status(ticket, Ticket.Status.BUG_CANDIDATE)
        alert_operators(ticket, f"support AI suspects: {cls}", urgent=cls == "security_suspected")
    elif cls == "needs_info" and not blocked:
        discard()
        decision = "asked_for_information"
        items = "\n".join(f"- {MISSING_INFO[c]}" for c in finding.missing_info if c in MISSING_INFO) or "- More detail on what you did and what happened."
        send_template(ticket, "needs_info", items=items)
        _set_status(ticket, Ticket.Status.NEEDS_INFO)
    elif cls in {"out_of_scope", "abuse"} and not ticket.needs_human:
        discard()
        decision = cls
        send_template(ticket, cls)
        _set_status(ticket, Ticket.Status.REJECTED if cls == "abuse" else Ticket.Status.CLOSED)
    elif (
        cls in ANSWER_CLASSES
        and draft is not None
        and finding.confidence == "high"
        and not blocked
        and not flagged
        and not ticket.needs_human
        and ticket.priority == Ticket.Priority.NORMAL
        and limits.auto_send
        and cls in limits.auto_send_classes
    ):
        decision = "auto_sent"
        release(draft, reviewer=None, action=Review.Action.AUTO)
    else:
        decision = "review"
        _set_status(ticket, Ticket.Status.IN_REVIEW)

    reason = "guard blocked the draft" if blocked else "guard flag" if flagged else "shadow mode" if not limits.auto_send else cls
    _event(ticket, "release_gate", decision=decision, reason=reason if decision == "review" else "")
    return decision


def send_template(ticket, key: str, *, notify: bool = True, **context) -> Message:
    """Send one of the fixed texts. Nothing in it was written by the AI, so it needs no review."""
    message = Message.objects.create(
        ticket=ticket,
        author_kind=Message.Author.SYSTEM,
        body=TEMPLATES[key].format(**context),
        sent_at=timezone.now(),
    )
    Review.objects.create(message=message, action=Review.Action.TEMPLATE, note=key)
    _event(ticket, "template_sent", template=key)
    if notify:
        notify_customer(ticket)
    return message


@transaction.atomic
def release(draft: Message, *, reviewer, action: str, edited_body: str | None = None) -> Message:
    """Send an AI draft to the customer. The last check before anything leaves."""
    if draft.state != Message.State.DRAFT:
        raise NotAllowed("This draft was already handled.")
    body = (edited_body if edited_body is not None else draft.body).strip()
    if not body:
        raise NotAllowed("The reply is empty.")
    leaked = find_secrets(body)
    if leaked:
        raise NotAllowed(f"The reply contains something that looks like a secret ({', '.join(leaked)}). Remove it first.")
    ticket = draft.ticket
    draft.body = body + (AI_NOTICE if action == Review.Action.AUTO else AI_NOTICE_REVIEWED)
    draft.state, draft.sent_at = Message.State.SENT, timezone.now()
    draft.save()
    Review.objects.create(message=draft, reviewer=reviewer, action=action)
    _event(ticket, "reply_released", actor_kind="operator" if reviewer else "system", actor=reviewer, action=action)
    if reviewer:
        audit("ticket.reply_released", actor=reviewer, actor_kind="operator", account=ticket.account, obj=ticket.public_id, data={"action": action})
    _set_status(ticket, Ticket.Status.ANSWERED)
    notify_customer(ticket)
    return draft


# --------------------------------------------------------------------------- operators


def approve_draft(draft: Message, reviewer, edited_body: str | None = None) -> Message:
    edited = edited_body is not None and edited_body.strip() != draft.body.strip()
    return release(draft, reviewer=reviewer, action=Review.Action.EDIT if edited else Review.Action.APPROVE, edited_body=edited_body)


@transaction.atomic
def reject_draft(draft: Message, reviewer, note: str = "") -> None:
    if draft.state != Message.State.DRAFT:
        raise NotAllowed("This draft was already handled.")
    draft.state = Message.State.REJECTED
    draft.save(update_fields=["state"])
    Review.objects.create(message=draft, reviewer=reviewer, action=Review.Action.REJECT, note=note[:300])
    _event(draft.ticket, "draft_rejected", actor_kind="operator", actor=reviewer, note=note[:300])
    audit("ticket.draft_rejected", actor=reviewer, actor_kind="operator", account=draft.ticket.account, obj=draft.ticket.public_id)


@transaction.atomic
def operator_reply(ticket, reviewer, body: str, *, internal: bool = False) -> Message:
    body = (body or "").strip()
    if not body:
        raise NotAllowed("The message is empty.")
    message = Message.objects.create(
        ticket=ticket,
        author_kind=Message.Author.OPERATOR,
        author=reviewer,
        visibility=Message.Visibility.INTERNAL if internal else Message.Visibility.PUBLIC,
        body=body,
        sent_at=timezone.now(),
    )
    _event(ticket, "internal_note" if internal else "operator_replied", actor_kind="operator", actor=reviewer)
    audit("ticket.operator_message", actor=reviewer, actor_kind="operator", account=ticket.account, obj=ticket.public_id, data={"internal": internal})
    if not internal:
        ticket.messages.filter(state=Message.State.DRAFT).update(state=Message.State.DISCARDED)
        if ticket.status not in {Ticket.Status.BUG_CANDIDATE, Ticket.Status.CONFIRMED}:
            _set_status(ticket, Ticket.Status.ANSWERED)
        notify_customer(ticket)
    return message


@transaction.atomic
def confirm_bug(ticket, reviewer, issue_url: str = "") -> None:
    ticket.issue_url = issue_url[:200]
    _event(ticket, "bug_confirmed", actor_kind="operator", actor=reviewer, issue_url=issue_url[:200])
    audit("ticket.bug_confirmed", actor=reviewer, actor_kind="operator", account=ticket.account, obj=ticket.public_id)
    _set_status(ticket, Ticket.Status.CONFIRMED)


@transaction.atomic
def dismiss_bug(ticket, reviewer) -> None:
    _event(ticket, "bug_dismissed", actor_kind="operator", actor=reviewer)
    _set_status(ticket, Ticket.Status.IN_REVIEW)


def close_stale(now=None) -> int:
    """Daily job: answered tickets and tickets waiting for the customer close after a quiet period."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=SupportSettings.load().stale_days)
    closed = 0
    for ticket in Ticket.objects.filter(status__in=[Ticket.Status.ANSWERED, Ticket.Status.NEEDS_INFO], updated_at__lt=cutoff):
        close_ticket(ticket, actor_kind="system")
        closed += 1
    return closed


def review_queue():
    return Ticket.objects.filter(status__in=[Ticket.Status.IN_REVIEW, Ticket.Status.BUG_CANDIDATE]).select_related("account").order_by("priority", "created_at")  # "critical" sorts before "normal"
