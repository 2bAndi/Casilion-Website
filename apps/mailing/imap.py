"""A small IMAP client for the inbox, taken over from the Tangosummer ticket shop. Tests replace :func:`open_mailbox`.

Reading never changes the mailbox: messages are fetched with ``BODY.PEEK`` from a read-only
``SELECT``, so the mail program still shows them as unread. Only a reply sets ``\\Answered`` on
the original and files a copy in the Sent folder.
"""

from __future__ import annotations

import contextlib
import imaplib
import re
import socket
import ssl
import time
from datetime import date


class MailboxError(Exception):
    pass


class Mailbox:
    def __init__(self, host: str, user: str, password: str, port: int = 993):
        self.host, self.user, self.password = host, user, password
        self.port = port or 993
        self.conn: imaplib.IMAP4_SSL | None = None

    # -- connection ---------------------------------------------------------------------

    def __enter__(self) -> Mailbox:
        step = "connect"
        try:
            self.conn = imaplib.IMAP4_SSL(
                self.host, self.port, ssl_context=ssl.create_default_context(), timeout=30
            )
            step = "login"
            self.conn.login(self.user, self.password)
        except (OSError, imaplib.IMAP4.error) as exc:
            raise MailboxError(explain(exc, step, self.host, self.port, self.user)) from exc
        return self

    def __exit__(self, *exc) -> None:
        if self.conn is not None:
            with contextlib.suppress(Exception):  # closing a broken connection
                self.conn.logout()

    def _ok(self, result):
        status, data = result
        if status != "OK":
            raise MailboxError(f"IMAP: {status} {data!r}")
        return data

    # -- reading ------------------------------------------------------------------------

    def select(self, folder: str = "INBOX", readonly: bool = True) -> int:
        """Open a folder; returns its UIDVALIDITY."""
        self._ok(self.conn.select(folder, readonly=readonly))
        # imaplib answers response() with the code itself, not "OK": ("UIDVALIDITY", [b"123"])
        _code, data = self.conn.response("UIDVALIDITY")
        return int(data[0]) if data and data[0] else 0

    def uids_after(self, last_uid: int) -> list[int]:
        data = self._ok(self.conn.uid("SEARCH", None, f"UID {last_uid + 1}:*"))
        return sorted(u for u in map(int, (data[0] or b"").split()) if u > last_uid)

    def uids_since(self, day: date) -> list[int]:
        data = self._ok(self.conn.uid("SEARCH", None, f"SINCE {day.strftime('%d-%b-%Y')}"))
        return sorted(map(int, (data[0] or b"").split()))

    def fetch(self, uid: int) -> bytes | None:
        data = self._ok(self.conn.uid("FETCH", str(uid), "(BODY.PEEK[])"))
        for part in data:
            if isinstance(part, tuple) and len(part) > 1:
                return part[1]
        return None

    # -- after a reply --------------------------------------------------------------------

    def mark_answered(self, uid: int) -> None:
        self.select("INBOX", readonly=False)
        self._ok(self.conn.uid("STORE", str(uid), "+FLAGS", "(\\Answered)"))

    def folders(self) -> list[tuple[str, str]]:
        """``(flags, name)`` of every folder, e.g. ``("\\HasNoChildren \\Sent", "Sent Items")``."""
        found = []
        for line in self._ok(self.conn.list()):
            text = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
            match = re.search(r'\(([^)]*)\) "?[^" ]*"? "?([^"]+)"?$', text)
            if match:
                found.append((match.group(1), match.group(2)))
        return found

    def sent_folder(self) -> str:
        names = []
        for flags, name in self.folders():
            if "\\Sent" in flags:
                return name
            names.append(name)
        for candidate in ("Sent", "Sent Items", "Gesendet", "INBOX.Sent"):
            if candidate in names:
                return candidate
        return "Sent"

    def append_sent(self, raw: bytes) -> None:
        folder = self.sent_folder()
        quoted = f'"{folder}"' if " " in folder else folder
        self._ok(self.conn.append(quoted, "(\\Seen)", imaplib.Time2Internaldate(time.time()), raw))


def explain(exc: Exception, step: str, host: str, port: int, user: str) -> str:
    """What went wrong, in words that say where to look - the raw answer stays at the end."""
    raw = f"{exc.__class__.__name__}: {exc}"
    where = f"{host}:{port}"
    if isinstance(exc, socket.gaierror):
        hint = f"The name {host} is not found (DNS) - check the server in the settings."
    elif isinstance(exc, ConnectionRefusedError):
        hint = (
            f"{where} refuses the connection: IMAP with TLS (port {port}) is not open on the "
            "mail server - is its IMAP listener on and the port published/allowed?"
        )
    elif isinstance(exc, (TimeoutError, socket.timeout)):
        hint = f"No answer from {where} within 30 seconds - a firewall drops port {port}?"
    elif isinstance(exc, ssl.SSLCertVerificationError):
        hint = f"The certificate of {host} is not valid for this name ({exc.verify_message})."
    elif isinstance(exc, ssl.SSLError):
        hint = (
            f"{where} does not speak TLS right away - port {port} may expect STARTTLS "
            "(enter the IMAPS port, usually 993)."
        )
    elif step == "login" and isinstance(exc, imaplib.IMAP4.error):
        hint = (
            f"The mail server refuses the login as {user}: user name or password wrong, "
            "or IMAP not allowed for this account."
        )
    else:
        return raw
    return f"{hint} [{raw}]"


def open_mailbox(config) -> Mailbox:
    if not (config.effective_host and config.effective_user and config.effective_password):
        raise MailboxError("The mailbox is not set up: server, user and password are needed (Backoffice, Inbox, Settings).")
    return Mailbox(config.effective_host, config.effective_user, config.effective_password, config.port)
