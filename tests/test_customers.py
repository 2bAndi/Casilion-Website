"""The customer page of the backoffice, the mail log and the inbox. No mail server is contacted:
the IMAP client is replaced by a mailbox that plays one."""
from email.message import EmailMessage as RawMail
from unittest import mock

from django.core import mail
from django.core.mail import send_mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, User
from apps.audit.models import AuditEvent
from apps.billing import services as billing
from apps.billing.models import Order, Subscription
from apps.mailing import inbox
from apps.mailing.models import Conversation, InboxMessage, MailboxSettings, MailLog
from apps.support import services as support
from apps.support.models import Ticket

from .base import CassilionTestCase, make_customer, run_worker_once
from .test_support import MEMORY_QUESTION


def raw_mail(sender="Dana Demo <dana@example.com>", subject="Question about my invoice", body="Hello, where is my invoice?", message_id="<m1@example.com>", **headers) -> bytes:
    message = RawMail()
    message["From"], message["To"], message["Subject"], message["Message-ID"] = sender, "info@cassilion.com", subject, message_id
    message["Date"] = "Fri, 20 Nov 2026 09:00:00 +0100"
    for name, value in headers.items():
        message[name.replace("_", "-")] = value
    message.set_content(body)
    return message.as_bytes()


class FakeMailbox:
    """What the IMAP client offers, over a list of raw mails. UIDs count from 1."""

    def __init__(self, mails, validity=7):
        self.mails, self.validity, self.selected_readonly = mails, validity, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def select(self, folder="INBOX", readonly=True):
        self.selected_readonly = readonly
        return self.validity

    def uids_since(self, day):
        return list(range(1, len(self.mails) + 1))

    def uids_after(self, last_uid):
        return [uid for uid in range(1, len(self.mails) + 1) if uid > last_uid]

    def fetch(self, uid):
        return self.mails[uid - 1]


class MailLogTests(CassilionTestCase):
    def test_every_mail_to_a_customer_is_logged_with_the_account(self):
        user, account = make_customer()  # buying sends the order confirmation
        log = MailLog.objects.get()
        self.assertEqual((log.account, log.to, log.status), (account, user.email, "sent"))
        self.assertIn("Order confirmation", log.subject)
        self.assertEqual(len(log.attachments), 3)  # names of the files, not the files
        self.assertEqual(len(mail.outbox), 1)  # logging does not send twice

        send_mail("Hello ops", "internal", None, ["ops@cassilion.example"])
        self.assertIsNone(MailLog.objects.first().account)

    def test_sign_in_links_are_not_stored(self):
        self.client.post(reverse("account_signup"), {
            "full_name": "Ada Example", "email": "ada@example.com", "kind": "individual", "country": "DE",
            "password1": "a-long-test-password", "password2": "a-long-test-password"})
        sent = mail.outbox[-1]
        self.assertIn("/auth/", sent.body)  # the customer gets the link
        log = MailLog.objects.filter(to="ada@example.com").first()
        self.assertNotIn("/auth/", log.body)
        self.assertIn("[sign-in link, not stored]", log.body)

    @override_settings(MAIL_DELIVERY_BACKEND="tests.test_customers.BrokenBackend")
    def test_a_mail_that_could_not_be_sent_is_logged_as_failed(self):
        user, _account = make_customer(plan="community")
        with self.assertRaises(OSError):
            send_mail("Hi", "text", None, [user.email])
        log = MailLog.objects.get()
        self.assertEqual(log.status, "failed")
        self.assertIn("mail server is down", log.error)


class BrokenBackend:
    def __init__(self, **kwargs):
        pass

    def send_messages(self, messages):
        raise OSError("mail server is down")


class InboxTests(CassilionTestCase):
    def setUp(self):
        self.user, self.account = make_customer(email="dana@example.com")
        self.operator = User.objects.create_user("ops@example.com", "a-long-test-password", is_staff=True, full_name="Olivia Operator")
        mail.outbox.clear()

    def test_mails_become_conversations_and_find_their_customer(self):
        self.assertTrue(inbox.store(raw_mail()))
        self.assertFalse(inbox.store(raw_mail()))  # the same mail again changes nothing
        thread = Conversation.objects.get()
        self.assertEqual((thread.account, thread.status, thread.contact_email), (self.account, "new", "dana@example.com"))
        # An answer in the same thread, by its headers.
        inbox.store(raw_mail(body="Found it, thanks.", subject="Re: Question about my invoice", message_id="<m2@example.com>", In_Reply_To="<m1@example.com>"))
        self.assertEqual((Conversation.objects.count(), thread.messages.count()), (1, 2))
        # A stranger who quotes an order number of ours lands with that customer, as a hint.
        ref = Order.objects.get().ref
        inbox.store(raw_mail(sender="buchhaltung@example.org", subject="Zahlung", body=f"Wir haben {ref} überwiesen.", message_id="<m3@example.org>"))
        self.assertEqual(Conversation.objects.get(contact_email="buchhaltung@example.org").account, self.account)
        # Somebody unknown stays unassigned; an auto-reply needs no answer.
        inbox.store(raw_mail(sender="someone@example.net", subject="Hi", message_id="<m4@example.net>"))
        inbox.store(raw_mail(sender="noreply@example.net", subject="Out of office", message_id="<m5@example.net>", Auto_Submitted="auto-replied"))
        self.assertIsNone(Conversation.objects.get(contact_email="someone@example.net").account)
        self.assertEqual(Conversation.objects.get(contact_email="noreply@example.net").status, "done")

    def test_html_mail_is_kept_as_text_only(self):
        message = RawMail()
        message["From"], message["Subject"], message["Message-ID"] = "dana@example.com", "HTML", "<h1@example.com>"
        message.set_content('<html><head><style>p{color:red}</style></head><body><p>Hello <b>there</b></p><script>alert(1)</script>'
                            '<a href="https://example.com/x">link</a><a href="javascript:alert(2)">bad</a></body></html>', subtype="html")
        inbox.store(message.as_bytes())
        body = InboxMessage.objects.get().body
        self.assertIn("Hello there", body)
        self.assertIn("<https://example.com/x>", body)
        for unwanted in ("alert(1)", "color:red", "javascript:", "<b>", "<script"):
            self.assertNotIn(unwanted, body)
        self.client.force_login(self.operator)
        page = self.client.get(reverse("backoffice:conversation", args=[Conversation.objects.get().pk]))
        self.assertContains(page, "&lt;https://example.com/x&gt;")  # shown as text, escaped

    def test_fetch_reads_without_changing_the_mailbox_and_remembers_where_it_stopped(self):
        box = FakeMailbox([raw_mail(), raw_mail(message_id="<m2@example.com>", subject="Second")])
        config = MailboxSettings.load()
        with mock.patch("apps.mailing.inbox.open_mailbox", return_value=box):
            self.assertEqual(inbox.fetch(config), 2)
            box.mails.append(raw_mail(message_id="<m3@example.com>", subject="Third"))
            self.assertEqual(inbox.fetch(config), 1)
            self.assertEqual(inbox.fetch(config), 0)
        self.assertTrue(box.selected_readonly)
        config.refresh_from_db()
        self.assertEqual((config.last_uid, config.uidvalidity, config.last_error), (3, 7, ""))
        # A mailbox that cannot be reached is a message for the operator, not a crash.
        config.host, config.user, config.password = "", "", ""
        with override_settings(EMAIL_HOST="", EMAIL_HOST_USER="", EMAIL_HOST_PASSWORD=""):
            self.assertEqual(inbox.fetch(config), 0)
        self.assertIn("not set up", config.last_error)

    def test_reply_from_the_backoffice_lands_in_the_customers_thread(self):
        inbox.store(raw_mail())
        thread = Conversation.objects.get()
        self.client.force_login(self.operator)
        url = reverse("backoffice:conversation", args=[thread.pk])
        self.assertContains(self.client.get(url), "where is my invoice?")
        self.client.post(url, {"action": "reply", "body": "It is in your account under Plan and billing."})
        sent = mail.outbox[-1]
        self.assertEqual((sent.to, sent.subject, sent.extra_headers["In-Reply-To"]), (["dana@example.com"], "Re: Question about my invoice", "<m1@example.com>"))
        self.assertIn("> Hello, where is my invoice?", sent.body)
        thread.refresh_from_db()
        reply = thread.messages.get(direction="out")
        self.assertEqual((thread.status, reply.sent_by, reply.body), ("answered", self.operator, "It is in your account under Plan and billing."))
        self.assertFalse(MailLog.objects.filter(subject__startswith="Re:").exists())  # recorded in the conversation, not twice
        self.assertNotIn("X-Cassilion-Log", sent.extra_headers)
        # The customer answers: the conversation is open again.
        inbox.store(raw_mail(body="Thanks!", subject="Re: Question about my invoice", message_id="<m9@example.com>", In_Reply_To=reply.message_id))
        thread.refresh_from_db()
        self.assertEqual((thread.status, thread.messages.count()), ("new", 3))

    def test_assigning_and_settings(self):
        inbox.store(raw_mail(sender="someone@example.net", message_id="<m4@example.net>"))
        thread = Conversation.objects.get()
        self.client.force_login(self.operator)
        url = reverse("backoffice:conversation", args=[thread.pk])
        self.client.post(url, {"action": "assign", "customer": "nobody@example.org"})
        thread.refresh_from_db()
        self.assertIsNone(thread.account)
        self.client.post(url, {"action": "assign", "customer": "DANA@example.com"})
        thread.refresh_from_db()
        self.assertEqual(thread.account, self.account)
        self.client.post(url, {"action": "done"})
        self.assertContains(self.client.get(reverse("backoffice:inbox") + "?show=done"), "someone@example.net")
        self.assertNotContains(self.client.get(reverse("backoffice:inbox")), "someone@example.net")

        settings_url = reverse("backoffice:inbox_settings")
        self.client.post(settings_url, {"enabled": "on", "host": "mail.example.org", "port": 993, "user": "info@example.org", "new_password": "imap-secret", "initial_days": 30})
        config = MailboxSettings.load()
        self.assertEqual((config.enabled, config.password, config.effective_host), (True, "imap-secret", "mail.example.org"))
        self.assertNotContains(self.client.get(settings_url), "imap-secret")
        line = AuditEvent.objects.filter(action="mail.settings_changed").get()
        self.assertNotIn("imap-secret", str(line.data))
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT password FROM mailing_mailboxsettings")
            self.assertTrue(cursor.fetchone()[0].startswith("enc:"))


class CustomerPageTests(CassilionTestCase):
    def setUp(self):
        self.user, self.account = make_customer(email="dana@example.com")
        self.operator = User.objects.create_user("ops@example.com", "a-long-test-password", is_staff=True)
        self.url = reverse("backoffice:customer", args=[self.account.pk])

    def test_customers_cannot_open_it(self):
        self.client.force_login(self.user)
        for name, args in [("customers", []), ("customer", [self.account.pk]), ("inbox", []), ("inbox_settings", [])]:
            self.assertEqual(self.client.get(reverse(f"backoffice:{name}", args=args)).status_code, 302, name)

    def test_list_finds_customers_by_name_login_order_and_invoice(self):
        make_customer(email="eve@example.org", plan="community", name="Other AG")
        self.client.force_login(self.operator)
        order = Order.objects.get()
        url = reverse("backoffice:customers")
        self.assertContains(self.client.get(url), "Other AG")
        for query in ["example gmbh", "dana@", order.ref, order.invoices.get().number]:
            page = self.client.get(url, {"q": query})
            self.assertContains(page, "Example GmbH")
            self.assertNotContains(page, "Other AG")
        self.assertContains(self.client.get(url, {"q": "Other"}), "never")  # has not signed in yet

    def test_one_page_shows_everything_about_the_customer(self):
        # A login, a mail from the customer, a ticket answered and one that waits for a person.
        self.client.force_login(self.user)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.last_login)
        inbox.store(raw_mail())
        answered = support.open_ticket(self.account, self.user, channel="web", **MEMORY_QUESTION)
        run_worker_once()
        waiting = Ticket.objects.get(pk=answered.pk)
        self.assertEqual(waiting.status, Ticket.Status.IN_REVIEW)  # shadow mode: the draft waits for a person
        Subscription.objects.filter(account=self.account).update(status=Subscription.Status.CANCELLED)

        self.client.force_login(self.operator)
        overview = self.client.get(self.url)
        for expected in ["Example GmbH", "dana@example.com", "last sign-in", timezone.localtime(self.user.last_login).strftime("%H:%M"), "Save customer", "Cassilion Professional"]:
            self.assertContains(overview, expected)
        self.assertEqual(overview.context["figures"]["waiting"], 1)
        self.assertEqual(overview.context["figures"]["to_answer"], 1)

        mails = self.client.get(self.url, {"tab": "mails"})
        self.assertContains(mails, "Order confirmation")  # went out to the customer
        self.assertContains(mails, "sent by the site")
        self.assertContains(mails, "Question about my invoice")  # came in from the customer
        self.assertContains(mails, "from the customer")

        tickets = self.client.get(self.url, {"tab": "tickets"})
        self.assertContains(tickets, waiting.public_id)
        self.assertContains(tickets, "Draft reply waits for review")
        self.assertContains(tickets, "1 draft not sent")
        self.assertEqual([t.public_id for t in tickets.context["waiting"]], [waiting.public_id])
        draft = waiting.messages.get(state="draft")
        support.approve_draft(draft, self.operator)
        tickets = self.client.get(self.url, {"tab": "tickets"})
        self.assertEqual(tickets.context["waiting"], [])
        self.assertContains(tickets, "1 reply sent")

        orders = self.client.get(self.url, {"tab": "orders"})
        order = Order.objects.get()
        self.assertContains(orders, order.ref)
        self.assertContains(orders, order.invoices.get().number)

        licenses = self.client.get(self.url, {"tab": "licenses"})
        for expected in ["Cancelled", "Active", "Inactive", "In force", "Replaced"]:
            self.assertContains(licenses, expected)
        self.assertTrue(AuditEvent.objects.filter(action="customer.viewed", actor=self.operator, account=self.account).exists())

    def test_master_data_is_edited_and_audited(self):
        self.client.force_login(self.operator)
        data = {"account-name": "Example Holding GmbH", "account-kind": "company", "account-country": "DE", "account-status": "active",
                "address-line1": "Neue Str. 2", "address-line2": "", "address-postal_code": "10115", "address-city": "Berlin", "address-region": "",
                "tax-vat_id": "DE123456789", "tax-billing_email": "rechnung@example.com"}
        self.assertRedirects(self.client.post(self.url, data), self.url)
        account = Account.objects.get(pk=self.account.pk)
        self.assertEqual((account.name, account.address.city, account.tax_profile.billing_email), ("Example Holding GmbH", "Berlin", "rechnung@example.com"))
        line = AuditEvent.objects.filter(action="customer.updated").get()
        self.assertEqual(line.actor, self.operator)
        self.assertIn("account.name", line.data["fields"])
        self.assertNotIn("Example Holding", str(line.data))
        # A mail to the billing address now belongs to this customer too.
        send_mail("Your invoice", "attached", None, ["rechnung@example.com"])
        self.assertContains(self.client.get(self.url, {"tab": "mails"}), "Your invoice")
        # Bad input is shown, not saved.
        self.assertEqual(self.client.post(self.url, {**data, "account-name": ""}).status_code, 200)
        self.assertEqual(Account.objects.get(pk=self.account.pk).name, "Example Holding GmbH")
