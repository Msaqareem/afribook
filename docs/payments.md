# AFRIBOOK payments: how it is built and how it is meant to work

Status: Bucket 6. Written 2026-10-26. Everything here is implemented unless marked **NOT YET**.
Tests are written but, at the time of writing, **not run** (they need a local PostgreSQL test database).
The IntaSend adapter was written from IntaSend's documentation and has **not** been run against their sandbox yet.

---------------------------------------------------------------------------------------------------

## 1. The money model in one page

1. A customer holds a time slot, then pays by M-Pesa STK Push (the PIN prompt on their own phone).
2. The payment goes **to AFRIBOOK's payment-provider account** (IntaSend, later perhaps Paystack), not to the business.
   AFRIBOOK is therefore holding money that belongs to businesses. This replaces the "direct settlement to the
   business" idea in Doc 2 and needs the product owner's approval (given in conversation on 2026-10-26).
3. The provider settles to AFRIBOOK's own account (an individual account with national ID + KRA PIN to start).
4. **Weekly, by hand**, the admin pays each business the **service price** of every appointment that has already
   happened, then records the M-Pesa reference in AFRIBOOK. (Automatic payouts: NOT YET, planned Bucket 6b.)
5. The customer pays a **separate, visible payment fee** on top of the service price. It covers the provider's
   collection charge. The business always receives its full price. AFRIBOOK takes no commission.

```
 Customer pays  KES 1,523  =  KES 1,500 service price  +  KES 23 payment fee
        |
        v
 Payment provider keeps ~1.5% of 1,523 (= 22.85)   -> covered by the 23 fee
        |
        v  settles to AFRIBOOK (about T+2 working days)
 AFRIBOOK account  --- weekly, manually --->  Business receives KES 1,500 (full price)
```

What the business owes nothing for: provider fees (customer pays them). What AFRIBOOK pays out of its own pocket:
the cost of sending each payout (M-Pesa/transfer charges), which weekly batching keeps small.

**Legal/regulatory caveat (not verified, not legal advice):** holding third-party money may count as a regulated
payment service in Kenya (National Payment System Act, Central Bank of Kenya). Ask the provider and a lawyer
before real launch. Keep business money separate from AFRIBOOK's own subscription income in your accounts and ask an
accountant how KRA treats it.

---------------------------------------------------------------------------------------------------

## 2. The payment fee (transparency rules)

* The fee is **always a separate line** next to the service price, never folded into it. It is shown on: the slots
  page (as a note), the details page (full breakdown before holding), and the booking page (full breakdown).
* Wording: "Payment fee (1.5%, goes to the payment provider)".
* Formula (`app/utils/fees.py`), integer arithmetic only, rounded **up** to a whole shilling:

  `fee = ceil(price * bps / (10000 - bps))`   `total = price + fee`

  | Price | Fee at 1.5% | Total |
  |------:|------------:|------:|
  | 100   | 2           | 102   |
  | 500   | 8           | 508   |
  | 1,000 | 16          | 1,016 |
  | 1,500 | 23          | 1,523 |
  | 2,000 | 31          | 2,031 |

  Why "gross up" (divide by 1 - rate) and not just 1.5% of the price? The provider takes 1.5% of the **total**
  the customer pays. Charging only 1.5% of the price would leave the business slightly short. Property tests check
  the provider's cut is never more than the fee and the customer never overpays by KES 1 or more.
* The rate is the setting `PAYMENT_FEE_BPS` (basis points; 150 = 1.5%; 300 = 3% if IntaSend is used).
  Change it in `.env`; no code change.
* The fee is **frozen on each hold** (`booking_intents.fee_kes_snapshot`, `fee_bps_snapshot`). Changing the rate later
  never changes a price a customer was already shown, or an existing booking.
* If the rate is 0 the fee line shows KES 0 and totals equal the price.
* If you later want a margin above provider cost, that is a deliberate product decision (Doc 2 says no commission):
  record it in `decisions.md`, then show it honestly.

---------------------------------------------------------------------------------------------------

## 3. Customer journey (what the screens do)

| Step | Page | What happens |
|---|---|---|
| 1 | `/<slug>` | Public page lists services with a Book button. |
| 2 | `/<slug>/slots?service_id=` | Pick a day, then a time (Morning/Afternoon/Evening). A note says a payment fee is added and shown before paying. |
| 3 | `/<slug>/book?service_id=&start=` | Enter name and M-Pesa number. Full price breakdown is shown. Submitting creates a **hold** (5 min). |
| 4 | `/booking-status/<token>` (state "payable") | Countdown + breakdown + **Pay KES 1,523 with M-Pesa** button. |
| 5 | same page (state "waiting") | Prompt sent. "Check your phone." Page polls every 3 s. Hold stretched to at least 3 min from now. |
| 6a | same page (state "paid") | Confirmed. Shows booking reference `AB-XXXXXX` and the M-Pesa receipt. |
| 6b | same page (state "retry") | Cancelled/failed: message + Pay button again (max 3 attempts per hold, hold still counting down). |
| 6c | same page (state "review") | Money arrived but booking could not be confirmed automatically: "keep your M-Pesa message; we will confirm or refund; do not pay again". |
| 6d | same page (state "expired") | Hold ended, slot reopened, link to choose a time again. |

The browser never decides that payment worked. The page only *reads* the state our server holds. A booking is
created only by the server, from a verified provider message (section 5).

Customers have no account. Their access is the long unguessable token in `/booking-status/<token>`.
The page shows the phone number masked.

---------------------------------------------------------------------------------------------------

## 4. Data model (migration `0007_payments`)

All tables have row-level security enabled (no policies) so Supabase's public API cannot read them.

**`booking_intents`** (Bucket 5 table, extended): `fee_kes_snapshot`, `fee_bps_snapshot`. Statuses:
`pending_payment` (hold), `paid`, `failed` (unused so far), `expired`, `review`.
`amount_kes_snapshot` is the **service price**; `total_kes` = price + fee.

**`payment_attempts`** one row per request to charge a phone. Never stores a PIN. Columns of note:
`our_reference` (we generate; sent to the provider as `api_ref`), `provider_reference` (their id),
`amount_kes` (the total charged), `status`, `result_code`, `result_description`, `mpesa_receipt` (unique),
`callback_payload` (JSONB, secrets stripped), `last_checked_at`, `resolved_at`, `resolution_note`.
Statuses: `initiated` -> `pending` -> `succeeded` | `failed` | `cancelled` | `timeout` | `review`.
* Partial unique index: **at most one live (`initiated`/`pending`) attempt per hold** -> no double STK push.

**`bookings`** the confirmed appointment. Created only by `payment_service`. `public_reference` (`AB-XXXXXX`, no
0/O/1/I), unique `booking_intent_id` and `payment_attempt_id` (one payment = one booking), snapshots of name/phone/
service/duration/price/fee, `payout_id` (null until included in a payout).
* **Exclusion constraint** `ex_bookings_no_overlap`: `EXCLUDE USING gist (business_id WITH =, tstzrange(starts_at, ends_at,'[)') WITH &&)`.
  Even if application code were wrong, PostgreSQL refuses two overlapping bookings for one business. Back-to-back is allowed.
  Needs the `btree_gist` extension (the migration runs `CREATE EXTENSION IF NOT EXISTS btree_gist`; available on Supabase and standard Postgres).

**`payouts`** one manual payment to a business: `status` `pending`/`paid`/`cancelled`, `total_kes`, `booking_count`,
`destination_snapshot` (text copy of Till/Paybill at the time), `mpesa_reference`, `paid_at`.
A database check forbids `paid` without a reference and date.

---------------------------------------------------------------------------------------------------

## 5. How a payment becomes a booking (the rules in code)

File: `app/services/payment_service.py`. Entry points: `start_payment`, `handle_webhook`, `process_event`,
`reconcile_pending`, `maybe_reconcile`, `payment_view`.

### 5.1 Starting (`start_payment`)
1. Take the same advisory lock as hold creation (business + local date).
2. Re-read the hold. If it is not pending or has expired -> refuse ("This hold has ended").
3. If an attempt is already live -> return it (double-click safe).
4. If 3 attempts already exist -> refuse.
5. Create the attempt (`initiated`, amount = service price + fee). Stretch the hold to at least `now + PAYMENT_GRACE_MINUTES`. Commit.
6. **Outside** the database transaction, ask the provider to send the prompt.
   * Provider says no -> attempt `failed`, customer sees the provider's safe message, hold continues, they may retry.
   * Provider says yes -> attempt `pending`, store the provider's reference.

### 5.2 The callback (`POST /webhooks/payments/<provider>`)
1. Authenticity: the **provider adapter** checks it (mock: HMAC signature; IntaSend: the shared "challenge" string).
   Failure -> HTTP 400, nothing happens. A callback for a provider that is not the active one -> 400.
2. For a success message, the adapter does a **second check**: IntaSend's `verify_event` asks IntaSend's own API
   whether that invoice really is COMPLETE and for what amount, and uses *that* answer. If the provider cannot
   confirm -> HTTP 503 (provider retries; the reconcile job is the backstop).
3. Find the attempt by provider reference or our reference. Unknown -> HTTP 200 "ignored" (logged).
4. Take the advisory lock, re-read the attempt and hold, then apply the event:

| Event | Result |
|---|---|
| attempt already `succeeded` or `review` | **duplicate** - nothing changes |
| pending | nothing changes |
| cancelled / failed | attempt `cancelled`/`failed`; the hold stays so the customer can retry |
| success, **amount missing or different** | attempt + hold -> `review`; no booking |
| success, hold already settled elsewhere | `review` |
| success, **slot now taken** by a paid/held booking of someone else | `review`; **never** an overlapping booking |
| success, everything fine (even if the hold timer already ran out, as long as nobody else took the slot) | attempt `succeeded`, hold `paid`, **booking created** |

5. If a database safety net fires (unique/exclusion constraint) the transaction is rolled back and the payment is
   sent to `review` (or reported duplicate). Money is never silently lost and never creates a second booking.

### 5.3 Missing callbacks (reconciliation)
* While a customer's page polls (`/booking-status/<token>/json`), if the callback is overdue (older than
  `PAYMENT_RECONCILE_AFTER_SECONDS`, default 60) and not checked in the last 30 s, the server asks the provider
  for the status (`maybe_reconcile`).
* `flask reconcile-payments` does the same for all overdue pending attempts and marks attempts still unanswered after
  15 minutes `timeout`. It is safe to run every minute. (Bucket 7's worker will schedule it. For now run it by hand or via any scheduler.)
* A success that arrives for a `timeout`/`failed` attempt is still honoured if the slot is free; otherwise `review`.

### 5.4 Concurrency
Everything that changes a business's calendar for a day (creating a hold, starting a payment, applying a callback)
takes `pg_advisory_xact_lock(business_id, local_date)`. Six simultaneous identical callbacks produce exactly one booking
(test `test_simultaneous_duplicate_callbacks_create_exactly_one_booking`).

---------------------------------------------------------------------------------------------------

## 6. Admin: payouts (weekly, manual) - `/admin/payouts`

**What is owed:** for each business, the sum of `amount_kes_snapshot` (**service price only**) of bookings whose
appointment has **ended**, and that are not yet in a payout. Future appointments are not owed yet.

**Procedure each week (suggested: Monday):**
1. Open Admin -> Payouts. "Owed now" lists each business, how many bookings, the total, and where to send it (Till/Paybill from the owner's Payments page).
2. Press **Prepare payout** for a business. This locks those bookings into a payout so they can never be paid twice.
3. On the payout page, send exactly that amount to the destination shown (M-Pesa app, or the provider dashboard).
   Use **Download CSV** if you want the list of bookings as a statement for the owner.
4. Paste the M-Pesa reference and press **Mark as paid**. The reference (6-40 letters/numbers) is required.
5. Made a mistake before sending? **Cancel this payout**: its bookings become payable again. A paid payout cannot be cancelled.

Payout rules in code (`app/services/payout_service.py`): a booking can be in at most one payout; eligibility is
`ends_at <= now`; the destination is copied into the payout at creation; marking paid needs a reference.

**NOT YET:** businesses cannot see their payouts (Bucket 8 dashboard); no M-Pesa phone-number or bank payout destination
(only Till/Paybill, from Bucket 2); no automatic payouts (Bucket 6b); no SMS to the business when paid (Bucket 7).

---------------------------------------------------------------------------------------------------

## 7. Admin: payments needing review - `/admin/payments/review`

Shows every payment that arrived but could not become a booking: wrong amount, the slot was taken after the hold
ended, or a database safety net refused it. Each row shows business, amount, customer phone, reason, M-Pesa receipt
and our reference. **Refunds are sent by hand** (M-Pesa/provider dashboard, or arrange another time with the business),
then record how you resolved it (e.g. the refund reference). Resolved items leave the list; the note is kept.
Doc 2: customer refund automation is out of scope for the MVP.

---------------------------------------------------------------------------------------------------

## 8. Provider layer (`app/payments/`)

`PaymentProvider` (base.py) has four methods: `initiate_stk`, `parse_webhook`, `verify_event`, `query_status`.
Booking code never imports a specific provider; it uses `current_app.extensions["payment_provider"]`.

* **`mock`** (default in development and tests): no money. `initiated` records what the app asked for.
  Callbacks are HMAC-signed with `MOCK_PAYMENT_SECRET`. When the customer's page is in the "waiting" state and
  `ALLOW_MOCK_PAYMENT_PAGE` is true (development/testing only), a "Test mode" panel offers buttons: pay, pay with the
  success message delivered twice, pay the wrong amount, cancel, fail for insufficient balance. These buttons call the
  **same** `handle_webhook` code the real route uses. Production refuses to start with `PAYMENT_PROVIDER=mock`.
* **`intasend`**: STK Push via `POST /api/v1/payment/mpesa-stk-push/`, status via `POST /api/v1/payment/status/`.
  Sandbox host `sandbox.intasend.com` when `INTASEND_TEST_MODE=true`. The webhook is authenticated by the
  `challenge` value IntaSend includes, which must equal `INTASEND_WEBHOOK_CHALLENGE`; money is accepted only after IntaSend's status API
  confirms it. Customer-facing errors are generic; provider response bodies are never logged or shown.
* **Adding Paystack later:** write `app/payments/paystack.py` implementing the four methods (Paystack signs webhooks with
  an HMAC of the body; `charge.success` event) and add it to `build_payment_provider`. Nothing else changes.
  Check first that Paystack lets the server trigger the M-Pesa prompt (its docs disagree; see decisions log).

---------------------------------------------------------------------------------------------------

## 9. Settings (`.env`)

| Setting | Default | Meaning |
|---|---|---|
| `PAYMENT_PROVIDER` | `mock` | `mock` or `intasend`. Production rejects `mock`. |
| `PAYMENT_FEE_BPS` | `150` | Customer payment fee in basis points (150 = 1.5%). |
| `PAYMENT_GRACE_MINUTES` | `3` | Hold is stretched to at least this once the prompt is sent. |
| `PAYMENT_RECONCILE_AFTER_SECONDS` | `60` | Ask the provider if a callback is this overdue. |
| `BOOKING_HOLD_MINUTES` | `5` | Initial hold length (Bucket 5). |
| `INTASEND_TEST_MODE` | `true` | `true` = sandbox. |
| `INTASEND_SECRET_KEY` | empty | From the IntaSend dashboard. **Only in your private `.env`. Never paste into chat or commit.** |
| `INTASEND_PUBLISHABLE_KEY` | empty | Not used by the server flow yet. |
| `INTASEND_WEBHOOK_CHALLENGE` | empty | A long random string you invent; also entered in IntaSend's webhook settings. |
| `MOCK_PAYMENT_SECRET` | dev value | Signs mock callbacks. |

Security rules honoured: no PINs anywhere; secrets only in environment variables; the webhook route is CSRF-exempt
(it is called by the provider, not a browser) and protected by the provider authenticity check instead; the webhook
rejects unauthenticated, tampered and wrong-provider messages; customer status page is rate-limited
(120/hour page, 600/hour JSON, 15 pay attempts/hour per IP, 20 hold requests/hour per IP).

---------------------------------------------------------------------------------------------------

## 10. How to test it yourself

### 10.1 With the mock provider (no accounts needed)
1. `flask db upgrade` (applies 0005, 0006, 0007; 0007 needs the `btree_gist` extension, normally allowed on Supabase).
2. `flask run`. As an owner: profile, a service (e.g. KES 1,500, 60 min), Availability (hours), Payments (Till 123456).
3. In a private window open `/<your-slug>`, press Book, choose a time, enter name and `0712 345 678`.
4. On the booking page you should see **Service price 1,500 / Payment fee 23 / Total 1,523**. Press **Pay KES 1,523 with M-Pesa**.
5. In the "Test mode" panel press **Pay the right amount**: the page shows **Payment received** and a reference `AB-...`.
6. Repeat with **Pay (success message arrives twice)** (still one booking), **Pay the WRONG amount** (goes to review, Admin -> Payment review), and **Cancel the prompt** (you can retry).
7. Make an admin (`flask create-admin`). To try payouts the appointment must be in the past, which you cannot do through the UI. Use the test suite, or in SQL edit a confirmed booking's `starts_at`/`ends_at` back by a few days (and the same hold in `booking_intents`), then open Admin -> Payouts.

### 10.2 With the IntaSend sandbox (when you have a sandbox key)
1. Sign up at the IntaSend **sandbox** and copy the test secret key.
2. In `.env`: `PAYMENT_PROVIDER=intasend`, `INTASEND_TEST_MODE=true`, `INTASEND_SECRET_KEY=...`, `INTASEND_WEBHOOK_CHALLENGE=<long random>`.
3. Expose your local server over HTTPS (a tunnel such as ngrok or Cloudflare Tunnel) and in the sandbox dashboard set the
   webhook URL to `https://<tunnel>/webhooks/payments/intasend` with the same challenge.
4. Make a booking and pay with IntaSend's sandbox test number. Watch the app log: if the callback or the status
   response has different field names than `app/payments/intasend.py` expects (marked `CHECK`), fix only that file.
5. Also test: cancel the prompt, wrong PIN, and let it time out; then run `flask reconcile-payments`.

### 10.3 Automated tests
`pytest tests/unit` runs without a database. `pytest` (all) needs `TEST_DATABASE_URL` (see README). New test files:
`tests/unit/test_fees.py`, `test_payment_providers.py`, `tests/integration/test_payments.py`, `test_payouts.py`.

---------------------------------------------------------------------------------------------------

## 11. What is NOT done yet / open questions

* **Live money is off.** Nothing real until: IntaSend (or Paystack) account verified, sandbox flow proven, the legal question in section 1 answered, terms/refund/privacy pages published (Bucket 10).
* IntaSend adapter untested against the real sandbox; Paystack adapter not written.
* SMS: no payment/booking confirmation SMS to customer or business yet (Bucket 7). Until then the customer relies on the confirmation page and reference.
* Owner's bookings list, "mark completed", and payout history: Bucket 8.
* Payout destination is Till/Paybill only. Many small businesses only have a personal M-Pesa number or a bank account: add `PHONE`/`BANK` destination types (proposed Bucket 6.1).
* Automatic payouts (provider transfer APIs): Bucket 6b.
* Subscription billing and blocking bookings for expired subscriptions: Bucket 10.
* Refunds are manual (admin review page). Customer-facing cancellation is out of scope for the MVP.
* Questions still to put to the provider: may an individual account collect for other businesses and pay out; limits; settlement time; webhook retry behaviour; exact sandbox field names.
