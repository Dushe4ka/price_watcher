# hCaptcha 使用指南

hCaptcha 通过 iframe 组件展示验证码挑战。求解器使用 Playwright 控制的 Chromium 访问目标页面，点击 hCaptcha 复选框，等待挑战完成，提取响应令牌。

## 支持的任务类型

| 任务类型 | 说明 |
|---------|------|
| `HCaptchaTaskProxyless` | 基于浏览器的 hCaptcha 求解 |

## 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `websiteURL` | string | 包含验证码的页面完整 URL |
| `websiteKey` | string | 页面 HTML 中的 `data-sitekey` 值 |

## 测试目标

hCaptcha 提供官方测试密钥：

| URL | Site key | 行为 |
|-----|----------|------|
| `https://accounts.hcaptcha.com/demo` | `10000000-ffff-ffff-ffff-000000000001` | 始终通过（测试密钥） |
| `https://demo.hcaptcha.com/` | `10000000-ffff-ffff-ffff-000000000001` | 始终通过（测试密钥） |

## 创建任务

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

## 轮询结果

就绪时返回：

```json
{
  "errorId": 0,
  "status": "ready",
  "solution": {
    "gRecaptchaResponse": "P1_eyJ0eXAiOiJKV1QiLC..."
  }
}
```

!!! note "字段命名"
    令牌以 `solution.gRecaptchaResponse` 返回，保持与 YesCaptcha API 的兼容性。

## 验收状态

| 目标 | Site key | 状态 | 说明 |
|------|----------|------|------|
| `https://accounts.hcaptcha.com/demo` | `10000000-ffff-ffff-ffff-000000000001` | ⚠️ 依赖挑战类型 | 无头浏览器可能仍会收到图像挑战 |

### 无头浏览器说明

即使使用测试密钥（`10000000-ffff-ffff-ffff-000000000001`），hCaptcha 在检测到无头浏览器时仍可能弹出图像挑战。求解器在点击复选框后最多等待 30 秒以获取令牌。

对于无头环境，推荐使用 `HCaptchaClassification` 任务类型求解图像网格挑战，然后注入令牌。

## 注意事项

- hCaptcha 挑战通常比 reCAPTCHA v2 需要更多时间，求解器点击后最多等待 30 秒。
- 测试密钥（`10000000-ffff-ffff-ffff-000000000001`）在低风险环境下会立即通过。
