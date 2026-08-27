# Runbook: local development

Everything here runs without touching a marketplace. The one command that
can reach a live marketplace is gated and called out explicitly in §6.

Architecture background: `docs/architecture/marketplace-fallback.md`.
Deployment: `docs/runbooks/vps-deployment.md`. Failures:
`docs/runbooks/troubleshooting.md`.

---

## 1. Set up

```
git clone https://github.com/Dushe4ka/price_watcher.git
cd price_watcher
python3.12 -m venv .venv
# activate it, then:
pip install -r requirements.txt
cp .env.example .env
```

`.env` is git-ignored and excluded from the Docker build context; only
`.env.example` is tracked. Never commit a filled-in `.env`.

If you intend to run browser code locally rather than in a container, install
the browsers too:

```
playwright install chromium
patchright install chrome
```

`patchright install chrome` needs an amd64 machine — Google Chrome for Linux
is amd64-only. On Apple Silicon, use the containers (§4), which pin
`platform: linux/amd64`.

The vendored CAPTCHA snapshot is deliberately **not** part of this
environment. `vendor/ohmycaptcha/` has its own `requirements.txt` pinning a
different Playwright version and belongs in its own virtual environment; it
is never installed into `.venv` or into the images.

---

## 2. Run the tests

The suite needs no `.env` and no database: `tests/__init__.py` supplies
deterministic settings when the dotenv loader is switched off.

```
PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .
```

Useful subsets:

```
PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_documented_configuration -v
PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_marketplace_service -v
PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests/integration -t . -v
```

`tests/integration/` drives a **real** Chromium through the production
session manager, profile lock and allowlist, with every request intercepted
and answered from a loopback fixture server and a blackhole proxy configured
so nothing can escape interception. It takes roughly a dozen seconds.
`tests/live/` stays gated behind `LIVE_MARKETPLACE_TESTS` and is never run by
CI.

`tests/test_browser_source_limits.py` executes the in-page Ozon fetch
JavaScript through a real `node` process and skips itself when `node` is
missing, so install Node if you are changing that JavaScript.

`tests/test_documented_configuration.py` is the contract that keeps these
runbooks honest: it introspects `Settings`' marketplace-related model fields
and fails if one of them is undocumented, documented with the wrong default,
missing from `.env.example`, or if a runbook names a repository file that does
not exist. Adding a `MARKETPLACE_*` setting means updating §5 below.

---

## 3. Style and hygiene checks (run before every commit)

```
python -m flake8 src/browser src/captcha src/marketplaces src/ozon src/wb scripts tests
python -m pycodestyle src/browser src/captcha src/marketplaces scripts tests
python -m pyflakes <changed files>
python -m compileall -q src bot
python scripts/repository_hygiene.py --json
git diff --check
```

`scripts/repository_hygiene.py` checks the tracked file list, the Dockerfiles
and `.dockerignore` for secrets, virtualenvs, browser profiles and
environment files reaching Git or the Docker build context. It never reads
`.env` itself. Expected output: `{"violations": []}`.

Compose policy is verified without a Docker daemon:

```
python scripts/verify_compose.py docker-compose.yml docker-compose.production.yml
python scripts/verify_compose.py docker-compose.yml docker-compose.local.yml
```

It merges the overlays the way Compose would and checks that each
browser-owning service keeps a private profile volume, runs as a single
non-root worker, publishes no browser-control port, and that the two images'
shared browser-runtime stages have not drifted.

---

## 4. Run the stack in Docker

The base `docker-compose.yml` publishes no ports on purpose. Always pick an
overlay:

```
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.local.yml logs -f telegram_bot
docker compose -f docker-compose.yml -f docker-compose.local.yml down
```

The local overlay binds every published port to `127.0.0.1`: API on `8000`,
nginx on `8080`, Postgres on `5432`. The bot publishes nothing at all.

- API + Swagger: <http://localhost:8000/docs>
- Migrations run at `api` startup via `start.sh`.

Both services set `WEB_CONCURRENCY=1`, `BROWSER_PROFILE_ROOT=/data/browser-profiles`
and their own `MARKETPLACE_RUNTIME_ROLE` (`api` / `bot`), and each gets its
own profile volume. Do not "simplify" that: see
`docs/runbooks/vps-deployment.md` §3.

---

## 5. Marketplace configuration reference

Every setting below is read from the process environment. Defaults are the
values in `src/core/config.py`; `.env.example` carries the same values.

### Runtime and profiles

| Variable | Default | Meaning |
| --- | --- | --- |
| `MARKETPLACE_RUNTIME_ROLE` | `local` | `local` \| `api` \| `bot`. Selects the browser-profile subtree for this process. |
| `BROWSER_PROFILE_ROOT` | `browser-profiles` | Root of the persistent profiles; the actual profile is `<root>/<role>/<marketplace>`. |
| `WEB_CONCURRENCY` | — (must be `1`) | Not a `Settings` field. `validate_single_browser_worker()` refuses to open a persistent browser unless this is exactly `WEB_CONCURRENCY=1`. |

### Source chains

| Variable | Default | Meaning |
| --- | --- | --- |
| `WILDBERRIES_SOURCE_CHAIN` | `browser,apify` | Ordered, comma-separated, no duplicates. |
| `OZON_SOURCE_CHAIN` | `browser,apify` | Same. |
| `YANDEX_MARKET_SOURCE_CHAIN` | `public,browser,apify` | The only chain with a `public` leg. |

Allowed source names: `public`, `browser`, `apify`. An empty value falls back
to the default chain.

### Timeouts, size and retry bounds

| Variable | Default | Meaning |
| --- | --- | --- |
| `MARKETPLACE_TOTAL_TIMEOUT_SEC` | `30` | Per-source budget (`1..300`). Used by each browser source and by the Apify HTTP client. |
| `MARKETPLACE_OPERATION_TIMEOUT_SEC` | `90` | Deadline shared by the whole fallback chain (`1..900`). **Must be strictly greater** than the per-source timeout, or `Settings` refuses to load. |
| `MARKETPLACE_MAX_CONTENT_BYTES` | `2000000` | Cap on any single response/evaluation result (`1..10485760`). |
| `MARKETPLACE_RETRY_MAX_ATTEMPTS` | `2` | Transport attempts inside one source (`1..2`). Apify is always `1`. |
| `MARKETPLACE_RETRY_BASE_DELAY_MS` | `250` | Backoff before a retry. |
| `MARKETPLACE_RETRY_MAX_DELAY_MS` | `1000` | Upper bound; must not be less than the base delay. |

### Apify fallback (inert until fully configured)

| Variable | Default | Meaning |
| --- | --- | --- |
| `APIFY_TOKEN` | empty | Apify API token. Also accepted as `APIFY_API_TOKEN`. Empty ⇒ every Apify call answers `disabled`. |
| `APIFY_WILDBERRIES_CRAWL_CATEGORY_ACTOR_ID` | empty | Actor for Wildberries `crawl_category`. |
| `APIFY_WILDBERRIES_PARSE_PRODUCT_ACTOR_ID` | empty | Actor for Wildberries `parse_product`. |
| `APIFY_WILDBERRIES_SEARCH_PRODUCTS_ACTOR_ID` | empty | Actor for Wildberries `search_products`. |
| `APIFY_OZON_CRAWL_CATEGORY_ACTOR_ID` | empty | Actor for Ozon `crawl_category`. |
| `APIFY_OZON_PARSE_PRODUCT_ACTOR_ID` | empty | Actor for Ozon `parse_product`. |
| `APIFY_OZON_SEARCH_PRODUCTS_ACTOR_ID` | empty | Actor for Ozon `search_products`. |
| `APIFY_YANDEX_MARKET_CRAWL_CATEGORY_ACTOR_ID` | empty | Actor for Yandex Market `crawl_category`. |
| `APIFY_YANDEX_MARKET_PARSE_PRODUCT_ACTOR_ID` | empty | Actor for Yandex Market `parse_product`. |
| `APIFY_YANDEX_MARKET_SEARCH_PRODUCTS_ACTOR_ID` | empty | Actor for Yandex Market `search_products`. |

Both the token **and** the matching per-operation actor ID must be set before
Apify opens a socket. The dataset mapping is a project-owned synthetic schema
that has not been validated against a live actor.

### CAPTCHA handling (opt-in, default off)

| Variable | Default | Meaning |
| --- | --- | --- |
| `CAPTCHA_ADAPTER_MODE` | `disabled` | `disabled` \| `ohmycaptcha`. Composes the vendored adapter's reviewed primitives; if it cannot load, the coordinator continues without it. |
| `OHMYCAPTCHA_API_KEY` | empty | Key for the vendored adapter. |
| `SMARTCAPTCHA_MODE` | `disabled` | `disabled` \| `frictionless`. See §12 of the architecture doc for the exact approved scope. |
| `SMARTCAPTCHA_CLIENT_KEY` | empty | Yandex SmartCaptcha client key. |
| `SMARTCAPTCHA_WIDGET_ID` | empty | Public widget ID, 1–128 chars from `A-Za-z0-9._:-`. Empty disables the handler even in `frictionless` mode. |

---

## 6. Smoke and probe scripts

### Mock-mode stack smoke (safe, no marketplace traffic)

```
python scripts/smoke_marketplace_stack.py --mode controlled
```

Boots the real marketplace runtime, the real FastAPI app and the real bot
shutdown hook in-process under a mock environment (every chain reduced to
`apify`, no token, no actor IDs, both CAPTCHA modes `disabled`) and asserts
every marketplace operation answers `disabled` — the machine-checkable form of
"no live traffic". No browser is started, because no chain contains `browser`.

```
python scripts/smoke_marketplace_stack.py --mode compose --timeout 180
```

Same environment, but through Docker: up, poll the API, check the bot
container, then down with a clean-shutdown assertion. Needs a working Docker
daemon; exits `2` when there is none, or when one of the container names
`docker-compose.yml` pins (`api`, `telegram_bot`, `db`, `nginx`) is already
taken on the host.

### Live probe (reaches a real marketplace — opt in deliberately)

```
LIVE_MARKETPLACE_TESTS=1 python -m scripts.live_marketplace_probe \
    --marketplace ozon --operation crawl_category
```

Inert unless `LIVE_MARKETPLACE_TESTS=1`; the gate is checked before any
service is composed, so an ungated run cannot reach a marketplace at all.
Flags: `--marketplace` (`wildberries` \| `ozon` \| `yandex_market`),
`--operation` (`crawl_category` \| `parse_product` \| `search_products`),
and the optional `--category`, `--query`, `--product-id`. Query and product ID
are never echoed back.

One bounded operation, output restricted to the telemetry allowlist, so a
transcript can be pasted into an issue as-is. Exit codes: `0` success/empty/
not-found, `1` failure, `2` gate disabled, `3` bad argument.

### Ozon profile ageing (reaches Ozon, runs forever)

```
OZON_AGE_INTERVAL_MIN=30 python -m scripts.age_ozon_profile
```

Crawls every configured Ozon category on an interval so the persistent
profile accumulates genuine history. No Telegram dependency. Default interval
is 30 minutes.

Older per-marketplace smokes also exist: `scripts/smoke_wb_crawl.py`,
`scripts/smoke_ozon_crawl.py`, `scripts/smoke_yandex_market_crawl.py`. These
hit the live sites.

---

## 7. Legal and ethical boundary

These tools automate **public, unauthenticated** marketplace pages. Do not
point them at anything behind a login, do not use them to create accounts or
place orders, and respect each marketplace's terms of service and rate limits.
CAPTCHA support is deliberately narrow (`docs/architecture/marketplace-fallback.md`
§12): the frictionless SmartCaptcha path only drives an already-present widget
on the page that was challenged. Defeating an interactive human-verification
challenge is out of scope and stays out of scope.
