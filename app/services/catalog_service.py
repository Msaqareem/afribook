"""Service (offering) management for a business."""
import re

from sqlalchemy import func, select

from app.extensions import db
from app.models.business import Business
from app.models.service import Service
from app.services.errors import ValidationError

MAX_SERVICES_PER_BUSINESS = 50
PRICE_MIN, PRICE_MAX = 1, 250_000          # whole KES
DURATION_MIN, DURATION_MAX = 5, 480        # minutes


def _parse_whole_number(raw, label: str) -> int:
    cleaned = re.sub(r"[\s,]", "", raw or "")
    if not re.fullmatch(r"[0-9]{1,9}", cleaned):
        raise ValidationError(f"{label} must be a whole number.")
    return int(cleaned)


def list_services(business_id: int) -> list[Service]:
    return list(db.session.scalars(
        select(Service).where(Service.business_id == business_id)
        .order_by(Service.is_active.desc(), func.lower(Service.name), Service.id)
    ))


def list_active_services(business_id: int) -> list[Service]:
    return [s for s in list_services(business_id) if s.is_active]


def count_services(business_id: int) -> tuple[int, int]:
    """Return (total, active)."""
    services = list_services(business_id)
    return len(services), sum(1 for s in services if s.is_active)


def get_owned_service(business: Business, service_id: int) -> Service | None:
    """A service, but only if it belongs to this business."""
    return db.session.scalar(
        select(Service).where(Service.id == service_id, Service.business_id == business.id)
    )


def save_service(business: Business, form, service: Service | None = None) -> Service:
    name = " ".join((form.get("name") or "").split())
    if not (2 <= len(name) <= 80):
        raise ValidationError("Service name must be 2-80 characters.")

    description = " ".join((form.get("description") or "").split()) or None
    if description and len(description) > 300:
        raise ValidationError("Description must be 300 characters or fewer.")

    price = _parse_whole_number(form.get("price_kes"), "Price")
    if not (PRICE_MIN <= price <= PRICE_MAX):
        raise ValidationError(f"Price must be between KES {PRICE_MIN:,} and KES {PRICE_MAX:,}.")

    duration = _parse_whole_number(form.get("duration_minutes"), "Duration")
    if not (DURATION_MIN <= duration <= DURATION_MAX):
        raise ValidationError(f"Duration must be between {DURATION_MIN} and {DURATION_MAX} minutes.")

    if service is None:
        total, _ = count_services(business.id)
        if total >= MAX_SERVICES_PER_BUSINESS:
            raise ValidationError(f"You can have at most {MAX_SERVICES_PER_BUSINESS} services.")
        service = Service(business_id=business.id)
        db.session.add(service)

    service.name = name
    service.description = description
    service.price_kes = price
    service.duration_minutes = duration
    service.is_active = form.get("is_active") == "on"
    db.session.commit()
    return service


def set_active(service: Service, active: bool) -> Service:
    service.is_active = active
    db.session.commit()
    return service


def get_bookable_service(business: Business, service_id: int) -> Service | None:
    """The only way booking code should load a service: it must be active and the business public.

    Inactive services, hidden/suspended businesses and other businesses' services all return None.
    """
    service = db.session.scalar(
        select(Service).where(Service.id == service_id, Service.business_id == business.id)
    )
    if service is None or not service.is_active or not business.is_listed:
        return None
    return service
