import pytest

from app.utils.fees import payment_fee_kes, percent_label, total_kes


@pytest.mark.parametrize("price,bps,fee", [
    (1500, 150, 23),     # 1500 * 150 / 9850 = 22.84 -> 23
    (1000, 150, 16),     # 15.23 -> 16
    (500, 150, 8),       # 7.61 -> 8
    (100, 150, 2),       # 1.52 -> 2
    (1, 150, 1),         # always at least 1 shilling when a rate applies
    (2000, 300, 62),     # 61.86 -> 62
    (985, 150, 15),      # 985 * 150 / 9850 = 15 exactly, so no rounding up
])
def test_known_fees(price, bps, fee):
    assert payment_fee_kes(price, bps) == fee


@pytest.mark.parametrize("price", [1, 7, 99, 500, 1499, 1500, 2500, 12345, 250000])
@pytest.mark.parametrize("bps", [50, 150, 300])
def test_provider_cut_never_exceeds_fee_and_overshoot_is_under_one_shilling(price, bps):
    fee = payment_fee_kes(price, bps)
    total = price + fee
    provider_cut = total * bps / 10_000
    assert provider_cut <= fee + 1e-9          # the business never subsidises the provider
    assert fee - provider_cut < 1 + 1e-9       # and the customer overpays by less than KES 1


def test_zero_rate_and_zero_price():
    assert payment_fee_kes(1500, 0) == 0
    assert payment_fee_kes(0, 150) == 0
    assert total_kes(1500, 0) == 1500


def test_total():
    assert total_kes(1500, 150) == 1523


@pytest.mark.parametrize("price,bps", [(-1, 150), (100, -1), (100, 10_000)])
def test_invalid_inputs(price, bps):
    with pytest.raises(ValueError):
        payment_fee_kes(price, bps)


def test_percent_label():
    assert percent_label(150) == "1.5%"
    assert percent_label(300) == "3%"
    assert percent_label(125) == "1.25%"
