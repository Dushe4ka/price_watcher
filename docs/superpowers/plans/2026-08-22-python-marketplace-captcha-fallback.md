# Python Marketplace CAPTCHA Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить структурированный fallback `native/public → browser → Apify` для Wildberries, Ozon и Yandex Market с same-Page CAPTCHA handling, persistent browser profiles, безопасными diagnostics и локальным/VPS runtime.

**Architecture:** Существующее Python-приложение получает in-process `MarketplaceService`, который исполняет типизированные source adapters через единый fallback/retry policy. Browser adapters используют persistent context текущего процесса, а OhMyCaptcha применяется только через изолированный adapter к уже выданной marketplace Page; отдельный HTTP gateway не создаётся.

**Tech Stack:** Python 3.12, `unittest`, FastAPI 0.115.12, python-telegram-bot 22.1, httpx 0.28.1, Playwright 1.53.0, Patchright 1.61.2, Pydantic 2.11.3, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-22-python-marketplace-captcha-fallback-design.md`

## Global Constraints

- Основной runtime сохраняет `playwright==1.53.0` и `patchright==1.61.2`; vendor Playwright 1.49.1 не устанавливается в main environment.
- `vendor/ohmycaptcha/**` неизменяем; runtime adapter живёт только под `src/captcha/` и не добавляет vendor root в `sys.path`.
- Default chains: WB/Ozon — `browser,apify`; Yandex Market — `public,browser,apify`.
- Fallback вызывает каждый source не более одного раза; только `SourceRetryExecutor` выполняет не более двух transport attempts внутри общего deadline.
- `EMPTY` терминален только после structural validation; challenge, 429, timeout, invalid content и schema drift не превращаются в empty.
- Challenge detection, handling, validation и extraction используют один и тот же Page и persistent Context lease.
- SmartCaptcha по умолчанию `disabled`; разрешённый автоматический scope — checkbox/frictionless. Interactive image/audio/slider завершается fail-closed и передаётся следующему fallback.
- Browser profiles изолированы по `(runtime role, marketplace)`, защищены OS lock, имеют mode `0700`; `WEB_CONCURRENCY=1`.
- DTO не принимают произвольные navigation URL, proxy, actor ID или actor payload. Marketplace URL и Apify input строятся кодом и проверяются allowlist.
- Логи не содержат query, product ID/URL/title, body/HTML, cookies, Authorization, proxy, CAPTCHA token/value/length и provider keys.
- Unit/controlled integration tests не выполняют live marketplace traffic. Live probe требует `LIVE_MARKETPLACE_TESTS=1` и выполняет одну bounded operation.
- Каждый production change проходит RED → GREEN → refactor, focused tests, regression suite, changed-file style/compile checks, отдельный Conventional Commit и push.
- Merge в `develop` или `main` не входит в эту работу.

---

### Task 3: Hermetic test bootstrap and structured contracts

**Files:**
- Create: `src/marketplaces/__init__.py`
- Create: `src/marketplaces/contracts.py`
- Create: `src/marketplaces/errors.py`
- Modify: `tests/__init__.py`
- Create: `tests/test_marketplace_contracts.py`
- Create: `tests/test_marketplace_errors.py`

**Interfaces:**
- Consumes: `CategoryCrawlResult` из `src/crawlers/base.py`, `ParsedProduct` из `src/parsers/base.py`.
- Produces: `MarketplaceName`, `SourceName`, `MarketplaceOperation`, `SourceOutcome`, `SafeErrorCode`, `CategoryRequest`, `ProductRequest`, `SearchRequest`, `SourceAttempt`, `SourceResult[T]`, `MarketplaceResult[T]`, safe result factories и `MarketplaceOperationError`.

- [ ] **Step 1: Написать failing tests для contracts и безопасных ошибок**

```python
class SourceResultTests(unittest.TestCase):
    def test_failure_cannot_carry_value(self) -> None:
        with self.assertRaises(ValueError):
            SourceResult(
                source=SourceName.BROWSER,
                outcome=SourceOutcome.CHALLENGE,
                value=('unexpected',),
                attempt=SourceAttempt(
                    source=SourceName.BROWSER,
                    outcome=SourceOutcome.CHALLENGE,
                    duration_ms=4,
                    item_count=0,
                ),
            )

    def test_error_string_does_not_include_raw_exception(self) -> None:
        marker = 'sentinel-secret-value'
        error = MarketplaceOperationError(
            marketplace='ozon',
            operation=MarketplaceOperation.PARSE_PRODUCT,
            error_code=SafeErrorCode.TRANSPORT_FAILED,
            attempts=(),
            cause=RuntimeError(marker),
        )
        self.assertNotIn(marker, str(error))
        self.assertNotIn(marker, repr(error))
```

В `tests/__init__.py` установить только synthetic mandatory Settings через
`os.environ.setdefault`, если `PYTHON_DOTENV_DISABLED=1`; реальные `.env` и secrets
не читать.

- [ ] **Step 2: Наблюдать RED**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_marketplace_contracts tests.test_marketplace_errors -v`
Expected: import failure, потому что `src.marketplaces.contracts` ещё отсутствует.

- [ ] **Step 3: Реализовать immutable contracts и invariants**

```python
@dataclass(frozen=True, slots=True)
class SourceResult(Generic[T]):
    source: SourceName
    outcome: SourceOutcome
    value: T | None
    attempt: SourceAttempt

    def __post_init__(self) -> None:
        if self.attempt.source is not self.source:
            raise ValueError('attempt source must match result source')
        if self.attempt.outcome is not self.outcome:
            raise ValueError('attempt outcome must match result outcome')
        if self.outcome is SourceOutcome.SUCCESS and self.value is None:
            raise ValueError('success requires a value')
        if self.outcome is not SourceOutcome.SUCCESS and self.value is not None:
            raise ValueError('failure cannot carry a value')
```

Factories должны отдельно создавать success, structural empty и failure; safe error
хранит `cause` только для exception chaining и не включает его текст в `str/repr`.

- [ ] **Step 4: Проверить GREEN и регрессию**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_marketplace_contracts tests.test_marketplace_errors -v`
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .`
Run: `python -m compileall -q src/marketplaces tests/test_marketplace_contracts.py tests/test_marketplace_errors.py`

- [ ] **Step 5: Commit и push**

```bash
git add src/marketplaces tests/__init__.py tests/test_marketplace_contracts.py tests/test_marketplace_errors.py
git commit -m "feat(marketplaces): add structured source outcomes"
git push origin feature/marketplace-captcha-fallback
```

### Task 4: Deterministic fallback and single retry owner

**Files:**
- Create: `src/marketplaces/fallback.py`
- Create: `src/marketplaces/retry.py`
- Create: `tests/test_marketplace_fallback.py`
- Create: `tests/test_marketplace_retry.py`

**Interfaces:**
- Consumes: Task 3 `SourceResult[T]`, `MarketplaceResult[T]`, enums/factories.
- Produces: `SourceCall[T](source, invoke)`, `execute_fallback(marketplace, operation, calls) -> MarketplaceResult[T]`, `RetryPolicy` и `SourceRetryExecutor.run(call, policy, sleep, clock) -> SourceResult[T]`.

- [ ] **Step 1: Написать table-driven failing tests**

```python
async def test_challenge_continues_to_browser_success(self) -> None:
    calls: list[str] = []

    async def blocked() -> SourceResult[tuple[str, ...]]:
        calls.append('public')
        return source_failure(
            SourceName.PUBLIC,
            SourceOutcome.CHALLENGE,
            SafeErrorCode.CHALLENGE_DETECTED,
        )

    async def solved() -> SourceResult[tuple[str, ...]]:
        calls.append('browser')
        return source_success(SourceName.BROWSER, ('item',))

    result = await execute_fallback(
        'ozon',
        MarketplaceOperation.SEARCH_PRODUCTS,
        (SourceCall(SourceName.PUBLIC, blocked),
         SourceCall(SourceName.BROWSER, solved)),
    )
    self.assertEqual(['public', 'browser'], calls)
    self.assertEqual(SourceName.BROWSER, result.selected_source)
```

Retry tests inject fake `sleep` and `clock`, prove exactly two calls for retriable
transport failure and exactly one call for challenge, parse drift, auth, config,
empty and success.

- [ ] **Step 2: Наблюдать RED**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_marketplace_fallback tests.test_marketplace_retry -v`
Expected: modules `fallback` and `retry` not found.

- [ ] **Step 3: Реализовать fallback и retry budget**

```python
@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 2
    base_delay_ms: int = 250
    max_delay_ms: int = 1000

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 2:
            raise ValueError('max_attempts must be between 1 and 2')
```

`execute_fallback` отклоняет duplicate sources и mismatched attempt source,
завершает цепочку только на success/validated empty/not-found и агрегирует attempts.
`SourceRetryExecutor` повторяет только rate-limited/transport errors, уважает общий
deadline и возвращает один `SourceAttempt.transport_attempts`.

- [ ] **Step 4: Проверить GREEN и регрессию**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_marketplace_fallback tests.test_marketplace_retry -v`
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .`
Run: `python -m compileall -q src/marketplaces`

- [ ] **Step 5: Commit и push**

```bash
git add src/marketplaces/fallback.py src/marketplaces/retry.py tests/test_marketplace_fallback.py tests/test_marketplace_retry.py
git commit -m "feat(marketplaces): centralize fallback and retry policy"
git push origin feature/marketplace-captcha-fallback
```

### Task 5: Strict configuration, source chains and profile namespaces

**Files:**
- Modify: `src/core/config.py`
- Modify: `.env.example`
- Create: `tests/test_marketplace_settings.py`
- Create: `tests/test_source_chains.py`

**Interfaces:**
- Consumes: Task 3 `SourceName`, marketplace names.
- Produces: `parse_source_chain`, `Settings.source_chain(marketplace)`, `Settings.profile_dir(role, marketplace)` и typed Settings fields.

- [ ] **Step 1: Написать failing tests для defaults, validation и redaction**

```python
def test_default_chains_match_approved_topology(self) -> None:
    settings = make_settings()
    self.assertEqual(
        (SourceName.BROWSER, SourceName.APIFY),
        settings.source_chain('wildberries'),
    )
    self.assertEqual(
        (SourceName.PUBLIC, SourceName.BROWSER, SourceName.APIFY),
        settings.source_chain('yandex_market'),
    )

def test_profile_paths_are_role_and_marketplace_isolated(self) -> None:
    settings = make_settings(browser_profile_root='/profiles')
    self.assertEqual(
        Path('/profiles/bot/ozon'),
        settings.profile_dir('bot', 'ozon'),
    )
    self.assertNotEqual(
        settings.profile_dir('bot', 'ozon'),
        settings.profile_dir('api', 'ozon'),
    )
```

Добавить cases для unknown/duplicate/empty chain, path traversal и sentinel secrets
в `repr`/ValidationError.

- [ ] **Step 2: Наблюдать RED**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_marketplace_settings tests.test_source_chains -v`
Expected: отсутствуют новые fields/methods.

- [ ] **Step 3: Реализовать строгую Pydantic-конфигурацию**

Добавить `SecretStr` для Apify token и provider keys, `Literal` для runtime role и
captcha modes, default chains из Global Constraints, общий timeout/content limit и
retry settings. `profile_dir` должен вычислить resolved child path и проверить, что
он остаётся внутри resolved root.

```python
def parse_source_chain(
    value: str,
    default: tuple[SourceName, ...],
) -> tuple[SourceName, ...]:
    raw = value.strip()
    if not raw:
        return default
    sources = tuple(SourceName(part.strip()) for part in raw.split(','))
    if len(sources) != len(set(sources)):
        raise ValueError('source chain cannot contain duplicates')
    return sources
```

- [ ] **Step 4: Проверить GREEN и sanitized example**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_marketplace_settings tests.test_source_chains -v`
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .`
Run: `python scripts/repository_hygiene.py --json`

- [ ] **Step 5: Commit и push**

```bash
git add src/core/config.py .env.example tests/test_marketplace_settings.py tests/test_source_chains.py
git commit -m "feat(config): add Python marketplace source policy"
git push origin feature/marketplace-captcha-fallback
```

### Task 6: Native source adapters and outcome classification

**Files:**
- Create: `src/marketplaces/sources/__init__.py`
- Create: `src/marketplaces/sources/protocols.py`
- Create: `src/marketplaces/sources/public.py`
- Create: `src/marketplaces/validation.py`
- Modify: `src/parsers/utils.py`
- Modify: `src/ozon/client.py`
- Modify: `src/wb/client.py`
- Modify: `src/crawlers/yandex_market.py`
- Modify: `src/parsers/yandex_market.py`
- Create: `tests/fixtures/marketplaces/ozon/success.json`
- Create: `tests/fixtures/marketplaces/ozon/empty.json`
- Create: `tests/fixtures/marketplaces/ozon/challenge.json`
- Create: `tests/fixtures/marketplaces/ozon/drift.json`
- Create: `tests/fixtures/marketplaces/wildberries/success.html`
- Create: `tests/fixtures/marketplaces/wildberries/empty.html`
- Create: `tests/fixtures/marketplaces/wildberries/challenge.html`
- Create: `tests/fixtures/marketplaces/wildberries/drift.html`
- Create: `tests/fixtures/marketplaces/yandex_market/success.html`
- Create: `tests/fixtures/marketplaces/yandex_market/empty.html`
- Create: `tests/fixtures/marketplaces/yandex_market/challenge.html`
- Create: `tests/fixtures/marketplaces/yandex_market/drift.html`
- Create: `tests/test_marketplace_validation.py`
- Create: `tests/test_native_source_outcomes.py`

**Interfaces:**
- Consumes: Tasks 3–5 requests/results/retry settings; existing pure marketplace mappers.
- Produces: `CategorySource`, `ProductSource`, `SearchSource`, `MarketplaceSourceError`, structural validators и native/public adapters.

- [ ] **Step 1: Добавить synthetic fixtures и failing classification tests**

```python
def test_challenge_html_is_not_valid_empty(self) -> None:
    html = load_fixture('wildberries/challenge.html')
    self.assertEqual(
        ValidationState.CHALLENGE,
        validate_wb_dom_snapshot(html),
    )

async def test_unproven_wb_public_source_is_disabled(self) -> None:
    result = await WildberriesPublicSource().search_products(
        SearchRequest(query='synthetic', limit=2),
    )
    self.assertEqual(SourceOutcome.DISABLED, result.outcome)
```

Fixtures используют только synthetic IDs/titles и не содержат live headers,
cookies, request IDs или marketplace response bodies.

- [ ] **Step 2: Наблюдать RED**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_marketplace_validation tests.test_native_source_outcomes -v`
Expected: validators/source adapters отсутствуют.

- [ ] **Step 3: Реализовать protocols, validators и typed failures**

Validators возвращают `VALID_WITH_ITEMS`, `VALID_EMPTY`, `CHALLENGE`, `DRIFT`.
Ozon/WB clients перестают превращать blocks в `None`/`[]` и поднимают
`MarketplaceSourceError` с fixed safe code. Yandex non-200/invalid schema получает
явный outcome. Старые client-local loops и `retry_request` удаляются только на
мигрированных путях; mapping functions остаются единственными парсерами payload.

- [ ] **Step 4: Проверить GREEN и старые parser tests**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_marketplace_validation tests.test_native_source_outcomes -v`
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_ozon_parser tests.test_wb_crawler tests.test_yandex_market_parser -v`
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .`

- [ ] **Step 5: Commit и push**

```bash
git add src/marketplaces src/parsers/utils.py src/ozon/client.py src/wb/client.py src/crawlers/yandex_market.py src/parsers/yandex_market.py tests/fixtures/marketplaces tests/test_marketplace_validation.py tests/test_native_source_outcomes.py
git commit -m "refactor(marketplaces): classify native source outcomes"
git push origin feature/marketplace-captcha-fallback
```

### Task 7: Shared Apify fallback adapter

**Files:**
- Create: `src/marketplaces/apify_client.py`
- Create: `src/marketplaces/sources/apify.py`
- Modify: `src/core/config.py`
- Modify: `.env.example`
- Create: `tests/test_apify_client.py`
- Create: `tests/test_apify_source.py`

**Interfaces:**
- Consumes: Task 3 request/results, Task 5 SecretStr settings, Task 6 source protocols.
- Produces: `ApifyClient.run_actor(marketplace, operation, request) -> list[dict[str, object]]`, `build_actor_input(marketplace, operation, request) -> dict[str, object]`, `ApifySource`.

- [ ] **Step 1: Написать failing tests с injected httpx transport**

```python
async def test_actor_input_is_code_owned(self) -> None:
    payload = build_actor_input(
        'ozon',
        MarketplaceOperation.SEARCH_PRODUCTS,
        SearchRequest(query='synthetic query', limit=3),
    )
    self.assertEqual(3, payload['maxItems'])
    self.assertNotIn('actorId', payload)
    self.assertNotIn('proxy', payload)

async def test_missing_token_returns_disabled(self) -> None:
    result = await make_source(token=None).search_products(
        SearchRequest(query='synthetic', limit=2),
    )
    self.assertEqual(SourceOutcome.DISABLED, result.outcome)
```

Добавить literal responses для 401/403, 429, 500, invalid schema и valid empty;
sentinel token не должен появляться в URL, exception или caplog.

- [ ] **Step 2: Наблюдать RED**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_apify_client tests.test_apify_source -v`
Expected: Apify modules отсутствуют.

- [ ] **Step 3: Реализовать fixed actor routing и outcome mapping**

`ApifyClient` получает actor IDs из Settings, token через Authorization header и
принимает только typed marketplace operation/request. 401/403 → `AUTH_ERROR`, 429
→ `RATE_LIMITED`, 5xx/network → `TRANSPORT_ERROR`, schema mismatch → `PARSE_DRIFT`,
valid zero dataset → `EMPTY`. Source не выполняет собственный retry.

- [ ] **Step 4: Проверить GREEN и redaction**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_apify_client tests.test_apify_source -v`
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .`
Run: `python -m compileall -q src/marketplaces`

- [ ] **Step 5: Commit и push**

```bash
git add src/marketplaces/apify_client.py src/marketplaces/sources/apify.py src/core/config.py .env.example tests/test_apify_client.py tests/test_apify_source.py
git commit -m "feat(marketplaces): add shared Apify fallback"
git push origin feature/marketplace-captcha-fallback
```

### Task 8: Persistent browser leases, allowlists and process isolation

**Files:**
- Create: `src/browser/__init__.py`
- Create: `src/browser/contracts.py`
- Create: `src/browser/allowlist.py`
- Create: `src/browser/profiles.py`
- Modify: `src/ozon/session.py`
- Modify: `src/wb/session.py`
- Create: `src/browser/yandex_market.py`
- Modify: `src/core/browser_proxy.py`
- Modify: `.gitignore`
- Modify: `.dockerignore`
- Modify: `scripts/repository_hygiene.py`
- Modify: `tests/test_repository_hygiene.py`
- Create: `tests/test_browser_allowlist.py`
- Create: `tests/test_browser_profiles.py`
- Create: `tests/test_browser_sessions.py`

**Interfaces:**
- Consumes: Task 5 `Settings.profile_dir`; current Ozon/WB sessions.
- Produces: `PageLike`, `BrowserContextLike`, `ProfileLock`, `BrowserSessionManager`, `build_marketplace_url`, `validate_main_frame_url`.

- [ ] **Step 1: Написать failing tests для allowlist, locks и lifecycle**

```python
def test_suffix_trick_is_rejected(self) -> None:
    with self.assertRaises(UnsafeMarketplaceUrl):
        validate_main_frame_url(
            'ozon',
            'https://www.ozon.ru.attacker.invalid/product/1',
        )

async def test_same_marketplace_leases_are_serialized(self) -> None:
    manager = make_fake_manager()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    observed: list[str] = []
    await run_two_leases(
        manager,
        'ozon',
        first_entered,
        release_first,
        observed,
    )
    self.assertEqual(['first-enter', 'first-exit', 'second-enter'], observed)
```

Добавить exact host/HTTPS/IP/userinfo/port cases, second process lock rejection,
profile mode `0700`, page close и persistent context survival.

- [ ] **Step 2: Наблюдать RED**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_browser_allowlist tests.test_browser_profiles tests.test_browser_sessions -v`
Expected: browser abstractions отсутствуют.

- [ ] **Step 3: Реализовать session manager и persistent contexts**

Ozon сохраняет Patchright 1.61.2 headed Chrome; WB использует Playwright 1.53.0
`launch_persistent_context`; Yandex получает отдельный Playwright context. Один lock
покрывает всю marketplace operation. Task Page закрывается при выходе из lease,
Context остаётся до idle/lifecycle close. Удалить `--no-sandbox` и
`--disable-dev-shm-usage`; popup закрывать, main-frame redirect валидировать.

- [ ] **Step 4: Проверить GREEN, hygiene и регрессию**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_browser_allowlist tests.test_browser_profiles tests.test_browser_sessions tests.test_repository_hygiene -v`
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .`
Run: `python scripts/repository_hygiene.py --json`

- [ ] **Step 5: Commit и push**

```bash
git add src/browser src/ozon/session.py src/wb/session.py src/core/browser_proxy.py .gitignore .dockerignore scripts/repository_hygiene.py tests/test_repository_hygiene.py tests/test_browser_allowlist.py tests/test_browser_profiles.py tests/test_browser_sessions.py
git commit -m "feat(browser): add isolated persistent marketplace profiles"
git push origin feature/marketplace-captcha-fallback
```
### Task 9: OhMyCaptcha adapter and same-Page challenge coordinator

**Files:**
- Create: `src/captcha/__init__.py`
- Create: `src/captcha/models.py`
- Create: `src/captcha/detector.py`
- Create: `src/captcha/coordinator.py`
- Create: `src/captcha/handlers.py`
- Create: `src/captcha/ohmycaptcha_adapter.py`
- Create: `tests/fixtures/challenges/clean.html`
- Create: `tests/fixtures/challenges/recaptcha-v2.html`
- Create: `tests/fixtures/challenges/recaptcha-v3.html`
- Create: `tests/fixtures/challenges/hcaptcha.html`
- Create: `tests/fixtures/challenges/turnstile.html`
- Create: `tests/fixtures/challenges/unknown.html`
- Create: `tests/test_challenge_detector.py`
- Create: `tests/test_challenge_coordinator.py`
- Create: `tests/test_ohmycaptcha_adapter.py`
- Create: `tests/test_challenge_log_redaction.py`

**Interfaces:**
- Consumes: Task 8 `PageLike`; pinned vendor snapshot at commit `0b543d5436700fa3455e634583e2642a8a64159f`.
- Produces: `ChallengeType`, `ChallengeDetection`, `ChallengeResolution`, `detect_challenge`, `ChallengeHandler`, `ChallengeCoordinator`, `OhMyCaptchaAdapter.vendor_scripts()`.

- [ ] **Step 1: Написать failing detector, identity и namespace tests**

```python
async def test_coordinator_uses_the_leased_page(self) -> None:
    page = FakePage(load_fixture('challenges/recaptcha-v2.html'))
    handler = RecordingHandler()
    coordinator = ChallengeCoordinator((handler,))
    await coordinator.resolve(page, deadline=FakeDeadline(5.0))
    self.assertIs(page, handler.page)

def test_vendor_loader_does_not_replace_application_src(self) -> None:
    import src
    application_src = src
    adapter = OhMyCaptchaAdapter(vendor_root=VENDOR_ROOT)
    adapter.vendor_scripts()
    self.assertIs(application_src, sys.modules['src'])
```

Добавить deterministic priority для challenge types, re-detection после handler,
unknown/clean cases и sentinel token/body/exception redaction.

- [ ] **Step 2: Наблюдать RED**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_challenge_detector tests.test_challenge_coordinator tests.test_ohmycaptcha_adapter tests.test_challenge_log_redaction -v`
Expected: `src.captcha` отсутствует.

- [ ] **Step 3: Реализовать synthetic vendor namespace и same-Page handlers**

Vendor package загружать через `importlib.util.spec_from_file_location` с
`submodule_search_locations`, не менять `sys.path`. Adapter экспортирует только
reviewed JS constants/algorithmic primitives. High-level vendor `solve()` не
вызывать, потому что он создаёт собственные Page/Context. Frictionless/checkbox
handler работает с переданным Page; interactive challenge возвращает
`CHALLENGE_UNSOLVABLE`. Success возможен только после повторного detector check.

- [ ] **Step 4: Проверить GREEN и обе среды**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_challenge_detector tests.test_challenge_coordinator tests.test_ohmycaptcha_adapter tests.test_challenge_log_redaction -v`
Run: `.venv-ohmycaptcha/bin/python -m pytest vendor/ohmycaptcha/tests -q`
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .`

- [ ] **Step 5: Commit и push**

```bash
git add src/captcha tests/fixtures/challenges tests/test_challenge_detector.py tests/test_challenge_coordinator.py tests/test_ohmycaptcha_adapter.py tests/test_challenge_log_redaction.py
git commit -m "feat(captcha): adapt pinned solver to current pages"
git push origin feature/marketplace-captcha-fallback
```

### Task 10: Explicit SmartCaptcha gate

**Files:**
- Create: `src/captcha/smartcaptcha.py`
- Modify: `src/captcha/coordinator.py`
- Modify: `src/core/config.py`
- Modify: `.env.example`
- Create: `tests/fixtures/challenges/smartcaptcha-callback.html`
- Create: `tests/test_smartcaptcha.py`
- Create: `tests/live/__init__.py`
- Create: `tests/live/test_smartcaptcha_live.py`
- Create: `docs/decisions/smartcaptcha-feasibility.md`

**Interfaces:**
- Consumes: Task 9 challenge contracts and same-Page coordinator.
- Produces: `SmartCaptchaMode`, `SmartCaptchaHandler.solve(page, detection, deadline) -> ChallengeResolution`.

- [ ] **Step 1: Написать failing controlled callback tests**

```python
async def test_disabled_mode_fails_closed(self) -> None:
    result = await SmartCaptchaHandler(
        SmartCaptchaMode.DISABLED,
    ).solve(FakeSmartCaptchaPage(), detection(), FakeDeadline(3.0))
    self.assertFalse(result.solved)
    self.assertEqual(
        SafeErrorCode.CHALLENGE_UNSUPPORTED,
        result.error_code,
    )

async def test_visible_challenge_is_not_reported_as_solved(self) -> None:
    page = FakeSmartCaptchaPage(events=('challenge-visible',))
    result = await SmartCaptchaHandler(
        SmartCaptchaMode.FRICTIONLESS,
    ).solve(page, detection(), FakeDeadline(3.0))
    self.assertFalse(result.solved)
```

Добавить success callback plus structural validator, JS error, timeout, expired
callback и redaction cases. Live test должен skip без `LIVE_MARKETPLACE_TESTS=1`.

- [ ] **Step 2: Наблюдать RED**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_smartcaptcha -v`
Expected: SmartCaptcha handler отсутствует.

- [ ] **Step 3: Реализовать disabled/frictionless modes**

Frictionless mode вызывает только уже существующий `window.smartCaptcha.execute()`
на переданном Page и слушает success/error/expired/visible events. Token не
возвращается наружу и не логируется. Visible challenge без структурно доступного
контента остаётся unsolved. Документировать границу: OhMyCaptcha не поддерживает
SmartCaptcha, fabricated token и server key запрещены.

- [ ] **Step 4: Проверить GREEN и default-off behavior**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_smartcaptcha -v`
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.live.test_smartcaptcha_live -v`
Expected live result: skipped without the explicit gate.
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .`

- [ ] **Step 5: Commit и push**

```bash
git add src/captcha/smartcaptcha.py src/captcha/coordinator.py src/core/config.py .env.example tests/fixtures/challenges/smartcaptcha-callback.html tests/test_smartcaptcha.py tests/live docs/decisions/smartcaptcha-feasibility.md
git commit -m "feat(captcha): gate SmartCaptcha behavior explicitly"
git push origin feature/marketplace-captcha-fallback
```

### Task 11: Marketplace browser source adapters

**Files:**
- Create: `src/marketplaces/sources/browser.py`
- Modify: `src/ozon/client.py`
- Modify: `src/wb/client.py`
- Modify: `src/crawlers/ozon.py`
- Modify: `src/crawlers/wildberries.py`
- Modify: `src/crawlers/yandex_market.py`
- Modify: `src/parsers/ozon.py`
- Modify: `src/parsers/wildberries.py`
- Modify: `src/parsers/yandex_market.py`
- Create: `tests/test_browser_sources.py`
- Create: `tests/test_browser_source_page_identity.py`
- Create: `tests/test_browser_source_limits.py`

**Interfaces:**
- Consumes: Task 6 protocols/validators, Task 8 manager/allowlist, Task 9 coordinator, Task 10 SmartCaptcha gate.
- Produces: `OzonBrowserSource`, `WildberriesBrowserSource`, `YandexMarketBrowserSource`.

- [ ] **Step 1: Написать failing browser source contract tests**

```python
async def test_page_identity_is_preserved_through_extraction(self) -> None:
    page = IdentityPage(valid_product_fixture())
    manager = SinglePageManager(page)
    coordinator = RecordingCoordinator()
    source = OzonBrowserSource(manager, coordinator)
    result = await source.parse_product(ProductRequest('synthetic-1'))
    self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
    self.assertIs(page, coordinator.page)
    self.assertIs(page, source.last_extraction_page)

async def test_oversized_content_is_not_parsed(self) -> None:
    source = make_yandex_source(content=b'x' * 1025, max_bytes=1024)
    result = await source.search_products(
        SearchRequest(query='synthetic', limit=1),
    )
    self.assertEqual(
        SafeErrorCode.CONTENT_TOO_LARGE,
        result.attempt.error_code,
    )
```

Добавить query encoding, redirect allowlist, timeout, 429, closed context,
post-fetch challenge и marketplace-specific valid-empty marker cases.

- [ ] **Step 2: Наблюдать RED**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_browser_sources tests.test_browser_source_page_identity tests.test_browser_source_limits -v`
Expected: browser source adapters отсутствуют.

- [ ] **Step 3: Реализовать три adapters без raw payload boundary**

WB использует existing DOM extraction JS; Ozon выполняет warmup/request/capture в
том же persistent Context; Yandex парсит bounded HTML существующими helpers. После
navigation и перед extraction выполняются detector/coordinator/validator. Source
возвращает только typed domain value и safe attempt, никогда HTML/JSON body.

- [ ] **Step 4: Проверить GREEN и marketplace regression**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_browser_sources tests.test_browser_source_page_identity tests.test_browser_source_limits -v`
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_ozon_parser tests.test_wb_crawler tests.test_yandex_market_parser -v`
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .`

- [ ] **Step 5: Commit и push**

```bash
git add src/marketplaces/sources/browser.py src/ozon/client.py src/wb/client.py src/crawlers src/parsers tests/test_browser_sources.py tests/test_browser_source_page_identity.py tests/test_browser_source_limits.py
git commit -m "feat(marketplaces): add browser fallback sources"
git push origin feature/marketplace-captcha-fallback
```

### Task 12: Composition root, consumer migration and lifecycle

**Files:**
- Create: `src/marketplaces/service.py`
- Create: `src/marketplaces/registry.py`
- Create: `src/marketplaces/diagnostics.py`
- Modify: `src/crawlers/base.py`
- Modify: `src/crawlers/__init__.py`
- Modify: `src/parsers/base.py`
- Modify: `src/parsers/__init__.py`
- Modify: `src/services/deal_pipeline.py`
- Modify: `src/services/market_search.py`
- Modify: `src/services/market_price_checker.py`
- Modify: `src/schemas/deal.py`
- Modify: `src/api/v1/product_parser.py`
- Modify: `src/api/v1/endpoints/deals.py`
- Modify: `src/main.py`
- Modify: `bot/main.py`
- Modify: `bot/deals_scheduler.py`
- Create: `tests/test_marketplace_registry.py`
- Create: `tests/test_marketplace_service.py`
- Create: `tests/test_deal_pipeline_diagnostics.py`
- Create: `tests/test_market_search_outcomes.py`
- Create: `tests/test_application_lifecycle.py`

**Interfaces:**
- Consumes: Tasks 3–11 source adapters, fallback, retry, settings и browser manager.
- Produces: `MarketplaceService` methods, lazy `get_marketplace_service`, compatibility wrappers, aggregate diagnostics и application lifecycle hooks.

- [ ] **Step 1: Написать failing composition/consumer tests**

```python
async def test_service_falls_back_browser_to_apify(self) -> None:
    service = make_service(
        chain=(SourceName.BROWSER, SourceName.APIFY),
        browser=challenge_source(),
        apify=successful_product_source(),
    )
    result = await service.parse_product(ProductRequest('synthetic-1'))
    self.assertEqual(SourceName.APIFY, result.selected_source)
    self.assertEqual(2, len(result.attempts))

async def test_valid_empty_does_not_increment_pipeline_errors(self) -> None:
    stats = DealRunStats()
    accumulate_marketplace_diagnostics(stats, valid_empty_result())
    self.assertEqual(0, stats.errors)
    self.assertEqual(1, stats.source_outcomes['browser']['empty'])
```

Lifecycle tests используют FastAPI `TestClient` как context manager и fake Telegram
post-shutdown callback; manager должен закрыться ровно один раз.

- [ ] **Step 2: Наблюдать RED**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_marketplace_registry tests.test_marketplace_service tests.test_deal_pipeline_diagnostics tests.test_market_search_outcomes tests.test_application_lifecycle -v`
Expected: service/registry/diagnostics отсутствуют.

- [ ] **Step 3: Реализовать composition root и миграцию consumers**

Реализовать три точные async-сигнатуры: `crawl_category(request:
CategoryRequest) -> MarketplaceResult[CategoryCrawlResult]`,
`parse_product(request: ProductRequest) -> MarketplaceResult[ParsedProduct]` и
`search_products(request: SearchRequest) ->
MarketplaceResult[tuple[ParsedProduct, ...]]`. Методы вызывают `execute_fallback`
с chain из Settings. Existing
`crawl_category`/`parse_product` остаются thin unwrapping wrappers; structured
consumers используют `*_result`. FastAPI lifespan закрывает resources после yield;
Telegram `post_shutdown` вызывает async close. API и bot получают свои role paths.

- [ ] **Step 4: Проверить GREEN и все основные consumers**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_marketplace_registry tests.test_marketplace_service tests.test_deal_pipeline_diagnostics tests.test_market_search_outcomes tests.test_application_lifecycle -v`
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .`
Run: `python -m compileall -q src bot`

- [ ] **Step 5: Commit и push**

```bash
git add src/marketplaces/service.py src/marketplaces/registry.py src/marketplaces/diagnostics.py src/crawlers src/parsers src/services src/schemas/deal.py src/api/v1 src/main.py bot tests/test_marketplace_registry.py tests/test_marketplace_service.py tests/test_deal_pipeline_diagnostics.py tests/test_market_search_outcomes.py tests/test_application_lifecycle.py
git commit -m "feat(marketplaces): wire fallback services into application flows"
git push origin feature/marketplace-captcha-fallback
```

### Task 13: Local/VPS Docker runtime and CI policy

**Files:**
- Modify: `Dockerfile.api`
- Modify: `Dockerfile.bot`
- Modify: `docker-compose.yml`
- Create: `docker-compose.local.yml`
- Create: `docker-compose.production.yml`
- Create: `infra/playwright/seccomp_profile.json`
- Create: `infra/playwright/README.md`
- Create: `scripts/verify_compose.py`
- Create: `.github/workflows/ci.yml`
- Modify: `.dockerignore`
- Create: `tests/test_compose_policy.py`

**Interfaces:**
- Consumes: Task 8 profile layout, Task 12 process lifecycle.
- Produces: browser-capable API/bot images, local/production overlays и executable Compose policy verifier.

- [ ] **Step 1: Написать failing rendered-policy tests**

```python
def test_production_services_have_isolated_profile_volumes(self) -> None:
    compose = render_compose('docker-compose.production.yml')
    api_mounts = compose['services']['api']['volumes']
    bot_mounts = compose['services']['bot']['volumes']
    self.assertIn('api_browser_profiles:/data/browser-profiles', api_mounts)
    self.assertIn('bot_browser_profiles:/data/browser-profiles', bot_mounts)
    self.assertNotEqual(api_mounts, bot_mounts)

def test_production_uses_one_worker_and_no_public_browser_port(self) -> None:
    compose = render_compose('docker-compose.production.yml')
    self.assertEqual('1', compose['services']['api']['environment']['WEB_CONCURRENCY'])
    self.assertNotIn('ports', compose['services']['bot'])
```

Добавить assertions для non-root, init/tini, shm, seccomp, profile volumes и main
dependency pins distinct from vendor pins.

- [ ] **Step 2: Наблюдать RED**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_compose_policy -v`
Expected: overlays/verifier отсутствуют.

- [ ] **Step 3: Реализовать shared browser image stages и Compose overlays**

Оба images устанавливают main Playwright/Patchright browsers, запускаются non-root,
используют `tini`, Xvfb, `shm_size` и matching seccomp. Добавить separate named
volumes, role env и one-worker validation. Local ports привязать к loopback;
production не предоставляет browser-control endpoint. CI выполняет unit/controlled
tests без credentials/live traffic.

- [ ] **Step 4: Проверить GREEN и реальные builds**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_compose_policy -v`
Run: `python scripts/verify_compose.py docker-compose.yml docker-compose.production.yml`
Run: `docker compose -f docker-compose.yml -f docker-compose.local.yml build api bot`
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .`

- [ ] **Step 5: Commit и push**

```bash
git add Dockerfile.api Dockerfile.bot docker-compose.yml docker-compose.local.yml docker-compose.production.yml infra/playwright scripts/verify_compose.py .github/workflows/ci.yml .dockerignore tests/test_compose_policy.py
git commit -m "build(docker): support persistent browser fallbacks"
git push origin feature/marketplace-captcha-fallback
```

### Task 14: Safe telemetry and guarded live probes

**Files:**
- Create: `src/marketplaces/telemetry.py`
- Modify: `src/ozon/client.py`
- Modify: `src/ozon/session.py`
- Modify: `src/wb/client.py`
- Modify: `src/wb/session.py`
- Modify: `src/services/deal_pipeline.py`
- Create: `scripts/live_marketplace_probe.py`
- Modify: `scripts/smoke_ozon_crawl.py`
- Modify: `scripts/smoke_wb_crawl.py`
- Create: `scripts/smoke_yandex_market_crawl.py`
- Create: `tests/test_safe_marketplace_logging.py`
- Create: `tests/test_live_marketplace_probe.py`

**Interfaces:**
- Consumes: Task 3 attempts/results, Task 12 diagnostics/service.
- Produces: `safe_attempt_fields`, `assert_live_tests_enabled`, `parse_marketplace`, `run_one_probe`.

- [ ] **Step 1: Написать failing sentinel-redaction и gate tests**

```python
def test_safe_attempt_fields_use_exact_allowlist(self) -> None:
    fields = safe_attempt_fields(result_with_sentinels())
    self.assertEqual(
        {
            'marketplace', 'operation', 'source', 'outcome',
            'duration_ms', 'item_count', 'transport_attempts',
            'error_code', 'retry_after_ms',
        },
        set(fields),
    )
    self.assertNotIn('sentinel-secret', repr(fields))

def test_live_probe_requires_explicit_gate(self) -> None:
    with self.assertRaises(LiveProbeDisabled):
        assert_live_tests_enabled({})
```

Sentinels разместить в query, URL, proxy, cookie, bearer token, CAPTCHA token,
raw body и exception; ни один не должен появляться в output/caplog/repr.

- [ ] **Step 2: Наблюдать RED**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_safe_marketplace_logging tests.test_live_marketplace_probe -v`
Expected: telemetry/probe interfaces отсутствуют.

- [ ] **Step 3: Реализовать allowlisted telemetry и bounded probe**

Probe принимает одну площадку и одну operation, принудительно использует page 1 и
малый limit, печатает только aggregate outcome/source/count/safe attempts. Exit 0
только для success/valid empty, non-zero для failed. Старые smoke scripts переводятся
на этот безопасный runner и не выводят product/title/ID.

- [ ] **Step 4: Проверить GREEN и output scan**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_safe_marketplace_logging tests.test_live_marketplace_probe -v`
Run: `PYTHON_DOTENV_DISABLED=1 python scripts/live_marketplace_probe.py --marketplace ozon --operation search_products`
Expected: non-zero exit и safe gate message без сетевого вызова.
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .`

- [ ] **Step 5: Commit и push**

```bash
git add src/marketplaces/telemetry.py src/ozon src/wb src/services/deal_pipeline.py scripts tests/test_safe_marketplace_logging.py tests/test_live_marketplace_probe.py
git commit -m "feat(observability): add safe marketplace diagnostics"
git push origin feature/marketplace-captcha-fallback
```

### Task 15: Controlled end-to-end browser/fallback acceptance

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/fixture_server.py`
- Create: `tests/integration/test_browser_fallback_flow.py`
- Create: `tests/integration/test_profile_restart.py`
- Create: `tests/integration/test_retry_ownership.py`
- Create: `tests/integration/test_redirect_and_content_limits.py`
- Create: `scripts/smoke_marketplace_stack.py`

**Interfaces:**
- Consumes: Tasks 3–14 complete runtime.
- Produces: controlled real-browser acceptance suite and mock stack smoke runner.

- [ ] **Step 1: Написать failing fixture-server integration tests**

```python
async def test_challenge_and_extraction_share_page_identity(self) -> None:
    async with fixture_server('challenge-then-result') as origin:
        result, identities = await run_controlled_browser_flow(origin)
    self.assertEqual('success', result.outcome)
    self.assertEqual(1, len(set(identities)))

async def test_retry_budget_is_not_multiplied_by_fallback(self) -> None:
    counters = await run_controlled_retry_flow(max_attempts=2)
    self.assertEqual({'public': 2, 'browser': 2, 'apify': 1}, counters)
```

Fixture server предоставляет clean, valid empty, challenge→result, rate-limit,
redirect, oversized content и delayed response. Test-only allowlist принимает только
ephemeral loopback origin и не меняет production hosts.

- [ ] **Step 2: Наблюдать RED**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests/integration -t . -v`
Expected: fixture/integration helpers отсутствуют.

- [ ] **Step 3: Реализовать controlled server и real-browser flows**

Использовать реальный Playwright browser из Task 13, temporary profile directory и
test-only injected allowlist. Проверить cookie/localStorage после manager restart,
disposal in-memory operation, redirect rejection, content cap и exact retry counts.
Smoke runner поднимает Compose mock mode и проверяет API/bot health/graceful stop без
live marketplaces.

- [ ] **Step 4: Проверить GREEN**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests/integration -t . -v`
Run: `python scripts/smoke_marketplace_stack.py --mode controlled`
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .`

- [ ] **Step 5: Commit и push**

```bash
git add tests/integration scripts/smoke_marketplace_stack.py
git commit -m "test(marketplaces): verify browser fallback end to end"
git push origin feature/marketplace-captcha-fallback
```

### Task 16: Architecture, migration and runbooks

**Files:**
- Modify: `README.md`
- Modify: `IMPLEMENTATION_PLAN.md`
- Modify: `TODO.md`
- Modify: `.env.example`
- Create: `docs/architecture/marketplace-fallback.md`
- Create: `docs/runbooks/local-development.md`
- Create: `docs/runbooks/vps-deployment.md`
- Create: `docs/runbooks/troubleshooting.md`
- Modify: `docs/decisions/smartcaptcha-feasibility.md`
- Create: `tests/test_documented_configuration.py`

**Interfaces:**
- Consumes: финальные public interfaces, Settings fields, scripts и Compose paths из Tasks 3–15.
- Produces: user/developer/operator documentation и executable documentation checks.

- [ ] **Step 1: Написать failing documentation contract tests**

```python
def test_every_marketplace_setting_is_documented(self) -> None:
    documented = read_all_runbooks()
    for name in public_marketplace_setting_names():
        self.assertIn(name.upper(), documented)

def test_runbook_commands_reference_existing_files(self) -> None:
    for path in documented_local_paths():
        self.assertTrue(REPOSITORY_ROOT.joinpath(path).exists(), path)
```

Test должен анализировать published Settings metadata и Markdown command paths, а не
искать одну жёстко заданную строку реализации.

- [ ] **Step 2: Наблюдать RED**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_documented_configuration -v`
Expected: runbooks отсутствуют и settings coverage неполна.

- [ ] **Step 3: Написать architecture и local/VPS runbooks**

Документировать data flow/state machine, structural empty, same-Page invariant,
profile layout, version matrix, vendor update procedure, exact source defaults,
SmartCaptcha scope, Apify gate, local/VPS commands, volumes/permissions, Xvfb,
sandbox/readiness, one-worker requirement и safe error troubleshooting. README можно
изменить с «CAPTCHA не планируется» на утверждённый bounded scope, сохранив warning
об авторизации и правилах площадок.

- [ ] **Step 4: Проверить GREEN, links и hygiene**

Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest tests.test_documented_configuration -v`
Run: `python scripts/repository_hygiene.py --json`
Run: `PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .`

- [ ] **Step 5: Commit и push**

```bash
git add README.md IMPLEMENTATION_PLAN.md TODO.md .env.example docs tests/test_documented_configuration.py
git commit -m "docs(marketplaces): document fallback operations and rollout"
git push origin feature/marketplace-captcha-fallback
```

### Task 17: Final verification, reviews and delivery

**Files:**
- Modify: только файл, для которого воспроизведён failing regression test.
- Update outside Git: Obsidian note `Стартапы/PriceWatcher (Lulu)/Журнал разработки.md`.

**Interfaces:**
- Consumes: вся ветка, task ledger и commits Tasks 1–16.
- Produces: verification evidence, independent whole-branch review и pushed delivery branch.

- [ ] **Step 1: Проверить Git и полный test matrix**

```bash
git status --short --branch
git merge-base --is-ancestor origin/develop HEAD
PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -t .
python -m compileall -q src bot scripts tests
python scripts/repository_hygiene.py --json
```

Changed-file Flake8/pycodestyle запускать по `git diff --name-only
origin/develop...HEAD` с исключением immutable `vendor/ohmycaptcha/**`. Не объявлять
исправленным существующий unrelated full-tree style debt.

- [ ] **Step 2: Проверить vendor boundary, browser integration и Compose**

```bash
.venv-ohmycaptcha/bin/python -m pytest vendor/ohmycaptcha/tests -q
.venv-ohmycaptcha/bin/python -m compileall -q vendor/ohmycaptcha
PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests/integration -t . -v
python scripts/verify_compose.py docker-compose.yml docker-compose.production.yml
docker compose -f docker-compose.yml -f docker-compose.local.yml build api bot
```

Сравнить imported upstream tree с pinned SHA, исключив только local metadata
`UPSTREAM.md` и `THIRD_PARTY_LICENSES.md`; inherited upstream whitespace не менять.

- [ ] **Step 3: Выполнить secret/artifact scans и gated live acceptance**

Не читать `.env`. Проверить tracked filenames, staged diff и controlled logs на
secret-like artifacts. Если token/proxy/actor configuration присутствуют в runtime
environment и `LIVE_MARKETPLACE_TESTS=1` установлен пользователем, выполнить ровно
по одной bounded operation WB/Ozon/Yandex. При отсутствии runtime credentials
зафиксировать live acceptance как открытый внешний пункт без раскрытия значений.

- [ ] **Step 4: Запустить независимое whole-branch review**

Перед review создать package от `origin/develop` до `HEAD`. Reviewer проверяет spec,
task ledger, deferred minors, security boundaries, concurrency, retry ownership,
tests и deployment. Все Critical/Important findings исправляются одним subagent fix
wave с regression tests и одним scoped re-review.

- [ ] **Step 5: Финальный push и отчёт**

```bash
git push origin feature/marketplace-captcha-fallback
git status --short --branch
git log --oneline origin/develop..HEAD
```

Обновить существующий Obsidian журнал результатами, exact commit SHAs, test/build
commands, live acceptance status и оставшимися внешними действиями. Todoist task
создавать только для реального отсутствующего actor/token/access решения; значения
секретов туда не записывать. Merge не выполнять.
