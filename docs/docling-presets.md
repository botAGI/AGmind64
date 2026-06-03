# Docling conversion presets

`docling` (CPU image `quay.io/docling-project/docling-serve-cpu`) parses PDFs to
Markdown/JSON. The `agmind docling bench` command times a preset against a real
PDF so you can pick the right speed/quality trade-off for your documents.

## Presets

The presets map to docling-serve `/v1/convert/file` form fields:

| Preset | OCR | Table structure | Table mode | Use for |
|--------|-----|-----------------|------------|---------|
| `fast` | off | off | — | Born-digital PDFs with selectable text; fastest. |
| `balanced` | on | on | fast | Default — mixed documents, quick table detection. |
| `scan` | on | on | accurate | Scanned / image-heavy PDFs; slowest, highest fidelity. |

## Benchmarking

```bash
agmind docling bench report.pdf --preset balanced --iter 3
agmind docling bench report.pdf --preset scan --json
```

- `--iter N` runs N conversions. Run 1 is **cold** (first-call / model-load
  overhead); the mean of the rest is **warm**.
- Both **wall-clock** and the server-reported **processing_time** are recorded.
  Base preset comparisons on the server time — wall-clock includes HTTP and
  serialization overhead that distorts short conversions.
- `--url` overrides the endpoint (default `$AGMIND_DOCLING_URL` or
  `http://localhost:5002`, the host-published docling port).

Cold timing is dominated by the one-time model download/load on a fresh cache;
pre-warm the `/var/lib/agmind/docling-cache` mount before comparing presets.
