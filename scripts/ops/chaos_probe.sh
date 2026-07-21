#!/usr/bin/env bash
# chaos_probe.sh — reboot/chaos regression probe (SPEC-16.3).
#
# Proves the DEPLOYED AGmind stack SELF-HEALS after chaos, exercising the real
# recovery path rather than a manual `compose up`. Two armed mechanisms cooperate:
#   - containers carry restart=unless-stopped (docker re-fires them when the daemon
#     comes back), and
#   - agmind-stack.service (Type=oneshot, enabled, WantedBy=multi-user.target)
#     re-runs `docker compose ... --profile <set> up -d --remove-orphans`
#     depends_on-ordered once docker.service is up, so a full reboot brings the
#     stack back in dependency order (milvus needs etcd+minio first, dify needs
#     postgres+redis). live-audit 2026-06-13: an unclean power-loss left prometheus
#     Exited(255) with the unless-stopped policy never re-firing → Grafana lost every
#     datasource; BootUnitStep + this boot unit close that gap.
#
# This probe (1) confirms the boot unit is ARMED (is-enabled=enabled — the exact
# regression the gate catches if BootUnitStep silently stops running or the unit is
# disabled), (2) snapshots the currently-`healthy` agmind container set, (3) induces
# chaos, then (4) polls until that SAME set returns to `healthy` — WITHOUT the probe
# itself running any `compose up`. Non-zero exit + a readable diagnostic on timeout.
#
# LIVE OPERATOR GATE: needs a self-hosted host with a deployed agmind stack
# (label com.docker.compose.project=agmind) and the enabled agmind-stack.service.
# It will NOT pass on GitHub-hosted CI (no deployed stack → fail-fast with a message).
#
# Usage:
#   scripts/ops/chaos_probe.sh [TIMEOUT_SECONDS]
# Env (all optional):
#   CHAOS_TIMEOUT        recovery poll budget in seconds (default 300; $1 overrides it)
#   CHAOS_POLL_INTERVAL  seconds between health polls (default 5)
#   CHAOS_MODE           docker-restart (default; bounces the daemon — the real boot
#                        path) | kill (SIGKILL the running agmind containers)
#
# No unpinned network calls. Self-review: `set -euo pipefail`, every expansion quoted.

set -euo pipefail

PROJECT_LABEL="com.docker.compose.project=agmind"
STACK_UNIT="agmind-stack.service"

TIMEOUT="${1:-${CHAOS_TIMEOUT:-300}}"
POLL_INTERVAL="${CHAOS_POLL_INTERVAL:-5}"
CHAOS_MODE="${CHAOS_MODE:-docker-restart}"

fail() {
    echo "chaos_probe: $*" >&2
    exit 1
}

for tool in docker systemctl; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        fail "required tool '$tool' not found on PATH (this is a LIVE operator gate; run it on a deployed self-hosted host)"
    fi
done

if ! [[ "$TIMEOUT" =~ ^[0-9]+$ ]]; then
    fail "invalid timeout '$TIMEOUT' (want a non-negative integer number of seconds)"
fi
if ! [[ "$POLL_INTERVAL" =~ ^[0-9]+$ ]] || [ "$POLL_INTERVAL" -lt 1 ]; then
    fail "invalid CHAOS_POLL_INTERVAL '$POLL_INTERVAL' (want a positive integer number of seconds)"
fi

# (1) The boot unit must be ARMED — if BootUnitStep never ran (the same gap class that
# left the stack reboot-unordered pre-2026-06-13) or the unit was disabled, the reboot
# self-heal mechanism this gate proves does not exist. Fail loudly instead of silently
# passing on restart=unless-stopped alone.
enabled_state="$(systemctl is-enabled "$STACK_UNIT" 2>/dev/null || true)"
if [ "$enabled_state" != "enabled" ]; then
    fail "$STACK_UNIT is not enabled (is-enabled=${enabled_state:-missing}); the reboot self-heal boot unit (BootUnitStep) is not armed on this host"
fi

# (2) Snapshot the currently-healthy agmind container set. Only containers that HAVE a
# healthcheck AND are healthy right now are tracked — no-healthcheck containers can't
# report `healthy`, so tracking them would make recovery undetectable/flaky.
mapfile -t HEALTHY_BEFORE < <(
    docker ps --filter "label=${PROJECT_LABEL}" --filter "health=healthy" \
        --format '{{.Names}}' 2>/dev/null | sort
)
if [ "${#HEALTHY_BEFORE[@]}" -eq 0 ]; then
    fail "no healthy containers found for compose project label '${PROJECT_LABEL}' — no deployed agmind stack to chaos-test (this gate needs a live self-hosted deployment, not GitHub-hosted CI)"
fi
echo "chaos_probe: snapshot — ${#HEALTHY_BEFORE[@]} healthy agmind container(s): ${HEALTHY_BEFORE[*]}"

# (3) Induce chaos. docker-restart is preferred: bouncing the daemon exercises the real
# post-power-loss path (containers must self-recover), the closest reproducible analogue
# to a reboot. `kill` is the sudo-free fallback (docker-group only) for hosts without
# passwordless `systemctl restart docker`.
case "$CHAOS_MODE" in
    docker-restart)
        echo "chaos_probe: chaos = sudo systemctl restart docker (daemon bounce)"
        sudo systemctl restart docker
        ;;
    kill)
        mapfile -t RUNNING < <(
            docker ps --filter "label=${PROJECT_LABEL}" -q 2>/dev/null
        )
        if [ "${#RUNNING[@]}" -eq 0 ]; then
            fail "CHAOS_MODE=kill but no running agmind containers to kill"
        fi
        echo "chaos_probe: chaos = docker kill ${#RUNNING[@]} agmind container(s)"
        docker kill "${RUNNING[@]}" >/dev/null
        ;;
    *)
        fail "unknown CHAOS_MODE '$CHAOS_MODE' (expected 'docker-restart' or 'kill')"
        ;;
esac

# (4) Poll until every previously-healthy container is `healthy` again, or the budget
# runs out. docker inspect is tolerated to fail transiently while the daemon restarts
# (treated as not-yet-healthy), so the loop rides out the bounce without aborting.
inspect_health() {
    docker inspect \
        --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' \
        "$1" 2>/dev/null || echo "absent"
}

deadline=$(( $(date +%s) + TIMEOUT ))
echo "chaos_probe: waiting up to ${TIMEOUT}s (poll ${POLL_INTERVAL}s) for the stack to self-heal..."
while :; do
    still=()
    for name in "${HEALTHY_BEFORE[@]}"; do
        if [ "$(inspect_health "$name")" != "healthy" ]; then
            still+=("$name")
        fi
    done

    if [ "${#still[@]}" -eq 0 ]; then
        echo "chaos_probe: OK — all ${#HEALTHY_BEFORE[@]} previously-healthy container(s) recovered to healthy"
        exit 0
    fi

    if [ "$(date +%s)" -ge "$deadline" ]; then
        break
    fi
    sleep "$POLL_INTERVAL"
done

# Timeout: emit a readable diagnostic — which containers are still unhealthy, their
# current docker state/health, and the boot unit's status (was it driven to recover?).
echo "chaos_probe: TIMEOUT after ${TIMEOUT}s — ${#still[@]} container(s) did NOT return to healthy:" >&2
for name in "${still[@]}"; do
    state="$(docker inspect --format '{{.State.Status}}' "$name" 2>/dev/null || echo absent)"
    health="$(inspect_health "$name")"
    echo "  - ${name}: state=${state} health=${health}" >&2
done
echo "chaos_probe: ${STACK_UNIT} is-active=$(systemctl is-active "$STACK_UNIT" 2>/dev/null || true) is-enabled=$(systemctl is-enabled "$STACK_UNIT" 2>/dev/null || true)" >&2
exit 1
