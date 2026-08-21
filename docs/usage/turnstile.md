# Cloudflare Turnstile Usage

Cloudflare Turnstile is an invisible or widget-based CAPTCHA alternative. The solver visits the target page with Chromium, interacts with the Turnstile widget, and extracts the resulting token from a hidden `cf-turnstile-response` input field.

## Supported task types

| Task type | Description |
|-----------|-------------|
| `TurnstileTaskProxyless` | Standard Turnstile solving |
| `TurnstileTaskProxylessM1` | Same path, alternate tier naming |

## Required fields

| Field | Type | Description |
|-------|------|-------------|
| `websiteURL` | string | Full URL of the page containing the Turnstile widget |
| `websiteKey` | string | The Turnstile `data-sitekey` value |

## Solution field

Unlike reCAPTCHA tasks, the result is returned in `solution.token` (not `solution.gRecaptchaResponse`):

```json
{
  "errorId": 0,
  "status": "ready",
  "solution": {
    "token": "0.ufq5RgSV..."
  }
}
```

## Test targets

Cloudflare provides official dummy site keys for testing:

| Site key | Behavior | URL |
|----------|----------|-----|
| `1x00000000000000000000AA` | Always passes | Any domain |
| `2x00000000000000000000AB` | Always fails | Any domain |
| `3x00000000000000000000FF` | Forces interactive challenge | Any domain |

The React Turnstile demo is a good live test target:

- **URL:** `https://react-turnstile.vercel.app/basic`
- **Site key:** `1x00000000000000000000AA` (test key, always passes)

## Create a task

```bash
curl -X POST http://localhost:8000/createTask \
  -H "Content-Type: application/json" \
  -d '{
    "clientKey": "your-client-key",
    "task": {
      "type": "TurnstileTaskProxyless",
      "websiteURL": "https://react-turnstile.vercel.app/basic",
      "websiteKey": "1x00000000000000000000AA"
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
    "token": "XXXX.DUMMY.TOKEN.XXXX"
  }
}
```

!!! info "Dummy token"
    Cloudflare test keys (`1x00000000000000000000AA`) return the dummy token `XXXX.DUMMY.TOKEN.XXXX`. This is the expected and correct behavior for test sitekeys — the token is accepted by Cloudflare's test infrastructure.

## Acceptance status

| Target | Site key | Status |
|--------|----------|--------|
| `https://react-turnstile.vercel.app/basic` | `1x00000000000000000000AA` | ✅ Dummy token returned |

## Operational notes

- Turnstile auto-solves most of the time without user interaction; the solver polls for the token after page load.
- Real production sitekeys will return a real token (not the dummy token).
- The `TurnstileTaskProxylessM1` type uses the same implementation path.
