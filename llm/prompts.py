"""Prompt templates rendered with Python format fields."""

from __future__ import annotations

from typing import Any


TEMPLATES: dict[str, str] = {
    "reason": """你是个人 AI OS 的推理核。根据身份、记忆和可用工具，决定是直接回复还是调用工具。

## 身份
{identity_block}

## 检索到的记忆
{memory_block}

## 当前任务
{working_block}

## 视觉/OCR（如有）
{vision_block}

规则：
- 需要外部信息、读文件、搜网、跑代码、看图或截屏时，使用 function calling。
- 不需要工具时直接给出对用户的完整回复。
- 不要编造记忆里没有的用户事实。
- 用用户的语言回答。
""",
    "reply": """你是个人 AI OS 的表达层。把推理结果和工具产出整理成对用户说的话。

## 身份要点
{identity_block}

要求：短句、先结论；不要输出 JSON；不要复述整段系统设定。
若工具失败，说明失败原因和下一步，不要假装成功。
""",
    "persist": """判断这一轮是否值得写入长期记忆。只输出 JSON，不要 Markdown。

用户说：
{user_text}

助手套：
{assistant_text}

规则：
- 寒暄、一次性查询、工具中间态：should_save=false
- 用户身份、偏好、关系、明确「记住」、对助手的纠正：should_save=true
- target 只能是 long_term / self / experience
- kind 只能是 fact / preference / event / relationship / knowledge

输出 schema：
{{"should_save": false, "kind": "fact", "summary": "", "importance": 0.0, "reason": "", "target": "long_term"}}
""",
}


class PromptEngine:
    """Named prompt templates with `{field}` substitution."""

    def render(self, name: str, **kwargs: Any) -> str:
        """Render a named template.

        Args:
            name: Template key (`reason`, `reply`, `persist`).
            **kwargs: Substitution fields. Missing keys become empty strings.

        Returns:
            Rendered prompt text.

        Raises:
            KeyError: If the template name is unknown.
        """
        if name not in TEMPLATES:
            raise KeyError(f"Unknown prompt template: {name}")
        template = TEMPLATES[name]

        class _Blank(dict[str, Any]):
            def __missing__(self, key: str) -> str:
                return ""

        return template.format_map(_Blank(**{k: ("" if v is None else v) for k, v in kwargs.items()}))
