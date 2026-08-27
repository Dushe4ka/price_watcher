# Runbook: VPS deployment

Deploying and operating the marketplace fallback stack on a Linux VPS.

Configuration reference: `docs/runbooks/local-development.md` §5 — every
`MARKETPLACE_*`, `APIFY_*`, `*_SOURCE_CHAIN`, `CAPTCHA_ADAPTER_MODE`,
`SMARTCAPTCHA_*`, `OHMYCAPTCHA_API_KEY`, `BROWSER_PROFILE_ROOT` and
`WEB_CONCURRENCY` value is documented there with its default. Architecture:
`docs/architecture/marketplace-fallback.md`. Failures:
`docs/runbooks/troubleshooting.md`.

---

## 1. Host prerequisites

* **x86_64.** Both images pin `platform: linux/amd64` because Google Chrome
  for Linux ships amd64 only and the Ozon session launches
  `channel='chrome'`. An arm64 host can only run them under emulation.
* Docker with Compose v2.
* Enough RAM for two headed Chromium/Chrome processes. Each browser service
  is given `shm_size: 1gb`, because Chromium crashes with OOM renderer errors
  on Docker's 64 MB default `/dev/shm`.
* Ports: only `80` is published, by nginx. The API, the bot and Postgres are
  reachable on the Compose network only, and no browser-control or remote
  debugging endpoint exists in this deployment at all.

---

## 2. First deploy

```
git clone https://github.com/Dushe4ka/price_watcher.git
cd price_watcher
cp .env.example .env
# fill in .env: database, Telegram, and any marketplace settings you override
docker compose -f docker-compose.yml -f docker-compose.production.yml build
```

**One-time volume ownership fix — required when upgrading an existing host.**
Both containers used to run as `root` and now run as UID/GID `10001`. Docker
only seeds a named volume's ownership when it *creates* it, so volumes that
already exist on the host stay root-owned and the new unprivileged process
gets `EACCES` on its first write. Run once, per host, **before** the first
non-root start:

```
docker compose -f docker-compose.yml -f docker-compose.production.yml \
  run --rm --no-deps --user 0 api chown -R 10001:10001 /app/media
```

Then start:

```
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d
```

Migrations run automatically at `api` startup (`start.sh` runs
`alembic upgrade head` before uvicorn).

The old single `ozon_profile` volume predates the `(role, marketplace)`
profile layout and was shared between both processes. Nothing migrates from
it — the new profiles start empty and re-age. Once the stack is healthy:

```
docker volume rm <project>_ozon_profile
```

---

## 3. The two settings that silently break persistent browsers

`docker-compose.production.yml` restates both of these itself rather than
inheriting them from the base file, because losing either one produces a
subtle failure rather than a loud one. Do not remove them.

### `WEB_CONCURRENCY=1` — one worker, always

`validate_single_browser_worker()` (`src/browser/profiles.py`) refuses to open
a persistent browser unless `WEB_CONCURRENCY` is exactly `1`. It is checked at
process startup, through `start_marketplace_services()`, and again inside
every browser lease.

This is not a performance knob. A persistent Chromium profile has exactly one
owner: a second uvicorn worker in the same container would try to open the
same profile directory, fail the `flock`, and every marketplace operation from
that worker would degrade to `transport_error` / `profile_locked`. Scale by
adding capacity elsewhere, never by raising this number.

### One profile volume per role

| Service | `MARKETPLACE_RUNTIME_ROLE` | Volume | Mount |
| --- | --- | --- | --- |
| `api` | `api` | `api_browser_profiles` | `/data/browser-profiles` |
| `bot` | `bot` | `bot_browser_profiles` | `/data/browser-profiles` |

Both set `BROWSER_PROFILE_ROOT=/data/browser-profiles`, and
`Settings.profile_dir()` then resolves each marketplace to
`<root>/<role>/<marketplace>` — e.g. `/data/browser-profiles/bot/ozon`. The
directory is created `0700` with a `0600` lock file inside it.

**The two roles must never share a volume.** They are separate volumes
precisely so both processes can own their own profiles; a shared volume means
one of the two processes cannot start its browsers at all.

Other mounts: `static:/app/media` (shared, written by the API) and
`./config:/app/config:ro`, which is how `config/monitored_categories.yaml` is
edited without a rebuild. The category map is re-read once per pipeline run
via `refresh_marketplace_category_urls()`, so an edit takes effect without a
restart — but every URL is re-validated against the allowlist, and a rejected
one is dropped with a warning.

---

## 4. Headed browsers, Xvfb and the sandbox

All three marketplace sessions launch with `headless=False`. Wildberries'
antibot challenge never resolves under headless Chromium — any mode, any
stealth patch, any IP — and only completes in a real headed session; Ozon runs
headed patchright/Chrome for the same class of reason. On a server with no
monitor that is solved with a virtual display.

Both images therefore end with:

```
ENTRYPOINT ["tini", "--"]
CMD ["xvfb-run", "-a", "/app/start.sh"]
```

`tini` as PID 1 is load-bearing, not cosmetic: `xvfb-run`'s wait for Xvfb's
readiness signal relies on ordinary signal delivery, which the kernel handles
differently for PID 1 — without a real init in front of it, `xvfb-run` hangs
before ever launching Python. It also reaps the zombie processes Chromium
leaves behind.

Container hardening, all applied to both browser services:

| Setting | Why |
| --- | --- |
| `user: "10001:10001"` | Root disables the Chromium sandbox regardless of seccomp, so non-root is a precondition for having a sandbox at all. |
| `security_opt: seccomp:./infra/playwright/seccomp_profile.json` | Docker's stock profile blocks `clone`/`setns`/`unshare`, which Chromium needs to put each renderer in a user namespace. This profile is the default plus that one rule, so the container keeps a restrictive syscall filter *and* Chromium keeps its sandbox while parsing untrusted marketplace HTML. |
| `shm_size: 1gb` | Avoids OOM renderer crashes on the 64 MB default `/dev/shm`. |
| `platform: linux/amd64` | Google Chrome for Linux is amd64-only. |

The seccomp path is resolved by the Docker **client**, relative to the Compose
file, so the repository root must be the Compose project directory. Do not
hand-edit `infra/playwright/seccomp_profile.json`; re-copy it from the pinned
upstream tag when `playwright` in `requirements.txt` is bumped. Full rationale:
`infra/playwright/README.md`.

---

## 5. Readiness and startup ordering

`start_marketplace_services()` runs at boot in both processes — `src/main.py`
(FastAPI lifespan, role `api`) and `bot/deals_scheduler.py` (role `bot`) — and
its failures propagate. A misconfigured worker count or a profile already
owned by another process therefore stops the container from starting, instead
of booting successfully and then serving laundered transport errors on every
request.

Consequences to expect:

* The `api` container **not** coming up after a config change is usually
  correct behaviour. Read the logs before restarting it in a loop.
* `db` has a healthcheck and both app services `depend_on` it being healthy;
  `bot` additionally waits for `api` to have started.
* The API image carries the full browser stack on purpose: the default source
  chains route all three marketplaces through the `browser` source, and the
  API calls `start_marketplace_services()` too. If you genuinely want a
  browser-free API, set every chain to a browser-free value
  (`WILDBERRIES_SOURCE_CHAIN=apify`, `OZON_SOURCE_CHAIN=apify`,
  `YANDEX_MARKET_SOURCE_CHAIN=public,apify`) — `MarketplaceSourceRegistry.start()`
  then skips the browser manager entirely. That is a configuration choice, not
  a different image.

Verify the deployed policy without a daemon, from the repository:

```
python scripts/verify_compose.py docker-compose.yml docker-compose.production.yml
```

---

## 6. Day-to-day operations

```
docker compose -f docker-compose.yml -f docker-compose.production.yml ps
docker compose -f docker-compose.yml -f docker-compose.production.yml logs -f api
docker logs telegram_bot
docker compose -f docker-compose.yml -f docker-compose.production.yml restart bot
```

Update and redeploy:

```
git pull
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --build
```

Rebuilding does **not** reset the browser profiles: they live in named volumes
and keep ageing across restarts, which is exactly what antibot reputation
depends on. Do not `down -v` on a healthy production host — that destroys both
profile volumes and the database volume.

Category changes: edit `config/monitored_categories.yaml` on the host. It is
mounted read-only into both containers and re-read once per pipeline run.

Backups worth taking: the `pg_data` volume, `.env`, and — if the Ozon profile
has been aged for a long time — the `bot_browser_profiles` volume.

---

## 7. Proxies

`PROXY_LIST` is comma-separated and applies to the Ozon and Wildberries
sessions. Prefer RU residential/mobile addresses for Ozon.

Chromium cannot do authenticated SOCKS5: use an unauthenticated
(IP-whitelisted) SOCKS5, or HTTP(S) with auth. For a phone-tunnel SOCKS5 bound
to the VPS host's loopback, the bot container reaches it through
`host.docker.internal`, which is why the `bot` service carries the
`extra_hosts: host-gateway` entry.

Never commit real proxy values. `scripts/repository_hygiene.py` will fail the
build if a `.env` variant reaches Git or the Docker build context.

---

## 8. Security boundary reminders

* Only nginx is published. Never add a published port to `api`, `bot` or `db`
  in production, and never expose a browser-control port (`9222`, `9229`,
  `4444`, `5900`, `6080`) — `scripts/verify_compose.py` fails the build if you
  do.
* Navigation is HTTPS-only against an exact-host allowlist
  (`www.ozon.ru`, `www.wildberries.ru`, `market.yandex.ru`) enforced in
  `src/browser/allowlist.py` and re-checked on every redirect. There is no way
  to widen it from configuration, and there should never be one.
* Logs disclose only the telemetry allowlist — marketplace, operation, source,
  outcome, durations, counts and a safe error code. No URL, query, product ID,
  cookie, token or body is ever emitted.
* `.env` never enters the image or the build context; secrets reach containers
  through the environment only.
