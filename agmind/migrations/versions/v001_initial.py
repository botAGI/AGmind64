"""v001 — initial baseline (Phase L.D).

Зачем: устанавливает schema_version=1 для existing installs. Сам state
(setup-state.json, snapshots/meta.json) на момент написания этой миграции уже
имеет форму, которую мы фиксируем как baseline. Будущие breaking changes
получат отдельные v002+ миграции, которые конвертируют формат вперёд.
"""

from __future__ import annotations

from agmind.migrations.base import Migration, MigrationContext


class V001Initial(Migration):
    version = 1
    description = "Initial baseline (schema_version=1) — ensures user state dir exists."

    def up(self, ctx: MigrationContext) -> None:
        ctx.user_state_dir.mkdir(parents=True, exist_ok=True)
        ctx.log.info("v001: baseline applied (user_state_dir=%s)", ctx.user_state_dir)

    def down(self, ctx: MigrationContext) -> None:
        # Невозвратная — мы не удаляем директорию, потому что там может лежать
        # setup-state.json, cf_dns_api_token и другие данные пользователя.
        ctx.log.info("v001: down is no-op (cannot revert below baseline)")
