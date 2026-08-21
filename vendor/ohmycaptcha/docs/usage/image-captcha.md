# Image CAPTCHA Usage

## Task type

- `ImageToTextTask`

## Request

```json
{
  "clientKey": "your-client-key",
  "task": {
    "type": "ImageToTextTask",
    "body": "<base64-encoded-image>"
  }
}
```

## Implementation notes

The image solver is implemented in `src/services/recognition.py` and is inspired by Argus-style structured multimodal annotation.

Current behavior:

- image input is resized to **1440×900**
- the model is prompted to classify the captcha into structured action types
- the normalized coordinate space starts at `(0, 0)` in the top-left corner

Supported response styles in the prompt:

- `click`
- `slide`
- `drag_match`

## Result shape

The current API returns the structured model output serialized as a string in `solution.text`.

Example:

```json
{
  "errorId": 0,
  "status": "ready",
  "solution": {
    "text": "{\"captcha_type\":\"slide\",\"drag_distance\":270}"
  }
}
```

## Backend compatibility

The multimodal path is designed for **OpenAI-compatible** APIs. This makes it suitable for hosted or self-hosted backends as long as they expose compatible image-capable chat completion behavior.

Accuracy depends heavily on the selected model and provider implementation.
