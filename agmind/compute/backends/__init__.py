"""Compute backends. Subclasses Backend ABC — см. base.py.

Engines внутри backend живут в `_engines/`. Каждый engine модуль
определяет один класс engine, который backend инстанцирует.
"""

from __future__ import annotations
