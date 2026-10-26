# AFRIBOOK decision log

Source of truth order: Document 3 (build prompt) → Document 2 (architecture) → flowchart. Where the
flowchart disagrees with the documents, the documents win.

| Date | Decision |
|---|---|
| 2026-10-19 | No admin listing-approval step: businesses go live automatically; admin can suspend (flowchart "Approve listing?" is not used). |
| 2026-10-19 | Owner login phone is also the business SMS phone by default (`contact_phone` can be changed later). |
| 2026-10-19 | Owners verify their phone by OTP at signup. Login is blocked until verified. |
| 2026-10-19 | Payment destination (Till/Paybill) is stored configuration in Bucket 2; M-Pesa integration is built at the end (Bucket 6 sandbox, live gated by Bucket 0). |
| 2026-10-19 | Database: PostgreSQL; Supabase connected as the cloud database/storage. |
| 2026-10-19 | Blueprint for service CRUD will be named `catalog` to avoid clashing with top-level `services/`. |
| 2026-10-19 | `/<slug>` catch-all is registered last, with a reserved-slug list (login, register, explore, dashboard, admin, health, static, etc.) enforced in Bucket 2. |
| 2026-10-21 | Schema addition to Doc 2 `password_reset_otps`: column `purpose` (`signup_verify`/`password_reset`) so a signup code can never reset a password. Additive; no frozen decision changed. |
| 2026-10-21 | Sessions embed a password fingerprint, so a password change logs out all other sessions (no extra column). |
| 2026-10-21 | RLS enabled (no policies) on every table so Supabase's public Data API cannot read app data. |

| 2026-10-25 | Bucket 2 seeds 11 starter categories in migration 0002 (Salon, Barber, Beauty & Makeup, Spa & Massage, Clinic, Dental, Mechanic & Car Care, Photography, Tutoring, Fitness & Training, Other). Admin edits them in Bucket 9. |
| 2026-10-25 | Logo upload deferred (optional per spec); Supabase Storage to be used when added. `businesses.logo_path` exists, unused. |
| 2026-10-25 | Slug is validated in the app and by a Postgres CHECK; owners may change it (warned that old links break). |
| 2026-10-25 | Payment account status starts `pending`; changing Till/Paybill number resets it to `pending`. No live M-Pesa use. |
| 2026-10-25 | Public page shows name, category, address and description only. The SMS phone number is not shown publicly. |
| 2026-10-25 | Owner request: no separate town/city field. A business has a county (dropdown) and one free-text Location (area, street or landmark) stored in `address`. `businesses.town` dropped by migration 0003 (existing towns folded into the location text). Directory filters will use category + county + text search. A map pin may be added later. |
| 2026-10-25 | Payment form: the Account number field is shown only when Paybill is selected; for Till it is hidden, disabled, and ignored by the server. |
| 2026-10-25 | Services: no delete (deactivate instead) so booking history stays valid. Price KES 1-250,000, duration 5-480 min, max 50 per business (constants in catalog_service.py). |
| 2026-10-25 | The service blueprint is named `catalog` (routes under /services) as planned. |
| 2026-10-25 | Slots start every 15 minutes from each opening window's start; the whole service must fit inside a window and clear breaks, blocked periods and busy intervals (half-open [start,end)). Configurable via BOOKING_SLOT_INCREMENT_MINUTES. |
| 2026-10-25 | Defaults chosen by me (change in .env): minimum notice 30 min (BOOKING_MIN_NOTICE_MINUTES), booking horizon 60 days (BOOKING_MAX_ADVANCE_DAYS). |
| 2026-10-25 | Weekly hours: up to 4 windows and 4 breaks per weekday, no overlaps; same range can be added to many days at once. Blocked periods are entered in business-local time, stored UTC, max 366 days, "whole day" end date is inclusive. Blocking never cancels existing bookings. |
| 2026-10-25 | `availability_service.busy_intervals` is a stub until Bucket 5; booking code must use `is_slot_available` for the final server-side recheck. |
| 2026-10-26 | Up to 8 breaks and 4 opening windows per day (breaks raised from 4); overlap errors name the clashing time. |
| 2026-10-26 | Times are shown in 12-hour AM/PM everywhere; pickers are hour/minute/AM-PM dropdowns (5-minute steps). Stored values stay 24-hour. |
| 2026-10-26 | Bucket 5 table `booking_intents` (Doc 2 columns plus service name/duration snapshots). Hold = 5 minutes (BOOKING_HOLD_MINUTES), max 3 open holds per phone per business, 20 hold requests/hour per IP. |
| 2026-10-26 | Double-booking protection: one Postgres transaction takes `pg_advisory_xact_lock(business_id, local date)`, rechecks availability (paid + unexpired holds + hours/breaks/blocks), then inserts. No exclusion constraint for holds because expiry is time-based; the confirmed `bookings` table (Bucket 6) gets one. |
| 2026-10-26 | Customers need no account; the unguessable `public_token` in /booking-status/<token> is their access. Slot times travel as UTC epoch seconds in URLs; the server always rechecks. |
| 2026-10-26 | Until payment exists (Bucket 6), a hold only reserves time and expires; nothing is confirmed. Paid intents also block the slot. |
| 2026-10-26 | Payments model: customers pay AFRIBOOK's provider account; AFRIBOOK pays businesses weekly BY HAND (service price only, after the appointment ended). Replaces Doc 2's direct settlement; approved in conversation. Automatic payouts later (6b). |
| 2026-10-26 | Customer pays a separate visible payment fee (default 1.5% = 150 bps, grossed up and rounded up to whole KES) so the business receives its full price. Frozen per hold. Configurable (PAYMENT_FEE_BPS). No AFRIBOOK commission. |
| 2026-10-26 | Provider: build against IntaSend first (allows individual/unregistered accounts; server-triggered STK documented; 3% fee means set PAYMENT_FEE_BPS=300 for it). Paystack is the cheaper backup (1.5%) once it confirms server-side STK for Kenya. Mock provider for development. |
| 2026-10-26 | Failed or cancelled payments keep the hold until it expires so the customer can retry (max 3 attempts); a prompt stretches the hold to at least 3 minutes. |
| 2026-10-26 | Money that cannot become a booking (wrong amount, slot taken) goes to `review`: never an overlapping booking. Refunds are manual (admin Payment review page). |
| 2026-10-26 | `bookings` has a PostgreSQL exclusion constraint against overlap (needs btree_gist). Callbacks are verified (provider authenticity + provider status API) and idempotent. |
| 2026-10-26 | Payout destinations remain Till/Paybill for now; M-Pesa phone and bank destinations proposed for Bucket 6.1. |

## Open items
- Legal: is holding businesses' money allowed for an individual account / does AFRIBOOK need authorisation? Ask provider + lawyer before live launch.
- Send IntaSend and Paystack the questions in docs/payments.md section 11.
- Add M-Pesa phone and bank payout destinations (6.1).
- Confirm min notice (30 min) and horizon (60 days) defaults.
- Dashboard "simple summary" scope (revenue yes/no): decide before Bucket 8.
- Bucket 0: Safaricom multi-merchant STK Push / direct settlement questions still unanswered.
- Subscription billing collection method: undecided.
