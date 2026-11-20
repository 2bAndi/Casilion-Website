# Server setup (IONOS VPS 212.227.82.248)

Cassilion runs on the server that already carries Tango-Kalender, in the same way: the
host **nginx** terminates TLS on ports 80/443 and proxies the site to a Docker container
bound to `127.0.0.1`. Cassilion uses **127.0.0.1:8150** and lives in `/opt/cassilion`.

> **Not yet run on the server.** The start-up sequence below was rehearsed without Docker
> and nginx: static build, migration, `create_operator`, two web workers and the AI worker
> under production settings against PostgreSQL 16, then registration with address
> confirmation, sandbox purchase, ticket and review in a browser. The Docker image has never
> been built and the nginx files have never been loaded; `docker compose up --build` and
> `nginx -t` on the server are the first real test of both.

The stack (`docker-compose.yml`): `web` (site, job API, both MCP endpoints), `ai-worker`
(rule-based for now), `db` (PostgreSQL 16).

## 1. Repository

Nothing is committed yet. On the development machine, in `D:\Code\Casilion`:

```powershell
git add -A
git commit -m "Cassilion web platform: skeleton"
git branch -M main
git remote add origin https://github.com/2bAndi/Casilion-Website.git
git push -u origin main
```

`.gitignore` keeps `var/`, `.env`, `data/`, `backups/` and `_to_delete/` out.

## 2. Code, nginx and certificate

The DNS records for cassilion.com and cassilion.de (`@` and `*`) already point at this server.
The nginx files come from the repository, so the code is cloned first.

```bash
sudo mkdir -p /opt/cassilion && sudo chown "$USER": /opt/cassilion
cd /opt/cassilion
git clone https://github.com/2bAndi/Casilion-Website.git .

ss -ltn | grep ':8150 ' || echo "port 8150 is free"      # pick another port here and in docker-compose.yml if it is not

# Request limit (http context): must exist before the snippet is loaded
sudo tee /etc/nginx/conf.d/cassilion-rate.conf >/dev/null <<'NGINX'
# Requests per visitor for cassilion.com (deploy/nginx/cassilion-app.conf)
limit_req_zone $binary_remote_addr zone=cassilion_rate:10m rate=5r/s;
NGINX

# First step: port 80 only, for the certificate check
sudo cp /opt/cassilion/deploy/nginx/cassilion.com-setup /etc/nginx/sites-available/cassilion.com
sudo ln -s /etc/nginx/sites-available/cassilion.com /etc/nginx/sites-enabled/cassilion.com
sudo nginx -t && sudo systemctl reload nginx

sudo certbot certonly --webroot -w /var/www/html \
    -d cassilion.com -d www.cassilion.com -d cassilion.de -d www.cassilion.de

# Second step: the real site
sudo cp /opt/cassilion/deploy/nginx/cassilion-app.conf /etc/nginx/snippets/
sudo cp /opt/cassilion/deploy/nginx/cassilion.com /etc/nginx/sites-available/cassilion.com
sudo nginx -t && sudo systemctl reload nginx
```

cassilion.com is the site; www.cassilion.com, cassilion.de and www.cassilion.de redirect to it.
While the container is down, visitors get a short "being updated" page with status 503.

## 3. First deployment

```bash
cd /opt/cassilion
cp .env.example .env
# Four secrets, generated and written into .env without showing them:
for key in SECRET_KEY POSTGRES_PASSWORD SANDBOX_WEBHOOK_SECRET AI_WORKER_TOKEN; do
  value=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
  sed -i "s|^$key=.*|$key=$value|" .env
done
nano .env                                    # EMAIL_HOST_PASSWORD: the password of the sending mailbox
mkdir -p data/var data/postgres backups      # data/var must be writable for user id 1000 (the container's user)
docker compose up -d --build
docker compose logs -f web      # wait for "Application startup complete"
curl -s http://127.0.0.1:8150/healthz
```

The entrypoint runs the database migrations; they also create the five plans. Demo data is
not loaded on a server (`seed_demo` refuses to run without `DEBUG`).

Create your own operator account (asks for a password; the address counts as confirmed):

```bash
docker compose exec web python manage.py create_operator you@example.org
docker compose exec web python manage.py check --deploy
```

`check --deploy` is expected to list only `security.W004` (HSTS, see `.env`) and
`security.W008` (the HTTPS redirect is done by nginx).

Then, in the browser:

1. `https://cassilion.com/` shows the landing page; the response carries `X-Robots-Tag: noindex, nofollow`.
2. Sign in as the operator, open `https://cassilion.com/ops/`, switch on two-factor authentication under `/auth/2fa/`.
3. Register a test customer. Without mail (section 6) the confirmation link is in the log:
   `docker compose logs web | grep -A20 Subject`.
4. As the customer: buy a plan through the sandbox checkout, open a ticket. Within a few
   seconds it appears in the review queue of the backoffice (`docker compose logs ai-worker` shows the analysis).

## 4. What is provisional on this server

Everything runs, and several parts are stand-ins. Before real customers arrive:

| Part | State | Switch |
|---|---|---|
| Search engines | Kept out: header, meta tag, closed `robots.txt` | `SITE_NOINDEX=0` when the site is meant to be found |
| Payments | Sandbox still on: no money moves there, **anyone who registers can give themselves any plan**. Stripe, PayPal and bank transfer are entered in the backoffice (Payments → Settings) | Backoffice → Payments → Settings → Sandbox: limit it to named testers or switch it off once Stripe is set up and tried with a real card (README, "Payments and invoices"). `ALLOW_SANDBOX_PAYMENTS` in `.env` is no longer needed |
| Tax | One rule: German VAT for every customer | the tax adviser's rules for other countries, before selling there |
| Licenses | Stand-in file signed by the website; not what a cluster verifies | open, see README |
| Documentation, known issues | Invented examples | replace the files in `knowledge/` |
| Legal pages | Imprint, privacy and terms are empty pages | fill `templates/core/legal.html` before the site is public in earnest |
| Downloads | No release is published on a fresh server | – |
| Agent tokens | Need two-factor authentication on the customer's account | `REQUIRE_MFA_FOR_AGENT_TOKENS` |
| HSTS | Off | `SECURE_HSTS_SECONDS`, once the certificate is in place for good |

## 5. Updates

```bash
cd /opt/cassilion && git pull && docker compose up -d --build
```

## 6. Email

Until SMTP is configured, mails (address confirmation, ticket notices, alerts to the
operators) are only written to the app log: `docker compose logs web | grep -A20 Subject`.

The site sends through the server's own mail platform, which relays to Brevo, as
Tango-Kalender does. It signs in as the mailbox of the sending address, `info@cassilion.com`
(`.env.example` carries these values; only the password is missing):

```
EMAIL_HOST=mail.kinhavn.de
EMAIL_PORT=587
EMAIL_USE_TLS=1
EMAIL_HOST_USER=info@cassilion.com
EMAIL_HOST_PASSWORD=...
DEFAULT_FROM_EMAIL=Cassilion <info@cassilion.com>
OPERATOR_EMAILS=info@cassilion.com
```

`docker compose up -d`, then `docker compose exec web python manage.py sendtestemail you@example.org`.
The sender address must be one the account may use. With `OPERATOR_EMAILS` set, server
errors (HTTP 500) are mailed there as well.

## 7. Scheduled jobs (host crontab)

```
20 3 * * * sh /opt/cassilion/docker/backup.sh >> /opt/cassilion/backups/backup.log 2>&1
40 4 * * * cd /opt/cassilion && docker compose exec -T web python manage.py expire_subscriptions >> /opt/cassilion/backups/daily.log 2>&1
45 4 * * * cd /opt/cassilion && docker compose exec -T web python manage.py close_stale_tickets >> /opt/cassilion/backups/daily.log 2>&1
50 4 * * * cd /opt/cassilion && docker compose exec -T web python manage.py reconcile_payments >> /opt/cassilion/backups/daily.log 2>&1
*/5 * * * * cd /opt/cassilion && docker compose exec -T web python manage.py fetch_inbox >> /opt/cassilion/backups/inbox.log 2>&1
```

`expire_subscriptions` sends the invoice for the next term to plans that end within thirty days,
reminds about unpaid ones and returns accounts whose plan ran out to the free plan.
`reconcile_payments` compares the payments of the last three days with what Stripe and PayPal
list themselves; differences appear in the backoffice under Payments.
`fetch_inbox` reads new mails from the company mailbox into the backoffice inbox, once reading is
switched on under Inbox → Settings. It changes nothing on the mail server.
`close_stale_tickets` closes tickets the customer left unanswered.

## 8. Backups

`docker/backup.sh` writes every night

* `backups/db_<date>.dump` – the database, checked with `pg_restore --list`
* `backups/var_<date>.tar.gz` – `data/var`: the license signing key and the release files

and keeps 14 daily plus 8 weekly (Sunday) copies in `backups/weekly/`. Off the server:
`BACKUP_RSYNC_TARGET` in `.env`. Keep a copy of `.env` in your password manager; it is not
part of the backup on purpose.

Restore (stops the app meanwhile). Not rehearsed yet: try it once on a copy before relying on it.

```bash
cd /opt/cassilion
docker compose stop web ai-worker
docker compose exec -T db pg_restore -U cassilion -d cassilion --clean --if-exists < backups/db_<date>.dump
tar -xzf backups/var_<date>.tar.gz -C data
docker compose up -d
```

## 9. The AI worker

The `ai-worker` service analyses tickets by rules (`LLM_BACKEND=stub`): no model, no source
code, nothing worth protecting. It runs on this server only for that reason.

Once a model or the product's source is involved, the worker moves to its own machine (the
inference node): remove the service from `docker-compose.yml` here, and start it there with

```
CASSILION_JOB_API=https://cassilion.com
AI_WORKER_TOKEN=<the value from the server's .env>
LLM_BACKEND=openai   LLM_BASE_URL=...   LLM_MODEL=...
PRODUCT_REPO_DIR=<read-only checkout of the product>
python -m ai_worker
```

It only opens outbound connections: to the job API and to the model endpoint. The product
source is never copied to, or mounted on, this server.
