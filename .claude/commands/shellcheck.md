---
description: Прогнать shellcheck по всем bash-скриптам проекта с чистым summary
---

# /shellcheck — lint всех bash-скриптов

AGmind state: все скрипты обязаны проходить `shellcheck -S warning`
(см. CLAUDE.md раздел 6). Эта команда находит все `*.sh` в репозитории
и прогоняет их за раз.

## Как действовать

1. Собери список скриптов:
   ```bash
   find /home/agmind/AGmind -name "*.sh" \
     -not -path "*/node_modules/*" \
     -not -path "*/.planning/archive/*" \
     -not -path "*/volumes/*"
   ```

2. Прогоняй shellcheck с severity warning:
   ```bash
   shellcheck -S warning -x <files>
   ```

   `-x` позволяет `source` других файлов (наш проект активно это делает).

3. Собери compact-summary:

   ```
   lib/config.sh        ✅
   lib/compose.sh       ✅
   install.sh           ⚠️  3 warnings
   scripts/agmind.sh    ✅
   ```

4. Если есть warnings/errors — покажи first 10 строк с `file:line: code: message`.
   Не фикси автоматически — спроси пользователя.

## Install shellcheck if missing

```bash
sudo apt-get install -y shellcheck
```
