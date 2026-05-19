# Traefik v3 как замена nginx для AGmindx86 (Strix Halo + llama.cpp)

Целевая версия: **Traefik 3.7.1** (релиз 11 мая 2026, исправляет CVE-2026-44774). Образ: `traefik:v3.7.1`. Образ llama.cpp: `ghcr.io/ggml-org/llama.cpp:server-vulkan` (используй явный тэг билда, `:server-vulkan` без даты не пиннится — рекомендую тэг по номеру билда, например `:server-vulkan-bXXXX`, который ggml-org публикует на каждый release).

---

## 1. SSE streaming — реальные подводные камни

**Главный миф**: Traefik «должен» автоматически распознавать streaming response и игнорировать `flushInterval`. Документация это утверждает, но в практике с llama-server/Ollama это **ломается**, когда:

- ответ идёт через `Transfer-Encoding: chunked` без явного `Content-Type: text/event-stream` на первом байте;
- включена `buffering` middleware (даже из-за `maxRequestBodyBytes` для аплоада моделей) — она склеивает чанки (GitHub traefik #7930);
- роутер опубликован через HTTP/2 entrypoint — `EventSource` спецификация требует HTTP/1.1.

**Рабочий рецепт** (подтверждён в issue ollama #13949 на Claude Code и в форумной ветке community.traefik.io «Problem with streaming SSE»):

```yaml
labels:
  # Принудительный flush каждые 1ms даже если Traefik не "узнал" SSE
  - "traefik.http.services.llama-q4.loadbalancer.responseforwarding.flushinterval=1ms"
  # Форсировать HTTP/1.1 (no-http2@file — TLS option из file provider, см. ниже)
  - "traefik.http.routers.llama-q4.tls.options=no-http2@file"
```

**Timeout 600s+**: настраивается **только в static config / CLI flags** на entrypoint — Docker labels это не выставляют (community.traefik.io #26940 явно подтверждает). Минимум для long-context:

```yaml
# static traefik.yml
entryPoints:
  websecure:
    address: ":443"
    transport:
      respondingTimeouts:
        readTimeout: 600s
        writeTimeout: 600s
        idleTimeout: 600s
serversTransport:
  forwardingTimeouts:
    dialTimeout: 30s
    responseHeaderTimeout: 600s
    idleConnTimeout: 600s
```

**Не используй `buffering` middleware** на SSE-роутерах вообще. Если нужен большой `maxRequestBodyBytes` (загрузка GGUF через API не наш случай — модели монтируются volume'ом), вешай buffering только на routers с не-streaming эндпоинтами.

---

## 2. Working docker-compose с labels (полный фрагмент)

```yaml
services:
  traefik:
    image: traefik:v3.7.1
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --providers.file.directory=/etc/traefik/dynamic
      - --providers.file.watch=true
      - --entrypoints.web.address=:80
      - --entrypoints.web.http.redirections.entrypoint.to=websecure
      - --entrypoints.web.http.redirections.entrypoint.scheme=https
      - --entrypoints.websecure.address=:443
      - --entrypoints.websecure.transport.respondingTimeouts.readTimeout=600s
      - --entrypoints.websecure.transport.respondingTimeouts.writeTimeout=600s
      - --entrypoints.websecure.transport.respondingTimeouts.idleTimeout=600s
      - --entrypoints.metrics.address=:9100
      - --metrics.prometheus=true
      - --metrics.prometheus.entryPoint=metrics
      - --certificatesresolvers.le.acme.email=ops@agmind.lan
      - --certificatesresolvers.le.acme.storage=/letsencrypt/acme.json
      - --certificatesresolvers.le.acme.dnschallenge=true
      - --certificatesresolvers.le.acme.dnschallenge.provider=cloudflare
      - --certificatesresolvers.le.acme.dnschallenge.delaybeforecheck=30
    environment:
      - CF_DNS_API_TOKEN=${CF_DNS_API_TOKEN}
    ports: ["80:80", "443:443", "9100:9100"]
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./traefik/letsencrypt:/letsencrypt
      - ./traefik/dynamic:/etc/traefik/dynamic:ro

  llama-q4:
    image: ghcr.io/ggml-org/llama.cpp:server-vulkan-b4500  # пиннуй конкретный билд
    devices: ["/dev/kfd", "/dev/dri"]
    group_add: ["video", "render"]
    volumes: ["./models:/models:ro"]
    command: >
      -m /models/qwen3-30b-q4.gguf --host 0.0.0.0 --port 8080
      --ctx-size 32768 --n-gpu-layers 999
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 5s
      retries: 3
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.llama-q4.rule=Host(`llama-q4.agmind.lan`)"
      - "traefik.http.routers.llama-q4.entrypoints=websecure"
      - "traefik.http.routers.llama-q4.tls=true"
      - "traefik.http.routers.llama-q4.tls.certresolver=le"
      - "traefik.http.routers.llama-q4.tls.domains[0].main=agmind.lan"
      - "traefik.http.routers.llama-q4.tls.domains[0].sans=*.agmind.lan"
      - "traefik.http.routers.llama-q4.tls.options=no-http2@file"
      - "traefik.http.services.llama-q4.loadbalancer.server.port=8080"
      - "traefik.http.services.llama-q4.loadbalancer.responseforwarding.flushinterval=1ms"
      - "traefik.http.services.llama-q4.loadbalancer.healthcheck.path=/health"
      - "traefik.http.services.llama-q4.loadbalancer.healthcheck.interval=30s"
      - "traefik.http.services.llama-q4.loadbalancer.passhostheader=true"
      - "traefik.http.routers.llama-q4.middlewares=chain-llm@file"
```

**ВАЖНО про DNS-01 wildcard для приватных доменов**: Let's Encrypt **не требует публичной резолвабельности** домена для DNS-01 — нужен только control над DNS-зоной, которую LE может проверить через публичный DNS API (Cloudflare, Route53, deSEC, и т.д.). Поэтому `*.agmind.lan` работать **не будет** (LE не выдаёт серты на TLD не из ICANN root). Варианты:
- купи дешёвый домен (`agmind.dev` / `.lan` нельзя) и используй wildcard через Cloudflare DNS-01;
- или подними **internal CA через Smallstep `step-ca`** + Traefik ACME (см. п.3 ниже) — без LE вообще.

Это типичная ловушка — нашёл подтверждение в нескольких 2025–2026 гайдах (major.io, technotim, simplehomelab).

---

## 3. mTLS между нодами кластера

Для 2-3 нод **не используй Traefik mTLS как primary transport** — это решает не ту задачу. Сравнение:

| Подход | Сложность | Плюсы | Минусы |
|---|---|---|---|
| WireGuard underlay (`wg0`) + Traefik по TCP/HTTP | низкая | прозрачно для приложений, encrypted by default, NAT punching | требует ключевого менеджмента |
| Traefik `serversTransport` mTLS | высокая | без VPN, end-to-end к самому Traefik | каждое подключение нода→нода требует cert, нет mesh-семантики |

**Рекомендация**: WireGuard (или `wg-easy`/`headscale`) для inter-node, Traefik работает поверх в plain HTTP на private interface. Это проще и быстрее на Strix Halo (нативная AES-NI).

Если всё-таки нужен Traefik mTLS — file provider (`/etc/traefik/dynamic/transport.yml`):

```yaml
http:
  serversTransports:
    cluster-mtls:
      serverName: "node.agmind.lan"
      certificates:
        - certFile: /certs/node-client.crt
          keyFile: /certs/node-client.key
      rootCAs:
        - /certs/ca.crt
      insecureSkipVerify: false

  services:
    inter-node-llama:
      loadBalancer:
        serversTransport: cluster-mtls
        servers:
          - url: https://node2.agmind.lan:8443
```

**Cert management**: для self-hosted самый чистый вариант — **Smallstep `step-ca`** (есть HA-режим, ACME-совместим, можно скриптовать rotation через `step ca renew --daemon`). cert-manager без k8s — оверкилл. Self-signed + cron rotation — fragile, не рекомендую.

---

## 4. Auto-discovery + middleware tiers БЕЗ редактирования config

Стандартный Docker provider даёт routes из labels — это работает. **Дефолтный middleware chain ко всем сервисам** реализуется через **file provider entryPoint middlewares**:

`/etc/traefik/dynamic/middlewares.yml`:
```yaml
http:
  middlewares:
    default-ratelimit:
      rateLimit:
        average: 10
        period: 1m
        sourceCriterion:
          ipStrategy: {depth: 1}
    default-security-headers:
      headers:
        stsSeconds: 31536000
        frameDeny: true
    authelia:
      forwardAuth:
        address: "http://authelia:9091/api/authz/forward-auth"
        trustForwardHeader: true
        authResponseHeaders:
          - Remote-User
          - Remote-Groups
          - Remote-Email
          - Remote-Name
    chain-llm:        # для llama-server: rate-limit + auth, БЕЗ buffering
      chain:
        middlewares: [default-ratelimit, default-security-headers, authelia]
    chain-internal:   # для Prometheus/Grafana внутри
      chain:
        middlewares: [authelia]
    chain-public:     # для публичных, без auth
      chain:
        middlewares: [default-ratelimit, default-security-headers]
```

И в static config:
```yaml
entryPoints:
  websecure:
    address: ":443"
    http:
      middlewares:
        - default-security-headers@file   # применяется ВСЕГДА ко всему через этот entrypoint
```

`entryPoints.<name>.http.middlewares` — это и есть «wildcard на все services». Сервис в compose выбирает свой tier одной меткой: `traefik.http.routers.X.middlewares=chain-llm@file`.

**Чистый pattern «tier per label»** (`tier=internal` → автоматически chain) — Traefik native такого **не умеет**, нужен внешний rewriter (e.g. написать sidecar который слушает Docker events и patch'ит labels). Я бы не делал — три явные chain-* через file provider покрывают use case.

---

## 5. ROCm/Vulkan GPU passthrough — конфликтов нет

Подтверждено в llama.cpp docker docs: `devices: [/dev/kfd, /dev/dri]` + `group_add: [video, render]` живут на уровне Docker runtime и **никак не пересекаются с Traefik labels** (это разные плоскости — runtime vs metadata). Traefik вообще не видит GPU. Единственное: убедись что user внутри контейнера llama-server в группе `render` (uid маппинг через `--group-add` решает).

---

## 6. Метрики Traefik

- Endpoint: `--metrics.prometheus=true --metrics.prometheus.entryPoint=metrics`, scrape `http://traefik:9100/metrics`.
- **Grafana dashboards** (актуальные 2026):
  - **17346** — Traefik Official Standalone (рекомендую, native Prometheus metrics).
  - **17347** — Official Kubernetes (нерелевантно для compose).
  - **2870** — Traefik 3 community dashboard (хорош для drill-down по router).
  - 4475 — устаревший v1/v2 metric names, **не используй для v3**.

---

## 7. Альтернативы

**Caddy v2 + caddy-docker-proxy**: для SSE требует **`flush_interval -1`** в `reverse_proxy` блоке (issue caddyserver/caddy #4247 — известный footgun). Caddyfile DSL чище Traefik labels, но auto-discovery через Docker нативно нет — нужен сторонний `lucaslorentz/caddy-docker-proxy`. Middleware tiers через Caddyfile snippets возможны, но менее декларативно. Для нашего 32-сервисного compose — паритет, Traefik выигрывает на богатстве middleware (forwardAuth, ratelimit с ipStrategy) из коробки.

**Pangolin (fosrl/pangolin)**: YC 2025, активная разработка. Это **не замена Traefik**, а ZTNA-надстройка (WireGuard tunnel + identity-aware proxy). Под капотом у него Traefik. Имеет смысл если: хочешь дать внешний доступ к llama-server без публикации портов. Для чисто LAN-стека — оверкилл.

**Когда Traefik НЕ подходит** (вещи, которые maintainerы не подсвечивают):
- любые per-service timeout настройки (только entrypoint-global);
- buffering middleware ломает chunked/SSE даже если кажется «безобидной»;
- HTTP/2 enabled по умолчанию на TLS-роутерах — тихо ломает SSE-клиенты без явного флага;
- `acme.json` race condition при multi-instance Traefik без distributed storage (только Traefik Enterprise решает чисто);
- метрики не дают per-stream latency для SSE — нужен external observability (OpenTelemetry trace).

---

## 8. Production case studies

Честно: **production-grade репо «self-hosted LLM + Traefik»** с 30+ сервисами и SSE я не нашёл. То что есть:

- **pezzos/ollama-docker-traefik** (GitHub) — минимальный demo Ollama+Traefik, без production hardening.
- **ErickWendel/ollama-webui-traefik-docker** — тоже demo-уровень.
- **av/harbor** (GitHub) — большой self-hosted LLM toolkit, упоминает Traefik integration; ближайший аналог нашего use case, рекомендую посмотреть его compose.
- **blog.xentoo.info** «Ollama, open-webui, mitmproxy in a docker compose stack, behind traefik» (2024) — реальный конфиг небольшого масштаба.
- **simplehomelab.com** + **botmonster.com** — production Traefik v3.6 гайды без LLM-специфики.

**Не нашёл доказательств** существования публичного opensource репо масштаба AGmindx86 (32 сервиса, multi-llama, Authelia, multi-node) на Traefik. Это белое пятно — стоит документировать наш собственный setup как референс.

---

## Резюме рекомендаций

1. Traefik 3.7.1, llama.cpp `:server-vulkan-bXXXX` (явный pinning).
2. Обязательно: `responseforwarding.flushinterval=1ms` + `tls.options=no-http2@file` на каждом llama-router.
3. Timeouts только в static config на entrypoint (600s минимум).
4. DNS-01 для wildcard работает только на публично-валидном домене; `.lan` → переходи на `step-ca` internal CA или купи `.dev`.
5. Multi-node: WireGuard underlay вместо Traefik mTLS — проще и быстрее.
6. Middleware tiers через `entryPoints.*.http.middlewares` + chain-* в file provider; не пытайся реализовать «tier по label» native.
7. Grafana dashboard 17346 + 2870.

---

## Sources

- [Traefik Buffering Middleware docs](https://doc.traefik.io/traefik/middlewares/http/buffering/)
- [Traefik v3 ServersTransport reference](https://doc.traefik.io/traefik/reference/routing-configuration/http/load-balancing/serverstransport/)
- [Disable response buffering — Traefik v3 community](https://community.traefik.io/t/disable-response-buffering/25764)
- [Problem with streaming SSE server behind traefik](https://community.traefik.io/t/problem-with-streaming-sse-server-behind-traefik/23007)
- [GitHub traefik/traefik #503 — EventSource delay](https://github.com/traefik/traefik/issues/503)
- [GitHub ollama/ollama #13949 — Ollama+Traefik+Claude Code SSE fix](https://github.com/ollama/ollama/issues/13949)
- [Traefik v3 RateLimit docs](https://doc.traefik.io/traefik/middlewares/http/ratelimit/)
- [Traefik per-service timeout limitation](https://community.traefik.io/t/how-to-set-entrypoints-transport-respondingtimeouts-for-only-one-service/26940)
- [Immich + Traefik 60s timeout discussion](https://github.com/immich-app/immich/discussions/8872)
- [Authelia Traefik v3 integration](https://www.authelia.com/integration/proxies/traefik/)
- [Traefik 3.7 release notes](https://community.traefik.io/t/traefik-3-7-release/29694)
- [llama.cpp docker docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md)
- [llama-server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- [Grafana dashboard 17346 Traefik Official](https://grafana.com/grafana/dashboards/17346-traefik-official-standalone-dashboard/)
- [Grafana dashboard 2870 Traefik 3](https://grafana.com/grafana/dashboards/2870-traefik3/)
- [Caddy flush_interval issue #4247](https://github.com/caddyserver/caddy/issues/4247)
- [caddy-docker-proxy](https://github.com/lucaslorentz/caddy-docker-proxy)
- [Pangolin fosrl/pangolin](https://github.com/fosrl/pangolin)
- [Wildcard LE + Cloudflare Traefik (major.io)](https://major.io/p/wildcard-letsencrypt-certificates-traefik-cloudflare/)
- [Production Docker Traefik v3.6 (botmonster)](https://botmonster.com/posts/deploy-docker-compose-traefik-production/)
- [Ollama+Traefik+mitmproxy compose (xentoo)](https://blog.xentoo.info/2024/03/23/ollama-open-webui-mitmproxy-in-a-docker-compose-stack-behind-traefik/)
- [av/harbor Wiki llama.cpp backend](https://github.com/av/harbor/wiki/2.2.2-Backend:-llama.cpp)
- [mTLS with Traefik (CoderCo)](https://blog.coderco.io/p/mtls-with-traefik-a-step-by-step)
