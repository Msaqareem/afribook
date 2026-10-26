**AFRIBOOK**

**Document 2: Technical Architecture Specification**

Kenya-first appointment booking SaaS with M-Pesa STK Push and SMS notifications

  --------------------------------------------------------------------------------------------------------------------------
  **Document control**                **Value**
  ----------------------------------- --------------------------------------------------------------------------------------
  Version                             1.0

  Status                              Architecture baseline for review; payment-provider setup is a go/no-go dependency

  Primary audience                    Product owner, AI coding assistant, developer, QA

  Preferred stack                     Flask, PostgreSQL, SQLAlchemy, Flask-Migrate, Jinja, Tailwind CSS, HTMX

  Core integrations                   Safaricom Daraja M-Pesa STK Push; Africa's Talking SMS

  Deployment direction                Render paid web service + managed PostgreSQL + separate scheduled/background process
  --------------------------------------------------------------------------------------------------------------------------

**Critical decision before live payment coding**: The customer experience will be STK Push → customer enters PIN in Safaricom's prompt → payment is sent to the business Till or Paybill. However, the production Daraja credential/merchant onboarding model must be confirmed with Safaricom or an approved payment partner. A Till/Paybill number alone does not automatically grant AFRIBOOK credentials to initiate STK Push for every independent merchant. Do not launch live payments until the approved model is verified.

# 1. Purpose and scope

This document translates AFRIBOOK's agreed MVP requirements into a technical baseline. It is intended to guide implementation in small, testable Agile buckets and reduce schema changes during coding.

## 1.1 MVP outcomes

-   A business owner can register, verify a phone number, configure one business, add services, define availability, configure one payment destination, and publish a booking page.

-   Customers can discover public businesses or open a direct business link, choose a service and available slot, provide their name and phone number, and pay by M-Pesa STK Push.

-   A booking is confirmed only after a verified successful payment callback.

-   Customer and business receive confirmation SMS; both receive a reminder 24 hours before the appointment when enough lead time exists.

-   Business owners can see upcoming bookings and mark bookings completed.

-   Admins can manage categories, businesses, subscriptions, suspensions, and basic platform analytics.

-   Subscription expiry blocks new bookings while existing booking records remain accessible to the owner.

## 1.2 Explicitly out of scope for MVP

-   Customer accounts, mobile apps, reviews/ratings, staff calendars, multi-branch support, Google Calendar integration, WhatsApp messaging, AI scheduling, CRM, and revenue sharing.

-   Customer-facing cancellation/refund automation. Business-owner dispute/refund handling is outside the first version; late or ambiguous payments must still be visible for admin review.

-   Taking a commission from booking payments. AFRIBOOK's initial business model is subscription-only.

# 2. Architecture principles

-   Keep the app as a modular Flask monolith; do not split into microservices for the MVP.

-   Use PostgreSQL as the source of truth for bookings, payment state, subscription state, and notification jobs.

-   Use server-rendered Jinja pages with Tailwind and HTMX/minimal JavaScript; prioritize mobile usability over visual effects.

-   Put business rules in service-layer modules, not directly in route handlers.

-   Treat provider callbacks as authoritative for payment results; a browser redirect is never proof of payment.

-   Make payment callbacks and SMS sending idempotent so retries do not duplicate bookings or messages.

-   Use migrations for every schema change; never manually alter production tables.

-   Keep payment integration behind a provider adapter so the merchant credential model can be changed without rewriting the booking engine.

# 3. System overview

Browser (customer, owner, or admin) → Flask application → PostgreSQL. The Flask app calls Daraja to initiate STK Push and Africa's Talking to send SMS. Safaricom calls a public HTTPS callback endpoint after the transaction. A separate worker/cron process sends reminders, expires holds, retries notifications, and reconciles payment states.

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Component**                       **Responsibility**
  ----------------------------------- -----------------------------------------------------------------------------------------------------------------------------
  Flask web app                       Routes, authentication, server-rendered pages, form validation, authorization.

  PostgreSQL                          Canonical records, transactional booking/slot protection, payment attempts, audit trail, reminder queue.

  Payment adapter                     Creates STK Push requests, parses callbacks, queries status when supported, normalizes provider responses.

  Callback endpoint                   Validates callback structure, records raw provider result safely, updates payment state idempotently, acknowledges quickly.

  Notification worker                 Processes confirmation SMS, 24-hour reminders, retries, expired holds, and reconciliation tasks.

  Africa's Talking adapter            Sends SMS and stores delivery/provider results.

  Admin portal                        Categories, account/business management, suspension, subscriptions, basic analytics and exception review.
  -----------------------------------------------------------------------------------------------------------------------------------------------------------------

# 4. Roles, tenancy, and authentication

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Actor**                           **Access**
  ----------------------------------- -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  Customer                            No account. Can view public business pages, request a slot, initiate payment, and see booking outcome using a secure booking reference.

  Business owner                      One owner account maps to one business in MVP. Can manage profile, services, availability, bookings, and payment destination.

  Super admin                         Separate privileged role, never selectable in public signup. Can manage categories, suspend businesses, review subscriptions and payment exceptions, and view platform analytics.
  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

Authentication baseline:

-   Owner login: normalized Kenyan phone number + password.

-   Store only a strong password hash (Werkzeug/Argon2/bcrypt as selected by the implementation); never store plaintext passwords.

-   Password recovery: short-lived SMS OTP, store only a hash of the OTP, enforce expiry, retry limits, and rate limits; responses must not reveal whether a phone number has an account.

-   Use Flask-Login or equivalent session management, CSRF protection, secure/HttpOnly/SameSite cookies, and session invalidation after password changes.

-   Every owner route must check ownership server-side. Hiding UI controls is not authorization.

# 5. Core data model

Use SQLAlchemy models and Flask-Migrate/Alembic. Prefer UUIDs for public booking/intent identifiers where practical; internal integer IDs are also acceptable if public identifiers remain unguessable. Use UTC timestamps in the database and display times in Africa/Nairobi.

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Table**               **Proposed columns**                                                                                                                                                                                                                                                                                                                  **Rules / notes**
  ----------------------- ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  users                   id; phone_e164 UNIQUE; password_hash; role (owner/admin); is_active; phone_verified_at; created_at; updated_at                                                                                                                                                                                                                        Phone normalized to +254... or 254... consistently; one owner per business.

  categories              id; name; slug UNIQUE; is_active; sort_order; created_at                                                                                                                                                                                                                                                                              Admin-managed; each business selects one category.

  businesses              id; owner_user_id UNIQUE FK users; category_id FK; name; slug UNIQUE; contact_phone; county; town; address; description; logo_path; is_public DEFAULT true; status; timezone DEFAULT Africa/Nairobi; created_at; updated_at                                                                                                           One business per owner in MVP; status active/suspended.

  payment_accounts        id; business_id UNIQUE FK; payment_type (TILL/PAYBILL); shortcode; account_reference_template nullable; status; verified_at; created_at; updated_at                                                                                                                                                                                   One configured payment destination per business. Do not store platform Daraja secrets here. Merchant-specific credentials, if required by the approved model, must be encrypted.

  services                id; business_id FK; name; description; price_kes; duration_minutes; is_active; created_at; updated_at                                                                                                                                                                                                                                 Price must be positive; duration must be a positive integer; historical booking snapshots prevent later service edits changing past bookings.

  weekly_availability     id; business_id FK; weekday; start_time; end_time; created_at; updated_at                                                                                                                                                                                                                                                             Multiple windows per weekday allowed; end must be after start; no row means closed.

  weekly_breaks           id; business_id FK; weekday; start_time; end_time                                                                                                                                                                                                                                                                                     Recurring breaks removed from selectable slots.

  blocked_periods         id; business_id FK; starts_at; ends_at; reason; created_at                                                                                                                                                                                                                                                                            Supports full-day or partial-day blocks; start must precede end.

  booking_intents         id; public_token UNIQUE; business_id FK; service_id FK; customer_name; customer_phone; starts_at; ends_at; amount_kes_snapshot; status; hold_expires_at; idempotency_key UNIQUE; created_at; updated_at                                                                                                                               Temporary reservation created before payment. Status: pending_payment, paid, failed, expired, review.

  payment_attempts        id; booking_intent_id FK; provider; checkout_request_id UNIQUE nullable; merchant_request_id; amount_kes; customer_phone; transaction_type; shortcode_snapshot; account_reference; mpesa_receipt UNIQUE nullable; status; result_code; result_description; callback_payload JSONB; initiated_at; callback_received_at; completed_at   Never store customer PIN. Callback handling must be idempotent.

  bookings                id; public_reference UNIQUE; booking_intent_id UNIQUE FK; payment_attempt_id UNIQUE FK; business_id FK; service_id FK; customer_name_snapshot; customer_phone_snapshot; service_name_snapshot; duration_minutes_snapshot; amount_kes_snapshot; starts_at; ends_at; status (confirmed/completed); completed_at; created_at             Only successful payment creates a confirmed booking. No customer cancellation flow in MVP.

  subscriptions           id; business_id FK; plan_code; amount_kes; currency; status; starts_at; ends_at; provider_reference nullable; last_payment_at; created_at; updated_at                                                                                                                                                                                 Pricing configurable; proposed initial price KES 500/month, not hard-coded. Billing provider/flow needs its own decision.

  sms_logs                id; booking_id FK nullable; booking_intent_id FK nullable; recipient_phone; recipient_role; message_type; provider; provider_message_id; status; attempts; scheduled_at; sent_at; last_error; created_at                                                                                                                              Use uniqueness/idempotency key to avoid duplicate confirmation/reminder sends.

  notification_jobs       id; job_type; dedupe_key UNIQUE; payload JSONB; run_at; status; attempts; last_error; created_at; updated_at                                                                                                                                                                                                                          Persistent queue for reminders, retries, and cleanup; process in a separate worker or scheduled process.

  password_reset_otps     id; user_id FK nullable; phone_e164; code_hash; expires_at; attempts; consumed_at; created_at                                                                                                                                                                                                                                         Short expiry, strict attempt limits, and rate limits.

  admin_audit_logs        id; admin_user_id FK; action; entity_type; entity_id; metadata JSONB; created_at                                                                                                                                                                                                                                                      Record sensitive admin actions and changes.
  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

## 5.1 Database constraints and indexes

-   Unique: users.phone_e164, businesses.owner_user_id, businesses.slug, categories.slug, payment_accounts.business_id, booking_intents.public_token, booking_intents.idempotency_key, payment_attempts.checkout_request_id (when present), payment_attempts.mpesa_receipt (when present), bookings.public_reference, bookings.booking_intent_id, bookings.payment_attempt_id.

-   Indexes: businesses(category_id, county, town, is_public, status); services(business_id, is_active); booking_intents(business_id, starts_at, status, hold_expires_at); bookings(business_id, starts_at, status); notification_jobs(status, run_at); sms_logs(status, scheduled_at).

-   Use CHECK constraints for positive service price/duration and valid start/end ordering where feasible.

-   Store money as integer KES whole shillings for the MVP if fractional KES is not required; use a clearly named \`price_kes\` field and never use floating-point money.

-   Keep provider callback payloads only as needed for reconciliation and support; redact unnecessary personal data and define retention rules.

# 6. Availability and booking engine

## 6.1 Slot generation

-   Customer selects a service; service duration determines the appointment interval.

-   Load the business's weekly opening windows for the requested weekday and apply any date-specific blocked periods.

-   Generate candidate starts using a configurable slot increment (initial default: 15 minutes) or aligned service-duration increments; decide one consistent rule before implementation.

-   Reject candidates that fall outside opening hours, overlap a recurring break, overlap a blocked period, overlap a confirmed booking, or overlap an unexpired payment hold.

-   Display times in Africa/Nairobi; store timestamps as timezone-aware UTC values.

-   Recalculate availability on the server when the customer submits a slot. Client-side availability is only a convenience, never authoritative.

## 6.2 Preventing double bookings

The booking request must run in a PostgreSQL transaction. Acquire a transaction-scoped advisory lock for the business and appointment date (or another equivalent per-business/time lock), re-check confirmed bookings and unexpired holds, and create the hold only if no overlap exists. Use half-open intervals \[start, end) so one appointment can end exactly when the next begins. Add an exclusion constraint for overlapping confirmed bookings if the chosen schema/status design supports it; do not rely on UI checks alone.

## 6.3 Hold expiry and late callbacks

-   Create a booking intent and short-lived hold before initiating STK Push. Proposed default hold: 5 minutes, configurable after sandbox testing.

-   If payment fails or times out, mark the intent accordingly and release the hold.

-   A worker expires old holds and triggers provider status reconciliation where available.

-   If a successful payment callback arrives after its slot was released or rebooked, do not create an overlapping booking. Mark the payment/intent \`review\`, alert admin and owner, and support a documented manual resolution/refund process.

# 7. M-Pesa STK Push integration

**Required customer flow**

1.  Customer chooses service and available time, then enters name and M-Pesa phone number.

2.  Backend creates a booking intent/hold and payment attempt.

3.  Backend requests STK Push. The customer receives the Safaricom prompt and enters their PIN there.

4.  Safaricom processes the transaction to the configured business Till or Paybill, subject to the approved Daraja merchant/credential setup.

5.  Backend receives the provider callback, validates and records the result, and confirms the booking only for a verified successful payment.

6.  Customer and business receive confirmation SMS. The result page can poll the backend for status, but the browser must not decide that payment succeeded.

## 7.1 Payment configuration caveat --- release gate

Safaricom's public Daraja STK Push documentation describes the shortcode, passkey, transaction type, customer phone, and callback fields used to initiate a payment. The STK request is made under a business shortcode/credential setup. Therefore, before live implementation, AFRIBOOK must confirm with Safaricom or an approved payment partner how a multi-tenant SaaS can initiate STK Push for many independent merchants and settle funds directly to each merchant's own Till/Paybill.

-   Do not assume a business can enter only its Till/Paybill number and that AFRIBOOK's one set of credentials will automatically authorize every merchant destination.

-   Confirm whether each merchant needs to be onboarded/authorized under the platform, whether merchant-specific shortcode/passkey details are required, or whether an approved aggregator/submerchant product is necessary.

-   The agreed business outcome remains direct settlement to the business, not AFRIBOOK holding booking money. If the approved technical model cannot do that, stop and present the alternatives for explicit product-owner approval; do not silently switch to platform settlement.

-   Use \`CustomerPayBillOnline\` for Paybill and \`CustomerBuyGoodsOnline\` for Till only where confirmed by the selected production Daraja configuration.

-   Use sandbox credentials for development; keep consumer key, consumer secret, passkey, and any merchant secrets in environment variables or encrypted secret storage, never source code or logs.

## 7.2 Callback handling

-   Expose a public HTTPS callback endpoint with a hard-to-guess path and appropriate request validation; follow Safaricom's current callback contract.

-   Match callback to the stored CheckoutRequestID/merchant request ID; verify expected amount, phone, shortcode/reference, and receipt fields where supplied.

-   Make processing idempotent. A repeated callback must not create a second booking or send duplicate SMS.

-   Persist the callback result before returning a fast success acknowledgement; enqueue downstream actions rather than sending SMS inside the callback request.

-   For ambiguous or missing callbacks, query transaction status where the selected Daraja product supports it and show the customer a pending/retry status without inviting a duplicate payment immediately.

-   Never log customer PINs or credentials. Log request IDs and sanitized result details for troubleshooting.

# 8. SMS notifications and background jobs

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Event**               **Recipients**                      **Timing**
  ----------------------- ----------------------------------- ----------------------------------------------------------------------------------------------------------
  Booking confirmed       Customer + business contact phone   After verified successful payment and booking creation.

  Appointment reminder    Customer + business contact phone   24 hours before start time, if the booking was created with at least 24 hours' lead time.

  Payment needs review    Owner and/or admin                  Internal alert for successful but unassignable/late payment or repeated callback/reconciliation failure.
  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------

-   Use Africa's Talking through a small adapter module. Keep credentials in environment variables.

-   Queue SMS jobs in PostgreSQL with a unique dedupe key. Record provider message IDs, attempts, success/failure, and last error.

-   Run reminders and retries from a separate worker or scheduled process; do not rely only on an in-process timer in the web server because deploys/restarts can lose scheduled work.

-   If an appointment is booked less than 24 hours ahead, send the confirmation but skip the 24-hour reminder.

-   Keep message templates concise and include business name, service, date/time, amount/payment confirmation where appropriate, and booking reference. Avoid exposing unnecessary personal details.

# 9. Subscription behavior

-   AFRIBOOK charges businesses a subscription only; no percentage or transaction fee on booking payments in the initial release.

-   Proposed starting price: KES 500/month, configurable in admin/settings and not embedded in route code. Final price and billing collection flow must be confirmed before release.

-   When subscription expires, block creation of new booking intents and public payment attempts for that business; existing bookings remain visible to the owner.

-   Use an explicit subscription status and end timestamp; do not infer expiry only from a front-end label.

-   If subscription billing is not yet integrated, support a controlled admin-managed/manual activation path for internal MVP testing, with audit logs. Do not represent manual activation as automated billing.

# 10. HTTP routes / blueprint boundaries

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Blueprint**           **Example route families**                                                                           **Purpose**
  ----------------------- ---------------------------------------------------------------------------------------------------- ------------------------------------------------------------------------------------
  auth                    /register, /login, /logout, /password-reset                                                          Owner authentication, phone verification, OTP recovery.

  owner/business          /dashboard, /settings/profile, /settings/payment                                                     Business profile, slug, location, public visibility, payment setup.

  services                /services, /services/new, /services/\<id\>/edit                                                      Create/update/activate services.

  availability            /availability, /blocked-periods                                                                      Weekly opening hours, breaks, blocked dates/periods.

  public                  /explore, /\<slug\>                                                                                  Directory and public business booking page.

  bookings                /\<slug\>/slots, /booking-intents, /booking-status/\<token\>, /bookings, /bookings/\<id\>/complete   Slot lookup, booking hold, payment initiation flow, owner booking list/completion.

  payments                /webhooks/mpesa/stk                                                                                  Provider callback and payment status/reconciliation endpoints.

  admin                   /admin/categories, /admin/businesses, /admin/subscriptions, /admin/payment-exceptions                Platform management and exception review.

  health                  /health                                                                                              Deployment health check without leaking secrets.
  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

Routes should remain thin. Put slot generation in \`availability_service.py\`, booking transactions in \`booking_service.py\`, provider logic in \`payments/\`, and notification scheduling in \`notification_service.py\`.

# 11. Suggested project structure

afribook/\
app/\
\_\_init\_\_.py\
config.py\
extensions.py\
models/\
user.py\
business.py\
category.py\
service.py\
availability.py\
booking.py\
payment.py\
subscription.py\
notification.py\
audit.py\
blueprints/\
auth/\
owner/\
services/\
availability/\
public/\
bookings/\
payments/\
admin/\
services/\
auth_service.py\
availability_service.py\
booking_service.py\
subscription_service.py\
notification_service.py\
integrations/\
mpesa/\
provider.py\
daraja_adapter.py\
callback_parser.py\
sms/\
provider.py\
africastalking_adapter.py\
tasks/\
worker.py\
scheduled_jobs.py\
templates/\
static/\
migrations/\
tests/\
unit/\
integration/\
e2e/\
wsgi.py\
requirements.txt\
.env.example\
README.md

# 12. Security, privacy, and operational requirements

-   Validate and normalize phone numbers server-side; use one canonical storage format.

-   Protect all state-changing forms with CSRF tokens; use ORM parameterization and template auto-escaping.

-   Apply rate limits to login, OTP requests, STK initiation, booking intent creation, and public status polling.

-   Use HTTPS in production, secure cookies, environment-based secrets, least-privilege database credentials, and no secrets in GitHub.

-   Validate owner/business ownership on every private route; enforce role checks on every admin route.

-   Sanitize callback data before logging; retain only data needed for payment support, audit, and legal requirements.

-   Back up PostgreSQL and test restore procedures. Configure application error monitoring and structured logs.

-   Add audit records for business suspension, category changes, subscription changes, and manual payment exception resolution.

-   Set clear retention and privacy rules for customer name/phone and booking history; collect no extra customer fields unless a later approved requirement needs them.

# 13. Testing strategy and acceptance criteria

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Area**                            **Minimum acceptance tests**
  ----------------------------------- -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  Authentication                      Unique phone; invalid login; OTP expiry/retry lockout; password reset does not reveal account existence; owner/admin permissions.

  Business setup                      Unique slug; one business per owner; category required; public visibility toggle; location fields validated.

  Services                            Positive price/duration; inactive service not bookable; booking snapshots remain unchanged after service edits.

  Availability                        Opening hours, breaks, blocked periods, existing bookings, and service duration produce correct slots; timezone boundary tests.

  Concurrency                         Two simultaneous requests for overlapping slots cannot both obtain valid holds or confirmed bookings.

  Payments                            STK initiation failure; customer cancellation; timeout; success callback; duplicate callback; callback with mismatched amount/reference; missing callback reconciliation; late success after hold expiry.

  SMS                                 Confirmation sent once to each recipient; reminder queued at 24 hours; retries recorded; duplicate job does not send twice.

  Subscriptions                       Expired subscription blocks new booking intents but owner can still view existing bookings.

  Admin                               Category CRUD; suspend/unsuspend business; audit log written; non-admin blocked.

  Deployment                          Health endpoint; migrations apply cleanly; HTTPS callback reachable; background worker runs; secrets absent from repository.
  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

# 14. Agile implementation sequence

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Bucket**                       **Build scope**                                                                                                                          **Exit gate**
  -------------------------------- ---------------------------------------------------------------------------------------------------------------------------------------- ---------------------------------------------------------------------
  0\. Technical validation         Confirm Daraja production merchant model for STK Push to independent Till/Paybill destinations; confirm subscription billing approach.   Written go/no-go decision before live payment implementation.

  1\. Foundation + auth            Flask app factory, config, PostgreSQL, migrations, owner/admin roles, login, OTP recovery.                                               Automated tests pass; migrations reproducible.

  2\. Business profile             Onboarding, one business per owner, category, slug, county/town/address, public visibility.                                              Owner can publish an accessible business page.

  3\. Services                     Service CRUD, price/duration, active/inactive.                                                                                           Invalid services cannot be published/booked.

  4\. Availability                 Weekly hours, breaks, blocked periods, slot generation.                                                                                  Availability tests pass.

  5\. Booking holds                Public booking flow, customer details, database locking, hold expiry.                                                                    Concurrent overlap test passes.

  6\. Payment sandbox              Payment adapter interface, sandbox STK, callback persistence/idempotency, recovery states.                                               End-to-end sandbox test passes; live model still gated by Bucket 0.

  7\. SMS + worker                 Confirmation, 24-hour reminder, retry queue, SMS logs.                                                                                   No duplicate sends in retry tests.

  8\. Owner dashboard              Upcoming/completed bookings, mark completed, simple revenue summary.                                                                     Owner sees only own business records.

  9\. Explore + admin              Directory search/filter, category admin, suspension, subscription view, exception review.                                                Permissions and directory filters pass.

  10\. Subscription + production   Subscription expiry enforcement, deployment, backups, monitoring, production credentials.                                                Go-live checklist signed off.
  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

# 15. AI coding instructions / change control

-   Implement one bucket at a time and stop at its exit gate for review.

-   Before changing a model or schema, explain why the change is needed, which migration it creates, and whether existing data needs backfill.

-   Never rewrite unrelated modules or remove working behavior without approval.

-   Every bucket must include tests, migration files where needed, updated \`.env.example\`, and concise setup instructions.

-   Do not hard-code credentials, prices, phone numbers, callback URLs, or subscription amounts.

-   Do not implement live STK Push until the merchant credential/settlement model is confirmed. Build a mock/sandbox adapter first.

-   When a requirement is ambiguous or conflicts with this document, pause and ask rather than inventing a business rule.

-   Keep the interface responsive and functional; avoid unnecessary animations or complex UI frameworks.

# 16. External integration references and decision log

Safaricom's Daraja developer portal lists M-Pesa Express (Prompt) as an API for initiating a Buy Goods or Pay Bill payment from a customer account. The public C2B documentation describes Paybill/Till shortcodes and the transaction types CustomerPayBillOnline and CustomerBuyGoodsOnline. These references confirm the general STK/C2B pattern, but do not replace confirmation of AFRIBOOK's multi-merchant production authorization and settlement arrangement.

-   Safaricom Daraja portal: https://daraja.safaricom.co.ke/

-   Safaricom Developer Portal --- M-Pesa APIs: https://developer.safaricom.co.ke/apis

-   Safaricom Developer Portal --- Customer to Business: https://developer.safaricom.co.ke/apis/CustomerToBusiness

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Decision**                        **Baseline**
  ----------------------------------- -------------------------------------------------------------------------------------------------------------------------------------
  Customer payment                    M-Pesa STK Push; PIN entered only in Safaricom prompt.

  Destination                         Business-selected Till or Paybill; direct settlement to business is required, subject to approved provider setup.

  Booking confirmation                Only after verified successful callback/reconciliation.

  Business model                      Subscription only; no booking transaction commission.

  Initial subscription price          Proposed KES 500/month, configurable and pending final billing confirmation.

  Reminder                            24 hours before appointment; skip if booking is made with less than 24 hours' lead time.

  Booking completion                  Owner manually marks completed.

  Customer account                    Not required.

  Business tenancy                    One owner account to one business in MVP.

  Payment release gate                Safaricom/approved partner confirms how one platform supports STK Push for multiple independent merchant Till/Paybill destinations.
  -------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**End of Document 2**
