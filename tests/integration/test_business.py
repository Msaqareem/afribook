import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.business import Business, Category, PaymentAccount
from tests.helpers import PASSWORD, PHONE, PHONE_E164, login, make_user

OTHER_PHONE = "0722000111"
OTHER_E164 = "+254722000111"


def category_id(app, slug="salon"):
    with app.app_context():
        return db.session.scalar(select(Category.id).where(Category.slug == slug))


def profile_data(app, **overrides):
    data = {
        "name": "Glow Salon", "slug": "glow-salon", "category_id": category_id(app),
        "county": "Nairobi", "address": "Mpaka Road, Westlands, near Sarit Centre",
        "contact_phone": PHONE, "description": "Hair and beauty.", "is_public": "on",
    }
    data.update(overrides)
    return data


def owner_client(app, phone=PHONE, e164=PHONE_E164):
    make_user(app, phone=e164)
    client = app.test_client()
    login(client, phone=phone)
    return client


def get_business(app, slug="glow-salon"):
    with app.app_context():
        business = db.session.scalar(select(Business).where(Business.slug == slug))
        if business is not None:
            db.session.expunge(business)
        return business


def business_count(app):
    with app.app_context():
        return db.session.scalar(select(func.count()).select_from(Business))


# ------------------------------------------------------------------ seed data
def test_starter_categories_are_seeded(app):
    with app.app_context():
        slugs = set(db.session.scalars(select(Category.slug)))
    assert {"salon", "barber", "clinic", "other"} <= slugs


# ------------------------------------------------------------------- onboarding
def test_profile_form_has_county_and_location_but_no_town(app):
    html = owner_client(app).get("/settings/profile").get_data(as_text=True)
    assert 'name="county"' in html and 'name="address"' in html
    assert 'name="town"' not in html and "Town / city" not in html



def test_dashboard_prompts_setup_when_no_business(app):
    client = owner_client(app)
    resp = client.get("/dashboard")
    assert resp.status_code == 200 and b"Set up my business" in resp.data


def test_profile_form_renders_with_categories_and_counties(app):
    client = owner_client(app)
    resp = client.get("/settings/profile")
    assert resp.status_code == 200
    assert b"Salon" in resp.data and b"Nairobi" in resp.data and b"Murang&#39;a" in resp.data


def test_create_business_then_redirected_to_payment(app):
    client = owner_client(app)
    resp = client.post("/settings/profile", data=profile_data(app))
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/settings/payment")
    business = get_business(app)
    assert business.name == "Glow Salon" and business.county == "Nairobi"
    assert business.contact_phone == PHONE_E164 and business.is_public is True
    assert business.status == "active" and business.timezone == "Africa/Nairobi"


def test_slug_is_normalised_to_lowercase(app):
    client = owner_client(app)
    client.post("/settings/profile", data=profile_data(app, slug="  Glow-Salon "))
    assert get_business(app, "glow-salon") is not None


@pytest.mark.parametrize("field,value", [
    ("slug", "ab"), ("slug", "has space"), ("slug", "login"), ("slug", "admin"),
    ("name", "x"), ("county", "Atlantis"), ("address", "no"), ("address", ""),
    ("contact_phone", "12345"), ("category_id", "999999"), ("category_id", "abc"),
    ("description", "x" * 501),
])
def test_invalid_profile_is_rejected(app, field, value):
    client = owner_client(app)
    resp = client.post("/settings/profile", data=profile_data(app, **{field: value}))
    assert resp.status_code == 400
    assert business_count(app) == 0


def test_inactive_category_is_rejected(app):
    with app.app_context():
        cat = db.session.scalar(select(Category).where(Category.slug == "dental"))
        cat.is_active = False
        db.session.commit()
        cid = cat.id
    try:
        client = owner_client(app)
        assert client.post("/settings/profile", data=profile_data(app, category_id=cid)).status_code == 400
    finally:
        with app.app_context():
            db.session.get(Category, cid).is_active = True
            db.session.commit()


def test_slug_must_be_unique_across_owners(app):
    first = owner_client(app)
    first.post("/settings/profile", data=profile_data(app))
    second = owner_client(app, phone=OTHER_PHONE, e164=OTHER_E164)
    resp = second.post("/settings/profile", data=profile_data(app, name="Other Salon", contact_phone=OTHER_PHONE))
    assert resp.status_code == 400 and b"already taken" in resp.data
    assert business_count(app) == 1


def test_one_business_per_owner_second_post_updates(app):
    client = owner_client(app)
    client.post("/settings/profile", data=profile_data(app))
    resp = client.post("/settings/profile", data=profile_data(app, name="Glow Salon & Spa", address="Kilimani, Ngong Road"))
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/dashboard")
    assert business_count(app) == 1
    assert get_business(app).address == "Kilimani, Ngong Road"


def test_owner_can_keep_own_slug_when_updating(app):
    client = owner_client(app)
    client.post("/settings/profile", data=profile_data(app))
    assert client.post("/settings/profile", data=profile_data(app, address="CBD, Tom Mboya Street")).status_code == 302


def test_owner_can_change_slug(app):
    client = owner_client(app)
    client.post("/settings/profile", data=profile_data(app))
    client.post("/settings/profile", data=profile_data(app, slug="glow-beauty"))
    assert get_business(app, "glow-beauty") is not None and get_business(app, "glow-salon") is None


def test_owner_pages_require_owner_role(app):
    anon = app.test_client()
    assert anon.get("/settings/profile").status_code == 302
    assert anon.get("/settings/payment").status_code == 302
    make_user(app, phone="+254733000111", role="admin")
    admin = app.test_client()
    login(admin, phone="0733000111")
    assert admin.get("/settings/profile").status_code == 403
    assert admin.post("/settings/profile", data=profile_data(app)).status_code == 403
    assert admin.get("/settings/payment").status_code == 403


# ------------------------------------------------------------------ public page
def test_public_page_visible_to_anyone(app):
    client = owner_client(app)
    client.post("/settings/profile", data=profile_data(app))
    resp = app.test_client().get("/glow-salon")
    assert resp.status_code == 200
    assert b"Glow Salon" in resp.data and b"Westlands" in resp.data and b"Nairobi" in resp.data and b"Only you can see" not in resp.data


def test_public_page_slug_is_case_insensitive(app):
    owner_client(app).post("/settings/profile", data=profile_data(app))
    assert app.test_client().get("/Glow-Salon").status_code == 200


def test_unknown_slug_is_404(app):
    assert app.test_client().get("/no-such-business").status_code == 404


def test_hidden_business_404_for_public_but_preview_for_owner(app):
    client = owner_client(app)
    client.post("/settings/profile", data=profile_data(app, is_public=""))  # unticked checkbox sends nothing
    assert get_business(app).is_public is False
    assert app.test_client().get("/glow-salon").status_code == 404
    resp = client.get("/glow-salon")
    assert resp.status_code == 200 and b"Only you can see" in resp.data


def test_suspended_business_is_not_public(app):
    client = owner_client(app)
    client.post("/settings/profile", data=profile_data(app))
    with app.app_context():
        db.session.scalar(select(Business).where(Business.slug == "glow-salon")).status = "suspended"
        db.session.commit()
    assert app.test_client().get("/glow-salon").status_code == 404


def test_other_owner_cannot_preview_hidden_business(app):
    client = owner_client(app)
    client.post("/settings/profile", data=profile_data(app, is_public=""))
    other = owner_client(app, phone=OTHER_PHONE, e164=OTHER_E164)
    assert other.get("/glow-salon").status_code == 404


def test_static_routes_win_over_slug_route(app):
    client = app.test_client()
    assert b"Log in" in client.get("/login").data
    assert client.get("/health").get_json() == {"status": "ok"}


def test_description_is_escaped_on_public_page(app):
    client = owner_client(app)
    client.post("/settings/profile", data=profile_data(app, description="<script>alert(1)</script>"))
    resp = app.test_client().get("/glow-salon")
    assert b"<script>alert(1)</script>" not in resp.data and b"&lt;script&gt;" in resp.data


# ---------------------------------------------------------------------- payment
def payment_data(**overrides):
    data = {"payment_type": "TILL", "shortcode": "123456", "account_reference_template": ""}
    data.update(overrides)
    return data


def owner_with_business(app):
    client = owner_client(app)
    client.post("/settings/profile", data=profile_data(app))
    return client


def get_payment(app):
    with app.app_context():
        account = db.session.scalar(select(PaymentAccount))
        if account is not None:
            db.session.expunge(account)
        return account


def test_payment_form_shows_account_number_only_for_paybill(app):
    client = owner_with_business(app)
    till_html = client.get("/settings/payment").get_data(as_text=True)
    assert 'id="reference-box" class="hidden"' in till_html
    assert 'name="account_reference_template"' in till_html and "disabled" in till_html
    client.post("/settings/payment", data=payment_data(payment_type="PAYBILL", shortcode="400200"))
    paybill_html = client.get("/settings/payment").get_data(as_text=True)
    assert 'id="reference-box" class=""' in paybill_html
    assert "Paybill business number" in paybill_html


def test_payment_requires_business_first(app):
    client = owner_client(app)
    resp = client.get("/settings/payment")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/settings/profile")


def test_save_till(app):
    client = owner_with_business(app)
    resp = client.post("/settings/payment", data=payment_data())
    assert resp.status_code == 302
    account = get_payment(app)
    assert account.payment_type == "TILL" and account.shortcode == "123456" and account.status == "pending"


def test_till_ignores_account_reference(app):
    client = owner_with_business(app)
    client.post("/settings/payment", data=payment_data(account_reference_template="ABC"))
    assert get_payment(app).account_reference_template is None


def test_paybill_with_reference(app):
    client = owner_with_business(app)
    client.post("/settings/payment", data=payment_data(
        payment_type="paybill", shortcode="400200", account_reference_template="AFRIBOOK-{booking_ref}"))
    account = get_payment(app)
    assert account.payment_type == "PAYBILL" and account.account_reference_template == "AFRIBOOK-{booking_ref}"


@pytest.mark.parametrize("overrides", [
    {"payment_type": "CARD"}, {"payment_type": ""}, {"shortcode": "1234"}, {"shortcode": "12345678"},
    {"shortcode": "12ab56"}, {"payment_type": "PAYBILL", "account_reference_template": "bad;ref!"},
])
def test_invalid_payment_is_rejected(app, overrides):
    client = owner_with_business(app)
    assert client.post("/settings/payment", data=payment_data(**overrides)).status_code == 400
    assert get_payment(app) is None


def test_changing_destination_resets_verification(app):
    client = owner_with_business(app)
    client.post("/settings/payment", data=payment_data())
    with app.app_context():
        account = db.session.scalar(select(PaymentAccount))
        account.status = "verified"
        db.session.commit()
    client.post("/settings/payment", data=payment_data(shortcode="654321"))
    assert get_payment(app).status == "pending"


def test_unchanged_destination_keeps_verification(app):
    client = owner_with_business(app)
    client.post("/settings/payment", data=payment_data())
    with app.app_context():
        db.session.scalar(select(PaymentAccount)).status = "verified"
        db.session.commit()
    client.post("/settings/payment", data=payment_data())
    assert get_payment(app).status == "verified"


def test_one_payment_account_per_business(app):
    client = owner_with_business(app)
    client.post("/settings/payment", data=payment_data())
    client.post("/settings/payment", data=payment_data(shortcode="654321"))
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(PaymentAccount)) == 1


# --------------------------------------------------------- database constraints
def test_db_rejects_bad_slug_format(app):
    uid = make_user(app)
    with app.app_context():
        db.session.add(Business(owner_user_id=uid, category_id=category_id(app), name="X Co", slug="Bad_Slug",
                                contact_phone=PHONE_E164, county="Nairobi", address="Street 1"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_db_rejects_bad_shortcode(app):
    client = owner_with_business(app)
    with app.app_context():
        business = db.session.scalar(select(Business))
        db.session.add(PaymentAccount(business_id=business.id, payment_type="TILL", shortcode="12"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
