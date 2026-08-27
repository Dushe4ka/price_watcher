# Runbook: troubleshooting marketplace failures

How to read a marketplace failure and what to do about it.

Configuration reference: `docs/runbooks/local-development.md` §5.
Mechanism: `docs/architecture/marketplace-fallback.md`.

---

## 1. What you are allowed to see

Logs and probe transcripts disclose exactly nine fields, and nothing else:

```
marketplace  operation  source  outcome  duration_ms
item_count   transport_attempts  error_code  retry_after_ms
```

This is an allowlist in `src/marketplaces/telemetry.py`, not a redactor: no
URL, query, product ID, cookie, header, proxy, CAPTCHA token or response body
can reach a log line even if a caller hands the module an object that carries
one, and exceptions are rendered by class name only. So do not go looking for
"the failing URL" in the logs — it is not there by design. Reproduce with the
gated probe instead:

```
LIVE_MARKETPLACE_TESTS=1 python -m scripts.live_marketplace_probe \
    --marketplace wildberries --operation crawl_category
```

Read a result line as: the `outcome` is what the whole chain decided,
`source` is which source it came from, `transport_attempts` is how many times
that one source was actually invoked (1 or 2), and one `attempt` line is
printed per source that ran.

---

## 2. Outcome triage

| `outcome` | Chain behaviour | First reading |
| --- | --- | --- |
| `success` | stops here | Fine. |
| `empty` | stops here | The marketplace itself said "no items", with structural proof. Usually correct — see §3. |
| `not_found` | stops here | The product page exists but this product is not on it, or the page answered `404`. Counted as an error by the pipeline counters. |
| `challenge` | next source runs | Antibot wall or CAPTCHA. §5. |
| `rate_limited` | retried, then next source | Throttled. §6. |
| `transport_error` | retried, then next source | Navigation/network/timeout/profile-lock. §7, §8. |
| `parse_drift` | next source runs | The page arrived but no longer looks the way the code expects. §4. |
| `auth_error` | next source runs | Credentials rejected — in practice, Apify. |
| `invalid_config` | next source runs | The request or the configuration is unusable. §9. |
| `disabled` | next source runs | Deliberately not configured. Apify without a token or actor ID. |

If the final result carries `selected_source: none`, no source reached a
terminal outcome and the reported outcome is simply the **last** source's.
Read every `attempt` line, not just the result line.

---

## 3. `empty` is a real answer

`empty` is only produced on positive structural evidence — Wildberries'
`catalog-empty`/`search-empty` markers, Yandex Market's `searchEmpty` zone or
`search-empty` marker with no JSON-LD product, or an Ozon widget payload that
is structurally intact with a present-but-empty product collection. A blank
page, a truncated body or an unfamiliar layout produces `parse_drift`, never
`empty`.

So a genuine `empty` normally means the category or query really has no
matching items right now. If you believe there *should* be items:

1. Open the same category URL in a normal browser. If it shows products, the
   marketplace changed its empty-state markup and the classifier now
   mis-reads a populated page — that is a code fix in
   `src/marketplaces/validation.py`, not a configuration change.
2. Check `item_count` on the attempt line. `empty` always reports `0`.

Because `empty` is terminal, a false `empty` silently suppresses the rest of
the chain. It is the one outcome worth being suspicious about when volumes
drop without any error appearing in the logs.

---

## 4. `parse_drift` — the marketplace changed

`error_code: parse_drift` means the response arrived, was not a wall, and did
not match the expected structure. Causes, in order of likelihood:

* the marketplace redesigned its markup or widget payload (most common — this
  has happened repeatedly for WB category IDs and Yandex Market URL schemes);
* an A/B variant is being served to that profile;
* the response was partially rendered when it was read.

Confirm it is systematic (every attempt, both category and search) before
changing code. A one-off is usually a slow render.

`error_code: content_too_large` is a *different* failure that also reports
`outcome: parse_drift`: the response exceeded `MARKETPLACE_MAX_CONTENT_BYTES`
(default `2000000`). Ozon enforces this inside the page while streaming, so an
oversize body is cancelled rather than transferred. Raise the cap only if you
have a reason to believe the real page is legitimately bigger; the ceiling is
`10485760`.

---

## 5. `challenge` — antibot walls

Two codes:

* **`challenge_detected`** — a wall was found and no handler could act on it,
  or the page answered `403`/`407`, or Ozon's in-page fetch was redirected.
* **`challenge_unsupported`** — the coordinator returned
  `CHALLENGE_UNSOLVABLE`: the challenge is interactive, or SmartCaptcha is not
  in `frictionless` mode, or no trusted `SMARTCAPTCHA_WIDGET_ID` is
  configured, or the widget errored or expired, or no handler supports the
  detected type, or detection itself failed. This is fail-closed behaviour
  working as designed. If the coordinator ran out of time instead, the source
  reports `timeout` rather than a challenge — see §8.

A `challenge` is **never retried** inside a source (`_RETRIABLE_OUTCOMES` is
only `rate_limited` and `transport_error`). Hitting the same wall again with
the same profile makes things worse.

What actually helps, in order:

1. **Let the profile age.** Antibots judge a profile's history. A freshly
   created persistent profile is a liability. Use
   `python -m scripts.age_ozon_profile` to put real recurring traffic through
   the Ozon profile, and do not delete profile volumes casually.
2. **Confirm the session is headed.** Wildberries' challenge never resolves
   under headless Chromium on any IP. In containers that means `xvfb-run` and
   `tini` are intact — see `docs/runbooks/vps-deployment.md` §4.
3. **IP reputation** for Ozon: `PROXY_LIST` with RU residential/mobile
   addresses, and `OZON_PROXY_REQUIRED=true` to refuse to go out without one.
   Shared mobile pools have repeatedly failed; a dedicated address is what has
   any chance.
4. **Let the fallback do its job.** With the default `browser,apify` chain, a
   walled browser leg should hand over to Apify. If it does not, check §8 —
   the usual cause is a timeout misconfiguration, not the wall.

Automatically defeating an interactive human-verification challenge is out of
scope and stays out of scope. See `docs/decisions/smartcaptcha-feasibility.md`.

---

## 6. `rate_limited`

`error_code: rate_limited`, from a `429`. The source retries once (subject to
`MARKETPLACE_RETRY_MAX_ATTEMPTS`, default `2`) after
`MARKETPLACE_RETRY_BASE_DELAY_MS`, then the chain moves on.

`retry_after_ms` carries a server-advertised cooldown when one was published,
bounded to 300 000 ms. A browser attempt legitimately reports `none`: an
in-page `429` exposes no response headers to the browser transport, so no
value is invented.

If it is persistent, slow the crawl down rather than raising the retry budget
(which is hard-capped at `2` anyway): `CRAWL_INTERVAL_MINUTES`,
`MAX_PRODUCTS_PER_CATEGORY`, and the per-marketplace request delays.

---

## 7. `profile_locked` — the classic misconfiguration

`outcome: transport_error`, `error_code: profile_locked`. Another process
already holds the exclusive `flock` on that persistent profile. It is an
operator misconfiguration, not a network fault, which is exactly why it has
its own code instead of reading as a transport blip.

Checklist:

1. **`WEB_CONCURRENCY` must be exactly `1`.** More than one uvicorn worker in
   one container means two owners for one profile. `WEB_CONCURRENCY=1` is set
   in both `docker-compose.yml` and `docker-compose.production.yml` on
   purpose; if the process started at all, this check passed at boot, but a
   later override in `.env` can still break a lease.
2. **The `api` and `bot` roles must not share a profile volume.** They have
   separate volumes (`api_browser_profiles`, `bot_browser_profiles`) and
   separate `MARKETPLACE_RUNTIME_ROLE` values so that
   `Settings.profile_dir()` resolves to different directories.
3. **No stray local process.** A `python -m scripts.age_ozon_profile` left
   running, or a local run sharing `BROWSER_PROFILE_ROOT` with a container
   bind mount, will hold the lock.
4. A crashed container can leave the lock file behind, but not the lock: the
   `flock` is released when the file descriptor dies with the process. If the
   lock genuinely persists, a process is still alive — find it before
   deleting anything.

Never delete a profile directory to "fix" this. That throws away the ageing
that makes the profile useful in the first place.

---

## 8. `timeout` and the two timeout settings

`outcome: transport_error`, `error_code: timeout`. Read
`transport_attempts` first — it distinguishes two very different situations:

| `transport_attempts` | Meaning |
| --- | --- |
| `1` or `2` | This source really ran and really timed out. |
| `0` | This source **never ran at all**: the shared operation deadline had already expired before its turn. |

`transport_attempts: 0` on a later source in the chain is the signature of a
timeout misconfiguration. The two settings are separate for exactly this
reason:

* `MARKETPLACE_TOTAL_TIMEOUT_SEC` (default `30`) is **one source's own**
  budget, used by each browser source, the Yandex Market public source's
  HTTP client, and the Apify HTTP client.
* `MARKETPLACE_OPERATION_TIMEOUT_SEC` (default `200`) is the deadline shared
  by the **whole chain**, built once per operation.

If the operation budget is not comfortably larger than the sum of the
per-source budgets a chain can spend, a first source that burns its entire
budget leaves nothing for the rest — so an antibot-walled browser leg would
never hand over to Apify. `Settings.validate_operation_timeout_bounds`
enforces this at load time, and the process refuses to start otherwise. Two
invariants are checked: the operation timeout must be **strictly greater**
than the per-source timeout, AND it must cover the worst case of the
*longest* configured chain (`_DEFAULT_SOURCE_CHAINS` in `src/core/config.py`)
with every source retried up to `MARKETPLACE_RETRY_MAX_ATTEMPTS` times, each
consuming its full per-source budget. The validator reads the real chain
lengths and the real configured retry budget, so it stays correct even if a
chain is extended later — you no longer have to work this out by hand. The
`200`/`30` default covers Yandex Market's default 3-source
`public,browser,apify` chain (3 sources × 2 attempts × 30 s = 180 s, plus
headroom).

Note that the shared deadline is enforced *between* sources and before each
retry, not pushed into a source's own I/O — a source can overrun the operation
deadline by up to its own budget before the chain notices. That is why the
margin matters.

`error_code: transport_failed` is the ordinary one: navigation returned no
response, the page closed, the status was `5xx`, or an unclassified transport
exception occurred. Retried once, then the chain moves on.

---

## 9. `invalid_config`

`error_code: invalid_config`. The request or the configuration cannot be used
by this source. In practice:

* **A category slug with no configured URL.** Browser sources resolve
  `crawl_category` only through the map built from
  `config/monitored_categories.yaml`; an unknown slug, or one whose URL failed
  allowlist validation at load time, is `invalid_config`. Check the startup
  logs for `Rejected category URL for …` warnings — a URL that is not
  `https://` on the exact allowlisted host is dropped silently from the map.
* **A malformed product ID.** Must be ASCII digits, no leading zero, at most
  30 characters.
* **A malformed search request.** `limit` in `1..100`, `page` in `1..100`,
  non-empty query of at most 500 characters.
* **A malformed Apify actor ID.**
* **An off-host response URL** from Ozon's in-page fetch.

`error_code: auth_failed` (`outcome: auth_error`) means credentials were
rejected — realistically an invalid `APIFY_TOKEN`.

---

## 10. The whole chain answered `disabled`

Every source reporting `disabled` means the chains are configured as
`apify` with no `APIFY_TOKEN` and no actor IDs. That is exactly the mock-mode
configuration `scripts/smoke_marketplace_stack.py` sets up deliberately, so if
you see it in a real deployment, check whether a mock-mode environment leaked
into `.env`.

---

## 11. The process will not start

Startup failures are deliberate: `start_marketplace_services()` runs at boot in
both `src/main.py` and `bot/deals_scheduler.py`, and its failures propagate so
a misconfigured process cannot boot and then serve laundered errors.

| Symptom | Cause |
| --- | --- |
| `persistent browsers require WEB_CONCURRENCY=1` | `WEB_CONCURRENCY` is unset or not exactly `1`. |
| `persistent browser profile is already in use` | §7. |
| Pydantic validation error naming the operation timeout | `MARKETPLACE_OPERATION_TIMEOUT_SEC` is not strictly greater than `MARKETPLACE_TOTAL_TIMEOUT_SEC`. |
| Pydantic validation error naming the retry delays | `MARKETPLACE_RETRY_MAX_DELAY_MS` is less than `MARKETPLACE_RETRY_BASE_DELAY_MS`. |
| `source chain cannot contain duplicates` / unknown source | A `*_SOURCE_CHAIN` value repeats a source or names something other than `public`, `browser`, `apify`. |
| `invalid SmartCaptcha widget ID` | `SMARTCAPTCHA_WIDGET_ID` is not 1–128 characters from the allowed set. |
| `extra_forbidden` on some variable | `Settings` uses `extra='forbid'`; an unknown key in the environment is rejected outright. |
| `xvfb-run` appears to hang before Python starts | `tini` is not PID 1 — see `docs/runbooks/vps-deployment.md` §4. |
| `EACCES` writing to a volume | The one-time `chown` for pre-existing root-owned volumes was skipped — see `docs/runbooks/vps-deployment.md` §2. |
| `CAPTCHA adapter unavailable, continuing without it` | `CAPTCHA_ADAPTER_MODE=ohmycaptcha` but the vendored snapshot failed its contract check. Not fatal; the coordinator continues without it. Verify `vendor/ohmycaptcha/UPSTREAM.md` still carries the pinned commit `src/captcha/ohmycaptcha_adapter.py` expects. |

---

## 12. Before filing a bug

```
PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .
python scripts/verify_compose.py docker-compose.yml docker-compose.production.yml
python scripts/repository_hygiene.py --json
python scripts/smoke_marketplace_stack.py --mode controlled
```

Then attach the probe transcript from §1. It is safe to paste verbatim: it
carries only the allowlisted fields.
