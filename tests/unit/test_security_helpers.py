import pytest

from app.security import is_safe_next_url
from app.services.auth_service import hash_otp


@pytest.mark.parametrize("url", ["/dashboard", "/admin/", "/a/b?x=1"])
def test_safe_next_urls(url):
    assert is_safe_next_url(url)


@pytest.mark.parametrize("url", [None, "", "//evil.com", "https://evil.com", "evil.com", "/\\evil.com", "javascript:alert(1)"])
def test_unsafe_next_urls(url):
    assert not is_safe_next_url(url)


def test_hash_otp_is_bound_to_phone_purpose_and_secret():
    base = hash_otp("123456", "+254712345678", "signup_verify", "s")
    assert base == hash_otp("123456", "+254712345678", "signup_verify", "s")
    assert base != hash_otp("123457", "+254712345678", "signup_verify", "s")
    assert base != hash_otp("123456", "+254712345679", "signup_verify", "s")
    assert base != hash_otp("123456", "+254712345678", "password_reset", "s")
    assert base != hash_otp("123456", "+254712345678", "signup_verify", "other")
    assert "123456" not in base and len(base) == 64
