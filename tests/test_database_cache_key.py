from app.database import cache_key


def test_cache_key_distinguishes_city_and_country() -> None:
    base = cache_key("engineer", "TCS", 10)

    assert cache_key("engineer", "TCS", 10, "Chennai", "India") != base
    assert cache_key("engineer", "TCS", 10, "Bengaluru", "India") != cache_key(
        "engineer", "TCS", 10, "Chennai", "India"
    )


def test_cache_key_normalizes_location_case_and_whitespace() -> None:
    assert cache_key("engineer", None, 10, " Delhi ", "INDIA") == cache_key(
        "engineer", None, 10, "delhi", "india"
    )
