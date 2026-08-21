# 图片验证码使用指南

## 任务类型

- `ImageToTextTask`

## 请求示例

```json
{
  "clientKey": "your-client-key",
  "task": {
    "type": "ImageToTextTask",
    "body": "<base64-encoded-image>"
  }
}
```

## 实现说明

图片 solver 位于 `src/services/recognition.py`，采用受 Argus 启发的结构化多模态标注思路。

当前行为：

- 输入图片会被缩放到 **1440×900**
- 模型会被提示识别验证码类型并输出结构化结果
- 归一化坐标空间以左上角 `(0, 0)` 为原点

提示词当前支持的结构化类型包括：

- `click`
- `slide`
- `drag_match`

## 返回结构

当前 API 会把模型输出的结构化 JSON 序列化后放在 `solution.text` 中返回。

示例：

```json
{
  "errorId": 0,
  "status": "ready",
  "solution": {
    "text": "{\"captcha_type\":\"slide\",\"drag_distance\":270}"
  }
}
```

## 后端兼容性

多模态路径面向 **OpenAI-compatible** 接口设计，因此只要后端支持图像输入并具备兼容的 chat completion 行为，就可以接托管或自托管服务。

实际准确率会强烈依赖所选模型与供应商实现质量。
