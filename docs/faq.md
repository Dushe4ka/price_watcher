# FAQ

## Does this fully replace YesCaptcha?

No. It implements a YesCaptcha-style API surface for the task types available in this repository. It should not be described as full vendor parity.

## Does `minScore` guarantee a target reCAPTCHA score?

No. The request model accepts `minScore` for compatibility, but the current solver does not enforce score targeting.

## Can I use local or self-hosted multimodal models?

Yes, if they expose an OpenAI-compatible API with image-capable chat completion behavior.

## Does `ImageToTextTask` return plain OCR text?

Not necessarily. The current implementation returns structured recognition output serialized into `solution.text`.

## Is task state persistent?

No. Task state is stored in memory and cleaned up after the configured TTL window.

## What affects reCAPTCHA v3 results?

Common factors include IP quality, browser fingerprint, target site behavior, page action correctness, and runtime environment.
