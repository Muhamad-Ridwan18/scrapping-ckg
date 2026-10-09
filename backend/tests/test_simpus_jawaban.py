import pytest

from app.schemas.puskesmas import SimpusApiIn, _validate_simpus_api_url
from app.services.simpus_jawaban import jawaban_endpoint, jawaban_to_asik_blob


def test_jawaban_endpoint_appends_path():
    assert jawaban_endpoint("https://ckg.example.com") == (
        "https://ckg.example.com/api/v1/ckg/jawaban"
    )
    assert jawaban_endpoint("https://ckg.example.com/api/v1/ckg/jawaban") == (
        "https://ckg.example.com/api/v1/ckg/jawaban"
    )


def test_public_https_url_ok():
    assert _validate_simpus_api_url("https://CKG.Example.com/api/v1/ckg/jawaban") == (
        "https://ckg.example.com/api/v1/ckg/jawaban"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "http://localhost/api",
        "http://127.0.0.1",
        "http://10.0.0.5",
        "http://app.test",
        "http://host.local",
        "ckg.example.com",
    ],
)
def test_simpus_url_rejects_internal(bad):
    with pytest.raises(ValueError):
        _validate_simpus_api_url(bad)


def test_simpus_api_in_strips_token():
    data = SimpusApiIn(api_url="https://ckg.example.com", token="  abc  ")
    assert data.token == "abc"


def test_simpus_api_in_allows_blank_token_for_url_only_update():
    data = SimpusApiIn(api_url="https://ckg.example.com", token="  ")
    assert data.token == ""


def test_jawaban_maps_ppm_to_label():
    blob = jawaban_to_asik_blob(
        "3201000000000001",
        {"FRM000078": {"PPM00000208": "Ya"}},
    )
    assert blob["detail_data"]["data_individu"]["NIK"] == "3201000000000001"
    assert blob["pelayanan_nakes"] == []
    blocks = blob["pemeriksaan_mandiri"]
    assert len(blocks) == 1
    assert blocks[0]["form_data"]
    label, value = next(iter(blocks[0]["form_data"].items()))
    assert "Talasemia" in label
    assert value == "Ya"
    assert "PPM00000208" not in blocks[0]["form_data"]
