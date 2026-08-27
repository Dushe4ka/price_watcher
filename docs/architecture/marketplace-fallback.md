# Marketplace fallback architecture

How one marketplace operation is executed: which sources are tried, in what
order, under which deadline, and what each possible answer means.

Everything below is written against the code in `src/marketplaces/`,
`src/browser/` and `src/captcha/`. Operator procedures live in
`docs/runbooks/local-development.md`, `docs/runbooks/vps-deployment.md` and
`docs/runbooks/troubleshooting.md`.

---

## 1. The three operations

`MarketplaceService` (`src/marketplaces/service.py`) exposes exactly three
operations, one per member of `MarketplaceOperation`:

| Operation | Request | Success value |
| --- | --- | --- |
| `crawl_category` | `CategoryRequest(category_slug, limit)` | `CategoryCrawlResult` |
| `parse_product` | `ProductRequest(product_id)` | `ParsedProduct` |
| `search_products` | `SearchRequest(query, limit, page)` | `tuple[ParsedProduct, ...]` |

A request never carries a URL. Category navigation is resolved inside
`MarketplaceSourceRegistry` from `config/monitored_categories.yaml`, and every
resolved URL is re-validated against the allowlist (§7) before it is handed to
a source. Product and search URLs are built by `build_marketplace_url` in
`src/browser/allowlist.py` from typed fields only.

Both process roles compose the runtime at boot — `src/main.py` for the API
(`configure_marketplace_runtime('api')`) and `bot/deals_scheduler.py` for the
bot (`configure_marketplace_runtime('bot')`) — and both call
`start_marketplace_services()` before serving anything, so a misconfigured
worker count or an already-owned browser profile fails loudly at startup
instead of being laundered into a per-request transport error.

---

## 2. Data flow

```
caller
  │  CategoryRequest | ProductRequest | SearchRequest
  ▼
MarketplaceService._run                      (src/marketplaces/service.py)
  │  builds ONE OperationDeadline from MARKETPLACE_OPERATION_TIMEOUT_SEC
  │  asks the registry for the ordered chain
  ▼
MarketplaceSourceRegistry.sources_for        (src/marketplaces/registry.py)
  │  ((SourceName.BROWSER, OzonBrowserSource), (SourceName.APIFY, ApifySource))
  ▼
MarketplaceService._retrying_call            one SourceCall per source
  │  wraps each call in SourceRetryExecutor.run(policy, sleep, clock, deadline)
  ▼
execute_fallback                             (src/marketplaces/fallback.py)
  │  awaits each SourceCall EXACTLY ONCE, in order,
  │  stopping at the first terminal outcome
  ▼
MarketplaceResult(outcome, value, attempts, selected_source)
```

Two invariants are worth stating explicitly, because they are easy to break:

* **`execute_fallback` never retries.** It calls each source once. The bounded
  transport retry happens *inside* that single call, owned by
  `SourceRetryExecutor` (`src/marketplaces/retry.py`). This is why a retried
  source still produces exactly one `SourceAttempt` entry, with a measured
  `transport_attempts` count rather than a second attempt row.
* **A source may appear at most once in a chain.** `execute_fallback` raises on
  duplicates, and `parse_source_chain` in `src/core/config.py` rejects a
  configured chain that repeats a source name.

---

## 3. The outcome state machine

`SourceOutcome` (`src/marketplaces/contracts.py`) is the whole vocabulary:

| Outcome | Terminal? | Meaning |
| --- | --- | --- |
| `success` | yes | The source produced a value. |
| `empty` | yes | The source produced a *structurally valid* page with no items (§4). |
| `not_found` | yes | A product page structurally exists but this product is not on it (or answered `404`). |
| `challenge` | no | An antibot wall or CAPTCHA that could not be resolved on the page. |
| `rate_limited` | no | The source was throttled (`429`). Retriable. |
| `transport_error` | no | Navigation, network, timeout or profile-lock failure. Retriable. |
| `parse_drift` | no | The response arrived but no longer matches the expected structure. |
| `auth_error` | no | Credentials were rejected. |
| `invalid_config` | no | The request or the configuration is unusable for this source. |
| `disabled` | no | The source is deliberately not configured (Apify without a token/actor). |

Terminal outcomes stop the chain: `_TERMINAL_OUTCOMES` is exactly
`{success, empty, not_found}`. Everything else lets the next source run. If no
source reaches a terminal outcome, `MarketplaceResult` reports the **last**
attempt's outcome, carries `value=None`, and leaves `selected_source=None`.

Only `rate_limited` and `transport_error` are retriable inside one source
(`_RETRIABLE_OUTCOMES` in `src/marketplaces/retry.py`). A `challenge` is
deliberately never retried: re-hitting a wall with the same profile makes the
wall harder, not softer.

Counting nuance in `src/marketplaces/diagnostics.py`: pipeline error counters
treat `success` and `empty` as non-errors, but `not_found` *does* increment
`stats.errors`. `fallback_activations` counts results with more than one
attempt.

---

## 4. Structural `EMPTY` — a real answer, not a failure

The central correctness idea of this stack: **"zero products" and "we were
blocked" must never collapse into the same result.**

`src/marketplaces/validation.py` classifies a raw response *before* anything is
mapped, into one `ValidationState`:

| `ValidationState` | Becomes |
| --- | --- |
| `valid_with_items` | mapping proceeds; `success` (or `parse_drift` if mapping yields nothing) |
| `valid_empty` | `empty` — or `not_found` for `parse_product` |
| `challenge` | `challenge` / `challenge_detected` |
| `drift` | `parse_drift` |

`valid_empty` is only ever returned on **positive structural evidence** that the
marketplace itself said "nothing here":

* **Wildberries** — a real empty-state marker in the DOM
  (`data-testid="catalog-empty"` / `search-empty`, or the matching class).
* **Yandex Market** — `data-zone-name="searchEmpty"`, `data-testid="search-empty"`
  or a `search-empty` class, and no JSON-LD product survived the canonical
  walker.
* **Ozon** — the widget payload is structurally intact (`widgetStates` plus
  `layout`), no product summary maps out of it, and either `widgetStates` is
  empty or a product *collection* key (`items`, `products`, `tiles`, `skuList`,
  `searchResults`) is present and empty. A payload that merely fails to parse,
  or that contains a product-shaped mapping the extractor could not consume, is
  `drift`, never `empty`.

An absent marker is never treated as emptiness. A blank page, a truncated body,
a wall or an unrecognised layout all fall through to `drift` (→ `parse_drift`)
or `challenge`, both of which are non-terminal and therefore let the next
source try. That is the whole point: `empty` stops the chain, so it must be
earned.

---

## 5. Retry and deadline architecture

Three pieces, all in `src/marketplaces/retry.py`, composed only by
`MarketplaceService`:

* **`RetryPolicy(max_attempts, base_delay_ms, max_delay_ms)`** — a bounded,
  source-local budget. `max_attempts` is hard-limited to `1..2` by the
  dataclass itself, so no configuration can turn a fallback chain into a
  hammer.
* **`OperationDeadline(expires_at)`** — one absolute monotonic deadline per
  operation, built once in `_run` and shared by every source in the chain.
* **`SourceRetryExecutor`** — the sole owner of internal retries. It refuses to
  start a source whose shared deadline has already passed (returning
  `transport_error` / `timeout` with `transport_attempts=0`), retries only
  retriable outcomes, and skips the backoff sleep when that sleep would itself
  cross the deadline.

### The two timeouts, and why they are separate

| Setting | Default | Read by | Scope |
| --- | --- | --- | --- |
| `MARKETPLACE_TOTAL_TIMEOUT_SEC` | `30` | `src/marketplaces/registry.py` (browser sources), `src/marketplaces/apify_client.py` (HTTP client) | **one source's own invocation** |
| `MARKETPLACE_OPERATION_TIMEOUT_SEC` | `90` | `src/marketplaces/service.py` `_run` only | **the whole fallback chain** |

They used to be one setting, and that was a defect: a browser source that
consumed its entire 30 s per-source budget left the shared deadline already
expired, so Apify — the fallback that exists precisely for an antibot wall —
was never invoked at all. `Settings.validate_operation_timeout_bounds` now
enforces the invariant at load time: `MARKETPLACE_OPERATION_TIMEOUT_SEC` must
be **strictly greater** than `MARKETPLACE_TOTAL_TIMEOUT_SEC`, or the process
refuses to start. Size it above the sum of the per-source budgets you expect a
chain to spend; the `90`/`30` default covers the two-source `browser,apify`
chain with headroom for one internal retry.

`Settings.validate_retry_delay_bounds` enforces the neighbouring invariant:
`MARKETPLACE_RETRY_MAX_DELAY_MS` must not be less than
`MARKETPLACE_RETRY_BASE_DELAY_MS`.

The shared deadline is enforced *between* sources and before each retry, not
pushed down into a source's own I/O. A source can therefore overrun the
operation deadline by up to its own per-source budget before the chain
notices — sizing the operation timeout comfortably above one full per-source
budget is what makes that acceptable.

### Per-source policies

`MarketplaceService._retry_policy` picks the budget:

* `SourceName.APIFY` → `RetryPolicy(max_attempts=1)`. A billed actor run is not
  something to repeat behind the operator's back; the executor becomes a
  transparent pass-through that still honours the shared deadline and still
  reports a measured attempt count.
* `SourceName.PUBLIC` and `SourceName.BROWSER` → the configured
  `MARKETPLACE_RETRY_MAX_ATTEMPTS` / `MARKETPLACE_RETRY_BASE_DELAY_MS` /
  `MARKETPLACE_RETRY_MAX_DELAY_MS`.

---

## 6. Source chains

Chains are ordered and per-marketplace. The defaults live in
`_DEFAULT_SOURCE_CHAINS` in `src/core/config.py` and are mirrored by the
corresponding settings' own defaults:

| Marketplace | Setting | Default chain |
| --- | --- | --- |
| Wildberries | `WILDBERRIES_SOURCE_CHAIN` | `browser,apify` |
| Ozon | `OZON_SOURCE_CHAIN` | `browser,apify` |
| Yandex Market | `YANDEX_MARKET_SOURCE_CHAIN` | `public,browser,apify` |

Yandex Market is the only marketplace with a `public` leg because its pages are
still server-rendered: a plain HTTP fetch plus JSON-LD works, so the cheap
source goes first and the browser is a fallback rather than the norm.
Wildberries requires a headed browser (its antibot challenge never resolves
under headless Chromium, on any IP), and Ozon requires a headed
patchright/Chrome session, so neither has a usable public leg.

A chain is a comma-separated list of `public`, `browser`, `apify` with no
duplicates. Reducing every chain to `apify` is a supported, browser-free
deployment: `MarketplaceSourceRegistry.start()` skips the browser manager
entirely when no chain contains `browser`. See `infra/playwright/README.md`.

### The Apify leg: a gate, then a fallback

`ApifySource` (`src/marketplaces/sources/apify.py`) is *inert by default*.
`ApifyClient.is_enabled` requires **both** a non-empty `APIFY_TOKEN` **and** the
actor ID for that exact marketplace/operation pair
(`APIFY_<MARKETPLACE>_<OPERATION>_ACTOR_ID`); otherwise the source returns
`disabled` before a socket is opened. This is what makes "the chain always ends
in `apify`" safe to ship unconfigured, and it is also how the mock-mode smoke
in `scripts/smoke_marketplace_stack.py` proves no live traffic can occur.

When enabled, the client calls the actor's `run-sync-get-dataset-items`
endpoint with `MARKETPLACE_TOTAL_TIMEOUT_SEC` as its HTTP timeout. **The
dataset field mapping is a project-owned synthetic schema and has not been
validated against any live Apify actor** — treat the Apify leg as wired but
unproven until someone runs a real actor against it.

---

## 7. The navigation boundary: HTTPS-only, exact host

`src/browser/allowlist.py` is the single gate for every main-frame navigation.
`_ALLOWED_HOSTS` is an exact-host allowlist — not a suffix match, not a domain
match:

```
ozon           www.ozon.ru
wildberries    www.wildberries.ru
yandex_market  market.yandex.ru
```

`validate_main_frame_url` rejects anything that is not **`https`**, any URL
carrying userinfo, any host outside that exact set, any IP literal, and any
port other than the default `443`. There is no test mode, no override flag and
no second validation path; `ozon.ru`, `m.ozon.ru` and `evil.www.ozon.ru` are
all rejected.

The boundary is enforced repeatedly, not once: before navigation, on the
resulting `page.url`, before every content read and evaluation, on Ozon's
in-page fetch response URL, and on every `framenavigated` event of the leased
page via `_LeasePageGuard` (`src/browser/profiles.py`), which closes the page
and raises `UnsafeMarketplaceUrl` out of the lease when a redirect leaves the
allowlist. Popups opened by the page are closed unconditionally.

---

## 8. Browser sources: one lease, one Page, one Context

`_BrowserSourceBase` (`src/marketplaces/sources/browser.py`) runs a whole
operation inside a single `manager.lease(marketplace)` block:

```
async with manager.lease('ozon') as page:      # exactly one Page
    navigate(page, url)                        # allowlist-checked
    resolve_challenge(page)                    # SAME page
    capture/read content from page             # SAME page
    resolve_challenge(page)                    # SAME page, post-read re-check
    if that resolution is SOLVED:
        re-read content from page              # SAME page
```

**The same-Page / same-Context invariant.** A CAPTCHA or antibot challenge is
only meaningful in the browsing context that was challenged: the cookies, the
storage state and the antibot's own client-side state all live there. So the
coordinator is handed the *exact* `Page` object the source is working on
(`self._coordinator.resolve(page, deadline=deadline)`), it re-detects on that
same page after a handler acts, and only then reports `SOLVED`. Nothing in this
stack opens a second page, a second context or a second browser to solve a
challenge and carry a token back — a token minted elsewhere would not match the
challenged context anyway. `ChallengeResolution.SOLVED` triggers a re-read on
the same page precisely because the pre-challenge bytes are known to be the
wall's, not the marketplace's.

`BrowserSessionManager.lease` (`src/browser/profiles.py`) enforces the rest:

* one `asyncio.Lock` per marketplace, so operations against one marketplace are
  serialized end-to-end rather than interleaved on a shared profile;
* `validate_single_browser_worker()` is re-checked inside the lease;
* the idle session is closed first if `*_BROWSER_IDLE_SEC` has elapsed, then a
  live persistent context is (re)opened lazily;
* on exit the page is closed, popup/redirect cleanup tasks are drained, and the
  session's idle clock is touched. The **context** survives the lease; only the
  page is per-operation.

**Byte caps.** Every read is bounded by `MARKETPLACE_MAX_CONTENT_BYTES`
(default `2000000`): `page.content()` is UTF-8-encoded and measured;
`page.evaluate` results are walked with a bounded depth (32) and charged per
scalar; and Ozon's in-page `fetch` bridge streams the response through a reader
that cancels and returns `too_large` the moment the byte counter is exceeded —
the cap is enforced *in the page*, before the bytes ever cross into Python.
Exceeding it is `parse_drift` / `content_too_large`, never a silent truncation.

Per-marketplace specifics:

* **Ozon** — navigates to the home page, resolves any challenge, then performs
  an in-page `fetch` of `entrypoint-api.bx/page/json/v2` with
  `redirect: 'manual'` and `credentials: 'include'`. A redirect, an off-host
  response URL, an oversize body or an undecodable body each map to their own
  safe outcome. Forbidden fetch headers (notably `User-Agent`) are stripped
  rather than sent, since the browser would drop them anyway.
* **Wildberries** — navigates to the real category/product page and evaluates
  the canonical bounded DOM-extraction JavaScript (`CATEGORY_CARDS_JS` /
  `DETAIL_PAGE_JS` from `src/wb/constants.py`). No JSON API, no text search.
* **Yandex Market** — reads bounded HTML and maps it through the canonical
  JSON-LD walker in `src/parsers/ym_api.py`.

---

## 9. Persistent browser profiles

### Layout

`Settings.profile_dir(role, marketplace)` resolves to:

```
<BROWSER_PROFILE_ROOT>/<role>/<marketplace>
```

`role` is one of `local`, `api`, `bot` (`MARKETPLACE_RUNTIME_ROLE`);
`marketplace` is one of `wildberries`, `ozon`, `yandex_market`. The resolved
path is containment-checked against its root, so a crafted root cannot escape.
In containers `BROWSER_PROFILE_ROOT` is `/data/browser-profiles`, backed by a
**separate named volume per role** (`api_browser_profiles`,
`bot_browser_profiles`).

One profile per `(runtime_role, marketplace)` — nine possible directories, of
which one role's three exist in a given process. The reason two roles must
never share a volume is mechanical: a persistent Chromium profile has exactly
one owner, so a shared volume means one of the two processes cannot open its
browsers at all.

### Locking

`ProfileLock` (`src/browser/profiles.py`) creates the profile directory `0700`,
opens `.profile.lock` inside it `0600`, and takes a **non-blocking exclusive
`flock`**. A second opener fails immediately with `ProfileInUseError` rather
than blocking or corrupting the profile. Browser sources translate that into
`transport_error` with `SafeErrorCode.PROFILE_LOCKED` — a distinct code, so a
misconfiguration does not read as a network blip in the logs. The raising
exception is never chained or rendered, so no profile path can reach a log
line.

`validate_single_browser_worker()` refuses to open a persistent browser unless
`WEB_CONCURRENCY` is exactly `'1'`. It is checked at startup
(`BrowserSessionManager.start`, reached through
`start_marketplace_services()`) and again inside every lease.

### Ageing

Profiles are long-lived on purpose: an antibot judges a profile's history, so a
freshly created profile is a liability. `scripts/age_ozon_profile.py` exists to
put real, recurring traffic through the Ozon profile without needing a live
Telegram token.

Note that `OZON_PROFILE_DIR` (default `.ozon-profile`) is a *separate, legacy*
setting used by the older standalone Ozon crawler path; the marketplace
fallback stack uses `BROWSER_PROFILE_ROOT` and `profile_dir()` exclusively.

---

## 10. Browser and driver version matrix

| Component | Pin | Where |
| --- | --- | --- |
| Playwright (main runtime) | `playwright==1.53.0` | `requirements.txt` |
| Patchright (main runtime) | `patchright==1.61.2` | `requirements.txt` |
| Bundled Chromium | installed by `playwright install chromium` | `Dockerfile.api` / `Dockerfile.bot`, into `/ms-playwright` |
| Google Chrome (stable) | installed by `patchright install chrome` | same images, system-wide under `/opt/google/chrome` |
| Playwright (OhMyCaptcha vendor snapshot) | `playwright==1.49.1` | `vendor/ohmycaptcha/requirements.txt` — **separate environment, never installed into the images** |

Which engine each marketplace session uses:

| Marketplace | Driver | Launch |
| --- | --- | --- |
| Ozon (`src/ozon/session.py`) | patchright | `launch_persistent_context(headless=False, channel='chrome')` |
| Wildberries (`src/wb/session.py`) | playwright | `launch_persistent_context(headless=False)` (bundled Chromium) |
| Yandex Market (`src/browser/yandex_market.py`) | playwright | `launch_persistent_context(headless=False)` (bundled Chromium) |

All three run **headed**. Under Docker that means `xvfb-run` and `tini` as PID
1 — see `infra/playwright/README.md`, which also explains the `linux/amd64`
pin (Google Chrome for Linux is amd64-only) and the seccomp profile that lets
Chromium keep its own user-namespace sandbox.

`scripts/verify_compose.py` fails the build if the main runtime and the vendor
snapshot ever end up on the same Playwright pin, if either image loses `tini`,
`xvfb-run` or its non-root `USER`, or if the two images' shared browser-runtime
stages drift apart.

---

## 11. The OhMyCaptcha vendor boundary

`vendor/ohmycaptcha/` is an **unmodified upstream snapshot**, imported by
`git subtree --squash` at commit
`0b543d5436700fa3455e634583e2642a8a64159f` (MIT; see
`THIRD_PARTY_LICENSES.md` and `vendor/ohmycaptcha/UPSTREAM.md`).

The boundary is real and must stay that way:

* It has **its own `requirements.txt`** pinning `playwright==1.49.1`, installed
  into a **separate virtual environment** (`.venv-ohmycaptcha`). It is never
  installed into the API or bot images, which install only the root
  `requirements.txt`.
* Nothing imports it directly. `src/captcha/ohmycaptcha_adapter.py` loads it
  under a private, path-hashed module namespace, verifies the pinned commit
  marker in `vendor/ohmycaptcha/UPSTREAM.md`, extracts exactly four reviewed JavaScript
  primitives, checks each against a marker contract, and discards the whole
  namespace on any mismatch (`VendorContractError`). No solver object, HTTP
  client or vendor page ever escapes the adapter.
* The adapter is only composed at all when `CAPTCHA_ADAPTER_MODE=ohmycaptcha`;
  if it cannot load, `build_challenge_coordinator` logs one warning and
  continues without it.

### Update procedure

Per `vendor/ohmycaptcha/UPSTREAM.md`, and not to be shortcut:

1. Review the upstream changelog, requirements, license and security notes.
2. Run the upstream test suite against the candidate SHA.
3. `git subtree pull --prefix=vendor/ohmycaptcha <upstream-url> <reviewed-40-char-SHA> --squash`.
4. Run the gateway upstream-contract, Python, Compose and manual live tests.
5. Record the new SHA in `vendor/ohmycaptcha/UPSTREAM.md`, in
   `THIRD_PARTY_LICENSES.md`, in `PINNED_UPSTREAM_COMMIT` in
   `src/captcha/ohmycaptcha_adapter.py`, and in the commit body.

Step 5 is not optional: the adapter refuses to load a snapshot whose
`vendor/ohmycaptcha/UPSTREAM.md` does not carry the exact pinned commit it was built against.

---

## 12. SmartCaptcha: the approved bounded scope

Full reasoning and sources: `docs/decisions/smartcaptcha-feasibility.md`.

Approved and implemented, `SMARTCAPTCHA_MODE=frictionless` only:

* operate **only** on a `window.smartCaptcha` object already present on the
  leased marketplace page, with a pre-configured, format-validated public
  `SMARTCAPTCHA_WIDGET_ID`;
* use only the documented `subscribe(widgetId, …)` / `execute(widgetId)` API;
* never accept, store or forward the `success` callback's token;
* re-detect the challenge on the same page afterwards and report `SOLVED` only
  once it is gone.

Explicitly out of scope: creating a browser, context, page or widget; loading
an external script; clicking an interactive challenge; DOM scraping; reading
private provider state; `subscribe(undefined, …)`. Anything visible,
interactive, errored or expired fails closed with `challenge_unsupported`.
The default is `SMARTCAPTCHA_MODE=disabled`, and an empty
`SMARTCAPTCHA_WIDGET_ID` disables the handler even in `frictionless` mode.

A disappearing challenge proves only that the CAPTCHA stage finished. It proves
nothing about the marketplace payload — that is `src/marketplaces/validation.py`'s job (§4), and
it runs afterwards regardless.

---

## 13. What a failure is allowed to say

`src/marketplaces/telemetry.py` is an exact allowlist, not a redactor. It
inspects no payload, request or transport object; every rendered value is
re-derived from a closed enum or coerced to an integer. The complete set of
fields any marketplace attempt may disclose:

```
marketplace  operation  source  outcome  duration_ms
item_count   transport_attempts  error_code  retry_after_ms
```

A query, a product identity, a URL, a cookie, an `Authorization` header, a
proxy, a CAPTCHA token or a response body therefore cannot reach a log line
even if a caller hands the module an object that carries one. Exceptions are
rendered by class name only (`safe_exception_label`), and
`silence_transport_request_logs()` raises `httpx`/`httpcore`/`urllib3`/
`telegram.request` to `WARNING` so their `INFO` request lines — which contain
full URLs — never appear.

`SafeErrorCode` is the closed set of reasons a failure may cite; each code's
operational meaning is in `docs/runbooks/troubleshooting.md`.
`accumulate_marketplace_diagnostics` in `src/marketplaces/diagnostics.py` folds
the same safe fields into the pipeline run counters.
