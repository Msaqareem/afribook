"""Customer payment fee. Pure functions, no Flask.

The business must receive its full service price. The payment provider takes a percentage of
whatever the customer pays, so the customer's total is "grossed up":

    total = price / (1 - rate)         fee = total - price   (rounded UP to a whole shilling)

Example (1.5% = 150 bps): price 1,500 -> fee 23 -> total 1,523.
The provider keeps 1.5% of 1,523 = 22.85, so the business's 1,500 is fully covered.
"""

BPS_DENOMINATOR = 10_000


def payment_fee_kes(price_kes: int, fee_bps: int) -> int:
    """Whole-KES fee to add on top of `price_kes`. 0 if the rate is 0."""
    if price_kes < 0:
        raise ValueError("price_kes must not be negative")
    if not 0 <= fee_bps < BPS_DENOMINATOR:
        raise ValueError("fee_bps must be between 0 and 9999")
    if fee_bps == 0 or price_kes == 0:
        return 0
    numerator = price_kes * fee_bps
    denominator = BPS_DENOMINATOR - fee_bps
    return -(-numerator // denominator)  # ceiling division on integers (no float rounding)


def total_kes(price_kes: int, fee_bps: int) -> int:
    return price_kes + payment_fee_kes(price_kes, fee_bps)


def percent_label(fee_bps: int) -> str:
    """150 -> '1.5%', 300 -> '3%'."""
    text = f"{fee_bps / 100:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"
