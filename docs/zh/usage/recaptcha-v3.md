# reCAPTCHA v3 使用指南

## 验收目标

本仓库使用以下目标完成了验证：

- URL：`https://antcpt.com/score_detector/`
- site key：`6LcR_okUAAAAAPYrPe-HK_0RULO1aZM15ENyM-Mf`

## 创建任务

```bash
curl -X POST http://localhost:8000/createTask \
  -H "Content-Type: application/json" \
  -d '{
    "clientKey": "your-client-key",
    "task": {
      "type": "RecaptchaV3TaskProxyless",
      "websiteURL": "https://antcpt.com/score_detector/",
      "websiteKey": "6LcR_okUAAAAAPYrPe-HK_0RULO1aZM15ENyM-Mf",
      "pageAction": "homepage"
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

当任务完成时，你会收到 `solution.gRecaptchaResponse`。

## 当前代码库的验收结果

一次本地验收已经成功完成以下流程：

- 启动服务
- 创建任务
- 轮询到 `ready`
- 返回非空 token

## 运行注意事项

- 返回 token 不代表可以保证指定 score。
- 目标站行为可能随时间变化。
- IP 质量与浏览器环境会影响结果。
- 当前仓库中，所有已注册的 reCAPTCHA v3 变体共享同一套内部 solver 路径。
