#!/usr/bin/env python3
"""Generates the three hand-laid-out architecture figures as standalone SVG files."""
import html
import pathlib
import sys

OUT = pathlib.Path(sys.argv[1])
FONT = 'Arial, "Helvetica Neue", Helvetica, sans-serif'

CSS = f"""
.cdg text{{font-family:{FONT};fill:#1c2230}}
.cdg .cd-fig{{font-size:15px;font-weight:700;fill:#1c2230}}
.cdg .cd-title{{font-size:13px;font-weight:700}}
.cdg .cd-sub{{font-size:11.5px;fill:#465064}}
.cdg .cd-zone{{font-size:11px;font-weight:700;letter-spacing:.07em}}
.cdg .cd-zc-ext{{fill:#5b6577}}
.cdg .cd-zc-app{{fill:#2f59b0}}
.cdg .cd-zc-ai{{fill:#6540b8}}
.cdg .cd-zc-ops{{fill:#23794e}}
.cdg .cd-lab{{font-size:11px;fill:#2b3345}}
.cdg .cd-note{{font-size:11.5px;fill:#465064}}
.cdg .cd-num{{font-size:11.5px;font-weight:700;fill:#ffffff}}
.cdg .cd-badge{{fill:#1c2230}}
.cdg .cd-badge-g{{fill:#c2255c}}
.cdg .cd-bg{{fill:#ffffff}}
.cdg .cd-box{{fill:#ffffff;stroke:#5b6577;stroke-width:1.2}}
.cdg .cd-infra{{fill:#f6f8fb;stroke:#5b6577;stroke-width:1.2;stroke-dasharray:4 3}}
.cdg .cd-ext{{fill:#f1f3f6;stroke:#7c8698;stroke-width:1.2}}
.cdg .cd-prod{{fill:#ffffff;stroke:#7c8698;stroke-width:1.2;stroke-dasharray:5 4}}
.cdg .cd-3p{{fill:#fff4dc;stroke:#c98a12;stroke-width:1.2}}
.cdg .cd-opsbox{{fill:#e8f6ee;stroke:#2e9460;stroke-width:1.2}}
.cdg .cd-z-ext{{fill:#f1f3f6;stroke:#9aa3b2;stroke-width:1}}
.cdg .cd-z-app{{fill:#eaf1fd;stroke:#3f6fd1;stroke-width:1.2}}
.cdg .cd-z-ai{{fill:#f2ecfe;stroke:#7a52d1;stroke-width:1.2}}
.cdg .cd-z-ops{{fill:#e8f6ee;stroke:#2e9460;stroke-width:1.2}}
.cdg .cd-grp{{fill:none;stroke:#7a52d1;stroke-width:1;stroke-dasharray:3 3}}
.cdg .cd-guard{{fill:#fff5f8;stroke:#c2255c;stroke-width:2}}
.cdg .cd-line{{stroke:#3d4658;stroke-width:1.4;fill:none}}
.cdg .cd-dash{{stroke-dasharray:5 4}}
.cdg .cd-head{{fill:#3d4658}}
.cdg .cd-lbg{{fill:#ffffff;opacity:.92}}
"""


class Fig:
    def __init__(self, fid, w, h, title, desc):
        self.fid, self.w, self.h, self.title, self.desc = fid, w, h, title, desc
        self.el = []

    def add(self, s):
        self.el.append(s)

    def rect(self, x, y, w, h, cls, rx=8):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" class="{cls}"/>')

    def text(self, x, y, s, cls, anchor="middle", extra=""):
        self.add(f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}"{extra}>{html.escape(s)}</text>')

    def box(self, x, y, w, h, title, subs=(), cls="cd-box", num=None, guard=False):
        self.rect(x, y, w, h, cls)
        block = 14 + len(subs) * 15
        ty = y + (h - block) / 2 + 11
        self.text(x + w / 2, round(ty, 1), title, "cd-title")
        for i, s in enumerate(subs):
            self.text(x + w / 2, round(ty + 17 + i * 15, 1), s, "cd-sub")
        if num is not None:
            bc = "cd-badge-g" if guard else "cd-badge"
            self.add(f'<circle cx="{x + 16}" cy="{y}" r="11" class="{bc}"/>')
            self.text(x + 16, y + 4, str(num), "cd-num")

    def line(self, pts, start=False, end=True, dash=False):
        d = "M" + " L".join(f"{x},{y}" for x, y in pts)
        a = ""
        if end:
            a += f' marker-end="url(#ah-{self.fid})"'
        if start:
            a += f' marker-start="url(#ah-{self.fid})"'
        cls = "cd-line" + (" cd-dash" if dash else "")
        self.add(f'<path d="{d}" class="{cls}"{a}/>')

    def label(self, x, y, s, anchor="middle", bg=True, rotate=False, cls="cd-lab"):
        w = len(s) * 5.7 + 10
        if bg and not rotate:
            if anchor == "middle":
                rx = x - w / 2
            elif anchor == "start":
                rx = x - 5
            else:
                rx = x - w + 5
            self.add(f'<rect x="{round(rx, 1)}" y="{y - 12}" width="{round(w, 1)}" height="16" rx="3" class="cd-lbg"/>')
        extra = f' transform="rotate(-90 {x} {y})"' if rotate else ""
        self.text(x, y, s, cls, anchor, extra)

    def render(self):
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
            f'width="{self.w}" height="{self.h}" class="cdg" role="img" '
            f'aria-labelledby="t-{self.fid} d-{self.fid}">\n'
            f'<title id="t-{self.fid}">{html.escape(self.title)}</title>\n'
            f'<desc id="d-{self.fid}">{html.escape(self.desc)}</desc>\n'
            f"<style>{CSS}</style>\n"
            f'<defs><marker id="ah-{self.fid}" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" class="cd-head"/></marker></defs>\n'
            f'<rect x="0" y="0" width="{self.w}" height="{self.h}" class="cd-bg"/>\n'
        )
        return head + "\n".join(self.el) + "\n</svg>\n"


# --------------------------------------------------------------------------
# Figure 1 - system context
# --------------------------------------------------------------------------
def fig_context():
    f = Fig(
        "ctx", 1200, 690,
        "Cassilion web platform - system context",
        "Visitors, customers, customer AI agents and the installed Cassilion database on the left; "
        "the web platform with its isolated AI support enclave in the centre; payment provider, "
        "e-mail delivery, operators and the product repository on the right.",
    )
    f.text(30, 30, "Figure 1 — System context", "cd-fig", "start")

    # system boundary
    f.rect(365, 52, 470, 608, "cd-z-app", 14)
    f.text(385, 76, "CASSILION WEB PLATFORM (SYSTEM BOUNDARY)", "cd-zone cd-zc-app", "start")

    cx, cw, ch = 390, 420, 56
    rows = [100, 170, 240, 310, 380]
    centre = [
        ("Public site & documentation", ["product pages · docs · pricing · legal"]),
        ("Customer area & shop", ["accounts · plans · licenses · downloads · invoices"]),
        ("Support desk", ["tickets · input guardrails · release gate · sent log"]),
        ("Customer MCP", ["the same functions for the customer's AI agent"]),
        ("Backoffice & operator MCP", ["review queue · bug alerts · sales · audit"]),
    ]
    for y, (t, s) in zip(rows, centre):
        f.box(cx, y, cw, ch, t, s)

    # AI enclave
    f.rect(390, 456, 420, 186, "cd-z-ai", 10)
    f.text(406, 478, "AI SUPPORT ENCLAVE (ISOLATED)", "cd-zone cd-zc-ai", "start")
    f.box(410, 490, 380, 62, "First-level support AI",
          ["analyst → bridge → responder → output guard"])
    f.box(410, 564, 380, 62, "Knowledge base",
          ["public docs + private source snapshot (never leaves)"])

    # left actors
    lx, lw = 30, 210
    f.box(lx, rows[0], lw, ch, "Visitor", ["anonymous · reads site and docs"], "cd-ext")
    f.box(lx, rows[1], lw, ch, "Customer", ["person or company member"], "cd-ext")
    f.box(lx, rows[2], lw, ch, "Cassilion DB at the customer", ["checks its license file offline"], "cd-prod")
    f.box(lx, rows[3], lw, ch, "Customer's AI agent", ["any MCP client (IDE, assistant)"], "cd-ext")

    # right side
    rx, rw = 960, 210
    f.box(rx, rows[1], rw, ch, "Payment provider", ["checkout · tax · invoices"], "cd-3p")
    f.box(rx, rows[2], rw, ch, "E-mail delivery", ["SMTP relay"], "cd-3p")
    f.box(rx, rows[4], rw, ch, "Operators (Cassilion team)", ["VPN / allow-list + MFA"], "cd-opsbox")
    f.box(rx, 564, rw, 62, "Product repo, docs & CI", ["private Git · signed builds"], "cd-opsbox")

    mid = lambda y: y + ch // 2
    # left arrows
    f.line([(240, mid(rows[0])), (390, mid(rows[0]))]); f.label(302, mid(rows[0]) - 7, "HTTPS")
    f.line([(240, mid(rows[1])), (390, mid(rows[1]))]); f.label(302, mid(rows[1]) - 7, "HTTPS · login + MFA")
    f.line([(240, mid(rows[2])), (390, mid(rows[2]))], dash=True); f.label(302, mid(rows[2]) - 7, "diagnostics bundle")
    f.line([(240, mid(rows[3])), (390, mid(rows[3]))]); f.label(302, mid(rows[3]) - 7, "MCP · OAuth 2.1")
    # right arrows
    f.line([(810, mid(rows[1])), (960, mid(rows[1]))], start=True); f.label(898, mid(rows[1]) - 7, "checkout · webhooks")
    f.line([(810, mid(rows[2])), (960, mid(rows[2]))]); f.label(898, mid(rows[2]) - 7, "transactional mail")
    f.line([(810, mid(rows[4])), (960, mid(rows[4]))], start=True); f.label(898, mid(rows[4]) - 7, "review · alerts")
    f.line([(790, 595), (960, 595)]); f.label(898, 588, "fetches snapshot")
    # enclave pulls jobs from support desk
    f.line([(410, 521), (378, 521), (378, 280), (390, 280)])
    f.label(356, 452, "pulls jobs · returns drafts", rotate=True)
    f.text(30, 676, "Arrows point from the side that opens the connection. Dashed: carried by the customer inside a ticket. "
           "Amber: third-party services. Green: operator side.", "cd-note", "start")
    return f


# --------------------------------------------------------------------------
# Figure 2 - building blocks and trust zones
# --------------------------------------------------------------------------
def fig_blocks():
    f = Fig(
        "blk", 1200, 880,
        "Cassilion web platform - building blocks and trust zones",
        "Internet clients reach a reverse proxy, which fronts a Django modular monolith in the application zone. "
        "An isolated AI support enclave pulls jobs through a narrow job API. Operators and the product Git/CI "
        "form a separate zone.",
    )
    f.text(30, 30, "Figure 2 — Building blocks and trust zones", "cd-fig", "start")

    # internet row
    f.rect(30, 46, 1140, 96, "cd-z-ext", 10)
    f.text(46, 66, "INTERNET (UNTRUSTED)", "cd-zone cd-zc-ext", "start")
    for x, t, s in [(46, "Browsers", "visitors · customers"),
                    (420, "MCP clients", "customers' AI agents"),
                    (794, "Payment provider", "signed webhooks")]:
        f.box(x, 78, 360, 50, t, [s], "cd-box")
        f.line([(x + 180, 128), (x + 180, 162)])

    # edge
    f.rect(30, 162, 1140, 44, "cd-box", 8)
    f.text(46, 189, "Edge — reverse proxy (Caddy)", "cd-title", "start")
    f.text(262, 189, "TLS · rate limits · body-size limits · security headers / CSP · separate host names: www · mcp · ops",
           "cd-sub", "start")
    f.line([(600, 206), (600, 232)])

    # application zone
    f.rect(30, 232, 1140, 304, "cd-z-app", 10)
    f.text(46, 254, "APPLICATION ZONE — DJANGO MODULAR MONOLITH (ONE CODEBASE, ONE IMAGE)", "cd-zone cd-zc-app", "start")
    cols = [46, 270, 494, 718, 942]
    w = 208
    row1 = [
        ("web", ["public site · docs · i18n", "pricing · legal pages"], "cd-box"),
        ("accounts", ["users · organisations · roles", "addresses · MFA · consents"], "cd-box"),
        ("billing", ["plans · orders · subscriptions", "provider adapter · webhooks"], "cd-box"),
        ("licensing", ["entitlements · signed license", "files · key ids · revocation"], "cd-box"),
        ("downloads", ["release catalogue", "expiring links · checksums"], "cd-box"),
    ]
    row2 = [
        ("support", ["tickets · state machine", "quotas · release gate"], "cd-box"),
        ("guard", ["input guardrails: normalise", "redact · classify · decide"], "cd-guard"),
        ("mcp", ["customer MCP · operator MCP", "OAuth 2.1 resource server"], "cd-box"),
        ("backoffice", ["review queue · sent log", "bug alerts · kill switch"], "cd-box"),
        ("audit", ["append-only event log", "who did what, acting as whom"], "cd-box"),
    ]
    row3 = [
        ("Job API", ["the only door to the enclave"], "cd-guard"),
        ("PostgreSQL", ["customers · orders · tickets"], "cd-infra"),
        ("Task queue + worker", ["mail · webhooks · issuing"], "cd-infra"),
        ("License signer", ["Ed25519 key, not in the repo"], "cd-infra"),
        ("Object storage", ["release artefacts · attachments"], "cd-infra"),
    ]
    for x, (t, s, c) in zip(cols, row1):
        f.box(x, 268, w, 72, t, s, c)
    for x, (t, s, c) in zip(cols, row2):
        f.box(x, 354, w, 72, t, s, c)
    f.text(46, 449, "Runtime services", "cd-sub", "start")
    for x, (t, s, c) in zip(cols, row3):
        f.box(x, 458, w, 58, t, s, c)

    # AI enclave
    f.rect(30, 566, 720, 276, "cd-z-ai", 10)
    f.text(734, 588, "AI SUPPORT ENCLAVE — NO INBOUND PORT · NO INTERNET EGRESS", "cd-zone cd-zc-ai", "end")
    ec = [46, 220, 394, 568]
    ew = 160
    f.box(ec[0], 602, ew, 64, "AI worker", ["pulls and runs jobs", "counts tokens + cost"])
    f.box(ec[1], 602, ew, 64, "Analyst", ["docs + source code", "→ structured finding"])
    f.box(ec[2], 602, ew, 64, "Bridge", ["schema-only hand-over", "no source text crosses"], "cd-guard")
    f.box(ec[3], 602, ew, 64, "Responder", ["public docs only", "→ draft reply"])
    f.box(ec[0], 682, ew, 64, "Evaluation set", ["golden tickets", "regression runs"])
    f.box(ec[1], 682, 334, 64, "LLM runtime (Ollama / vLLM)", ["OpenAI-compatible gateway · model swap by config"])
    f.box(ec[3], 682, ew, 64, "Output guard", ["DLP · fingerprint · canary", "grounding · policy judge"], "cd-guard")
    f.box(ec[0], 762, 682, 64, "Knowledge index",
          ["public corpus: docs, release notes, known issues  ·  internal corpus: source snapshot per release tag, design notes"])
    f.line([(206, 634), (220, 634)])
    f.line([(380, 634), (394, 634)])
    f.line([(554, 634), (568, 634)])
    f.line([(648, 666), (648, 682)])

    # operator zone
    f.rect(780, 566, 390, 276, "cd-z-ops", 10)
    f.text(796, 588, "OPERATOR ZONE", "cd-zone cd-zc-ops", "start")
    f.box(796, 602, 340, 64, "Operator workstations", ["browser + operator MCP client", "VPN / allow-list + MFA"])
    f.box(796, 682, 340, 64, "Notifications", ["e-mail + push on suspected bug / security"])
    f.box(796, 762, 340, 64, "Product Git + CI", ["private repo · issues · signed release builds"])

    # cross-zone arrows
    f.line([(126, 602), (126, 516)])
    f.label(140, 555, "outbound HTTPS only — claims job, posts result", "start")
    f.line([(900, 536), (900, 566)], start=True)
    f.label(888, 555, "review · approve · alerts", "end")
    f.line([(728, 794), (796, 794)])
    f.line([(1136, 794), (1162, 794), (1162, 487), (1150, 487)])
    f.label(1150, 555, "CI publishes signed builds", "end")
    f.text(30, 866, "Red outline: guardrail or controlled crossing point. Dashed: runtime services. Arrows point from the side that opens "
           "the connection: the enclave fetches jobs and repo snapshots itself; nothing connects into it.", "cd-note", "start")
    return f


# --------------------------------------------------------------------------
# Figure 7 - AI support pipeline with guardrails
# --------------------------------------------------------------------------
def fig_pipeline():
    f = Fig(
        "pipe", 1200, 830,
        "Cassilion AI first-level support - pipeline with guardrails",
        "A ticket passes an entitlement gate and input guardrails in the application zone, is analysed and "
        "answered in the isolated AI enclave by an analyst and a responder separated by a schema-only bridge, "
        "passes output guardrails and is routed by a release gate to auto-send, operator review or bug alert.",
    )
    f.text(30, 30, "Figure 7 — AI first-level support: pipeline with guardrails", "cd-fig", "start")

    # lane 1
    f.rect(30, 48, 1140, 168, "cd-z-app", 10)
    f.text(1154, 70, "APPLICATION ZONE — INTAKE", "cd-zone cd-zc-app", "end")
    xs = [50, 330, 610, 890]
    f.box(xs[0], 96, 250, 100, "Ticket intake",
          ["web form or customer MCP", "text · product version · logs", "signed-in customers only"], num=1)
    f.box(xs[1], 96, 250, 100, "G1 · Entitlement gate",
          ["does the plan include support?", "quota and rate limit", "size and file-type limits"], "cd-guard", num=2, guard=True)
    f.box(xs[2], 96, 250, 100, "G2 · Input guardrails",
          ["normalise · redact secrets and PII", "injection + scope classifiers", "reject · flag for review · pass"], "cd-guard", num=3, guard=True)
    f.box(xs[3], 96, 250, 100, "Ticket stored, job queued",
          ["original kept for operators", "redacted copy for the AI", "status: analysing"], num=4)
    for a, b in zip(xs, xs[1:]):
        f.line([(a + 250, 146), (b, 146)])

    # lane 2
    f.rect(30, 246, 1140, 226, "cd-z-ai", 10)
    f.text(1154, 268, "AI SUPPORT ENCLAVE — NO INTERNET · NO CUSTOMER DATABASE · READ-ONLY TOOLS", "cd-zone cd-zc-ai", "end")
    f.rect(42, 282, 444, 172, "cd-grp", 8)
    f.rect(714, 282, 444, 172, "cd-grp", 8)
    f.text(54, 444, "internal context — may read the private source", "cd-lab", "start")
    f.text(726, 444, "public context only — never sees private source", "cd-lab", "start")
    es = [50, 274, 498, 722, 946]
    f.box(es[0], 304, 204, 106, "Retrieve",
          ["public docs + known issues", "source at the customer's", "release tag (read-only)"], num=5)
    f.box(es[1], 304, 204, 106, "Analyst",
          ["usage error or product bug?", "writes a structured finding", "with file:line pointers"], num=6)
    f.box(es[2], 304, 204, 106, "G3 · Bridge",
          ["schema-only hand-over:", "class · confidence · doc IDs", "no source text crosses"], "cd-guard", num=7, guard=True)
    f.box(es[3], 304, 204, 106, "Responder",
          ["sees public docs only", "drafts the customer reply", "with links into the docs"], num=8)
    f.box(es[4], 304, 204, 106, "G4 · Output guardrails",
          ["secrets · source fingerprint", "canaries · internal symbols", "grounding · policy judge"], "cd-guard", num=9, guard=True)
    for a, b in zip(es, es[1:]):
        f.line([(a + 204, 357), (b, 357)])

    # connector lane1 -> lane2
    f.line([(1015, 196), (1015, 231), (152, 231), (152, 304)])
    f.label(600, 235, "the enclave pulls the job (outbound connection only)")

    # lane 3
    f.rect(30, 502, 1140, 286, "cd-z-app", 10)
    f.text(1154, 524, "APPLICATION ZONE + OPERATORS — RELEASE", "cd-zone cd-zc-app", "end")
    f.box(50, 588, 204, 110, "G5 · Release gate",
          ["routes by class,", "confidence and", "guard report"], "cd-guard", num=10, guard=True)
    oy = [540, 616, 692]
    f.box(320, oy[0], 400, 62, "Usage, configuration or known issue",
          ["confident and all guards green → sent automatically *"])
    f.box(320, oy[1], 400, 62, "Low confidence, guard flag or documentation gap",
          ["review queue → an operator approves, edits or rejects"], "cd-opsbox")
    f.box(320, oy[2], 400, 62, "Suspected bug or security issue",
          ["operators alerted at once · customer gets an acknowledgement"], "cd-opsbox")
    f.box(776, 588, 180, 110, "Sent log + audit",
          ["every outgoing reply", "visible to operators", "with its guard report"])
    f.box(1000, 588, 150, 110, "Customer",
          ["portal · MCP", "e-mail notice", "marked as AI reply"], "cd-ext")
    for y in oy:
        f.line([(254, 643), (287, 643), (287, y + 31), (320, y + 31)])
        f.line([(720, y + 31), (748, y + 31), (748, 643), (776, 643)])
    f.line([(956, 643), (1000, 643)])

    # connector lane2 -> lane3
    f.line([(1048, 410), (1048, 487), (152, 487), (152, 588)])
    f.label(600, 491, "result: finding (pointers only) + draft reply + guard report")

    f.text(46, 776, "* Starts switched off: in shadow mode every reply is reviewed. Enabled later per class and plan, "
           "with a global kill switch. Operator edits feed the evaluation set.", "cd-note", "start")
    f.text(30, 814, "Red outline G1–G5: guardrails. Green: an operator decides. "
           "The model that writes to the customer (8) never has the private source in its context.", "cd-note", "start")
    return f


# --------------------------------------------------------------------------
# Figure 10 - environments
# --------------------------------------------------------------------------
def fig_env():
    f = Fig(
        "env", 1200, 560,
        "Cassilion web platform - local development and production environments",
        "The same container images run locally under Docker Compose and in production. In both, an AI worker "
        "in its own zone pulls jobs from the web application; production splits into an app server and an AI node.",
    )
    f.text(30, 30, "Figure 10 — Environments: local first, then production", "cd-fig", "start")

    def panel(x, title, app_title, app_boxes, ai_title, ai_boxes):
        f.rect(x, 48, 470, 452, "cd-z-ext", 10)
        f.text(x + 16, 70, title, "cd-zone cd-zc-ext", "start")
        f.rect(x + 14, 82, 442, 216, "cd-z-app", 10)
        f.text(x + 28, 103, app_title, "cd-zone cd-zc-app", "start")
        for i, (t, sub) in enumerate(app_boxes):
            bx = x + 28 + (i % 2) * 212
            by = 114 + (i // 2) * 58
            f.box(bx, by, 202, 50, t, [sub], "cd-infra" if t in ("PostgreSQL + pgvector", "PostgreSQL", "Object storage", "MinIO") else "cd-box")
        f.rect(x + 14, 334, 442, 152, "cd-z-ai", 10)
        f.text(x + 442, 355, ai_title, "cd-zone cd-zc-ai", "end")
        for i, (t, sub) in enumerate(ai_boxes[:2]):
            f.box(x + 28 + i * 212, 366, 202, 50, t, [sub])
        t, sub = ai_boxes[2]
        f.box(x + 28, 424, 414, 50, t, [sub], "cd-infra")
        f.line([(x + 129, 366), (x + 129, 298)])
        f.label(x + 141, 321, "pulls jobs — outbound HTTPS only", "start")

    panel(30, "LOCAL DEVELOPMENT — WINDOWS 11 + DOCKER COMPOSE", "APP SERVICES",
          [("web", "Django with hot reload"), ("worker", "task queue"),
           ("PostgreSQL + pgvector", "one container"), ("Mailpit", "catches all outgoing mail"),
           ("MinIO", "release artefacts"), ("Payment sandbox", "test checkout + webhook forwarder")],
          "AI SERVICES",
          [("ai-worker", "same image as production"), ("Ollama", "on the GPU workstation (LAN)"),
           ("Knowledge index", "built from a local checkout of the product repo")])
    panel(700, "PRODUCTION", "APP SERVER (EU)",
          [("Caddy", "TLS · rate limits · host names"), ("web", "Django on ASGI"),
           ("worker", "task queue"), ("PostgreSQL", "nightly backup, off-site copy"),
           ("Object storage", "release artefacts"), ("Monitoring", "errors · uptime · site_status")],
          "AI NODE — OWN GPU OR DEDICATED SERVER",
          [("ai-worker", "no inbound port"), ("LLM runtime", "Ollama / vLLM"),
           ("Knowledge index + repo snapshot", "the private source lives only here")])

    f.box(530, 222, 140, 104, "Git + CI", ["tests · image build", "guardrail regression", "on golden tickets"], "cd-opsbox")
    f.line([(500, 250), (530, 250)])
    f.line([(670, 250), (700, 250)])
    f.text(600, 212, "git push → same images →", "cd-lab")
    f.text(30, 524, "Staging is the production Compose file on a second small host. Settings come from environment variables; "
           "secrets and the license signing key never live in the repo.", "cd-note", "start")
    f.text(30, 544, "The AI zone has the same shape everywhere, so the pull-only rule is exercised from the first local run.",
           "cd-note", "start")
    return f


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fig in [
        ("01-system-context.svg", fig_context()),
        ("02-building-blocks-trust-zones.svg", fig_blocks()),
        ("07-ai-support-pipeline.svg", fig_pipeline()),
        ("10-environments.svg", fig_env()),
    ]:
        (OUT / name).write_text(fig.render(), encoding="utf-8")
        print("wrote", name)
