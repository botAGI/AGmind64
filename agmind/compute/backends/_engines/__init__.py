"""Engine implementations внутри backend.

Каждый engine модуль определяет один класс — `LlamaCppCPUEngine`,
`LlamaCppVulkanEngine`, `LlamaCppHIPEngine`, `VLLMROCmEngine` (M2),
`InfinityROCmEngine` (M2).

Engine модули НЕ должны зависеть друг от друга. Backend выбирает
engine через factory `_engines.get(engine_name)` (см. backend.make()).
"""

from __future__ import annotations
