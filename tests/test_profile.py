import pytest
from travelops.profile import load_profile, data_dir


def test_defaults_and_yaml_values(tmp_path):
    p = load_profile(tmp_path)
    assert p.home_airports == () and p.nearby_airports == () and p.currency == "EUR" and p.travellers.adults == 1
    (tmp_path / "profile.yml").write_text(
        "home_airports: [LIS]\ncurrency: USD\ntravellers: {adults: 2}\ncabin: business\nstays: {adults: 3, min_rating: 9}\n"
    )
    p = load_profile(tmp_path)
    assert p.home_airports == ("LIS",) and p.currency == "USD" and p.cabin == "business"
    assert p.travellers.adults == 2 and p.stays.adults == 3 and p.stays.min_rating == 9


def test_invalid_baggage_and_numbers_are_clear(tmp_path):
    (tmp_path / "profile.yml").write_text("baggage: mystery\n")
    with pytest.raises(ValueError, match="baggage"):
        load_profile(tmp_path)
    (tmp_path / "profile.yml").write_text("travellers: {adults: 0}\n")
    with pytest.raises(ValueError, match="adults"):
        load_profile(tmp_path)


def test_data_directory_override(tmp_path, monkeypatch):
    assert data_dir(tmp_path) == tmp_path / "data"
    monkeypatch.setenv("TRAVELOPS_DATA", str(tmp_path / "custom"))
    assert data_dir(tmp_path) == tmp_path / "custom"
