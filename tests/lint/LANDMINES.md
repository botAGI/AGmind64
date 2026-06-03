# Rendered-output landmines

`scripts/checks/landmines_check.py` renders the full service profile in-process
and scans the **output** for mechanically-checkable Правила Карпатого. It
complements the descriptor-level gates (`digest_check.py`, the
no-unguarded-interpolation renderer test) by asserting the same invariants on
what the renderer actually emits — where a regression would actually ship.

`critical` hits fail the gate (exit 1); `warning` hits are reported but do not
fail. The table below is the human mirror of the canonical `LANDMINES` dict in
`landmines_check.py` (a test guards the two against drift).

| ID  | Severity | Правило  | Check |
|-----|----------|----------|-------|
| L01 | critical | #8       | no `:latest` tag in any rendered image |
| L02 | critical | #8       | every rendered image is digest-pinned (`@sha256:`) |
| L03 | critical | #5       | every volume is a host bind mount (no anonymous/named volume) |
| L04 | critical | #10/#11  | no bare `${VAR}` (unguarded) left in the rendered output |
| L05 | warning  | logging  | every service caps its log size (`logging.options.max-size`) |
