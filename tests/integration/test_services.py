import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.business import Business
from app.models.service import Service
from app.services import catalog_service
from tests.helpers import make_user, login
from tests.integration.test_business import (OTHER_E164, OTHER_PHONE, owner_client, profile_data)


def service_data(**overrides):
    data = {"name": "Haircut", "description": "Wash and cut.", "price_kes": "1500",
            "duration_minutes": "45", "is_active": "on"}
    data.update(overrides)
    return data


def owner_with_business(app, phone="0712345678", e164="+254712345678", slug="glow-salon"):
    client = owner_client(app, phone=phone, e164=e164)
    client.post("/settings/profile", data=profile_data(app, slug=slug, name=f"Biz {slug}", contact_phone=phone))
    return client


def all_services(app):
    with app.app_context():
        rows = list(db.session.scalars(select(Service).order_by(Service.id)))
        for row in rows:
            db.session.expunge(row)
        return rows


def add_service(client, **overrides):
    return client.post("/services/new", data=service_data(**overrides))


# ------------------------------------------------------------------ access
def test_requires_login_and_owner_role(app):
    anon = app.test_client()
    assert anon.get("/services/").status_code == 302
    assert anon.post("/services/new", data=service_data()).status_code == 302
    make_user(app, phone="+254733000111", role="admin")
    admin = app.test_client()
    login(admin, phone="0733000111")
    assert admin.get("/services/").status_code == 403
    assert admin.post("/services/new", data=service_data()).status_code == 403


def test_requires_business_first(app):
    client = owner_client(app)
    resp = client.get("/services/")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/settings/profile")
    assert client.post("/services/new", data=service_data()).status_code == 302
    assert all_services(app) == []


# ---------------------------------------------------------------- create/list
def test_empty_state(app):
    client = owner_with_business(app)
    assert b"no services yet" in client.get("/services/").data


def test_create_service(app):
    client = owner_with_business(app)
    resp = add_service(client)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/services/")
    svc = all_services(app)[0]
    assert (svc.name, svc.price_kes, svc.duration_minutes, svc.is_active) == ("Haircut", 1500, 45, True)
    assert isinstance(svc.price_kes, int)
    page = client.get("/services/").get_data(as_text=True)
    assert "Haircut" in page and "KES 1,500" in page and "45 min" in page


def test_price_with_thousands_separator_is_accepted(app):
    client = owner_with_business(app)
    add_service(client, price_kes="1,500")
    assert all_services(app)[0].price_kes == 1500


def test_unticked_checkbox_creates_inactive_service(app):
    client = owner_with_business(app)
    add_service(client, is_active="")
    assert all_services(app)[0].is_active is False


@pytest.mark.parametrize("field,value", [
    ("name", "x"), ("name", ""), ("name", "y" * 81),
    ("price_kes", "0"), ("price_kes", "-5"), ("price_kes", "abc"), ("price_kes", "12.5"),
    ("price_kes", ""), ("price_kes", "250001"),
    ("duration_minutes", "0"), ("duration_minutes", "4"), ("duration_minutes", "481"),
    ("duration_minutes", "abc"), ("duration_minutes", "-30"), ("duration_minutes", ""),
    ("description", "z" * 301),
])
def test_invalid_service_is_rejected(app, field, value):
    client = owner_with_business(app)
    assert add_service(client, **{field: value}).status_code == 400
    assert all_services(app) == []


def test_boundary_values_are_accepted(app):
    client = owner_with_business(app)
    assert add_service(client, name="Min", price_kes="1", duration_minutes="5").status_code == 302
    assert add_service(client, name="Max", price_kes="250000", duration_minutes="480").status_code == 302


def test_service_limit_per_business(app):
    client = owner_with_business(app)
    with app.app_context():
        business = db.session.scalar(select(Business))
        for i in range(catalog_service.MAX_SERVICES_PER_BUSINESS):
            db.session.add(Service(business_id=business.id, name=f"S{i}", price_kes=100, duration_minutes=30))
        db.session.commit()
    assert add_service(client).status_code == 400


# ----------------------------------------------------------------------- edit
def test_edit_service(app):
    client = owner_with_business(app)
    add_service(client)
    sid = all_services(app)[0].id
    assert b"Haircut" in client.get(f"/services/{sid}/edit").data
    resp = client.post(f"/services/{sid}/edit", data=service_data(name="Premium Cut", price_kes="2000", duration_minutes="60"))
    assert resp.status_code == 302
    svc = all_services(app)[0]
    assert (svc.name, svc.price_kes, svc.duration_minutes) == ("Premium Cut", 2000, 60)
    assert len(all_services(app)) == 1


def test_invalid_edit_leaves_service_unchanged(app):
    client = owner_with_business(app)
    add_service(client)
    sid = all_services(app)[0].id
    assert client.post(f"/services/{sid}/edit", data=service_data(price_kes="0")).status_code == 400
    assert all_services(app)[0].price_kes == 1500


def test_edit_unknown_service_is_404(app):
    client = owner_with_business(app)
    assert client.get("/services/9999/edit").status_code == 404


def test_cannot_edit_or_toggle_another_owners_service(app):
    first = owner_with_business(app)
    add_service(first)
    sid = all_services(app)[0].id
    second = owner_with_business(app, phone=OTHER_PHONE, e164=OTHER_E164, slug="other-biz")
    assert second.get(f"/services/{sid}/edit").status_code == 404
    assert second.post(f"/services/{sid}/edit", data=service_data(name="Hacked")).status_code == 404
    assert second.post(f"/services/{sid}/toggle").status_code == 404
    svc = all_services(app)[0]
    assert svc.name == "Haircut" and svc.is_active is True


def test_owner_list_shows_only_own_services(app):
    first = owner_with_business(app)
    add_service(first, name="Mine")
    second = owner_with_business(app, phone=OTHER_PHONE, e164=OTHER_E164, slug="other-biz")
    add_service(second, name="Theirs")
    assert b"Mine" in first.get("/services/").data and b"Theirs" not in first.get("/services/").data


# --------------------------------------------------------------------- toggle
def test_toggle_deactivates_and_reactivates(app):
    client = owner_with_business(app)
    add_service(client)
    sid = all_services(app)[0].id
    client.post(f"/services/{sid}/toggle")
    assert all_services(app)[0].is_active is False
    client.post(f"/services/{sid}/toggle")
    assert all_services(app)[0].is_active is True


def test_toggle_requires_post(app):
    client = owner_with_business(app)
    add_service(client)
    sid = all_services(app)[0].id
    assert client.get(f"/services/{sid}/toggle").status_code == 405


# ---------------------------------------------------------------- public page
def test_public_page_lists_only_active_services(app):
    client = owner_with_business(app)
    add_service(client, name="Active Cut", price_kes="1500", duration_minutes="90")
    add_service(client, name="Hidden Cut", is_active="")
    page = app.test_client().get("/glow-salon").get_data(as_text=True)
    assert "Active Cut" in page and "KES 1,500" in page and "1 h 30 min" in page
    assert "Hidden Cut" not in page


def test_deactivating_removes_service_from_public_page(app):
    client = owner_with_business(app)
    add_service(client, name="Soon Gone")
    assert "Soon Gone" in app.test_client().get("/glow-salon").get_data(as_text=True)
    client.post(f"/services/{all_services(app)[0].id}/toggle")
    assert "Soon Gone" not in app.test_client().get("/glow-salon").get_data(as_text=True)


def test_public_page_without_services(app):
    owner_with_business(app)
    assert b"No services are listed yet" in app.test_client().get("/glow-salon").data


def test_service_text_is_escaped_publicly(app):
    client = owner_with_business(app)
    add_service(client, name="<b>Bold</b>", description="<script>alert(1)</script>")
    page = app.test_client().get("/glow-salon").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_dashboard_shows_service_counts(app):
    client = owner_with_business(app)
    add_service(client, name="A")
    add_service(client, name="B", is_active="")
    page = client.get("/dashboard").get_data(as_text=True)
    assert "1 active" in page and "1 inactive" in page


# ---------------------------------------------------- bookability (for Bucket 5)
def _business(app, slug="glow-salon"):
    with app.app_context():
        return db.session.scalar(select(Business).where(Business.slug == slug)).id


def test_get_bookable_service_rules(app):
    client = owner_with_business(app)
    add_service(client, name="On")
    add_service(client, name="Off", is_active="")
    on, off = all_services(app)
    with app.app_context():
        business = db.session.get(Business, _business(app))
        assert catalog_service.get_bookable_service(business, on.id).name == "On"
        assert catalog_service.get_bookable_service(business, off.id) is None      # inactive
        assert catalog_service.get_bookable_service(business, 99999) is None       # unknown
        business.is_public = False
        assert catalog_service.get_bookable_service(business, on.id) is None       # hidden business
        business.is_public, business.status = True, "suspended"
        assert catalog_service.get_bookable_service(business, on.id) is None       # suspended
        db.session.rollback()


def test_get_bookable_service_rejects_other_businesses_service(app):
    first = owner_with_business(app)
    add_service(first)
    sid = all_services(app)[0].id
    owner_with_business(app, phone=OTHER_PHONE, e164=OTHER_E164, slug="other-biz")
    with app.app_context():
        other = db.session.get(Business, _business(app, "other-biz"))
        assert catalog_service.get_bookable_service(other, sid) is None


# ------------------------------------------------------------------ DB checks
@pytest.mark.parametrize("price,duration", [(0, 30), (-1, 30), (100, 0), (100, -5)])
def test_db_rejects_non_positive_values(app, price, duration):
    owner_with_business(app)
    with app.app_context():
        business = db.session.scalar(select(Business))
        db.session.add(Service(business_id=business.id, name="Bad", price_kes=price, duration_minutes=duration))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
