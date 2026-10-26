"""Business profile, slug and payment-destination logic."""
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models.business import PAYMENT_PAYBILL, PAYMENT_TILL, Business, Category, PaymentAccount
from app.services.errors import ValidationError
from app.utils.kenya import canonical_county
from app.utils.phone import normalize_ke_phone

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MIN, SLUG_MAX = 3, 40

# Words that would collide with application routes or look official.
RESERVED_SLUGS = frozenset({
    "admin", "api", "dev", "app", "about", "afribook", "availability", "blocked-periods", "booking",
    "booking-intents", "booking-status", "bookings", "contact", "dashboard", "explore", "health",
    "help", "login", "logout", "owner", "password-reset", "pricing", "privacy", "register",
    "services", "settings", "slots", "static", "support", "terms", "verify-phone", "webhooks", "www",
})

SHORTCODE_RE = re.compile(r"^[0-9]{5,7}$")
REFERENCE_RE = re.compile(r"^[A-Za-z0-9 _\-{}]{1,64}$")


# ----------------------------------------------------------------------- slug
def validate_slug(raw: str | None) -> str:
    slug = (raw or "").strip().lower()
    if not (SLUG_MIN <= len(slug) <= SLUG_MAX):
        raise ValidationError(f"Web address must be {SLUG_MIN}-{SLUG_MAX} characters long.")
    if not SLUG_RE.fullmatch(slug):
        raise ValidationError("Web address may only use lowercase letters, numbers and single hyphens (no spaces).")
    if slug in RESERVED_SLUGS:
        raise ValidationError("That web address is reserved. Please choose another.")
    return slug


# -------------------------------------------------------------------- queries
def list_active_categories() -> list[Category]:
    return list(db.session.scalars(
        select(Category).where(Category.is_active.is_(True)).order_by(Category.sort_order, Category.name)
    ))


def get_business_for_owner(user_id: int) -> Business | None:
    return db.session.scalar(
        select(Business).options(joinedload(Business.category), joinedload(Business.payment_account))
        .where(Business.owner_user_id == user_id)
    )


def get_business_by_id(business_id: int) -> Business | None:
    return db.session.get(Business, business_id)


def get_business_by_slug(slug: str) -> Business | None:
    slug = (slug or "").lower()
    if not SLUG_RE.fullmatch(slug) or len(slug) > SLUG_MAX:
        return None
    return db.session.scalar(
        select(Business).options(joinedload(Business.category)).where(Business.slug == slug)
    )


def get_viewable_business(slug: str, viewer=None) -> tuple[Business | None, bool]:
    """Return (business, is_owner_preview). Hidden/suspended pages are visible only to their owner."""
    business = get_business_by_slug(slug)
    if business is None:
        return None, False
    if business.is_listed:
        return business, False
    if viewer is not None and getattr(viewer, "is_authenticated", False) and business.owner_user_id == viewer.id:
        return business, True
    return None, False


# -------------------------------------------------------------------- profile
def _text(form, key: str, label: str, min_len: int, max_len: int) -> str:
    value = " ".join((form.get(key) or "").split())
    if not (min_len <= len(value) <= max_len):
        raise ValidationError(f"{label} must be {min_len}-{max_len} characters.")
    return value


def save_profile(user, form) -> Business:
    """Create or update the owner's single business from a submitted form."""
    name = _text(form, "name", "Business name", 2, 80)
    address = _text(form, "address", "Location", 3, 200)

    county = canonical_county(form.get("county"))
    if county is None:
        raise ValidationError("Choose a county from the list.")

    contact_phone = normalize_ke_phone(form.get("contact_phone"))
    if contact_phone is None:
        raise ValidationError("Enter a valid Kenyan mobile number for booking SMS.")

    description = " ".join((form.get("description") or "").split()) or None
    if description and len(description) > 500:
        raise ValidationError("Description must be 500 characters or fewer.")

    try:
        category_id = int(form.get("category_id") or "")
    except ValueError:
        raise ValidationError("Choose a category.") from None
    category = db.session.get(Category, category_id)
    if category is None or not category.is_active:
        raise ValidationError("Choose a category.")

    slug = validate_slug(form.get("slug"))
    business = get_business_for_owner(user.id)
    taken = db.session.scalar(select(Business).where(Business.slug == slug))
    if taken is not None and (business is None or taken.id != business.id):
        raise ValidationError("That web address is already taken. Please choose another.")

    fields = dict(
        name=name, slug=slug, category_id=category.id, county=county, address=address,
        contact_phone=contact_phone, description=description, is_public=form.get("is_public") == "on",
    )
    if business is None:
        business = Business(owner_user_id=user.id, **fields)
        db.session.add(business)
    else:
        for key, value in fields.items():
            setattr(business, key, value)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ValidationError("That web address is already taken. Please choose another.") from None
    return business


# -------------------------------------------------------------------- payment
def save_payment_account(business: Business, form) -> PaymentAccount:
    """Store the Till/Paybill destination. Configuration only; nothing is charged or verified here."""
    payment_type = (form.get("payment_type") or "").upper()
    if payment_type not in (PAYMENT_TILL, PAYMENT_PAYBILL):
        raise ValidationError("Choose Till or Paybill.")

    shortcode = re.sub(r"\s", "", form.get("shortcode") or "")
    if not SHORTCODE_RE.fullmatch(shortcode):
        raise ValidationError("Enter your Till or Paybill number (5-7 digits).")

    reference = " ".join((form.get("account_reference_template") or "").split()) or None
    if payment_type == PAYMENT_TILL:
        reference = None
    elif reference and not REFERENCE_RE.fullmatch(reference):
        raise ValidationError("Account reference may use letters, numbers, spaces, - _ and {braces} (max 64).")

    account = business.payment_account
    if account is None:
        account = PaymentAccount(business_id=business.id)
        db.session.add(account)
    elif account.payment_type != payment_type or account.shortcode != shortcode:
        # A changed destination must be re-verified before it is ever used.
        account.status = "pending"
        account.verified_at = None
    account.payment_type = payment_type
    account.shortcode = shortcode
    account.account_reference_template = reference

    db.session.commit()
    return account
