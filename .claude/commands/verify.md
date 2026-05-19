---
description: Запустить Definition of Done из CLAUDE.md — проверить все применимые verify-команды
---

# /verify — проверка "работы зелёно"

Канон Boris Cherny: **задача не закрыта пока не прогнан verify**. Эта команда
запускает все применимые verify-команды из раздела 10 `CLAUDE.md` и показывает
пользователю зелёный/красный результат по каждой.

## Как действовать

1. Определи какие verify-проверки нужны по изменениям в текущем diff:
   ```bash
   git diff --name-only HEAD
   ```

2. Для каждой применимой категории запусти команду из CLAUDE.md раздел 10:

   - **Bash-скрипты** (`lib/*.sh`, `scripts/*.sh`, `install.sh`) →
     `shellcheck -S warning <files>`
   - **nginx template** (`templates/nginx.conf.template`) →
     `sudo docker exec agmind-nginx nginx -t` если контейнер жив
   - **docker-compose.yml / versions.env** → валидация что образы существуют
   - **install.sh** → не запускай полный прогон, только `bash -n install.sh`
     + shellcheck
   - **Любые изменения runtime** → `docker ps --format '{{.Names}} {{.Status}}'`
     покажет стек-state

3. Собери результаты в таблицу:

   ```
   ✅ shellcheck: passed
   ✅ nginx -t: ok
   ❌ docker ps: agmind-api unhealthy → hint: docker logs agmind-api
   ```

4. Если что-то красное — **не пытайся автоматически чинить.** Покажи ошибку
   пользователю и предложи следующий шаг.

## Что НЕ делать

- Не запускай `sudo bash install.sh` без явной команды — это долгий прогон с
  rebuild контейнеров. Только dry-run проверки.
- Не трогай production-данные (volumes, БД).
- Не рестартуй контейнеры без нужды.
