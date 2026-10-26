import pytest

from app.utils.phone import mask_phone, normalize_ke_phone


@pytest.mark.parametrize(
    "raw",
    ["0712345678", "0712 345 678", "+254712345678", "254712345678", "712345678",
     "00254712345678", "(0712) 345-678", " 0712345678 "],
)
def test_normalizes_to_e164(raw):
    assert normalize_ke_phone(raw) == "+254712345678"


def test_accepts_01_prefix():
    assert normalize_ke_phone("0112345678") == "+254112345678"


@pytest.mark.parametrize(
    "raw",
    [None, "", "abc", "071234567", "07123456789", "+255712345678", "0212345678",
     "+2547123456789", "07123x5678", "٠٧١٢٣٤٥٦٧٨"],
)
def test_rejects_invalid(raw):
    assert normalize_ke_phone(raw) is None


def test_mask_phone():
    assert mask_phone("+254712345678") == "+254******678"
    assert mask_phone(None) == "***"
