# 常见问题

## 这能完全替代 YesCaptcha 吗？

不能。它实现的是本仓库所支持任务类型对应的 YesCaptcha 风格 API，而不是对所有商业平台功能的完整覆盖。

## `minScore` 能保证目标 reCAPTCHA 分数吗？

不能。请求模型里保留了 `minScore` 字段用于兼容，但当前 solver 不会根据它做分数控制。

## 可以使用本地或自托管多模态模型吗？

可以，前提是它们提供支持图像输入的 OpenAI-compatible API。

## `ImageToTextTask` 返回的是纯 OCR 文本吗？

不一定。当前实现会把结构化识别结果序列化后放入 `solution.text`。

## 任务状态会持久化吗？

不会。任务状态保存在内存中，并会在 TTL 到期后清理。

## 哪些因素会影响 reCAPTCHA v3 结果？

常见因素包括 IP 质量、浏览器指纹、目标站行为、`pageAction` 是否正确，以及运行环境本身。
