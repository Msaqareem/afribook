import pytest

from app.utils.format import duration, kes


@pytest.mark.parametrize("value,expected", [(1, "KES 1"), (500, "KES 500"), (1500, "KES 1,500"), (250000, "KES 250,000")])
def test_kes(value, expected):
    assert kes(value) == expected


@pytest.mark.parametrize("minutes,expected", [
    (5, "5 min"), (45, "45 min"), (60, "1 h"), (90, "1 h 30 min"), (120, "2 h"), (480, "8 h"),
])
def test_duration(minutes, expected):
    assert duration(minutes) == expected
