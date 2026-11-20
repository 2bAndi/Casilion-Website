"""The backoffice menu, in one place.

Two levels: the areas of work, and the pages of the area you are in. Every page of the
backoffice belongs to exactly one entry here; detail pages (one ticket, one order) light up
the list they belong to. Inside a page, views and filters are tabs (class ``tabs``), and
search and actions sit in one toolbar (class ``toolbar``) - see templates/backoffice/base.html.
"""
from dataclasses import dataclass, field

from django.urls import reverse


@dataclass
class Page:
    name: str  # URL name in the backoffice namespace
    label: str
    also: tuple[str, ...] = ()  # further addresses that belong to this page
    count: str = ""  # key of a number shown next to the label when it is not zero
    owner: bool = False  # offered to owners only
    url: str = ""
    current: bool = False
    number: int = 0


@dataclass
class Area:
    label: str
    pages: list[Page] = field(default_factory=list)
    current: bool = False
    number: int = 0

    @property
    def url(self) -> str:
        return self.pages[0].url


def areas() -> list[Area]:
    return [
        Area("Overview", [Page("dashboard", "Overview")]),
        Area("Support", [
            Page("review_queue", "Review queue", count="review"),
            Page("tickets", "Tickets"),
            Page("inbox", "Inbox", count="mails"),
            Page("sent_log", "Sent replies"),
            Page("settings", "Support settings"),
            Page("inbox_settings", "Mailbox", owner=True),
        ]),
        Area("Customers", [
            Page("customers", "Customers"),
            Page("notices", "Withdrawals and cancellations", count="notices"),
        ]),
        Area("Sales", [
            Page("orders", "Orders"),
            Page("invoices", "Invoices"),
            Page("payment_issues", "Payments", count="payments"),
            Page("bank_import", "Bank statement"),
            Page("payment_settings", "Payment settings", owner=True),
        ]),
        Area("Products", [
            Page("products", "Products and plans", also=("/ops/plans/",)),
            Page("releases", "Releases"),
        ]),
        Area("Operations", [
            Page("team", "Team"),
            Page("audit_log", "Audit log"),
        ]),
    ]


def counts() -> dict[str, int]:
    """What waits for a person, shown as numbers in the menu."""
    from apps.billing.models import ReconciliationIssue
    from apps.core.models import ContractNotice
    from apps.mailing.models import Conversation
    from apps.support.models import Ticket

    return {
        "review": Ticket.objects.filter(status__in=[Ticket.Status.IN_REVIEW, Ticket.Status.BUG_CANDIDATE]).count(),
        "mails": Conversation.objects.filter(status=Conversation.Status.NEW, is_automatic=False).count(),
        "notices": ContractNotice.objects.filter(handled_at__isnull=True).count(),
        "payments": ReconciliationIssue.objects.filter(resolved=False).count(),
    }


def build(path: str, owner: bool = True) -> dict:
    """The menu for the page at ``path``: which area and which page are current."""
    menu, numbers = areas(), counts()
    for area in menu:
        area.pages = [page for page in area.pages if owner or not page.owner]
    best, best_length = None, -1
    for area in menu:
        for page in area.pages:
            page.url = reverse(f"backoffice:{page.name}")
            page.number = numbers.get(page.count, 0)
            area.number += page.number
            for prefix in (page.url, *page.also):
                # The longest address that fits wins: /ops/payments/settings/ before /ops/payments/.
                fits = path == prefix if page.name == "dashboard" else path.startswith(prefix)
                if fits and len(prefix) > best_length:
                    best, best_length = (area, page), len(prefix)
    if best:
        best[0].current = best[1].current = True
    current = best[0] if best else None
    return {"areas": menu, "area": current, "pages": current.pages if current and len(current.pages) > 1 else []}
