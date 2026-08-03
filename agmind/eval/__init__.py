"""RAG evaluation harness (M11 / Phase 18).

Deliberately import-light: ``agmind.cli`` imports every command module at startup, so nothing
here may pull numpy or a network client at import time. Submodules are imported on use.

Design contract: ``.planning/phases/18-rag-eval-m11/18-AI-SPEC.md``.
"""

from __future__ import annotations

__all__: list[str] = []
