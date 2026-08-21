# API 参考

## 接口列表

- `POST /createTask`
- `POST /getTaskResult`
- `POST /getBalance`
- `GET /api/v1/health`
- `GET /`

所有任务接口都基于 JSON，并遵循 YesCaptcha 风格的异步任务模式。

## `POST /createTask`

### 请求结构

```json
{
  "clientKey": "your-client-key",
  "task": {
    "type": "RecaptchaV3TaskProxyless",
    "websiteURL": "https://antcpt.com/score_detector/",
    "websiteKey": "6LcR_okUAAAAAPYrPe-HK_0RULO1aZM15ENyM-Mf",
    "pageAction": "homepage"
  }
}
```

### 支持的任务类型

#### reCAPTCHA v3

- `RecaptchaV3TaskProxyless`
- `RecaptchaV3TaskProxylessM1`
- `RecaptchaV3TaskProxylessM1S7`
- `RecaptchaV3TaskProxylessM1S9`

必填字段：

- `websiteURL`
- `websiteKey`
- 推荐传入 `pageAction`，该字段会透传给 `grecaptcha.execute()`

#### 图片识别

- `ImageToTextTask`

必填字段：

- `body` — base64 编码后的图片

### `minScore` 兼容性说明

请求模型接受 `minScore` 字段用于兼容，但当前 solver **不会**根据该字段做分数控制。

### 成功响应

```json
{
  "errorId": 0,
  "taskId": "uuid-string"
}
```

### 常见错误响应

```json
{
  "errorId": 1,
  "errorCode": "ERROR_TASK_NOT_SUPPORTED",
  "errorDescription": "Task type 'X' is not supported."
}
```

```json
{
  "errorId": 1,
  "errorCode": "ERROR_TASK_PROPERTY_EMPTY",
  "errorDescription": "websiteURL and websiteKey are required"
}
```

## `POST /getTaskResult`

### 请求

```json
{
  "clientKey": "your-client-key",
  "taskId": "uuid-from-createTask"
}
```

### 处理中响应

```json
{
  "errorId": 0,
  "status": "processing"
}
```

### reCAPTCHA v3 完成响应

```json
{
  "errorId": 0,
  "status": "ready",
  "solution": {
    "gRecaptchaResponse": "token..."
  }
}
```

### `ImageToTextTask` 完成响应

```json
{
  "errorId": 0,
  "status": "ready",
  "solution": {
    "text": "{\"captcha_type\":\"click\", ...}"
  }
}
```

### 未找到任务响应

```json
{
  "errorId": 1,
  "errorCode": "ERROR_NO_SUCH_CAPCHA_ID",
  "errorDescription": "Task not found"
}
```

## `POST /getBalance`

### 请求

```json
{
  "clientKey": "your-client-key"
}
```

### 响应

```json
{
  "errorId": 0,
  "balance": 99999.0
}
```

当前余额为静态兼容性响应。

## `GET /api/v1/health`

示例响应：

```json
{
  "status": "ok",
  "supported_task_types": [
    "RecaptchaV3TaskProxyless",
    "RecaptchaV3TaskProxylessM1",
    "RecaptchaV3TaskProxylessM1S7",
    "RecaptchaV3TaskProxylessM1S9",
    "ImageToTextTask"
  ],
  "browser_headless": true,
  "captcha_model": "gpt-5.4",
  "captcha_multimodal_model": "qwen3.5-2b"
}
```

## `GET /`

根接口会返回服务简述以及运行时已注册的任务类型。
