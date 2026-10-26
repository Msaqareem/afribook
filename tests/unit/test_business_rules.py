import pytest

from app.services.business_service import RESERVED_SLUGS, validate_slug
from app.services.errors import ValidationError
from app.utils.kenya import KENYA_COUNTIES, canonical_county


@pytest.mark.parametrize("raw,expected", [
    ("glow-salon", "glow-salon"), ("  Glow-Salon ", "glow-salon"), ("abc", "abc"), ("a1-b2-c3", "a1-b2-c3"),
])
def test_valid_slugs(raw, expected):
    assert validate_slug(raw) == expected


@pytest.mark.parametrize("raw", [
    None, "", "ab", "a" * 41, "has space", "under_score", "-leading", "trailing-", "double--hyphen",
    "UPPER case", "dots.here", "slash/here", "émoji", "login", "admin", "explore", "dashboard", "health",
])
def test_invalid_or_reserved_slugs(raw):
    with pytest.raises(ValidationError):
        validate_slug(raw)


def test_reserved_slugs_cover_application_routes():
    for word in ("login", "register", "dashboard", "admin", "explore", "health", "static", "settings"):
        assert word in RESERVED_SLUGS


def test_there_are_47_counties():
    assert len(KENYA_COUNTIES) == 47 == len(set(KENYA_COUNTIES))


def test_canonical_county_is_case_insensitive():
    assert canonical_county("nairobi") == "Nairobi"
    assert canonical_county(" MURANG'A ") == "Murang'a"
    assert canonical_county("Atlantis") is None
    assert canonical_county(None) is None
