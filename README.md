# AFRIBOOK

Kenya-first appointment booking SaaS (Flask + PostgreSQL). This repository currently contains
**Bucket 1: Foundation + auth**. See `docs/` for the architecture spec, build prompt and decision log.

## What Bucket 1 includes
- Flask application factory, config classes (development / testing / production), extensions
- `users` and `password_reset_otps` tables with Alembic migration `0001_initial`
- Owner sign-up with phone OTP verification, login/logout, password reset by SMS OTP
- Roles (`owner`, `admin`); admin accounts are created only with `flask create-admin`
- Kenyan phone normalisation (`+254XXXXXXXXX`), rate limiting, CSRF, secure cookies, `/health`
- Mock SMS provider (Africa's Talking arrives in Bucket 7). **No payments yet.**

## What Bucket 2 adds
- Tables `categories` (11 starter categories seeded), `businesses`, `payment_accounts` (migration `0002_business_profile`)
- Owner pages: `/settings/profile` (create/update the one business: county + one free-text Location field), `/settings/payment` (Till/Paybill, stored only; the Account number field appears only for Paybill)
- Public page `afribook.co.ke/<slug>` (hidden/suspended pages are 404 for the public, previewable by their owner)
- Slug rules: 3-40 chars, lowercase letters/numbers/single hyphens, reserved words blocked, enforced in app **and** by a database check constraint
- Logos are not implemented yet (the `logo_path` column exists and is unused)

**Upgrading an existing database:** `flask db upgrade` applies `0002` and `0003` (`0003` removes the separate town column and folds any saved town into Location). (If you created the Bucket 1 tables
from `docs/supabase_bucket1.sql`, the migration history is already recorded, so this just works.)

## What Bucket 3 adds
- Table `services` (migration `0004_services`): name, description, price in whole KES, duration in minutes, active flag
- Owner pages: `/services/` (list), `/services/new`, `/services/<id>/edit`, and a POST toggle to activate/deactivate (no delete, so history stays intact)
- Rules: price KES 1-250,000 (whole shillings), duration 5-480 minutes, max 50 services per business, all enforced on the server; the database also rejects non-positive price/duration
- Only active services appear on the public page, and `catalog_service.get_bookable_service()` is the single gate booking code must use (inactive service, hidden/suspended business, or another business's service all return nothing)
- Other owners' services return 404, never 403

**Upgrading:** `flask db upgrade` applies `0004`.

## What Bucket 4 adds
- Opening hours per weekday (multi-day add, split days), daily breaks, holidays/time off (`/availability`, `/blocked-periods`).
- Slot engine (`app/utils/slots.py`) and `availability_service.get_slots_for_day` / `is_slot_available`.
- Owner preview of customer-visible times; opening hours on the public page.
- Migration `0005_availability`. Run `flask db upgrade`.
- Settings: `BOOKING_SLOT_INCREMENT_MINUTES` (15), `BOOKING_MIN_NOTICE_MINUTES` (30), `BOOKING_MAX_ADVANCE_DAYS` (60).

## What Bucket 5 adds
- Customer flow without an account: `/<slug>` -> Book -> `/<slug>/slots` (day strip, times grouped Morning/Afternoon/Evening) -> `/<slug>/book` (name + M-Pesa number) -> hold -> `/booking-status/<token>` (countdown, release).
- `booking_service.create_hold`: advisory-lock transaction, idempotent, per-phone cap; `expire_holds` and `flask expire-holds`.
- Migration `0006_booking_intents`. Run `flask db upgrade`.
- Payment is not connected yet: a hold only reserves the time until it expires (Bucket 6).
- Settings: `BOOKING_HOLD_MINUTES` (5), `BOOKING_MAX_HOLDS_PER_PHONE` (3).

## What Bucket 6 adds
- M-Pesa payment flow with a provider layer (`mock` for development, `intasend` adapter), separate visible payment fee, verified idempotent callbacks, confirmed `bookings`, late/odd payment handling (`review`), reconciliation (`flask reconcile-payments`).
- Admin: weekly manual payouts (`/admin/payouts`) and payment review (`/admin/payments/review`).
- Migration `0007_payments` (needs `btree_gist`). Run `flask db upgrade`.
- **Read `docs/payments.md`** for how it all works, the settings, and how to test it.

## Setup (Windows PowerShell shown first, macOS/Linux second)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`: set `SECRET_KEY` (`python -c "import secrets; print(secrets.token_urlsafe(48))"`)
and the database URLs.

### Database: Supabase (cloud) for development
1. Create a Supabase project. Under **Project Settings → Database → Connection string** copy the
   **direct** or **session pooler** URI (port 5432) and put it in `.env` as `DATABASE_URL`,
   keeping `?sslmode=require`. Avoid the transaction pooler (port 6543) for now.
2. Apply the schema (creates the tables and enables row-level security):
   ```
   flask db upgrade
   ```
   Alternative with no local Python: paste `docs/supabase_bucket1.sql` into the Supabase SQL Editor.
   Use one method, not both.
3. Create your admin account:
   ```
   flask create-admin
   ```

> Supabase exposes the `public` schema through its Data API. The migration enables RLS on every table
> with no policies, so that API cannot read them. Flask connects as the database owner and is unaffected.
> Do the same for every table added in later buckets.

### Database: local PostgreSQL (recommended for running tests)
Create two databases, e.g. `afribook_dev` and `afribook_test`, and point `DATABASE_URL` /
`TEST_DATABASE_URL` at them. **Do not point `TEST_DATABASE_URL` at Supabase or any real data:** the
tests drop and recreate the whole `public` schema (the suite refuses names not ending in `_test`).

## Run
```
flask run          # http://127.0.0.1:5000
```
In development the mock SMS provider prints OTP messages to the console (`MOCK_SMS_ECHO=true`),
so copy the 6-digit code from the terminal.

## Test
```
pytest
```
Needs `TEST_DATABASE_URL`. Coverage: phone normalisation, OTP hashing/expiry/lockout/single use,
registration, login, roles, password reset, session invalidation, CSRF, rate limit, CLI, health,
and migrations (including a models-vs-migration drift check).

## Migrations
```
flask db upgrade                      # apply
flask db migrate -m "describe change" # generate after editing models, then review the file
flask db downgrade                    # roll back one step
```

## Environment variables
| Variable | Purpose |
|---|---|
| `APP_ENV` | `development` (default), `testing`, `production` |
| `SECRET_KEY` | Session/CSRF/OTP-HMAC key (32+ chars in production) |
| `DATABASE_URL` | PostgreSQL URL (`postgresql://...`; the driver is added automatically) |
| `TEST_DATABASE_URL` | Throwaway test database, name must end `_test` |
| `SMS_PROVIDER` | `mock` (only option until Bucket 7; refused in production) |
| `MOCK_SMS_ECHO` | Dev only: print SMS text including OTPs |
| `OTP_TTL_MINUTES`, `OTP_MAX_ATTEMPTS`, `OTP_MAX_SENDS_PER_HOUR` | OTP rules (10 / 5 / 3) |
| `RATELIMIT_STORAGE_URI` | `memory://` for dev; Redis in production |
| `PROXY_FIX_HOPS` | Reverse proxies in front of the app (Render: 1) |

## Project layout
See `docs/03-ai-build-prompt.md` section 4. Directories are added bucket by bucket.
