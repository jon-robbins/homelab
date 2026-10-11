from homelab_workers.shared.release_formatting import human_size, int_field


def test_human_size_gib() -> None:
    assert human_size(1 << 30) == "1.0 GiB"


def test_int_field() -> None:
    assert int_field("12") == 12
    assert int_field(None) is None
