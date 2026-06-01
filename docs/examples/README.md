# Reference service descriptors — adding a component later

These are **reference examples**, not part of the deployed catalog. They show how to
author a new AGmind service descriptor (and a component bundle) so you can **add or
replace a component on a running stack without tearing it down**.

- `services/keycloak.yaml` — SSO / OIDC + SAML identity provider (Keycloak 26.4),
  reuses the shared `postgres`, fronted by Traefik. Plus `components/keycloak.yaml`.
- `services/tailscale.yaml` — mesh VPN (WireGuard) for inter-node + remote operator
  access. Single service, no bundle.

Both are pinned to **real, pullable** images (verified via `docker buildx imagetools
inspect`) and **validate against `templates/schemas/service.json`** — but they are kept
here, outside `templates/services/`, so they don't enter the governed catalog (profile
lanes, capability bindings, CI secrets) until you deliberately promote them.

## Why re-running the installer is safe (incremental, not destructive)

`agmind deploy` / the installer's deploy step is **declarative + idempotent**:

```
render compose → diff vs current → snapshot → docker compose up -d --remove-orphans → healthcheck → (auto-rollback on failure)
```

- `compose up` recreates **only** containers whose config changed, leaves the rest
  running, and starts new ones. There is **no `down` / `down -v`** anywhere — the stack
  is never torn down.
- Data lives in **host bind-mounts under `/var/lib/agmind/*`** (postgres, qdrant,
  models, …). `compose up` (even with `--remove-orphans`) never touches them, so
  add/replace/remove of a service **does not lose data**.
- A **confirmation gate** prompts before any removal/recreation (unless `--no-prompt`),
  and a **snapshot + auto-rollback** covers a failed healthcheck.
- **Selection is remembered**: a re-run pre-selects your previously-deployed services
  (from `~/.local/share/agmind/setup-state.json`), so you *add* a component to the existing
  set instead of accidentally dropping services. Deploy the **full desired set** (the
  gate protects you if you don't), or use `agmind deploy --service A --service B …`.

So: **add Keycloak → re-run → diff shows `+keycloak` → it comes up, the other 36 keep
running, data intact.** Replace a component = bump its descriptor pin → re-deploy →
only that container is recreated.

## Promoting a reference descriptor into the deployed catalog

1. **Move it** to `templates/services/<name>.yaml`.
2. **Profiles** must be valid `ServiceProfile` enum values. `keycloak` uses
   `security` + `full` (already valid). `tailscale` uses `vpn`, which is **not yet a
   profile** — either add a `vpn` value to the `ServiceProfile` enum (+ topology lane)
   or re-profile it to `full`/`ops`.
3. **Secrets**: add any new `${VAR:?}`/`${VAR:-}` secrets to the runtime `.env`
   generation **and** to the CI compose-validate env (see
   `tests/services/test_ci_compose_secrets.py` + `.github/workflows/ci.yml`
   `compose-validate`), else CI's `docker compose config` goes red. New here:
   `KEYCLOAK_DB_PASSWORD`, `KEYCLOAK_ADMIN_PASSWORD`, `TAILSCALE_AUTHKEY`.
4. **Capability bindings**: `provides`/`consumes`/`requires` (e.g. `sso`, `auth`,
   `postgres_db`, `reverse_proxy`, `mesh_vpn`) participate in the component-contract /
   governance closure — register new capabilities so `component_check` / `topology_check`
   pass.
5. Run the gate: `pre-commit run --all-files` + `pytest -q` + the `scripts/checks/*.py`
   governance scripts (these are exactly the CI jobs).

## Keycloak specifics

- **No `curl`/`wget` in the image** (UBI-micro). Health is probed with bash `/dev/tcp`
  against the **management port 9000** (`/health/ready`) — Keycloak 26 serves health +
  Micrometer `/metrics` on 9000, not the HTTP port. (Live-verified `200 OK` → rc 0.)
- **DB**: reuses the shared `postgres`. Pre-create the role+DB once:
  ```sql
  CREATE ROLE keycloak LOGIN PASSWORD '<KEYCLOAK_DB_PASSWORD>';
  CREATE DATABASE keycloak OWNER keycloak;
  ```
  (or clone `postgres.yaml` → `keycloak-db.yaml` for isolation). Keycloak runs its own
  schema migration on first `start`.
- **Edge**: HTTP stays loopback (`127.0.0.1:8082:8080`); Traefik terminates TLS for
  `auth.<domain>` via `chain-public` (an IdP login page must be reachable
  unauthenticated — never put Authelia in front of your own IdP).
- `.env`: `KEYCLOAK_DB_PASSWORD`, `KEYCLOAK_ADMIN_PASSWORD` (required);
  `KEYCLOAK_HOSTNAME`, `KEYCLOAK_ADMIN`, `KEYCLOAK_DB_USERNAME` (optional). Delete/rotate
  the bootstrap admin after creating a real one.

## Tailscale specifics

- **Kernel mode** (default): the host needs `/dev/net/tun` (`modprobe tun`, persist via
  `/etc/modules-load.d/`); container gets `NET_ADMIN`+`NET_RAW`. For subnet-routing /
  exit-node also enable `net.ipv4.ip_forward=1` (+ ipv6).
- **Userspace fallback**: set `TAILSCALE_USERSPACE=true` to drop `/dev/net/tun` + caps
  (locked-down hosts); cost is throughput + needing the SOCKS5/HTTP proxy for host
  reachability. Then remove the `devices:`/`cap_add:` blocks.
- **No published ports** — `tailscaled` dials out; reach services over the tailnet IP /
  MagicDNS, not host ports. Not behind Traefik.
- **State**: `/var/lib/agmind/tailscale` (holds the node's WireGuard private key) —
  create `root:root 0700`, keep across upgrades to preserve node identity.
- `.env`: `TAILSCALE_AUTHKEY` (required — ephemeral + reusable + pre-approved tailnet
  key, e.g. `tskey-auth-…`). Optional: `TAILSCALE_HOSTNAME`, `TAILSCALE_ROUTES`,
  `TAILSCALE_EXTRA_ARGS`, `TAILSCALE_USERSPACE`.

## Upgrading a pin

Bump the tag, re-resolve the canonical index digest, update the bare 64-hex `digest:`:
```
docker buildx imagetools inspect <repo>:<newtag> --format '{{.Manifest.Digest}}'
```
Never hand-edit the digest.
