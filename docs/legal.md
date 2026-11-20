# Legal pages and consumer functions

What the site provides for German and EU consumer law, where it lives, and what a person has to
check before the site is opened to the public. Written on 2026-11-20. This is a careful first
version by the development side, not legal advice: have a lawyer read the four texts once before
paid plans are sold.

## What exists

| Piece | Address | Source |
|---|---|---|
| Imprint (Impressum, § 5 DDG) | `/legal/imprint/` | `templates/legal/imprint.<lang>.html` |
| Privacy notice (GDPR Art. 13) | `/legal/privacy/` | `templates/legal/privacy.<lang>.html` |
| Terms (AGB) | `/legal/terms/` | `templates/legal/terms.<lang>.html` |
| Withdrawal policy with model form | `/legal/withdrawal/` | `templates/legal/withdrawal.<lang>.html` |
| Withdrawal function (§ 356a BGB, since 19 June 2026) | `/legal/withdraw/` | `apps/core/legal.py` |
| Cancellation button (§ 312k BGB) | `/legal/cancel/` | `apps/core/legal.py` |
| Order page with binding order button (§ 312j BGB) | `/billing/checkout/<plan>/` | `apps/billing/views.py` |
| Order confirmation with terms and withdrawal policy attached | mail | `apps/billing/services.py` |

- Every text exists in German (binding) and English (translation). `?lang=de` or `?lang=en`
  selects one; without it the browser language decides, English otherwise.
- Company data comes from `settings.COMPANY` (`COMPANY_*` in `.env`), not from the texts.
- The footer of every page links the four texts and, set apart as buttons, the two functions.
- Withdrawals and cancellations are stored as `ContractNotice` (never edited), confirmed to the
  sender at once by mail with date and time, mailed to `OPERATOR_EMAILS`, and listed in the
  backoffice under Raw data → Contract notices. **Someone has to act on them by hand**: refund,
  end the plan. Nothing is cancelled automatically.
- An order stores when a consumer asked for support to start within the withdrawal period
  (`Order.early_start_requested_at`) and the date of the terms (`Order.terms_version`).

## Decisions taken as assumptions

1. **Consumers and businesses** can both buy (as on zuri-ai.com). A business-only shop would
   need none of the withdrawal parts, but would have to keep consumers out effectively.
2. **Prices include VAT.** The plan prices (10, 100, 1000 euros) are shown as final prices.
3. **Term:** twelve months, then indefinite with one month's notice and a pro rata refund. The
   billing code does not yet renew or refund by itself; it has to before the first renewal.
4. **Contact address** on the legal pages is `contact@zuri-ai.com`, as on zuri-ai.com.

## To check before going public

- [ ] VAT ID or business identification number of Zuri-AI GmbH into `.env` (`COMPANY_VAT_ID` /
      `COMPANY_ECON_ID`). zuri-ai.com shows neither. If the company has one, the imprint must.
- [ ] Withdrawal policy against the current statutory model (Anlage 1 zu Art. 246a § 1 EGBGB,
      version in force since 19 June 2026), word for word.
- [ ] Privacy notice against reality: log retention on the server (text says 14 days), server
      location (text says EU), data processing agreements signed with IONOS and Brevo.
- [ ] Privacy notice, section 6: name the payment provider once one is connected.
- [ ] Privacy notice, section 8: it says the AI system is run by us and ticket content goes to
      no AI provider. Rewrite this section before a hosted model is used.
- [ ] Software license terms (EULA) for the product itself: the terms refer to them, they do
      not exist on the site yet.
- [ ] Accessibility (BFSG): shops for consumers must be accessible and publish a statement,
      unless the company is a micro-enterprise (fewer than 10 staff and at most 2 million euros
      turnover or balance sheet).
- [ ] The German button labels: the site's interface is English, so the buttons read "Order
      with obligation to pay", "Confirm withdrawal", "Cancel now". With a German interface they
      must read "Zahlungspflichtig bestellen", "Widerruf bestätigen", "Jetzt kündigen".
- [ ] Change `LEGAL_AS_OF` whenever a text changes.
