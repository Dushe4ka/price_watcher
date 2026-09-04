# Playwright container hardening

This directory holds the container-level settings the API and bot images
need in order to run **headed** Chromium sessions (Wildberries, Ozon and
Yandex Market all launch with `headless=False`) as an unprivileged user.

## `seccomp_profile.json`

A verbatim copy of Microsoft's
[`utils/docker/seccomp_profile.json`](https://github.com/microsoft/playwright/blob/main/utils/docker/seccomp_profile.json),
which the [Playwright Docker guide](https://playwright.dev/docs/docker)
recommends for "crawling and scraping" workloads — exactly what this
project does.

What it is:

* the **default Docker seccomp profile** (`defaultAction`
  `SCMP_ACT_ERRNO`, so every syscall that is not explicitly listed is
  refused with `EPERM`), plus
* one extra allow rule, commented *"Allow create user namespaces"*, for
  `clone`, `setns` and `unshare`.

Why the extra rule matters: Chromium's own sandbox isolates each renderer
in a **user namespace**, and creating one requires those three syscalls.
Docker's stock profile blocks them, so a Chromium started under it can
only run with `--no-sandbox` — which removes the very layer that contains
a compromised renderer while it is parsing untrusted marketplace HTML.
With this profile the container keeps a restrictive syscall filter *and*
Chromium keeps its sandbox.

The profile is only effective for a **non-root** process: Playwright's
image runs browsers as root by default, and root disables the Chromium
sandbox regardless of seccomp. Both images therefore create and switch to
an unprivileged `pwuser` (UID/GID 10001), and the Compose files apply the
profile through:

```yaml
security_opt:
  - seccomp:./infra/playwright/seccomp_profile.json
```

The path is resolved by the Docker **client**, relative to the Compose
file, so the repository root must be the Compose project directory.

Do not hand-edit this file. Re-copy it from the pinned upstream tag when
`playwright` in `requirements.txt` is bumped.

## Other container settings, and why

| Setting | Reason |
| --- | --- |
| `ENTRYPOINT ["tini", "--"]` | Playwright recommends a real init to reap the zombie processes Chromium leaves behind. It is also load-bearing for `xvfb-run`, whose wait-for-Xvfb handshake relies on ordinary signal delivery and hangs forever when it is itself PID 1. |
| `xvfb-run -a …` | The marketplace sessions run headed; Xvfb gives them a display without a monitor. |
| `shm_size: 1gb` | Chromium crashes with out-of-memory renderer errors on Docker's 64 MB default `/dev/shm`. Playwright's own recommendation is `--ipc=host`; `shm_size` achieves the same for these workloads without sharing the host IPC namespace. |
| `ulimits.nofile: 65536` | Docker's 1024 default open-file limit is too low for a headed Chromium. Confirmed live: on a real end-to-end run against a real marketplace page, the CDP `Target.createTarget` call started failing outright ("Failed to open a new tab") as the container's open-file count climbed under repeated real page loads — raising the container is not enough by itself for this, the container's own ulimit has to move too. |
| `user: "10001:10001"` | Non-root is a precondition for the Chromium sandbox (above). |
| `platform: linux/amd64` | See below. |

## The images are amd64-only

Google Chrome for Linux is published for **amd64 only**. The Ozon session
launches `channel='chrome'` (patchright's stealth recommendation), so
`patchright install chrome` aborts with `ERROR: not supported on Linux
Arm64` when the build runs natively on Apple Silicon. This is not new —
the pre-existing bot image had the same constraint — but it is now stated
explicitly as `platform: linux/amd64` so that:

* the production VPS (x86_64) builds natively, and
* an Apple Silicon workstation builds the *same* image under emulation
  instead of failing halfway through the browser install.

Emulated builds are slow. Building on the target host, or with
`docker buildx build --platform linux/amd64` and a remote builder, is the
faster path.

## The shared browser runtime stage

Docker has no `include` directive, so the browser runtime instructions —
Chromium runtime libraries, Xvfb, tini, `pwuser`, the pinned
`requirements.txt` install and `playwright install chromium` /
`patchright install chrome` — are duplicated **verbatim** between
`Dockerfile.api` and `Dockerfile.bot`, delimited by:

```dockerfile
# >>> shared browser runtime >>>
...
# <<< shared browser runtime <<<
```

`scripts/verify_compose.py` and `tests/test_compose_policy.py` compare the
two blocks and fail if they ever drift, so "shared" is enforced rather
than merely intended. Edit both blocks together.

Browsers are installed into `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` and
made world-readable, because the default cache lives under the *building*
user's home directory (`/root`) and would be unreadable after the image
drops to `pwuser`. `patchright install chrome` installs Google Chrome
system-wide under `/opt/google/chrome` and is unaffected.

## Upgrading an existing deployment

Both containers used to run as root. Docker only seeds a named volume's
ownership when it creates it, so volumes that **already exist** on a host
stay root-owned and the new `pwuser` process gets `EACCES` on first write.
Run once, per host, before the first non-root start:

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml \
  run --rm --no-deps --user 0 api chown -R 10001:10001 /app/media
```

The old single `ozon_profile` volume is gone: it predates the
`(role, marketplace)` profile layout and was shared. Nothing migrates from
it — the new profiles start empty and re-age. Once the stack is healthy,
drop it:

```bash
docker volume rm <project>_ozon_profile
```

## Why the API image also carries the browser stack

`Settings` ships default source chains that include the `browser` source
for all three marketplaces (`wildberries`/`ozon`: `browser,apify`,
`yandex_market`: `public,browser,apify`), and both entrypoints call
`start_marketplace_services()` at boot, which starts the browser manager
whenever any chain contains `browser`. So with the defaults the API
process really does open persistent browsers and needs the same runtime
as the bot.

There is a supported lighter deployment: the source chains are
configurable per marketplace, and `MarketplaceSourceRegistry.start()`
skips the browser manager entirely when no chain contains `browser`. An
operator who sets, for example:

```env
WILDBERRIES_SOURCE_CHAIN=apify
OZON_SOURCE_CHAIN=apify
YANDEX_MARKET_SOURCE_CHAIN=public,apify
```

runs the API without ever launching Chromium. That is a configuration
choice, not a separate image: the images stay identical so a chain can be
changed with an env edit and a restart, never a rebuild. **The default is
API-with-browser**, which is what the Compose overlays are built for.
