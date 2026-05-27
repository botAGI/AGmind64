#!/usr/bin/env bash
# Phase M5 followup: self-hosted GitHub Actions runner setup для AGmind64.
#
# Что делает:
#   1. Скачивает latest actions/runner tarball для linux-x64 (динамически
#      через GH API — не хардкод).
#   2. Распаковывает в ~/.cache/gha-runner/<repo>/.
#   3. Получает registration token через gh CLI (нужен `gh auth login` и
#      `repo` scope; у тебя уже есть — проверено).
#   4. Регистрирует runner с labels: self-hosted, linux, x64, strix-halo.
#   5. Устанавливает systemd user-юнит для autostart на reboot.
#
# Зачем: GitHub-hosted runners лимитят Free план 2000 min/мес; для РФ
# карты не работают, spending limit не поднять. Self-hosted = бесплатно
# без лимитов, jobs бегут на твоём же x86 железе → test-strix-halo может
# тестировать реальный GPU вместо эмуляции.
#
# Usage:
#   bash scripts/ops/setup_gha_runner.sh [OWNER/REPO]
#
# Default repo: botAGI/AGmind64. Override через CLI arg.
#
# Безопасность: для PRs from external forks runner НЕ должен запускаться
# (произвольный код от незнакомцев на твоей машине). После setup открой
# Settings → Actions → General → "Fork pull request workflows from outside
# collaborators" → выбери "Require approval for first-time contributors".

set -euo pipefail

REPO="${1:-botAGI/AGmind64}"
RUNNER_DIR="${HOME}/.cache/gha-runner/${REPO//\//_}"
LABELS="self-hosted,linux,x64,strix-halo"
RUNNER_NAME="${RUNNER_NAME:-$(hostname)-agmind}"

log() { printf '\033[1;32m[setup-runner]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[setup-runner]\033[0m %s\n' "$*" >&2; exit 1; }

command -v gh >/dev/null || die "gh CLI не установлен. apt install gh — или см. https://cli.github.com/"
command -v curl >/dev/null || die "curl нужен"
command -v jq >/dev/null || die "jq нужен (apt install jq)"

gh auth status >/dev/null 2>&1 || die "gh не авторизован. Выполни: gh auth login"

# ---- 1. Resolve latest runner version + tarball URL ----
log "Resolving latest actions/runner release..."
RUNNER_URL=$(gh api repos/actions/runner/releases/latest --jq '
  .assets[]
  | select(.name | test("linux-x64-[0-9].*\\.tar\\.gz$"))
  | .browser_download_url
')
[[ -n "${RUNNER_URL}" ]] || die "Не смог найти runner tarball URL"
RUNNER_TARBALL=$(basename "${RUNNER_URL}")
RUNNER_VERSION=$(echo "${RUNNER_TARBALL}" | sed -E 's/actions-runner-linux-x64-([^.]+\.[^.]+\.[^.]+)\.tar\.gz/\1/')
log "Latest runner: v${RUNNER_VERSION}"

# ---- 2. Download + extract ----
mkdir -p "${RUNNER_DIR}"
cd "${RUNNER_DIR}"

if [[ -f "${RUNNER_TARBALL}" ]]; then
    log "Tarball уже скачан: ${RUNNER_TARBALL}"
else
    log "Скачиваю ${RUNNER_URL}..."
    curl -fL -o "${RUNNER_TARBALL}" "${RUNNER_URL}"
fi

if [[ -x "./config.sh" ]]; then
    log "Runner уже распакован в ${RUNNER_DIR}"
else
    log "Распаковываю..."
    tar xzf "${RUNNER_TARBALL}"
fi

# ---- 3. Registration token (single-use, expires 1h) ----
log "Получаю registration token через gh API..."
REG_TOKEN=$(gh api -X POST "repos/${REPO}/actions/runners/registration-token" --jq '.token')
[[ -n "${REG_TOKEN}" ]] || die "Не смог получить registration token (нет admin permissions на ${REPO}?)"

# ---- 4. Configure runner (idempotent — re-run safe) ----
if [[ -f ".runner" ]]; then
    log "Runner уже зарегистрирован в ${RUNNER_DIR}. Re-config? [y/N]"
    read -r ans
    if [[ "${ans}" =~ ^[Yy]$ ]]; then
        log "Удаляю предыдущую регистрацию..."
        ./config.sh remove --token "${REG_TOKEN}" || true
        REG_TOKEN=$(gh api -X POST "repos/${REPO}/actions/runners/registration-token" --jq '.token')
    else
        log "Skip re-config."
    fi
fi

if [[ ! -f ".runner" ]]; then
    log "Регистрирую runner '${RUNNER_NAME}' с labels '${LABELS}'..."
    ./config.sh \
        --unattended \
        --url "https://github.com/${REPO}" \
        --token "${REG_TOKEN}" \
        --name "${RUNNER_NAME}" \
        --labels "${LABELS}" \
        --work "_work" \
        --replace
fi

# ---- 5. Systemd user service для autostart ----
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
SERVICE_NAME="gha-runner-${REPO//\//-}.service"
SERVICE_PATH="${SYSTEMD_USER_DIR}/${SERVICE_NAME}"

mkdir -p "${SYSTEMD_USER_DIR}"
cat > "${SERVICE_PATH}" <<EOF
[Unit]
Description=GitHub Actions self-hosted runner for ${REPO}
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${RUNNER_DIR}
ExecStart=${RUNNER_DIR}/run.sh
Restart=on-failure
RestartSec=10
# Ограничение нагрузки чтобы CI не сожрал всё CPU когда хост занят другими
# задачами. Strix Halo 16C/32T — 50% оставит 8 ядер свободными.
CPUQuota=800%
KillMode=process

[Install]
WantedBy=default.target
EOF

log "Systemd user service создан: ${SERVICE_PATH}"
systemctl --user daemon-reload
systemctl --user enable --now "${SERVICE_NAME}"

# Enable lingering чтобы user-сервис стартовал на boot без login
if ! loginctl show-user "$(whoami)" 2>/dev/null | grep -q "Linger=yes"; then
    log "Включаю user-lingering (нужен sudo один раз — runner будет стартовать на boot без login)..."
    sudo loginctl enable-linger "$(whoami)" || log "Linger не включён — runner будет работать только пока ты залогинен"
fi

# ---- 6. Status ----
sleep 2
log "Статус:"
systemctl --user status "${SERVICE_NAME}" --no-pager -l | head -15 || true

log ""
log "✅ Готово. Runner '${RUNNER_NAME}' зарегистрирован на ${REPO}."
log "   Labels: ${LABELS}"
log "   Service: systemctl --user {status,restart,stop} ${SERVICE_NAME}"
log "   Logs:    journalctl --user -u ${SERVICE_NAME} -f"
log ""
log "Проверь на GitHub: https://github.com/${REPO}/settings/actions/runners"
log "Workflows с 'runs-on: [self-hosted, linux, x64]' теперь будут бегать здесь."
