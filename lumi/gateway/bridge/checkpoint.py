"""文件级 Checkpoint / rewind（从 AgentBridge 拆出的职责子模块）。

逻辑逐字照搬自原 AgentBridge；持 bridge 反向引用以读 _agent / _config / _shadow，
并复用 bridge 上的 checkpoint helper（_extract_label / _extract_cp_ids /
_find_clean_checkpoint_id）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from lumi.agents.runtime.checkpoint import CheckpointInfo, FileCheckpointManager
from lumi.agents.runtime.file_tracker import FileChangeTracker
from lumi.utils.logger import logger

if TYPE_CHECKING:
    from lumi.gateway.bridge.core import AgentBridge


class CheckpointService:
    """每轮前快照、列出与回退文件级 checkpoint。"""

    def __init__(self, bridge: AgentBridge) -> None:
        self._bridge = bridge

    def init_checkpoint(self, project_dir: Path) -> None:
        """初始化文件级 checkpoint manager

        Args:
            project_dir: 项目根目录路径
        """
        from lumi.agents.tools.providers.filesystem import get_backend

        b = self._bridge
        tid = b.current_thread_id
        if tid:
            b._tracker = FileChangeTracker()
            b._shadow = FileCheckpointManager(
                tid,
                Path(project_dir),
                b._tracker,
            )
            # 将 tracker 注册到 filesystem backend
            get_backend().set_tracker(b._tracker)

    async def create_checkpoint_before_turn(self, content: str | list) -> None:
        """在每轮 agent 执行前创建 checkpoint。

        从 content 提取用户消息摘要作为 label，
        从 LangGraph state 获取当前 **clean** checkpoint_id。
        若最新 checkpoint 处于 stale 状态（上一轮被中断，state.next 非空），
        则沿 parent 链回退到 clean checkpoint，确保回滚时不包含中断轮次的消息。
        """
        b = self._bridge
        if b._shadow is None:
            return

        try:
            label = b._extract_label(content)

            # 获取当前 LangGraph checkpoint_id（必须是 clean 状态）
            lg_cp_id = ""
            lg_parent_cp_id = ""
            if b._agent and b._config:
                try:
                    graph = b._agent.graph
                    state = await graph.aget_state(b._config)
                except Exception:
                    logger.warning(
                        "[AgentBridge] aget_state 失败，"
                        "checkpoint 将无法回退 LangGraph 会话",
                        exc_info=True,
                    )
                    state = None

                if state and state.config:
                    # 非 stale 或有 interrupt 的 stale：直接使用当前 checkpoint
                    has_interrupts = state.next and any(
                        intr for task in state.tasks for intr in task.interrupts
                    )
                    if not state.next or has_interrupts:
                        lg_cp_id, lg_parent_cp_id = b._extract_cp_ids(state)
                    else:
                        # stale 且无 interrupt：回退到 clean checkpoint
                        clean_id = await b._find_clean_checkpoint_id(graph, state)
                        if clean_id:
                            lg_cp_id = clean_id
                            # clean checkpoint 的 parent 即为其前一个 checkpoint
                            lg_parent_cp_id = b._extract_cp_ids(state)[1]
                        else:
                            logger.warning(
                                "[AgentBridge] 未找到 clean checkpoint，"
                                "此轮 checkpoint 将无法回退 LangGraph 会话"
                            )

            # 在线程池中执行文件操作，避免阻塞事件循环
            await asyncio.to_thread(
                b._shadow.create_checkpoint,
                label,
                lg_cp_id,
                lg_parent_cp_id,
            )
        except Exception:
            logger.error("[AgentBridge] 创建 checkpoint 失败", exc_info=True)

    async def list_checkpoints(self) -> list[CheckpointInfo]:
        """列出当前 thread 的所有 checkpoint"""
        b = self._bridge
        if b._shadow is None:
            return []
        return await asyncio.to_thread(b._shadow.list_checkpoints)

    async def rewind_to_checkpoint(
        self, checkpoint: CheckpointInfo
    ) -> tuple[bool, str]:
        """回退到指定 checkpoint：恢复文件 + 回退 LangGraph 会话。

        Args:
            checkpoint: 要回退到的 checkpoint

        Returns:
            (success, error_message) 元组
        """
        b = self._bridge
        if b._shadow is None:
            return False, "Checkpoint 未初始化"

        try:
            # 1. 恢复文件（在线程池中执行）
            file_ok = await asyncio.to_thread(
                b._shadow.restore_checkpoint, checkpoint.commit_hash
            )
            if not file_ok:
                return False, "文件恢复失败"

            # 2. 回退 LangGraph 会话 + 清理旧分支 checkpoints
            if b._config:
                thread_id = b._config["configurable"].get("thread_id", "")
                lg_cp_id = checkpoint.langgraph_checkpoint_id

                if lg_cp_id:
                    # 指向目标 checkpoint，下次 astream_events 从此分支
                    b._config["configurable"]["checkpoint_id"] = lg_cp_id
                else:
                    # 回滚到第一条消息之前：移除 checkpoint_id，等效于空会话
                    b._config["configurable"].pop("checkpoint_id", None)

                # 清理目标之后的所有 LangGraph checkpoints
                if thread_id and b._agent:
                    try:
                        if lg_cp_id:
                            deleted = await b._agent.aprune_checkpoints_after(
                                thread_id, lg_cp_id
                            )
                        else:
                            # 回到最初：删除整个 thread 的所有 checkpoints
                            await b._agent.adelete_thread(thread_id)
                            deleted = -1
                        if deleted:
                            logger.info(
                                "[AgentBridge] rewind 清理了旧 checkpoint (deleted=%s)",
                                deleted,
                            )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.warning(
                            "[AgentBridge] rewind checkpoint 清理失败，不影响回退",
                            exc_info=True,
                        )

            return True, ""

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[AgentBridge] rewind 失败", exc_info=True)
            return False, str(e)
