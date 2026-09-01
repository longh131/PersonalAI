# Personal AI OS（织 / Aion）

运行在 Windows 11 上的个人人工智能操作系统内核：人格 + 五层记忆 + LangGraph 推理 + 工具行动 + 语音/视觉待机。

## 一键运行

推荐 **Python 3.11 或 3.12**。3.13/3.14 上部分语音/向量包可能没有现成 wheel，`run.bat` 会自动降级为键盘对话。
2. 双击项目根目录的 **`run.bat`**。日常启动只读本机缓存，不再访问 Hugging Face。
3. 第一次会创建 `.venv`、安装依赖。若要补齐 **SenseVoice** 识别模型和向量模型：先保证能访问 GitHub 或 Hugging Face，再运行 `run.bat --download-models`。
4. 若尚未配置密钥，窗口会提示你粘贴 **DeepSeek API Key**（写入 `.env`）。
5. 看到「状态：待机」后即可说话或打字。

```text
待机时不录音。/listen 或 Ctrl+Shift+L 开始听
说完停顿约 1 秒即提交；语音进则语音出，打字只回文字
播报时开口或热键可打断
回答后 10 秒内可直接再说；说「退出」立即待机
/shot      截屏并 OCR / 识别
/quit      退出
把图片路径粘贴进来，会扫描识别
```

只想用键盘、不加载麦克风模型：

```bat
run.bat --text-only
```

健康检查（不进入对话）：

```bat
run.bat --self-check
```

一次性把 SenseVoice 和向量模型下载到本机：

```bat
run.bat --download-models
```

PowerShell 等价命令：`.\run.ps1`

## 对话与记忆

- 每一轮用户/助手发言都会记入会话日志（`conversation_turns`）。
- 寒暄不会写入长期记忆。你说「记住…」、自我介绍、偏好、纠正助手时会写入长期记忆。
- 之后可以直接问「我叫什么」「我喜欢什么」，由向量检索召回。

## 需要你提供的

| 项目 | 是否必须 | 说明 |
|---|---|---|
| DeepSeek API Key | **必须** | https://platform.deepseek.com |
| 麦克风 + 扬声器 | 语音功能需要 | 没有也能键盘聊 |
| 第一次联网 | 建议 | 下载依赖、SenseVoice、embedding 模型 |

没有 Key 时把密钥发给程序窗口即可，不必先手改文件。若你更想用手写：复制 `.env.template` 为 `.env`，填入 `DEEPSEEK_API_KEY=`。

## 手动安装（可选）

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

运行测试（不消耗 API）：

```bat
.venv\Scripts\activate
pytest
```

## 目录

- `core/` LangGraph 六节点引擎
- `identity/` + `config/identity.yaml` 人格
- `memory/` 五层记忆（SQLite + 向量）
- `llm/` DeepSeek 网关（预留 GPT / Claude）
- `tools/` 文件 / 搜索 / 代码 / 截屏 / 识图
- `senses/` 语音待机、SenseVoice 识别、edge-tts 播报、截屏 OCR

人格与显示名不要改代码，改 `config/identity.yaml` 的 `identity.name`。

播报默认用 edge-tts「晓晓」（需联网），断网自动退回系统慧慧。识别默认 SenseVoice（本地）。`.env` 里 `TTS_VOICE` 可改成 `zh-CN-YunxiNeural`（云希）或 `zh-CN-YunyangNeural`（云扬）。
