# 图像分类使用指南

图像分类任务将一张或多张验证码图片发送给 OpenAI-compatible 视觉模型，返回匹配格子的索引或布尔值答案。无需浏览器自动化，这些均为纯视觉模型 API 调用。

## 支持的任务类型

| 任务类型 | 说明 |
|---------|------|
| `HCaptchaClassification` | hCaptcha 3×3 网格——返回匹配格子索引 |
| `ReCaptchaV2Classification` | reCAPTCHA v2 3×3/4×4 网格——返回匹配格子索引 |
| `FunCaptchaClassification` | FunCaptcha 2×3 网格——返回正确格子索引 |
| `AwsClassification` | AWS 验证码图像选择 |

## 返回字段

| 任务类型 | 返回字段 | 示例 |
|---------|---------|------|
| `HCaptchaClassification` | `objects` 或 `answer` | `[0, 2, 5]` 或 `true` |
| `ReCaptchaV2Classification` | `objects` | `[0, 3, 6]` |
| `FunCaptchaClassification` | `objects` | `[4]` |
| `AwsClassification` | `objects` | `[1]` |

## HCaptchaClassification 示例

```json
{
  "clientKey": "your-client-key",
  "task": {
    "type": "HCaptchaClassification",
    "queries": ["<base64-image-1>", "<base64-image-2>", "<base64-image-3>"],
    "question": "Please click each image containing a bicycle"
  }
}
```

`queries` 字段接受 base64 编码的图片列表（每个格子一张）。

## ReCaptchaV2Classification 示例

```json
{
  "clientKey": "your-client-key",
  "task": {
    "type": "ReCaptchaV2Classification",
    "image": "<base64-encoded-grid-image>",
    "question": "Select all images with traffic lights"
  }
}
```

`image` 字段为完整的 reCAPTCHA 网格图片（3×3 = 9 格，或 4×4 = 16 格）。格子编号从 0 开始，从左到右、从上到下。

## FunCaptchaClassification 示例

```json
{
  "clientKey": "your-client-key",
  "task": {
    "type": "FunCaptchaClassification",
    "image": "<base64-encoded-grid-image>",
    "question": "Pick the image that shows a boat facing left"
  }
}
```

## 注意事项

- 所有分类任务均通过 `CAPTCHA_MULTIMODAL_MODEL`（默认：`qwen3.5-2b`）指定的视觉模型处理。
- 模型准确性取决于所配置的视觉模型质量。
- 图片无需预先缩放，求解器内部会处理归一化。
