from android_ratings import _is_valid_rating


def test_zero_and_garbage_ratings_rejected():
    assert not _is_valid_rating("0.0")
    assert not _is_valid_rating("0")
    assert not _is_valid_rating("abc")
    assert _is_valid_rating("3.2884")
    assert _is_valid_rating("5.00")
