---
recon: R0 — Claude Code autonomous workflow & methodologies beyond GSD
date: 2026-05-19
status: completed
source_agent: claude-code-guide
related: AGMIND_MIGRATION_SPEC.md (Part 2.9 daily routine), session 2026-05-19-overnight.md
---

# R0: Autonomous workflow в Claude Code (2026)

## Ключевые выводы (TL;DR)

1. **`/loop` self-paced** — основной механизм автономки внутри живой
   сессии. Stateful, без polling, сам решает когда продолжать.
2. **`/schedule` (Cloud Routines)** — для 24/7 без локальной машины,
   **не подходит** для нашего кейса: discrete fresh-clone runs, не
   stateful продолжение работы.
3. **Hooks через settings.json** — для guards (PreToolUse блокирует
   destructive ops), не для оркестрации flow.
4. **Spec-Driven Development (SDD)** — это уже наш AGMIND_MIGRATION_SPEC.md.
   Самая релевантная методология для autonomous миграции.
5. **Agentic Engineering (Karpathy 2026)** — plan → execute → evaluate,
   developers = reviewers, agents = workers. Резонирует с фазами A-G спеки.
6. **"Vibe coding" в 2026 уже устарел** — thenewstack.io/vibe-coding-is-passe/.
   На смену пришла agentic engineering со структурированным подходом.
7. **Plan-and-Execute (Anthropic Ultraplan)** — planning отделено от
   execution с явным апрув-гейтом. Совпадает с нашей DoD фазы A.

## Полный отчёт агента

### 1. SKILL `/loop` — детали

- Loop запускает repeating prompts на интервалах (VERIFIED: MindStudio).
- **Self-paced режим**: без указания интервала Claude сам решает когда
  запустить next тик ИЛИ использует Monitor tool вместо polling
  (VERIFIED: whats-new/2026-w15).
- Session-level scheduler: живёт внутри открытой сессии, expires через 7
  дней (VERIFIED: MindStudio).
- Завершение: `/stop-loop` команда (UNVERIFIED для exact syntax).
- Limits: max session context ~100k tokens перед auto-compact (VERIFIED).

### 2. SKILL `/schedule`

| Mode | Где | Гарантии | Кейс |
|------|-----|----------|------|
| `/loop` | Local session | Если сессия жива | Активная работа |
| Desktop Tasks | Локальная машина | Needs awake machine | Overnight if laptop on |
| Cloud Routines (`/schedule`) | Anthropic cloud | 24/7 | Production cron |

- `/schedule` = discrete remote agents, fresh clone каждый run.
- **НЕ подходит** для stateful long-running task (наш случай).

### 3. Hooks через settings.json

Доступные events: SessionStart, SessionStop, PreToolUse, PostToolUse,
Stop, SubagentStart, SubagentStop, InstructionsLoaded, ConfigChange,
WorktreeCreate, WorktreeRemove, PostCompact, Elicitation,
ElicitationResult (VERIFIED: Pixelmojo, thepromptshelf).

- Нет встроенного "restart on completion" — для этого /loop.
- Hooks для guards/side-effects, не для orchestration.
- Пример блокировки git push:
  ```json
  {
    "hooks": {
      "PreToolUse": [
        {
          "matcher": {"tools": ["Bash"], "commands": ["git push.*"]},
          "handler": {"type": "command", "command": "exit 2"}
        }
      ]
    }
  }
  ```

### 4. Background agents

- Надёжно для Bash subprocesses с `run_in_background: true`.
- Timeout 10 мин по умолчанию.
- Monitor tool для streaming событий, не polling.
- НЕ replace agent loop с decision-making.

### 5. Альтернативные методологии (детально)

#### Spec-Driven Development (SDD) ⭐
- SPEC.md как source of truth, не сам код.
- **10x fewer "regenerate from scratch" cycles** чем vibe coding
  (VERIFIED: GitHub Spec Kit 2026, AWS Kiro).
- **Это уже наш AGMIND_MIGRATION_SPEC.md.**

#### Agentic Engineering (Karpathy 2026)
- Сдвиг от vibe coding к структурированным agent workflows.
- Pattern: Plan (human approves) → Execute (agent works) → Evaluate.
- Karpathy Dec 2025: ~80% code written by agents.

#### Plan-and-Execute (Anthropic Ultraplan)
- Separate planning (cloud, interactive approval) от execution (local).
- Auto-re-plan on failures.
- Совпадает с нашей DoD фазы A → апрув → фаза B.

#### TDD / Red-Green-Refactor automation
- Применимо для фазы C/D/E (code generation).
- Pattern: `/loop 5m pytest && claude "fix failing tests"`.

#### Self-Evaluation Loop
- Agent reviews own code, identifies improvements.
- Эффективность: -40-50% human review cycles (VERIFIED: MindStudio, InfoQ).

#### 2026 Best Practice: Layered Autonomy
1. Planning: Spec-driven + Agentic
2. Execution: Plan-and-Execute + TDD
3. Validation: Self-evaluation + Monitor
4. Continuity: /loop OR /schedule

### 6. Безопасность overnight

Hard-stop gates:
- Git push/destructive ops через PreToolUse hooks.
- Permissions: read=allow, write=narrow allowlist, bash=allow safe readonly only.
- Network allowlist на github.com, api.anthropic.com.
- Audit trail в .claude/session-logs/ (автоматический).

### 7. Рекомендация для нашего phase A

```
Workflow:
  /loop self-paced для overnight continuation
  + PreToolUse hooks для блокировки git push/destructive ops
  + Spec-driven (AGMIND_MIGRATION_SPEC.md как source of truth)
  + Plan-and-Execute (DoD фазы A → апрув → B)
  + Self-evaluation loop в конце каждой фазы
```

Конкретный пример settings.json для overnight (см. секцию 6 — нужно
утвердить с пользователем перед применением).

## Применимость к нашей миграции

| Подход | Применять? | Где |
|--------|-----------|-----|
| Spec-Driven Development | ✅ Уже применяется | Корневая спека |
| Agentic Engineering | ✅ Plan/Execute/Evaluate per фаза | Все фазы |
| Plan-and-Execute | ✅ DoD-гейты между фазами | A→B, C→D и т.д. |
| `/loop` self-paced | ⚠️ Использовать если контекст исчерпан | Overnight continuation |
| `/schedule` Cloud | ❌ Не stateful, не подходит | - |
| TDD automation | ✅ Phase C-E | Code generation |
| Self-evaluation | ✅ В конце каждой фазы | Перед апрув-гейтом |
| Vibe coding | ❌ Устарел в 2026 | - |
| PreToolUse hooks | ⚠️ Нужен апрув user'а для settings.json | На фазе B+ |

## Sources

- https://code.claude.com/docs/en/whats-new/2026-w15
- https://claudefa.st/blog/guide/mechanics/monitor
- https://thenewstack.io/vibe-coding-is-passe/
- https://medium.com/predict/spec-driven-development-with-ai-coding-agents-the-definitive-guide-453fba1baf39
- https://www.pixelmojo.io/blogs/claude-code-hooks-production-quality-ci-cd-patterns
- https://aiforanything.io/blog/claude-code-ultraplan-guide-2026
- https://stevekinney.com/courses/ai-development/test-driven-development-with-claude
- https://checkmarx.com/learn/ai-security/claude-code-security-top-6-risks-controls-and-best-practices/
- https://wmedia.es/en/tips/claude-code-schedule-vs-loop-vs-cron
- https://www.infoq.com/news/2026/04/claude-code-review/
