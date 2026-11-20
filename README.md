# Cassilion web platform

The website that sells and supports the Cassilion database: public site and documentation,
registration, plans and licenses, downloads, support tickets with an AI first line behind
guardrails, a backoffice for the team, and MCP servers for customers' AI agents and for
the team's own assistants.

**Status: runnable skeleton.** Every step of the path exists and is covered by a test.
Nothing is production-ready, and several parts are stand-ins (see
[What is real, what is a stand-in](#what-is-real-what-is-a-stand-in)).

- [docs/architecture/index.html](docs/architecture/index.html) — architecture concept v0.2: the
  reasoning behind the design, decisions taken and open.
- [site/index.html](site/index.html) — the original static draft of the landing page (v0.1).
  It has been ported into the application and is the home page now; the draft is kept for
  reference and is no longer the place to edit.

"Cassilion" is the product's public name for now; the site is to run under cassilion.com.
The folder and the project are still spelled "Casilion".

## Quick start

Needs Python 3.12 or newer. No Docker, no database server, no GPU.

```powershell
# Windows (PowerShell), in D:\Code\Casilion
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py dev
```

```bash
# Linux, macOS
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python manage.py dev
```

`dev` creates the database, loads demo data on the first run, and starts two processes:
the web application with both MCP endpoints on <http://127.0.0.1:8000>, and the AI worker.
Mails are printed to the console. Runtime state lives in `var/` (delete it to start over).

### Demo logins

Password for all three: `cassilion-dev`

| Who | Sign in | What you see |
|---|---|---|
| `demo@example.com` | <http://127.0.0.1:8000/auth/login/> | Example GmbH on the Professional plan, two sample tickets |
| `community@example.com` | same | A private person on the free Community plan |
| `ops@cassilion.example` | same, then <http://127.0.0.1:8000/ops/> | The backoffice: review queue, sent log, switches |

The demo agent tokens for MCP are written to `var/dev-credentials.txt`.

### A tour in five minutes

1. Sign in as `ops@cassilion.example` and open the **review queue**. Two tickets wait: a
   configuration question with a draft reply from the support AI, and a crash report that was
   classified as a possible product bug. Open the first, read finding and guard report,
   edit the draft, send it.
2. Sign in as `demo@example.com`. The reply is there, marked as written by AI. Reply to it,
   or ask for a person.
3. Register a new account, look at the license file, try to open a ticket (the Community
   plan has none), buy a plan through the sandbox checkout, open a ticket.
4. Connect an AI agent (next section) and let it do the same.

## Design

One look for the whole site, taken from the landing page: a dark technical ground, a single
amber signal colour for everything that is Cassilion and for the action to take, a cool blue
for everything that is not Cassilion, mono labels for terms of art.

| File | What it holds |
|---|---|
| `static/css/tokens.css` | Colours, typefaces, radius. The one place to change the look. |
| `static/css/landing.css`, `static/js/landing.js`, `templates/core/home.html` | The landing page. The competitor scores for the table and both charts are the `PRODUCTS` array in `landing.js`. The plans in the "Early access" box come from the database. |
| `static/css/site.css` | Everything else: documentation, plans, sign-in, customer area, backoffice. |
| `templates/base.html` | Header and footer, shared by all pages. |
| `templates/_brand.html` | The logo (mark and name) as the pages use it. Standalone files for use elsewhere: `static/img/cassilion-logo.svg` (name as outlines, for dark surfaces), `static/img/cassilion-mark.svg`, `static/img/favicon.svg`. |
| `static/video/node-globe.webm`, `.mp4`, `static/img/node-globe.jpg` | The rotating node globe behind the hero: 720 px square, 24 frames a second, cut from the 1080p master (not in the repository). It plays only while the hero is on screen, and not at all for visitors who asked for reduced motion or data saving; then the still image shows. |

The typefaces (Bricolage Grotesque, Geist, Geist Mono) are self-hosted under the SIL Open Font
License. The content security policy allows no third-party origin and no inline style or script.

## Connecting an AI agent (MCP)

| Endpoint | For | Token |
|---|---|---|
| `http://127.0.0.1:8000/mcp` | A customer's AI agent | Customer area → AI agents |
| `http://127.0.0.1:8000/ops/mcp` | The team's assistant | Backoffice → Switches |

Both speak streamable HTTP and expect `Authorization: Bearer <token>`. Most MCP clients
take a server entry of this shape; where the file lives differs by client:

```json
{
  "mcpServers": {
    "cassilion": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer cas_agent_…" }
    }
  }
}
```

A client has to be able to send a custom header. Clients that only offer an OAuth sign-in
cannot connect yet; OAuth is the next step for this part.

Customer tools: `search_docs`, `read_doc`, `list_known_issues` (knowledge, open to every plan),
`get_account`, `list_licenses`, `get_license_file`, `list_releases`, `get_download_link`,
`get_checkout_link`, `report_bug`, `get_ticket`, `list_tickets`, and on paid plans
`open_ticket`, `reply_ticket`, `request_person`. Tickets are asynchronous: `open_ticket`
returns an ID, `get_ticket` says what is still needed and what happens next.

Operator tools: `site_status`, `list_review_queue`, `get_ticket_analysis`, `approve_reply`,
`reject_reply`, `reply_as_person`, `list_bug_candidates`, `confirm_bug`, `sales_overview`,
`set_auto_send`.

## How a ticket travels

```
customer or agent ──► G1 entitlement ──► G2 input guard ──► job queue
                      plan, quota        size, hidden text,      │
                                         injection, secrets      │  the worker pulls;
                                                                 ▼  nothing connects into it
        ┌──────────────────────── AI worker (enclave) ───────────────────────┐
        │  Analyst                 G3 bridge              Responder          │
        │  public docs             classes and public     public docs only   │
        │  + product source  ───►  document IDs only ───► writes the draft   │
        │  (optional)                                         │              │
        │                                           G4 output guard          │
        │                              secrets, canaries, source fingerprint,│
        │                              internal paths, links, grounding      │
        └────────────────────────────────────┬───────────────────────────────┘
                                             ▼
                              G5 release gate (application)
               schema, secret scan, switches ──► review queue or customer
```

- The worker receives ticket text with secrets and e-mail addresses removed, and no customer
  identity. It can read and return a result; it cannot send, look anything up or change anything.
- The Responder never sees what the Analyst read in the source. A draft that fails a leak
  check does not leave the worker at all.
- **Shadow mode** is the default: every AI-written reply waits for a person in the backoffice.
  Fixed texts (acknowledgement of a bug report, request for the diagnostics bundle) go out directly.
- A suspected bug or security problem is never answered by the AI. The customer gets a fixed
  text, the operators get a mail without customer content, and the ticket waits as a bug candidate.
- Reports of data loss, corruption or a vulnerability are marked critical before any analysis.
- After a set number of follow-ups, or when a ticket's token budget is used up, a person takes over.

## Switching on a model

By default the worker uses rules instead of a model (`LLM_BACKEND=stub`): deterministic,
no GPU, good enough to exercise the flow, not smart. To use a model, copy `.env.example`
to `.env` and set:

```ini
LLM_BACKEND=openai
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL=<a model your endpoint serves>
```

Any OpenAI-compatible endpoint works (Ollama, vLLM, llama.cpp server, LM Studio, a hosted API).
If the endpoint fails, the rules take over and the finding is marked low confidence.

To let the Analyst read the product's source, point `PRODUCT_REPO_DIR` at a read-only
checkout on the machine that runs the worker. Plant the strings from
`knowledge/internal/canaries.txt` as comments in that source; if one ever shows up in a
draft, the reply is blocked.

## Layout

```
manage.py            `python manage.py dev` starts everything
config/              settings, urls, asgi (Django plus the MCP endpoints)
apps/
  core/              site, documentation pages, dev and seed commands
  accounts/          users, accounts, members, address, tax profile
  billing/           plans, subscriptions, orders, provider adapter, sandbox
  licensing/         entitlements, signed license files
  downloads/         releases, artefacts, expiring links
  support/           tickets, messages, jobs, release gate, job API
  guard/             input guard
  mcp_server/        agent tokens, customer and operator MCP
  backoffice/        review queue, sent log, switches
  audit/             who did what
shared/              used on both sides of the wall: schemas, redaction, corpus
ai_worker/           the enclave: analyst, responder, output guard, job client
knowledge/           public documentation, known issues, canaries
templates/ static/   pages and design (see Design)
tests/               shop, support flow, guards, MCP, pages
docs/architecture/   concept and diagrams
```

`ai_worker` and `shared` import nothing from Django. The worker talks to the application
through the job API only (`/internal/jobs/…`, bearer token), so it can move to its own
host without change.

## Commands

```
python manage.py dev                       start web application and worker
python manage.py dev --no-worker           web application only
python -m ai_worker                        worker only (--once: one job, then exit)
python manage.py test tests                the test suite
python manage.py seed_demo --tickets       demo data (development only)
python manage.py create_operator <email>   an operator account for the backoffice (server)
python manage.py expire_subscriptions      daily: end plans that ran out, back to Community
python manage.py close_stale_tickets       daily: close tickets the customer left unanswered
python manage.py verify_license <file>     check a license file against the public key
```

Raw data and everything without a screen of its own: <http://127.0.0.1:8000/ops/admin/>.

## What is real, what is a stand-in

| Part | State |
|---|---|
| Accounts, plans, entitlements, quotas | Real logic. Plan names, prices and quotas are placeholders (migration `billing/0002_seed_plans`). |
| License files | **Stand-in.** The website signs a small file (Ed25519, key in `var/keys`) that names account and plan. A Cassilion cluster does not verify this file; see the next section. |
| Payments | **Stand-in.** A sandbox provider with its own checkout page and signed webhooks. No money moves. No invoices, no tax. A real provider plugs in behind `apps/billing/providers/base.py`. |
| Downloads | Real links with expiry and a download log. The build file is a placeholder. |
| Input guard, redaction, release gate | Real, rule-based. The injection patterns are a first set, not a study. |
| AI analysis | **Stand-in by default** (rules). The model path exists and is tested against a fake endpoint, not against a real model. Prompts are a first draft. |
| Output guard | Real checks. The source fingerprint and grounding checks need tuning on real material. |
| Documentation, known issues | **Invented examples.** Commands and option names are not Cassilion's. |
| MCP sign-in | **Interim.** Personal agent tokens. Two-factor can be required (`REQUIRE_MFA_FOR_AGENT_TOKENS`). OAuth 2.1 is the target. |
| Legal pages, texts | Imprint, privacy notice, terms, withdrawal policy in German and English; withdrawal function, cancellation button, binding order page. First version without a lawyer's review, see [docs/legal.md](docs/legal.md). The rest of the site is English only. |
| Operations | Missing: backups, rate limits, monitoring, deployment, evaluation set for the AI. |

## Open points against the product

Found when the product's functionality summary was read after the skeleton was built.
None of them is solved here.

1. **Licenses.** The product verifies licenses that bind a cluster, product and version, edition,
   capacity, term and features, signed with ES256 and ML-DSA-65, and issued with the separate
   offline tool `zuri_license_issuer`. An expired license puts a cluster into a read-only grace
   state and then a recovery-only state. The website therefore cannot sign licenses itself, needs
   a cluster identity from the customer before one can be issued, and must keep "support plan"
   and "license term" apart. What the skeleton has is entitlement bookkeeping plus a stand-in
   file. The swap point is `apps/licensing/` (`codec.py`, `services.issue_license`). Open: what a
   €0 license contains (edition, capacity, term), who runs the issuer, and how an issued file
   gets from the offline issuer to the customer area.
   Decided so far: a signing service is the target; at the start a person issues the license
   with the offline tool and uploads it. The details are deferred.
2. **Documentation and diagnostics.** The pages under `knowledge/public/` are invented. The real
   guides live in the product repository (`docs/guides/`), the real tooling is the admin API
   and its client. The split between public guides and internal architecture and implementation
   records in that repository is the natural line between public and internal knowledge.
3. **Components.** Tickets are filed under a short invented component list (`shared/schemas.py`).
   It should follow the product's areas: storage, ZQL, replication, search, geospatial, graph,
   knowledge bases, drivers, security, operations, deployment.
4. **Hosting.** The domains are to point at the server that also runs Tango-Kalender. The AI
   worker and the product source must not live on that machine; the worker only needs an
   outbound connection to the job API.

## Tests

```
python manage.py test tests
```

56 tests: purchase and license, entitlement and quota, the ticket flow through the worker,
injection and redaction, the wall between Analyst and Responder, every output check, both
MCP endpoints through the real ASGI application, and the pages of customer area and backoffice.
The suite passes on SQLite and on PostgreSQL 16.

## Legal pages

Imprint, privacy notice, terms and withdrawal policy (German binding, English translation), the
withdrawal function and the cancellation button German law asks for, and an order page with a
binding order button. What exists, which assumptions it rests on and what to check before the
site goes public: [docs/legal.md](docs/legal.md).

## On the server

[docs/server-setup.md](docs/server-setup.md) is the step-by-step procedure: the same pattern
as Tango-Kalender (host nginx, the stack from `docker-compose.yml` on `127.0.0.1:8150`,
`/opt/cassilion`, nightly backup). The whole application runs there from the start, with
search engines kept out (`SITE_NOINDEX=1`: header, meta tag, closed `robots.txt`).

The start-up sequence was rehearsed under production settings without Docker and nginx.
**The Docker image has never been built and the nginx files have never been loaded.**

## Before this goes anywhere public

With `DEBUG=0` the application refuses to start without `SECRET_KEY` and `AI_WORKER_TOKEN`,
and refuses the sandbox payment provider unless `.env` acknowledges it (`ALLOW_SANDBOX_PAYMENTS=1`). Beyond that, see the status table above and
sections 10 to 13 of the architecture concept.
