# 快速开始

## 环境要求

- Python 3.10+
- 通过 Playwright 安装 Chromium
- 具备访问以下资源的网络能力：
  - 目标网站
  - 你配置的 OpenAI-compatible 模型接口

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
```

## 环境变量

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `CLIENT_KEY` | 客户端鉴权密钥 | 未设置 |
| `CAPTCHA_BASE_URL` | OpenAI-compatible API 地址 | `https://your-openai-compatible-endpoint/v1` |
| `CAPTCHA_API_KEY` | 模型接口密钥 | 未设置 |
| `CAPTCHA_MODEL` | 强文本模型 | `gpt-5.4` |
| `CAPTCHA_MULTIMODAL_MODEL` | 多模态模型 | `qwen3.5-2b` |
| `CAPTCHA_RETRIES` | 重试次数 | `3` |
| `CAPTCHA_TIMEOUT` | 模型超时（秒） | `30` |
| `BROWSER_HEADLESS` | 是否无头运行 Chromium | `true` |
| `BROWSER_TIMEOUT` | 浏览器超时（秒） | `30` |
| `SERVER_HOST` | 监听地址 | `0.0.0.0` |
| `SERVER_PORT` | 监听端口 | `8000` |

## 启动服务

```bash
export CLIENT_KEY="your-client-key"
export CAPTCHA_BASE_URL="https://your-openai-compatible-endpoint/v1"
export CAPTCHA_API_KEY="your-api-key"
export CAPTCHA_MODEL="gpt-5.4"
export CAPTCHA_MULTIMODAL_MODEL="qwen3.5-2b"
python main.py
```

## 验证启动

### 根接口

```bash
curl http://localhost:8000/
```

### 健康检查

```bash
curl http://localhost:8000/api/v1/health
```

健康检查响应中应包含已注册任务类型以及当前运行时模型配置。

## 本地 / 自托管模型支持

图片识别路径基于 **OpenAI-compatible API** 设计。因此，只要你的后端具备兼容的 chat-completions 语义并支持图像输入，就可以把 `CAPTCHA_BASE_URL` 指向托管服务、内部网关或本地/自托管多模态网关。

文档采用通用兼容性表述，而不是对每一种模型服务栈做完整验证承诺。
