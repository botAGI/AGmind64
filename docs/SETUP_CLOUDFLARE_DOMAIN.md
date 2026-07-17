# Setup: Cloudflare API token + публичный домен для Traefik TLS

Этот гайд закрывает **R-recon R15** (LE wildcard vs step-ca). Используем Cloudflare как DNS-провайдер для Let's Encrypt DNS-01 challenge — TLS wildcard `*.yourdomain.tld` выдаётся бесплатно за 30 секунд, не требует выставлять сервер наружу.

## Что у тебя должно быть

- ✅ Купленный домен (любой публичный TLD: `.dev`, `.io`, `.app`, `.com`, и т.д.)
- ☐ Cloudflare account (free tier ОК)
- ☐ API token с правильными scopes
- ☐ NS records домена указывают на Cloudflare

---

## Шаг 1: Зарегистрировать домен в Cloudflare

1. Зайди на https://dash.cloudflare.com → **Add a site**.
2. Введи свой домен (например `agmind.example`).
3. Выбери **Free plan** (~$0/мес, полностью покрывает наш use case).
4. Cloudflare выдаст **2 nameservers** (типа `bart.ns.cloudflare.com` + `lucy.ns.cloudflare.com`).
5. У **твоего регистратора домена** (Namecheap, Porkbun, REG.RU, Reg.com, etc.) — заменить NS records на эти 2 от Cloudflare. **Это самый главный шаг.**
6. Подожди 5-30 минут (DNS propagation). Cloudflare пришлёт email когда домен активирован.

**Важно**: Cloudflare должен быть **authoritative DNS** для всего домена. Иначе LE DNS-01 не сможет создавать TXT-записи.

---

## Шаг 2: Создать API token

1. Зайди https://dash.cloudflare.com/profile/api-tokens
2. Нажми **Create Token** → выбери шаблон **"Edit zone DNS"** (или Custom token).
3. Настрой permissions:
   - **Zone** → `DNS` → `Edit`
4. Zone Resources:
   - **Include** → **Specific zone** → выбери свой домен.
5. (Опционально) **Client IP Address Filtering** — ограничь по IP твоей home сети.
6. (Опционально) **TTL** — даты валидности (без срока ОК для self-hosted).
7. **Continue to summary** → **Create Token**.
8. Cloudflare покажет токен **только один раз** — скопируй его (типа `ABCdef123-XYZ...`).

---

## Шаг 3: Положить токен в AGmind secrets

```bash
# Безопасно: chmod 600, владельца проверь
sudo mkdir -p /var/lib/agmind/secrets
sudo chmod 700 /var/lib/agmind/secrets
echo 'ABCdef123-XYZ...' | sudo tee /var/lib/agmind/secrets/cf_dns_api_token > /dev/null
sudo chmod 600 /var/lib/agmind/secrets/cf_dns_api_token
```

Compose файл Traefik (`templates/services/traefik.yaml`) уже мунтит:
```yaml
- /var/lib/agmind/traefik/letsencrypt:/letsencrypt
env:
  CF_DNS_API_TOKEN_FILE: /run/secrets/cf_dns_api_token
```
Token читается через secret mount при старте Traefik.

---

## Шаг 4: Заменить `agmind.dev` placeholder на твой домен

В Phase H'.C я заполнил `routing.host` плейсхолдером `agmind.dev` для 8 сервисов. Заменить на твой домен — одна команда:

```bash
cd ~/AGmind64
# YOUR_DOMAIN — замени на свой (без https://, без trailing slash)
YOUR_DOMAIN='agmind.example'

# Sed-replace во всех service descriptors
find templates/services -name '*.yaml' -exec sed -i "s/agmind\.dev/${YOUR_DOMAIN}/g" {} \;

# Также в Traefik service (acme email)
sed -i "s/ops@agmind\.dev/ops@${YOUR_DOMAIN}/" templates/services/traefik.yaml

# Verify
grep -r "Host\(" templates/services/ | head -5
```

После — регенерируй compose:
```bash
.venv/bin/agmind render compose --profile core,security --output /tmp/check.yml
grep "Host(" /tmp/check.yml | head -5
# Должно быть: Host(`llama.agmind.example`), Host(`grafana.agmind.example`), и т.д.
```

---

## Шаг 5: Verify что LE DNS-01 работает

После старта Traefik (Ansible deploy) — проверь acme.json:
```bash
sudo cat /var/lib/agmind/traefik/letsencrypt/acme.json | jq '.le.Certificates[].domain'
```

Должен показать `{ main: "*.agmind.example" }` через ~60 секунд после первого запроса.

---

## DNS records для LAN-only доступа

Cloudflare authoritative ≠ публично доступен. Чтобы `grafana.agmind.example` резолвилось **только в твоей LAN**:

### Вариант A: Локальный DNS (AdGuard Home / Pi-hole / dnsmasq)

Добавь wildcard rewrite:
```
*.agmind.example → 192.168.1.X   # IP твоего Strix Halo
```

Внешние резолверы (Google 8.8.8.8) увидят CF-managed NXDOMAIN или CF placeholder. Только в LAN через локальный DNS → твой Strix Halo.

### Вариант B: /etc/hosts на каждом клиенте

```
192.168.1.X  agmind.example
192.168.1.X  llama.agmind.example grafana.agmind.example chat.agmind.example dify.agmind.example portainer.agmind.example
```

Минусы: ручная синхронизация на каждом устройстве.

**Рекомендую A** — AdGuard Home (UI-driven) + один wildcard rule.

---

## Чеклист

- [ ] Домен зарегистрирован
- [ ] Cloudflare account + домен added
- [ ] NS records у регистратора → CF nameservers
- [ ] Подождал 5-30 мин, CF email "domain active"
- [ ] API token создан с **Zone:DNS:Edit** permissions
- [ ] Token → `/var/lib/agmind/secrets/cf_dns_api_token` (chmod 600)
- [ ] `agmind.dev` placeholder заменён на твой домен в descriptors
- [ ] Local DNS (AdGuard Home / dnsmasq) wildcard `*.yourdomain → <strix-halo-IP>`
- [ ] `agmind render compose` дал валидный compose (visual check)
- [ ] (После deploy) `traefik/letsencrypt/acme.json` содержит wildcard cert

---

## Если не хочешь публичный домен — fallback на step-ca

Альтернатива: self-hosted CA через [Smallstep step-ca](https://github.com/smallstep/certificates).

Минусы (см. наш discussion в чате):
- Нужно установить **root certificate** на КАЖДОЕ устройство которое заходит на UI (ноут, телефон, ноут клиента, гостевой ноут)
- Разные процедуры на Linux/Mac/Windows/iOS/Android
- При смене root certificate — повторная установка везде

Гайд по step-ca см. https://smallstep.com/docs/step-ca/getting-started/. Это **второй приоритет** — если домен есть, используем CF, это проще.
