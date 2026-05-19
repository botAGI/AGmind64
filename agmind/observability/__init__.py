"""AGmind observability module (Phase H'.D placeholder, Phase H'' impl).

Сейчас (H'.D): только декларация модуля, configs живут в `templates/observability/`.

В Phase H'' добавим:
- OpenTelemetry SDK integration для traces (LlamaServerClient → Tempo)
- GenAI semantic conventions (gen_ai.* attributes)
- TTFT histograms в приложении (R14 — llama.cpp /metrics не отдаёт)
- Helper functions для span creation с auto-context-binding

См. ADR-0007 (Observability Stack Architecture).
"""

from __future__ import annotations

__all__: list[str] = []
