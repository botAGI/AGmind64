"""Tests for agmind.compute.clients.docling — stdlib-urllib docling-serve client.

Mocks urllib (no live docling-serve). Confirms multipart encoding, preset→form
field mapping, JSON parsing, and error handling against the v1.18.0 contract
verified in research обкатка (GET /health, GET /version, POST /v1/convert/file).
"""

from __future__ import annotations

import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agmind.compute.clients.docling import (
    DoclingClient,
    DoclingError,
    encode_multipart,
    preset_fields,
)

pytestmark = pytest.mark.backend_any


def _mock_json(body: bytes):
    resp = MagicMock()
    resp.read.return_value = body
    resp.headers = {"Content-Type": "application/json"}
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=None)
    return resp


# ---- presets ----


def test_preset_fields_known_presets_differ() -> None:
    fast = preset_fields("fast")
    scan = preset_fields("scan")
    assert fast["do_ocr"] == "false"
    assert scan["do_ocr"] == "true"
    assert scan["table_mode"] == "accurate"


def test_preset_fields_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown preset"):
        preset_fields("turbo")


# ---- multipart encoding ----


def test_encode_multipart_includes_fields_file_and_boundary() -> None:
    content_type, body = encode_multipart(
        {"do_ocr": "true", "to_formats": ["md"]},
        [("files", "doc.pdf", b"%PDF-1.4 data", "application/pdf")],
        boundary="BOUND123",
    )
    assert content_type == "multipart/form-data; boundary=BOUND123"
    text = body.decode("latin-1")
    assert "--BOUND123" in text
    assert 'name="do_ocr"' in text
    assert 'name="files"; filename="doc.pdf"' in text
    assert "application/pdf" in text
    assert "%PDF-1.4 data" in text
    assert text.rstrip().endswith("--BOUND123--")


def test_encode_multipart_repeats_list_fields() -> None:
    _ct, body = encode_multipart({"to_formats": ["md", "json"]}, [], boundary="B")
    text = body.decode("latin-1")
    assert text.count('name="to_formats"') == 2
    assert "md" in text and "json" in text


# ---- health / version ----


def test_health_parses_json() -> None:
    client = DoclingClient("http://localhost:5002")
    with patch("urllib.request.urlopen", return_value=_mock_json(b'{"status": "ok"}')):
        assert client.health() == {"status": "ok"}


def test_version_parses_json() -> None:
    client = DoclingClient("http://localhost:5002")
    with patch(
        "urllib.request.urlopen",
        return_value=_mock_json(b'{"version": "1.18.0"}'),
    ):
        assert client.version()["version"] == "1.18.0"


# ---- convert_file ----


def test_convert_file_posts_multipart_and_returns_json(tmp_path: Path) -> None:
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(b"%PDF-1.4\n/Type /Page\n")
    client = DoclingClient("http://localhost:5002")

    captured: dict[str, object] = {}

    def fake_urlopen(req, *args, **kwargs):
        captured["url"] = req.full_url
        captured["content_type"] = req.get_header("Content-type")
        captured["method"] = req.get_method()
        return _mock_json(b'{"status": "success", "processing_time": 0.31}')

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = client.convert_file(pdf, preset="fast")

    assert captured["url"].endswith("/v1/convert/file")
    assert captured["method"] == "POST"
    assert str(captured["content_type"]).startswith("multipart/form-data; boundary=")
    assert result["status"] == "success"
    assert result["processing_time"] == 0.31


def test_convert_file_http_error_raises_docling_error(tmp_path: Path) -> None:
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    client = DoclingClient("http://localhost:5002")

    err = urllib.error.HTTPError("u", 422, "Unprocessable", {}, None)
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(DoclingError, match="422"):
            client.convert_file(pdf)


def test_convert_file_non_json_response_raises(tmp_path: Path) -> None:
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    client = DoclingClient("http://localhost:5002")

    zip_resp = MagicMock()
    zip_resp.read.return_value = b"PK\x03\x04zipbytes"
    zip_resp.headers = {"Content-Type": "application/zip"}
    zip_resp.__enter__ = MagicMock(return_value=zip_resp)
    zip_resp.__exit__ = MagicMock(return_value=None)
    with patch("urllib.request.urlopen", return_value=zip_resp):
        with pytest.raises(DoclingError, match="non-JSON|zip"):
            client.convert_file(pdf)
