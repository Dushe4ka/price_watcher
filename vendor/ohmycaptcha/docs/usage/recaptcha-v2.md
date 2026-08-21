# reCAPTCHA v2 Usage

reCAPTCHA v2 presents users with an "I'm not a robot" checkbox. The solver visits the target page with a real Chromium browser, clicks the checkbox, and extracts the resulting `gRecaptchaResponse` token.

## Supported task types

| Task type | Description |
|-----------|-------------|
| `NoCaptchaTaskProxyless` | Standard reCAPTCHA v2 checkbox |
| `RecaptchaV2TaskProxyless` | Same as above, alternate naming |
| `RecaptchaV2EnterpriseTaskProxyless` | reCAPTCHA v2 Enterprise variant |

## Required fields

| Field | Type | Description |
|-------|------|-------------|
| `websiteURL` | string | Full URL of the page containing the captcha |
| `websiteKey` | string | The `data-sitekey` value from the page's HTML |
| `isInvisible` | bool | Optional. Set `true` for invisible reCAPTCHA |

## Test target

The official Google demo page is suitable for acceptance validation:

- **URL:** `https://www.google.com/recaptcha/api2/demo`
- **Site key:** `6Le-wvkSAAAAAPBMRTvw0Q4Muexq9bi0DJwx_mJ-`

## Create a task

```bash
curl -X POST http://localhost:8000/createTask \
  -H "Content-Type: application/json" \
  -d '{
    "clientKey": "your-client-key",
    "task": {
      "type": "NoCaptchaTaskProxyless",
      "websiteURL": "https://www.google.com/recaptcha/api2/demo",
      "websiteKey": "6Le-wvkSAAAAAPBMRTvw0Q4Muexq9bi0DJwx_mJ-"
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

When ready, you receive:

```json
{
  "errorId": 0,
  "status": "ready",
  "solution": {
    "gRecaptchaResponse": "03AGdBq24..."
  }
}
```

## Invisible reCAPTCHA

For pages using invisible reCAPTCHA (no visible checkbox), add `"isInvisible": true`. The solver will call `grecaptcha.execute()` directly instead of clicking the checkbox:

```bash
curl -X POST http://localhost:8000/createTask \
  -H "Content-Type: application/json" \
  -d '{
    "clientKey": "your-client-key",
    "task": {
      "type": "NoCaptchaTaskProxyless",
      "websiteURL": "https://www.google.com/recaptcha/api2/demo",
      "websiteKey": "6Le-wvkSAAAAAPBMRTvw0Q4Muexq9bi0DJwx_mJ-",
      "isInvisible": true
    }
  }'
```

## Acceptance status

| Target | Status | Notes |
|--------|--------|-------|
| `https://www.google.com/recaptcha/api2/demo` | ⚠️ Audio challenge path | Google detects headless browsers |

### Headless browser detection

Google's risk analysis engine reliably detects headless Chromium and presents a visual image challenge rather than issuing a token directly. The solver implements an audio challenge fallback:

1. Click the checkbox — Google presents a challenge dialog.
2. Click the audio button in the challenge dialog.
3. Download the MP3 audio file.
4. Transcribe via the configured `CAPTCHA_MODEL` endpoint.
5. Submit the transcript to receive the token.

!!! warning "Audio transcription requirement"
    The audio challenge path requires a model endpoint capable of processing audio. The standard `CAPTCHA_MODEL` must support audio/speech input. Accuracy and availability depend on the configured endpoint.

### Recommended integration path

For reliable reCAPTCHA v2 solving in production, consider using the **classification task** approach:

1. Extract the challenge image grid from the page using Playwright.
2. Send the grid image to `ReCaptchaV2Classification` with the challenge question.
3. Use the returned cell indices to programmatically click the matching tiles.

See [Image Classification](classification.md) for details.

## Operational notes

- Token validity is approximately 120 seconds; submit promptly.
- The `RecaptchaV2EnterpriseTaskProxyless` type uses the same browser path.
- On less aggressive sites (not Google's own demo), the checkbox click may succeed without triggering a challenge.
