# Demo 3: inbound_lead_qualifier (consent-aware webhook, scorecard, CRM and draft replies)

Receives website enquiries on a local webhook. For each one it checks the data and consent, scores the lead with a transparent scorecard, and routes it to **Hot, Warm or Nurture**. It then writes the lead to a CSV "CRM" and drafts a personalised reply. **Nothing is ever sent.**

```
POST /webhook/enquiry -> field allow-list (drop extras) -> pydantic validation
  -> consent gate (given + timestamp + notice version + purpose)  -- fail -> parked_leads.csv (no PII)
  -> enrich (submitted fields + website domain only; no scraping)
  -> scorecard (config.json weights) -> route Hot/Warm/Nurture -> crm_leads.csv
  -> follow-up window check (7 days, configurable) -> draft reply (template | LLM polish) -> audit_log.jsonl
```

## Business value
- **Faster follow-up on the best leads.** Hot leads get a ready-to-review draft within seconds, so the consultant answers the right people first.
- **Scores you can explain.** Every score shows its breakdown (for example, `budget 25 + timeline 12 + ...`), and the weights live in `config.json`, not in a black box.
- **Consent and contact windows are enforced.** Leads without consent are parked without personal data, expired leads are blocked from follow-up, and an audit trail shows when and why each lead was handled.

## Scorecard (weights in `config.json`)
| Criterion | Weight | Example bands |
|---|---|---|
| Budget | 25 | Rs. 25 lakh or more = 1.0, Rs. 10 lakh or more = 0.7, Rs. 3 lakh or more = 0.4 |
| Timeline | 20 | 0-3 months = 1.0, 3-6 months = 0.6, 6-12 months = 0.3, exploring = 0.1 |
| Role | 20 | Decision maker (founder, CXO, director, VP, head) = 1.0, manager = 0.6 |
| Company size | 15 | 51-1000 employees = 1.0 (the target client size), 1000+ = 0.7 |
| Use-case fit | 20 | Core services = 1.0, adjacent services = 0.5 |
| Modifiers | +5 / -5 | Email domain matches the website / free-mail address |

Routing: Hot is 70 or more, Warm is 45 or more, and everything else is Nurture.

## How to run
```bash
cd inbound_lead_qualifier
./run_demo.sh                                   # server up, post 10 leads, server down, tests
python3 -m inbound_lead_qualifier serve         # terminal 1: webhook on 127.0.0.1:8765
python3 -m inbound_lead_qualifier send          # terminal 2: post samples/leads.json
```
```bash
pip install -r requirements.txt
python3 -m inbound_lead_qualifier genkey                    # print a new CRM encryption key
export CRM_ENCRYPTION_KEY=<the key>                         # from a secret manager, never in the repo
python3 -m inbound_lead_qualifier export-crm --out crm.csv  # decrypt to plaintext for an authorised export
```
Outputs go to `output/`: `crm_leads.csv.enc` (encrypted CRM; plain `crm_leads.csv` only in dev mode), `parked_leads.csv`, `drafts/*.txt`, `audit_log.jsonl` and `summary.json`. `run_demo.sh` uses a throwaway key for the run, so the demo always shows the encrypted path. To require a shared secret, set `WEBHOOK_TOKEN` (sent as the `X-Webhook-Token` header). `reference_time` in the config fixes "now" so demo runs give the same result every time. Set it to `null` in production.

## Switching to Gemini or Claude
Set `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`, and optionally `LLM_PROVIDER`. The LLM is only used to polish the tone of the draft. It is told not to add facts, prices or links. Its output is only accepted if the `DRAFT - NOT SENT` header and the consent-withdrawal line are still there. Scoring and routing always stay rules-based. Online calls use a timeout and retry with backoff and are logged (tokens, latency, estimated cost; prices in `config.json` are illustrative, check current pricing). **The online path has not been run against a live model yet**; use `python3 ../online_check.py --write` from the repo root once a key is set.

## What changed in this version
- **Rate limiting:** per-client sliding window (default 30 requests per minute, `limits.rate_per_minute`), answered with `429` and a `Retry-After` header. The client is the TCP peer; `X-Forwarded-For` is ignored unless `limits.trust_forwarded_for` is true (only behind a proxy you control).
- **Request limits:** body size limit (default 16 KiB, `limits.max_body_bytes`) checked from `Content-Length` before reading (`413`), `411` when the length is missing, `400` for an invalid length, over-nested or non-UTF-8 JSON, `415` unless `Content-Type` is JSON, and a socket timeout so a slow client cannot hold a thread.
- **Structured JSON logs** (`log_format`: `json` or `text`): one JSON object per line, and one `http_request` line per request (id, client address, status, duration, lead id). Logs never carry names, emails or phone numbers.
- **CRM encrypted at rest** with Fernet (AES-128-CBC + HMAC-SHA256) using `CRM_ENCRYPTION_KEY`. Several comma-separated keys are accepted for rotation (first key encrypts, all decrypt). **Dev-mode fallback:** with no key the CRM is plaintext and a warning is logged; set `"encryption": {"required": true}` in production to refuse to start without a key. A wrong key or a missing key with an encrypted file fails loudly instead of starting a second plaintext CRM. A plaintext CRM is copied into the encrypted store when a key is first added; the old plaintext file is **not** deleted for you.

## Limitations
- The CSV "CRM" stands in for a real CRM. In production, swap `CrmStore` for a HubSpot, Zoho CRM or other API client (the CSV upsert already prevents duplicate records).
- Use-case fit is based on keywords. Tune the keywords to the consultant's actual services.
- The rate limiter is in memory and per process. With several workers, use the gateway's limiter or a shared store. There is still no CAPTCHA and no HTTPS (it binds to localhost only): put it behind a reverse proxy with TLS before exposing it.
- Only the CRM file is encrypted. Draft replies (which contain the recipient's email address) and `parked_leads.csv` are not; the audit log holds masked values only. The whole CRM file is re-encrypted on every write, which is fine for a demo and not for a large table (use a database with column or disk encryption). Key management (storage, rotation schedule, access) is yours to set up. There is no retention or erasure job yet.
- Encryption protects the file at rest. Anyone who can read both the file and the key, or the running process, can read the data.

## Compliance notes (India)
- **DPDP Act 2023, s.6:** consent must be free, specific, informed, unconditional and unambiguous, given by a clear affirmative action, and linked to a notice (s.5). This demo records `consent.given`, the timestamp, the notice version and the purpose. Leads without valid consent are parked with **no personal data** (only id, time and reason).
- **s.6(10)** puts the burden of proving notice and consent on the business. `audit_log.jsonl` provides that evidence, with email and phone masked.
- **Data minimisation and purpose limitation:** fields outside the allow-list are dropped and never stored. Marketing opt-in is kept separate from consent to reply.
- **Follow-up window:** follow-up on an enquiry is limited to a configurable 7 days from the consent timestamp. After that, leads are marked `WINDOW_EXPIRED` and no draft is created. *To check:* confirm the exact enquiry-based window in the current **TRAI TCCCPR 2018 (as amended)**. TRAI rules cover SMS and voice, not email, but this demo applies the window to all channels to be safe. SMS follow-ups also need DLT-registered headers and templates.
- The **DPDP Rules 2025** (notified Nov 2025, phased start) add requirements for notice content and consent managers (*to check: the dates that apply*).
