"""Six LangGraph nodes plus the reason/tool router."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from core.state import AgentState
from identity.loader import IdentityLoader
from llm.gateway import LLMGateway
from memory.manager import MemoryManager, PersistDecision
from tools.builtin import looks_like_image_path
from tools.registry import ToolRegistry

TOOL_PROGRESS_LABELS = {
    "read_file": "读取文件",
    "search_files": "搜索文件",
    "web_search": "搜索网页",
    "execute_code": "执行代码",
    "capture_screen": "截屏",
    "analyze_image": "识别图片",
    "foreground_window": "查看当前窗口",
    "clipboard_text": "读取剪贴板",
    "list_windows": "列出窗口",
    "open_app": "打开应用",
    "focus_window": "切换窗口",
    "calendar_agenda": "查看日程",
    "set_reminder": "设定提醒",
    "list_reminders": "查看提醒",
    "system_status": "查看舰况",
    "forget_memory": "忘掉记忆",
    "correct_memory": "纠正记忆",
    "upsert_entity": "记下人物项目",
    "list_entities": "查看人物项目",
    "forget_entity": "忘掉人物项目",
    "set_protocol": "设置协议",
    "list_protocols": "查看协议",
    "daily_briefing": "整理简报",
    "background_task": "排队后台任务",
    "set_volume": "调节音量",
    "set_dnd": "设置勿扰",
    "lock_pc": "锁定电脑",
    "delete_file": "删除文件",
    "power_action": "电源操作",
    "_reply": "整理答复",
}


class GraphNodes:
    """Node implementations closed over engine dependencies."""

    def __init__(
        self,
        memory: MemoryManager,
        llm: LLMGateway,
        tools: ToolRegistry,
        identity: IdentityLoader,
        max_tool_iterations: int,
        vision: Any | None = None,
        on_progress: Any | None = None,
    ) -> None:
        self.memory = memory
        self.llm = llm
        self.tools = tools
        self.identity = identity
        self.max_tool_iterations = max_tool_iterations
        self.vision = vision
        self.on_progress = on_progress

    async def parse_input(self, state: AgentState) -> dict[str, Any]:
        """Normalize the utterance and detect coarse intent / image paths."""
        text = (state.get("user_input") or "").strip()
        intent: dict[str, Any] = {"kind": "chat", "force_remember": False}
        lowered = text.lower()
        if any(token in text for token in ("记住", "记下", "别忘了", "记一下")):
            intent["force_remember"] = True
            intent["kind"] = "remember"
        if any(token in text for token in ("截屏", "截图", "看看屏幕", "看下屏幕", "scan screen")):
            intent["kind"] = "screen"
        if any(token in text for token in ("今天简报", "早报", "晨间简报", "我要走了", "下班了", "离开简报", "我先走了")):
            intent["kind"] = "briefing"
        if looks_like_image_path(text) or state.get("input_type") == "image":
            intent["kind"] = "image"
            intent["image_path"] = text.strip().strip('"')
        if any(token in lowered for token in ("搜索", "search", "搜一下")):
            intent["kind"] = "search"
        logger.info("parse_input kind={}", intent["kind"])
        return {
            "parsed_intent": intent,
            "image_path": str(intent.get("image_path") or state.get("image_path") or ""),
            "messages": [{"role": "user", "content": text}],
        }

    async def weave_context(self, state: AgentState) -> dict[str, Any]:
        """Inject identity YAML and retrieve five-layer memory plus optional OCR."""
        query = state.get("user_input") or ""
        session_id = state.get("session_id") or ""
        bundle = await self.memory.weave(query, session_id)
        identity_block = self.identity.render(
            memory_user_model=bundle.identity_overlay,
            self_knowledge=bundle.self_knowledge,
        )
        vision_block = state.get("vision_block") or ""
        image_path = state.get("image_path") or ""
        if not vision_block and image_path and self.vision is not None:
            try:
                vision_block = await self.vision.analyze(Path(image_path))
            except Exception as exc:  # noqa: BLE001
                vision_block = f"图像分析失败：{exc}"
        hits = [
            {"layer": hit.layer, "content": hit.content, "score": hit.score}
            for hit in (bundle.long_term_hits + bundle.experience_hits + bundle.self_hits)
        ]
        logger.info("weave_context memories={}", len(hits))
        return {
            "identity_block": identity_block,
            "retrieved_memories": hits,
            "working_snapshot": bundle.working_snapshot,
            "vision_block": vision_block,
        }

    async def reason_decide(self, state: AgentState) -> dict[str, Any]:
        """Ask the LLM whether to call tools or answer directly."""
        memory_block = "\n".join(
            f"- [{item.get('layer')}] {item.get('content')}"
            for item in (state.get("retrieved_memories") or [])[:8]
        ) or "（无）"
        working = state.get("working_snapshot") or {}
        working_block = json.dumps(working, ensure_ascii=False) if working else "（无活动任务）"
        system = self.llm.render(
            "reason",
            identity_block=state.get("identity_block") or "",
            memory_block=memory_block,
            working_block=working_block,
            vision_block=state.get("vision_block") or "（无）",
        )
        history = [
            item
            for item in (self.memory.short_term.window() + list(state.get("messages") or []))
            if item.get("role") in {"user", "assistant", "tool"}
        ]
        # The latest user turn is already in messages; avoid duplicating it.
        history_without_last_user = history[:-1] if history and history[-1].get("role") == "user" else history
        user_text = state.get("user_input") or ""
        if state.get("tool_results"):
            user_text = "工具已返回，请根据工具结果继续：要么再调用工具，要么给出最终答复。"
        try:
            response = await self.llm.reason(
                system,
                user_text,
                history=history_without_last_user[-12:],
                tools=self.tools.list_openai_tools(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("reason_decide LLM error")
            return {
                "reasoning": "",
                "decision": "respond",
                "tool_calls": [],
                "iteration": int(state.get("iteration") or 0),
                "error": str(exc),
            }
        tool_calls = self.llm.parser.parse_tool_calls(response.raw)
        if not tool_calls and response.tool_calls:
            tool_calls = self.llm.parser.parse_tool_calls({"tool_calls": response.tool_calls})
        intent = state.get("parsed_intent") or {}
        if intent.get("kind") == "screen" and not tool_calls and int(state.get("iteration") or 0) == 0:
            tool_calls = [{"id": "auto_screen", "name": "capture_screen", "arguments": {}}]
        if intent.get("kind") == "image" and state.get("image_path") and not tool_calls:
            tool_calls = [
                {
                    "id": "auto_image",
                    "name": "analyze_image",
                    "arguments": {"path": state["image_path"]},
                }
            ]
        if intent.get("kind") == "briefing" and not tool_calls and int(state.get("iteration") or 0) == 0:
            user = str(state.get("user_input") or "")
            brief_kind = "leaving" if any(token in user for token in ("走了", "下班", "离开")) else "morning"
            if any(token in user for token in ("早报", "晨间")):
                brief_kind = "morning"
            tool_calls = [
                {"id": "auto_brief", "name": "daily_briefing", "arguments": {"kind": brief_kind}}
            ]
        decision = "use_tool" if tool_calls else "respond"
        iteration = int(state.get("iteration") or 0)
        if decision == "use_tool":
            iteration += 1
        extra_messages: list[dict[str, Any]] = []
        if response.content and decision == "respond":
            extra_messages.append({"role": "assistant", "content": response.content})
        logger.info("reason_decide decision={} tools={}", decision, [c.get("name") for c in tool_calls])
        return {
            "reasoning": response.content,
            "decision": decision,
            "tool_calls": tool_calls,
            "iteration": iteration,
            "messages": extra_messages,
            "error": None,
        }

    def route_after_reason(self, state: AgentState) -> str:
        """Conditional edge: tools (bounded) or final reply."""
        decision = state.get("decision") or "respond"
        calls = state.get("tool_calls") or []
        iteration = int(state.get("iteration") or 0)
        if decision == "use_tool" and calls and iteration <= self.max_tool_iterations:
            return "execute_tools"
        return "generate_reply"

    async def execute_tools(self, state: AgentState) -> dict[str, Any]:
        """Run requested tools and append tool messages for the next reason hop."""
        calls = state.get("tool_calls") or []
        for call in calls:
            name = str(call.get("name") or "")
            args = call.get("arguments") or {}
            preview = ""
            if isinstance(args, dict):
                preview = str(next(iter(args.values()), "") or "")[:80]
            if self.on_progress is not None:
                label = TOOL_PROGRESS_LABELS.get(name, name)
                await self.on_progress(name, f"{label} {preview}".strip())
            await self._touch_working_task(state, "in_progress", name)
        results = await self.tools.execute_many(calls)
        messages: list[dict[str, Any]] = []
        if calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": state.get("reasoning") or "",
                    "tool_calls": [
                        {
                            "id": call.get("id"),
                            "type": "function",
                            "function": {
                                "name": call.get("name"),
                                "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False),
                            },
                        }
                        for call in calls
                    ],
                }
            )
        for result in results:
            body = result.get("output") or result.get("error") or ""
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": result.get("tool_call_id"),
                    "name": result.get("name"),
                    "content": body,
                }
            )
        logger.info("execute_tools n={}", len(results))
        return {"tool_results": results, "tool_calls": [], "messages": messages}

    async def generate_reply(self, state: AgentState) -> dict[str, Any]:
        """Produce the user-facing reply, including LLM errors and tool summaries."""
        if self.on_progress is not None and state.get("tool_results"):
            await self.on_progress("_reply", "正在整理答复")
        if state.get("error") and not (state.get("reasoning") or state.get("tool_results")):
            text = f"这一轮推理失败了：{state['error']}。请检查网络或 API Key 后再说一次。"
            return {"response": text, "messages": [{"role": "assistant", "content": text}]}
        tool_used = bool(state.get("tool_results"))
        existing = (state.get("reasoning") or "").strip()
        if existing and not tool_used and not existing.startswith("{"):
            return {"response": existing, "messages": []}
        memory_brief = "\n".join(
            f"- {item.get('content')}" for item in (state.get("retrieved_memories") or [])[:5]
        )
        tool_brief = "\n".join(
            f"- {item.get('name')}: {(item.get('output') or item.get('error') or '')[:800]}"
            for item in (state.get("tool_results") or [])
        )
        system = self.llm.render("reply", identity_block=state.get("identity_block") or "")
        user = (
            f"用户原话：{state.get('user_input')}\n\n"
            f"视觉：{state.get('vision_block') or '无'}\n\n"
            f"记忆要点：\n{memory_brief or '无'}\n\n"
            f"工具结果：\n{tool_brief or '无'}\n\n"
            f"先前推理草稿：\n{existing or '无'}"
        )
        try:
            response = await self.llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.5,
            )
            text = response.content.strip() or existing or "我在，请再说具体一点。"
        except Exception as exc:  # noqa: BLE001
            logger.exception("generate_reply LLM error")
            text = existing or f"生成回复时出错：{exc}"
        if tool_used:
            await self._touch_working_task(state, "completed", "done")
        return {"response": text, "messages": [{"role": "assistant", "content": text}]}

    async def _touch_working_task(self, state: AgentState, status: str, step: str) -> None:
        """Create or update the in-flight working-memory task for this turn."""
        session_id = state.get("session_id") or ""
        if not session_id or self.memory.working is None:
            return
        try:
            active = await self.memory.working.get_active(session_id)
            title = (state.get("user_input") or "任务")[:40]
            payload: dict[str, Any] = {
                "title": (active or {}).get("title") or title,
                "goal": (active or {}).get("goal") or (state.get("user_input") or ""),
                "status": status,
                "current_step": step,
            }
            if active:
                payload["id"] = active["id"]
            await self.memory.upsert_task(session_id, payload)
        except Exception as exc:  # noqa: BLE001
            logger.debug("working task update skipped: {}", exc)

    async def update_memory(self, state: AgentState) -> dict[str, Any]:
        """Always log the turn; persist long-term memory only when warranted."""
        session_id = state.get("session_id") or ""
        user_text = state.get("user_input") or ""
        reply = state.get("response") or ""
        await self.memory.remember_turn(session_id, "user", user_text)
        await self.memory.remember_turn(session_id, "assistant", reply)
        heuristic = self.memory.heuristic_decision(user_text, reply)
        decision = heuristic
        intent = state.get("parsed_intent") or {}
        if heuristic.should_save is False and heuristic.reason == "needs model judgment":
            prompt = self.llm.render("persist", user_text=user_text, assistant_text=reply)
            try:
                judged = await self.llm.chat(
                    [{"role": "system", "content": prompt}, {"role": "user", "content": "请输出 JSON。"}],
                    temperature=0.0,
                )
                decision = self.llm.parser.parse_persist_decision(judged.content)
            except Exception as exc:  # noqa: BLE001
                logger.warning("persist judgment skipped: {}", exc)
                decision = heuristic
        if intent.get("force_remember"):
            decision = PersistDecision(True, "fact", user_text, 0.9, "forced by user", "long_term")
        if state.get("tool_results") and any(
            item.get("name") in {"web_search", "execute_code", "analyze_image", "capture_screen"}
            for item in state.get("tool_results") or []
        ):
            if decision.should_save and decision.target == "long_term":
                pass
            elif not decision.should_save and heuristic.reason not in {"greeting", "too short", "empty"}:
                # Keep experience for non-trivial tool use even when facts are not worth storing.
                if any(not item.get("ok", True) for item in state.get("tool_results") or []):
                    decision = PersistDecision(
                        True,
                        "knowledge",
                        reply[:200],
                        0.6,
                        "tool outcome",
                        "experience",
                    )
        await self.memory.maybe_persist(
            user_text,
            reply,
            decision,
            tool_results=list(state.get("tool_results") or []),
        )
        payload = {
            "should_save": decision.should_save,
            "kind": decision.kind,
            "summary": decision.summary,
            "importance": decision.importance,
            "reason": decision.reason,
            "target": decision.target,
        }
        logger.info("update_memory save={} reason={}", decision.should_save, decision.reason)
        return {"persist_decision": payload}
