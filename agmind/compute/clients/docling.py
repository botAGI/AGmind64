"""HTTP client for docling-serve (document parsing).

docling-serve (CPU image ``quay.io/docling-project/docling-serve-cpu``) exposes:
- GET  /health             — liveness
- GET  /version            — docling-serve + docling versions
- POST /v1/convert/file    — sync multipart convert (returns JSON with
  ``status`` + server-side ``processing_time``)

Stdlib only — no httpx/requests (not in runtime deps). Multipart/form-data is
encoded by hand. Mirrors the frozen-dataclass + custom-error shape of
``llama_server.py``. The default URL is the host-published docling port (5002),
NOT docling's internal 5001 or the parent's 8765.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agmind.core.logging import logger

log = logger(__name__)

# Fixed multipart boundary: unlikely to collide with PDF bytes, and keeps
# requests deterministic for tests (random not needed for correctness here).
_BOUNDARY = "agmindDoclingBoundary7f3a1c"

# Preset → docling /v1/convert/file form fields. Confirmed live on v1.18.0
# (do_ocr / do_table_structure / table_mode are real fields per обкатка).
_PRESETS: dict[str, dict[str, str]] = {
    "fast": {"do_ocr": "false", "do_table_structure": "false"},
    "balanced": {"do_ocr": "true", "do_table_structure": "true", "table_mode": "fast"},
    "scan": {"do_ocr": "true", "do_table_structure": "true", "table_mode": "accurate"},
}


class DoclingError(Exception):
    """Raised on docling-serve HTTP errors (4xx/5xx, network, non-JSON)."""


def preset_fields(preset: str) -> dict[str, str]:
    """Return the form fields for a named preset (fast / balanced / scan)."""
    try:
        return dict(_PRESETS[preset])
    except KeyError:
        raise ValueError(f"unknown preset '{preset}': expected one of {sorted(_PRESETS)}") from None


def encode_multipart(
    fields: dict[str, str | list[str]],
    files: list[tuple[str, str, bytes, str]],
    boundary: str = _BOUNDARY,
) -> tuple[str, bytes]:
    """Encode multipart/form-data.

    ``fields`` maps name→value; a list value is emitted as a repeated field (how
    docling expects array params like ``to_formats``). ``files`` is a list of
    ``(name, filename, content, content_type)``. Returns
    ``(content_type_header, body_bytes)``.
    """
    crlf = b"\r\n"
    out: list[bytes] = []

    def _field(name: str, value: str) -> None:
        out.append(f"--{boundary}".encode())
        out.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        out.append(b"")
        out.append(value.encode("utf-8"))

    for name, value in fields.items():
        values = value if isinstance(value, list) else [value]
        for item in values:
            _field(name, item)

    for name, filename, content, content_type in files:
        out.append(f"--{boundary}".encode())
        out.append(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode())
        out.append(f"Content-Type: {content_type}".encode())
        out.append(b"")
        out.append(content)

    out.append(f"--{boundary}--".encode())
    out.append(b"")
    body = crlf.join(out)
    return f"multipart/form-data; boundary={boundary}", body


@dataclass(frozen=True)
class DoclingClient:
    """REST client for docling-serve.

    Use:
        client = DoclingClient("http://localhost:5002")
        client.health()
        result = client.convert_file("doc.pdf", preset="fast")
        result["processing_time"]   # server-side seconds
    """

    base_url: str
    timeout: float = 300.0
    verify_ssl: bool = True
    extra_headers: dict[str, str] = field(default_factory=dict)

    def health(self) -> dict[str, Any]:
        """GET /health."""
        return self._get_json("/health")

    def version(self) -> dict[str, Any]:
        """GET /version."""
        return self._get_json("/version")

    def convert_file(
        self,
        pdf_path: str | Path,
        *,
        to_formats: tuple[str, ...] = ("md",),
        preset: str = "balanced",
        extra_fields: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """POST /v1/convert/file (sync, multipart). Returns the parsed JSON body."""
        path = Path(pdf_path)
        content = path.read_bytes()
        fields: dict[str, str | list[str]] = dict(preset_fields(preset))
        fields["to_formats"] = list(to_formats)
        if extra_fields:
            fields.update(extra_fields)
        content_type, body = encode_multipart(
            fields, [("files", path.name, content, "application/pdf")]
        )
        return self._post_json("/v1/convert/file", content_type, body)

    # ---- HTTP plumbing ----

    def _ssl_ctx(self, url: str) -> ssl.SSLContext | None:
        if not url.startswith("https"):
            return None
        ctx = ssl.create_default_context()
        if not self.verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _get_json(self, path: str) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + path
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        for k, v in self.extra_headers.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(
                req, timeout=self.timeout, context=self._ssl_ctx(url)
            ) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise DoclingError(f"HTTP {exc.code} GET {path}") from exc
        except urllib.error.URLError as exc:
            raise DoclingError(f"Network error GET {url}: {exc.reason}") from exc
        return _parse_json(raw, url)

    def _post_json(self, path: str, content_type: str, body: bytes) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + path
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", content_type)
        req.add_header("Accept", "application/json")
        for k, v in self.extra_headers.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(
                req, timeout=self.timeout, context=self._ssl_ctx(url)
            ) as resp:
                raw = resp.read()
                resp_ct = str(resp.headers.get("Content-Type", "")) if resp.headers else ""
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise DoclingError(f"HTTP {exc.code} POST {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise DoclingError(f"Network error POST {url}: {exc.reason}") from exc
        if "json" not in resp_ct.lower():
            raise DoclingError(
                f"non-JSON response from {url} (Content-Type: {resp_ct or 'unknown'}); "
                "image-export modes return a zip — bench expects JSON output formats"
            )
        return _parse_json(raw, url)


def _parse_json(raw: bytes, url: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise DoclingError(f"Non-JSON response from {url}: {raw[:200]!r}") from exc
    if not isinstance(payload, dict):
        raise DoclingError(f"Unexpected JSON response from {url}")
    return payload
