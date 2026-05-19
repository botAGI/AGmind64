---
recon: R-karpathy — «Метод Карпатого» + AI-assisted long-running coding (2025-2026)
date: 2026-05-19
status: completed
source_agent: general-purpose (WebSearch + WebFetch)
related: AGMIND_MIGRATION_SPEC.md, R0-autonomous-workflow.md
---

# R-karpathy: Метод Karpathy + современные AI-assisted workflows (2025-2026)

## TL;DR — что применять к нашей миграции

1. **Vibe coding (pure)** — НЕ применять. Karpathy сам отказался Feb 2026,
   на смену пришла "agentic engineering". Veracode: 45% AI-кода с
   уязвимостями. Для production-инфраструктурной миграции — провал.
2. **Karpathy Recipe-2019** — применять полностью. 7 фаз с DoD буквально
   = «end-to-end skeleton → overfit one batch → regularize → squeeze».
3. **Karpathy CLAUDE.md 4 принципа** — 3 из 4 применять (Think, Simplicity,
   Goal-Driven). Surgical (3) — с исключением для фазы B (legacy bulk-mv).
4. **Anthropic dual-agent split (init + worker)** — применять для overnight
   sessions.
5. **Persistent state в JSON** — `migration_progress.json` с executable
   DoD-checks.
6. **One phase per session** (Cognition Devin learnings) — не сваливать.
7. **No scope expansion** — out-of-scope в `progress.json::deferred[]`.
8. **Executable DoD** — никаких «human reviews and confirms»; только
   `make dod-phase-N` returns 0.

## Karpathy: 4 принципа CLAUDE.md (community-distilled)

Из `forrestchang/andrej-karpathy-skills` (skill в Claude Code marketplace):

| # | Принцип | Ключевая идея |
|---|---------|---------------|
| 1 | **Think Before Coding** | "Don't assume. Don't hide confusion. Surface tradeoffs." Если есть multiple interpretations — представить их, не выбирать молча. |
| 2 | **Simplicity First** | "Minimum code that solves the problem. Nothing speculative." Если написал 200 строк а можно 50 — переписать. |
| 3 | **Surgical Changes** | "Touch only what you must. Clean up only your own mess." Не рефакторить то, что не сломано. Соответствовать существующему стилю. |
| 4 | **Goal-Driven Execution** | "Define success criteria. Loop until verified." Strong success criteria → агент loop'ает автономно. Weak criteria → constant clarification. |

Особенно ценная трансформация задач из принципа 4:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

## Karpathy Recipe-2019 → наша миграция

| Recipe principle | Применение в нашей миграции |
|------------------|------------------------------|
| Become one with the data | Фаза A audit + topo analysis (уже сделано) |
| End-to-end skeleton, then dumb baseline | Фаза C — `agmind/compute/` ABC + CPU backend сначала |
| Fix random seed | Docker digests, pinned deps, фиксированные timezones в CI |
| Verify loss @ init | `audit_forbidden.py` exit=0 после фазы B |
| Overfit one batch | Фаза D — новый backend сначала на ОДНОМ сервисе end-to-end |
| Don't be a hero | Не изобретать AB, использовать готовые llama-cpp/vLLM-ROCm |
| Incremental complexity + hypothesis | Каждое нетривиальное изменение → запись гипотезы в progress.json |

## Anthropic harness best practices

Из `code.claude.com/docs/en/best-practices` и
`anthropic.com/engineering/effective-harnesses-for-long-running-agents`:

- **Loop pattern**: gather context → action → verify → repeat
- **Dual-agent split**:
  - `init.md` промпт — setup, inventory, прогресс-файл (один раз)
  - `worker.md` промпт — incremental progress (каждая последующая сессия)
- **Persistent state**: `claude-progress.txt`/`migration_progress.json` +
  git history с descriptive commits
- **JSON > Markdown** для feature-tracking (устойчивее к случайным правкам)
- **Session-startup checklist**: `pwd && git status && git log -10 &&`
  прочитать progress.json + audit + smoke tests, потом инкремент
- **Worktrees / parallel sessions** для code review (избегаем bias)
- **Subagents в `.claude/agents/`** — изолированный контекст + tools
- **Browser automation** для верификации UI claims
- **Prohibit edit/remove of tests** — strongly-worded instructions

## Cognition Devin 2025 annual review — практические уроки

- **Работает**: чётко ограниченные задачи 4-8ч объёма junior-инженера,
  batch-работа через много репо (security patches, framework migrations)
- **НЕ работает**: размытые требования, mid-task pivots, soft skills
- **Паттерн**: batch similar work, document expectations explicitly
  upfront, separate verification from execution
- **Use case**: first-pass работа требующая human review

## Cursor 2.0 Composer mode (Oct 2025)

- Multi-agent: до 8 параллельных агентов в git worktree isolation
- Sandbox terminals (macOS): RW workspace + без интернета по умолчанию
- Это можно эмулировать в Claude Code через subagents + worktrees

## 12 новых правил для AGMIND_MIGRATION_SPEC.md

Все из ресерча агента, формулировка директивная.

**R1. Persistent state (Anthropic-style).** `migration_progress.json` с
полями: `current_phase`, `phase_status`, `dod_checks[]` (каждый —
`{name, command, expected_exit, last_result}`), `last_session_id`,
`blockers[]`, `deferred[]`.

**R2. Session-startup checklist** (буквально):
1. `pwd && git status --short && git log -10 --oneline`
2. Прочитать `migration_progress.json`
3. Прогнать `make audit` (audit-скрипт)
4. Прогнать `make smoke` (baseline-тесты текущей фазы)
5. Только после зелёных 3-4 — приступать к инкрементальной работе.

**R3. Dual-agent split.**
- `init.md` для setup-сессии
- `worker.md` для всех последующих

**R4. Goal-driven phase transformation.** Каждая фаза в спеке:
- `Intent` (одно предложение)
- `DoD` (исполняемые проверки)
- `Out-of-scope` (явный negative list)

**R5. Surgical edits + phase-scope audit.** Audit-скрипт расширить
проверкой `touched_files \ allowed_files_for_current_phase = ∅`.
Исключение для фазы B (Legacy quarantine).

**R6. Test-as-DoD (executable DoD).** Никаких "human reviews".
Фазовый gate: `make dod-phase-N` returns 0 — фаза закрыта.

**R7. Hard freeze критических файлов.** В `progress.json::frozen_files`
SHA256 для audit script, DoD scripts, spec. Mismatch = stop session.

**R8. One-phase-per-run.** Не начинать следующую фазу даже если DoD
зелёное; завершить commit + summary + handoff.

**R9. No scope expansion clause.** Желание исправить вне scope →
`progress.json::deferred[]`, продолжить.

**R10. Hypothesis logging.** Перед нетривиальной правкой —
`progress.json::current_hypothesis` с ожидаемым результатом.

**R11. Fixed determinism.** Docker digests, lock-files, `PYTHONHASHSEED`,
timezone фиксированы.

**R12. Overfit one batch.** Фаза D: новый backend → сначала ОДИН сервис
end-to-end (включая бенч), потом остальные.

## Свежий CLAUDE.md — рекомендация агента

Спека Part 5.1 говорит «1 строка указатель». Агент аргументирует за
**8 строк operational rules** (cold-start):

```
# AGmindx86
1. The single source of truth is AGMIND_MIGRATION_SPEC.md. Read it before any non-trivial action.
2. Always run the session-startup checklist (R2 in the spec) first. Stop if red.
3. Surface assumptions, do not pick silently between interpretations (Karpathy guideline 1).
4. Minimum code that solves the current phase's DoD. Nothing speculative (Karpathy guideline 2).
5. Touch only files in the current phase's allowed_files. Out-of-scope goes to progress.json::deferred (Karpathy guideline 3).
6. DoD is the only ground truth for "done". No claiming completion without `make dod-phase-N` returning 0 (Karpathy guideline 4).
7. Do not edit: AGMIND_MIGRATION_SPEC.md, audit script, DoD scripts, frozen_files. If they must change, stop and report.
8. One phase per session. Commit + update progress.json + handoff at end.
```

**Альтернатива** (если пользователь хочет 1 строку):
`See AGMIND_MIGRATION_SPEC.md §0 ("Working with this codebase").`
И перенести 8 правил в новый §0 спеки.

## Антипаттерны для CLAUDE.md / spec

- Эмодзи, мотивационные фразы, "be helpful" — снижают сигнал-шум.
- Длинные обоснования принципов — раздувают контекст.
- «Когда сомневаешься — спроси» БЕЗ технического сигнала остановки — игнорируется агентом.
- Запреты текстом «не используй X» БЕЗ audit-проверки — не работают.

## Источники (проверено 2026-05-19)

Karpathy:
- https://x.com/karpathy/status/1886192184808149383 (vibe coding tweet)
- http://karpathy.github.io/2019/04/25/recipe/ (Recipe for Training)
- https://github.com/forrestchang/andrej-karpathy-skills (CLAUDE.md skill)

Vibe → Agentic shift:
- https://buttondown.com/verified/archive/the-end-of-vibe-coding-andrej-karpathys-shift-to/
- https://thenewstack.io/vibe-coding-spec-driven/
- https://towardsdatascience.com/from-vibe-coding-to-spec-driven-development/

Anthropic:
- https://code.claude.com/docs/en/best-practices
- https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents

Cognition:
- https://cognition.ai/blog/devin-annual-performance-review-2025

Cursor 2.0:
- https://cursor.com/changelog/2-0
