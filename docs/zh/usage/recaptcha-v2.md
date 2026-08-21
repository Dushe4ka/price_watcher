# reCAPTCHA v2 使用指南

reCAPTCHA v2 向用户展示"我不是机器人"复选框。求解器使用真实 Chromium 浏览器访问目标页面，点击复选框，并提取生成的 `gRecaptchaResponse` 令牌。

## 支持的任务类型

| 任务类型 | 说明 |
|---------|------|
| `NoCaptchaTaskProxyless` | 标准 reCAPTCHA v2 复选框 |
| `RecaptchaV2TaskProxyless` | 同上，备用命名 |
| `RecaptchaV2EnterpriseTaskProxyless` | reCAPTCHA v2 企业版 |

## 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `websiteURL` | string | 包含验证码的页面完整 URL |
| `websiteKey` | string | 页面 HTML 中的 `data-sitekey` 值 |
| `isInvisible` | bool | 可选。不可见 reCAPTCHA 设为 `true` |

## 测试目标

Google 官方 Demo 页面适合验收测试：

- **URL：** `https://www.google.com/recaptcha/api2/demo`
- **Site key：** `6Le-wvkSAAAAAPBMRTvw0Q4Muexq9bi0DJwx_mJ-`

## 创建任务

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

## 轮询结果

```bash
curl -X POST http://localhost:8000/getTaskResult \
  -H "Content-Type: application/json" \
  -d '{
    "clientKey": "your-client-key",
    "taskId": "uuid-from-createTask"
  }'
```

就绪时返回：

```json
{
  "errorId": 0,
  "status": "ready",
  "solution": {
    "gRecaptchaResponse": "03AGdBq24..."
  }
}
```

## 验收状态

| 目标 | 状态 | 说明 |
|------|------|------|
| `https://www.google.com/recaptcha/api2/demo` | ⚠️ 音频挑战路径 | Google 检测到无头浏览器 |

### 无头浏览器检测

Google 的风险分析引擎可靠地检测到无头 Chromium，会弹出图像挑战而非直接签发令牌。求解器实现了音频挑战回退方案：

1. 点击复选框——Google 弹出挑战对话框。
2. 在挑战对话框中点击音频按钮。
3. 下载 MP3 音频文件。
4. 通过配置的 `CAPTCHA_MODEL` 端点进行转录。
5. 提交转录文本以获得令牌。

### 推荐的集成方案

对于生产环境中可靠的 reCAPTCHA v2 求解，建议使用**分类任务**方案：

1. 使用 Playwright 从页面提取挑战图像网格。
2. 将网格图片发送给 `ReCaptchaV2Classification`，附上挑战问题。
3. 使用返回的格子索引以编程方式点击匹配的格子。

## 注意事项

- 令牌有效期约 120 秒，请尽快提交。
- `RecaptchaV2EnterpriseTaskProxyless` 类型使用相同的浏览器路径。
- 在侵入性较低的网站（非 Google 自己的 Demo），复选框点击可能直接成功而无需挑战。
