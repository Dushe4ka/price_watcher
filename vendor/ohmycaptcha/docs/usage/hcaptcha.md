# hCaptcha Usage

hCaptcha presents a CAPTCHA challenge via an iframe widget. The solver visits the target page with a Playwright-controlled Chromium browser, clicks the hCaptcha checkbox, waits for the challenge to resolve, and extracts the `h-captcha-response` token.

## Supported task type

| Task type | Description |
|-----------|-------------|
| `HCaptchaTaskProxyless` | Browser-based hCaptcha solving |

## Required fields

| Field | Type | Description |
|-------|------|-------------|
| `websiteURL` | string | Full URL of the page containing the captcha |
| `websiteKey` | string | The `data-sitekey` value from the page's HTML |

## Test targets

hCaptcha provides official test keys that produce predictable results:

| URL | Site key | Behavior |
|-----|----------|----------|
| `https://accounts.hcaptcha.com/demo` | `10000000-ffff-ffff-ffff-000000000001` | Always passes (test key) |
| `https://accounts.hcaptcha.com/demo` | `20000000-ffff-ffff-ffff-000000000002` | Enterprise safe-user test |
| `https://demo.hcaptcha.com/` | `10000000-ffff-ffff-ffff-000000000001` | Always passes (test key) |

## Create a task

```bash
curl -X POST http://localhost:8000/createTask \
  -H "Content-Type: application/json" \
  -d '{
    "clientKey": "your-client-key",
    "task": {
      "type": "HCaptchaTaskProxyless",
      "websiteURL": "https://accounts.hcaptcha.com/demo",
      "websiteKey": "10000000-ffff-ffff-ffff-000000000001"
    }
  }'
```

Response:

```json
{
  "errorId": 0,
  "taskId": "uuid-string"
}
```

## Poll for result

```bash
curl -X POST http://localhost:8000/getTaskResult \
  -H "Content-Type: application/json" \
  -d '{
    "clientKey": "your-client-key",
    "taskId": "uuid-from-createTask"
  }'
```

When ready:

```json
{
  "errorId": 0,
  "status": "ready",
  "solution": {
    "gRecaptchaResponse": "P1_eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."
  }
}
```

!!! note "Response field name"
    The token is returned in `solution.gRecaptchaResponse` for YesCaptcha API compatibility, even though hCaptcha natively uses the `h-captcha-response` field name.

## Acceptance status

| Target | Site key | Status | Notes |
|--------|----------|--------|-------|
| `https://accounts.hcaptcha.com/demo` | `10000000-ffff-ffff-ffff-000000000001` | ⚠️ Challenge-dependent | Headless browsers may still receive image challenges |

### Headless browser note

Even with the test site key (`10000000-ffff-ffff-ffff-000000000001`), hCaptcha may present an image challenge when the widget detects a headless browser. The solver clicks the checkbox and polls for a token for up to 30 seconds.

For headless environments, the recommended approach is to use the `HCaptchaClassification` task type to solve the image grid challenge, then inject the token. See [Image Classification](classification.md) for details.

## Image classification (HCaptchaClassification)

For programmatic grid classification without browser automation, see [Image Classification](classification.md).

## Operational notes

- hCaptcha challenges may require more time than reCAPTCHA v2 — the solver waits up to 5 seconds after clicking.
- Real-world sites with aggressive bot detection may require additional fingerprinting improvements.
- Test keys (`10000000-ffff-ffff-ffff-000000000001`) always pass and are useful for flow validation.
