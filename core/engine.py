"""PersonalAIEngine: lifecycle, wiring, and the public process() API."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Literal

from loguru import logger

from config.settings import PROJECT_ROOT, Settings
from core.graph import build_graph
from core.nodes import GraphNodes
from core.state import empty_state
from identity.loader import IdentityLoader
from llm.adapters import BaseLLMAdapter
from llm.gateway import LLMGateway
from memory.manager import MemoryManager, TextEmbedder
from tools.builtin import BuiltinTools
from tools.jobs import JobQueue
from tools.mcp import MCPBridge
from tools.registry import ToolRegistry

InputType = Literal["text", "voice", "video", "image"]


class PersonalAIEngine:
    """System facade. Call `initialize()` once, then `process()` per turn."""

    def __init__(
        self,
        settings: Settings,
        *,
        memory: MemoryManager | None = None,
        llm: LLMGateway | None = None,
        adapter: BaseLLMAdapter | None = None,
        vision: Any | None = None,
        embedder: TextEmbedder | None = None,
    ) -> None:
        self.settings = settings
        self.session_id = uuid.uuid4().hex
        self.identity = IdentityLoader(settings.identity_path())
        self.memory = memory or MemoryManager(settings, embedder=embedder)
        self.llm = llm or LLMGateway(settings, adapter=adapter)
        self.tools = ToolRegistry()
        self.mcp = MCPBridge(settings.mcp_server_url)
        self.vision = vision
        self.graph: Any | None = None
        self._nodes: GraphNodes | None = None
        self._turn_lock = asyncio.Lock()
        self.jobs = JobQueue()
        self._builtins: BuiltinTools | None = None

    def attach_vision(self, vision: Any) -> None:
        """Attach the vision subsystem after construction (avoids import cycles)."""
        self.vision = vision

    async def initialize(self) -> None:
        """Create tables, load identity, register tools, and compile the graph."""
        _configure_logging(self.settings)
        await self.memory.initialize()
        await self.memory.ensure_session(self.session_id)
        if self.vision is None:
            from senses.vision import VisionIO

            self.vision = VisionIO(enabled=self.settings.vision_enabled)
            await self.vision.initialize()
        builtins = BuiltinTools(
            self.settings, vision=self.vision, memory=self.memory, jobs=self.jobs
        )
        self._builtins = builtins
        builtins.register_all(self.tools)
        extra = await self.mcp.list_tools()
        if extra:
            logger.info("MCP advertised {} tools (not auto-registered in this phase)", len(extra))
        self.identity.load()
        await self.memory.add_identity_snapshot(self.identity.snapshot_text(), "startup")
        self._nodes = GraphNodes(
            memory=self.memory,
            llm=self.llm,
            tools=self.tools,
            identity=self.identity,
            max_tool_iterations=self.settings.max_tool_iterations,
            vision=self.vision,
        )
        self.graph = build_graph(self._nodes)
        logger.info("Personal AI Core ready session={}", self.session_id)

    async def process(
        self,
        user_input: str,
        *,
        input_type: InputType = "text",
        image_path: str = "",
        on_progress: Any | None = None,
    ) -> str:
        """Run one full turn through the six-node graph.

        Args:
            user_input: User utterance or typed line.
            input_type: text, voice, image, or video (video treated as image+text).
            image_path: Optional image to analyze with this turn.
            on_progress: Optional async callback(name, detail) for tool steps.

        Returns:
            Assistant reply text.
        """
        if self.graph is None:
            raise RuntimeError("Engine is not initialized")
        async with self._turn_lock:
            return await self._process_locked(
                user_input, input_type=input_type, image_path=image_path, on_progress=on_progress
            )

    async def _process_locked(
        self,
        user_input: str,
        *,
        input_type: InputType = "text",
        image_path: str = "",
        on_progress: Any | None = None,
    ) -> str:
        """Run the graph while holding `_turn_lock`."""
        if self._nodes is not None:
            self._nodes.on_progress = on_progress
        text = (user_input or "").strip()
        if self._builtins is not None:
            self._builtins.last_user_text = text
        if not text and not image_path:
            return "我在听。你可以说话、打字，或者说「截个屏」。"
        state = empty_state(
            self.session_id,
            text or "(图像输入)",
            input_type=input_type,
            image_path=image_path,
        )
        result = await self.graph.ainvoke(state, config={"recursion_limit": 48})
        return str(result.get("response") or "")

    async def self_check(self) -> dict[str, Any]:
        """Lightweight health dict used by startup and tests."""
        memory_ok = await self.memory.self_check()
        llm_ok = await self.llm.self_check()
        tools_ok = await self.tools.self_check()
        return {
            "session_id": self.session_id,
            "graph": self.graph is not None,
            "identity": bool(self.identity.load().identity.name),
            "memory": memory_ok,
            "llm": llm_ok,
            "tools": tools_ok,
            "workspace": str(self.settings.resolve_workspace()),
            "root": str(PROJECT_ROOT),
        }

    async def shutdown(self) -> None:
        """Flush memory connections."""
        await self.memory.close()
        logger.info("Engine shutdown")


def _configure_logging(settings: Settings) -> None:
    """Send logs to stderr and a rotating file under `logs/`."""
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        level=settings.log_level.upper(),
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}\n",
        colorize=True,
    )
    logger.add(
        str(settings.logs_dir() / "personal_ai.log"),
        level="DEBUG",
        rotation="10 MB",
        enqueue=True,
        encoding="utf-8",
    )
