# 架构说明

Personal AI OS = **人格 + 记忆 + 推理 + 行动 + 感官**。第一阶段跑在这台 Windows 11 笔记本上。

## 一次请求怎么走

```text
感官（键盘 / 语音 / 截屏）
  → parse_input        解析意图
  → weave_context      注入人格 YAML + 五层记忆（+ 可选 OCR）
  → reason_decide      DeepSeek 决定：直接答，或调用工具
  → execute_tools      本地工具（可多轮，最多 8 步）
  → reason_decide      看工具结果，决定是否再调
  → generate_reply     生成对用户的短句
  → update_memory      会话日志；值得长期记的才写入向量库
  → 感官（文字 或 edge-tts 播报）
```

引擎在 `core/`：`engine.py` 生命周期，`graph.py` 组图，`nodes.py` 六个节点，`state.py` 为 TypedDict 状态。

## 模块

| 目录 | 职责 |
|---|---|
| `config/` | `.env` 设置、`identity.yaml`、Hugging Face 离线策略 |
| `identity/` | 人格六段：身份、使命、性格、用户模型、规则、进化 |
| `memory/` | 短时窗口、工作任务、长期事实、经验、自我认知、已铺能力路；SQLite + 向量 |
| `llm/` | DeepSeek 网关；GPT / Claude 适配器预留；提示词与解析 |
| `tools/` | 读文件、搜文件、网页搜索、受限代码、截屏、识图、窗口、剪贴板、打开/切换应用、Outlook 日历、本机提醒、简报、后台任务、音量/锁屏/电源、缺能力找路 |
| `senses/` | 待机状态机、唤醒词、全局热键、托盘状态灯、SenseVoice、edge-tts、截屏 OCR |
| `main.py` | 入口：`--download-models` / `--self-check` / `--text-only` / 待机 |

MCP 桥在 `tools/mcp.py`：未配置 `MCP_SERVER_URL` 时不会假装有外部工具。

## 语音状态机

待机默认开着麦克风，但只做**唤醒检测**（VAD → 转写 → 匹配「你好小派 / 嗨小派」等），不把闲聊送进 LLM。

进入会话后：停顿约 1 秒结束一句；播报时开麦以便打断；播完再听约 10 秒；超时或「退出」回待机。`/quit` 才结束进程。

识别优先级：SenseVoice → Whisper → 在线备用。播报优先级：edge-tts → 系统 SAPI。

## 数据落在哪

| 路径 | 内容 |
|---|---|
| `.env` | 密钥与可调参数（不要提交） |
| `data/personal_ai.db` | 会话、长期记忆、任务、经验 |
| `models/sensevoice/` | 本地中文识别模型 |
| `config/identity.yaml` | 人格与显示名 |
| `logs/` | 运行日志 |

日常 `run.bat` 不联网下模型。模型更新用 `run.bat --download-models`。
