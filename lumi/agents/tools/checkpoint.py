"""Shadow Git Checkpoint — 文件快照管理

在项目目录之外维护一个独立的 shadow git repository，
每轮用户 prompt 发送前自动 commit 当前工作区状态。
回滚时 git checkout 恢复文件到指定快照。

项目本身的 Git 历史完全不受影响。

Shadow repo 位置：
    ~/.lumi/checkpoints/shadow/{thread_id}/.git
    GIT_WORK_TREE 指向项目目录
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from lumi.utils.logger import logger

# 单个 thread 最多保留的 checkpoint 数量
_MAX_CHECKPOINTS = 20

# git / 文件系统操作可能抛出的异常
_GIT_FS_ERRORS = (RuntimeError, OSError, subprocess.SubprocessError)


@dataclass(frozen=True)
class CheckpointInfo:
    """Checkpoint 摘要信息（用于 UI 展示）"""

    commit_hash: str  # shadow git commit hash (full)
    timestamp: float
    label: str  # 用户消息摘要
    langgraph_checkpoint_id: str  # 关联的 LangGraph checkpoint_id
    langgraph_parent_checkpoint_id: str | None = None
    # diff 统计（与前一个 checkpoint 对比），由 list_checkpoints 填充
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0

    @property
    def checkpoint_id(self) -> str:
        """shadow git commit hash (short, 前 8 位)"""
        return self.commit_hash[:8]

    @property
    def display_time(self) -> str:
        """格式化相对时间"""
        from datetime import datetime, timezone

        now = datetime.now(tz=timezone.utc)
        ts = datetime.fromtimestamp(self.timestamp, tz=timezone.utc)
        delta = now - ts
        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            return "just now"
        minutes = total_seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        return ts.strftime("%m-%d %H:%M")


class ShadowGitManager:
    """Shadow Git Checkpoint 管理器

    使用独立的 git 仓库追踪项目工作区的文件变化，
    不影响项目本身的 git 历史。
    """

    def __init__(self, thread_id: str, project_dir: Path, base_dir: Path | None = None):
        """
        Args:
            thread_id: 会话线程 ID
            project_dir: 项目根目录路径（shadow git 的 GIT_WORK_TREE）
            base_dir: shadow repo 根目录，默认 ~/.lumi/checkpoints/shadow
        """
        if base_dir is None:
            base_dir = Path.home() / ".lumi" / "checkpoints" / "shadow"
        self._thread_id = thread_id
        self._project_dir = project_dir.resolve()
        self._repo_dir = (base_dir / thread_id).resolve()
        self._git_dir = self._repo_dir / ".git"
        self._meta_path = self._repo_dir / "meta.json"
        self._initialized = False

    @property
    def git_dir(self) -> Path:
        return self._git_dir

    @property
    def project_dir(self) -> Path:
        return self._project_dir

    # ── Git 命令执行 ──

    def _git(self, *args: str, check: bool = True, timeout: int = 30) -> str:
        """执行 git 命令，自动设置 GIT_DIR 和 GIT_WORK_TREE。

        Returns:
            命令的 stdout
        """
        env_override = {
            "GIT_DIR": str(self._git_dir),
            "GIT_WORK_TREE": str(self._project_dir),
            # 禁用项目级 git hooks 和配置干扰
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(self._repo_dir),  # 避免读取用户 .gitconfig
        }
        import os

        env = {**os.environ, **env_override}

        result = subprocess.run(
            ["git", *args],
            cwd=str(self._project_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed (rc={result.returncode}): "
                f"{result.stderr.strip()}"
            )
        return result.stdout.strip()

    # ── 初始化 ──

    def _ensure_init(self) -> None:
        """确保 shadow git repo 已初始化"""
        if self._initialized:
            return

        if not self._git_dir.exists():
            self._repo_dir.mkdir(parents=True, exist_ok=True)
            # git init 不能带 GIT_WORK_TREE/GIT_DIR，需要干净环境
            import os as _os

            clean_env = {
                k: v
                for k, v in _os.environ.items()
                if k not in ("GIT_DIR", "GIT_WORK_TREE")
            }
            subprocess.run(
                ["git", "init", str(self._repo_dir)],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
                env=clean_env,
            )
            # 配置 shadow repo
            self._git("config", "user.name", "lumi-checkpoint")
            self._git("config", "user.email", "checkpoint@lumi.local")
            # 复用项目的 .gitignore（如果存在）
            gitignore = self._project_dir / ".gitignore"
            if gitignore.exists():
                exclude_dir = self._git_dir / "info"
                exclude_dir.mkdir(parents=True, exist_ok=True)
                exclude_file = exclude_dir / "exclude"
                exclude_file.write_text(
                    gitignore.read_text(encoding="utf-8"), encoding="utf-8"
                )
            logger.info(
                "[ShadowGit] 初始化 shadow repo: %s (project_dir=%s)",
                self._git_dir,
                self._project_dir,
            )

        # 确保 meta 文件存在
        if not self._meta_path.exists():
            self._save_meta([])

        self._initialized = True

    # ── Checkpoint 创建 ──

    def create_checkpoint(
        self,
        label: str,
        langgraph_checkpoint_id: str,
        langgraph_parent_checkpoint_id: str = "",
    ) -> CheckpointInfo | None:
        """在 agent 执行前创建一个 checkpoint。

        对工作区执行 git add -A && git commit，保存当前文件状态。
        如果工作区没有变化（和上次 commit 完全一样），仍然创建
        一个空 commit 以记录会话断点。

        Args:
            label: 用户消息摘要
            langgraph_checkpoint_id: 当前 LangGraph checkpoint_id
            langgraph_parent_checkpoint_id: LangGraph parent checkpoint_id

        Returns:
            CheckpointInfo，失败返回 None
        """
        try:
            self._ensure_init()

            # Stage 所有变化（包括新文件和删除）
            self._git("add", "-A")

            # Commit（允许空 commit 以记录会话断点）
            now = time.time()
            safe_label = label.replace('"', '\\"')[:100]
            commit_msg = (
                f"checkpoint: {safe_label}\n\n"
                f"timestamp: {now}\n"
                f"langgraph_cp: {langgraph_checkpoint_id}\n"
                f"langgraph_parent_cp: {langgraph_parent_checkpoint_id}"
            )
            self._git("commit", "--allow-empty", "-m", commit_msg)

            # 获取 commit hash
            commit_hash = self._git("rev-parse", "HEAD")

            info = CheckpointInfo(
                commit_hash=commit_hash,
                timestamp=now,
                label=label[:100],
                langgraph_checkpoint_id=langgraph_checkpoint_id,
                langgraph_parent_checkpoint_id=langgraph_parent_checkpoint_id or None,
            )

            # 追加到 meta — 即使失败，git commit 已存在，记录 ERROR 但仍返回 info
            try:
                meta = self._load_meta()
                meta.append(self._info_to_dict(info))
                if len(meta) > _MAX_CHECKPOINTS:
                    meta = meta[-_MAX_CHECKPOINTS:]
                self._save_meta(meta)
            except _GIT_FS_ERRORS:
                logger.error(
                    "[ShadowGit] checkpoint %s 已提交但 meta.json 写入失败",
                    info.checkpoint_id,
                    exc_info=True,
                )

            logger.info(
                "[ShadowGit] checkpoint %s: %s (lg_cp=%s)",
                info.checkpoint_id,
                label[:50],
                langgraph_checkpoint_id[:16] if langgraph_checkpoint_id else "N/A",
            )
            return info

        except _GIT_FS_ERRORS:
            logger.error("[ShadowGit] 创建 checkpoint 失败", exc_info=True)
            return None

    # ── Checkpoint 列表 ──

    def list_checkpoints(self) -> list[CheckpointInfo]:
        """列出所有 checkpoint（按时间正序，最旧在前），附带 diff 统计。

        diff 归属逻辑：每个 checkpoint 显示的是该轮 agent 执行后产生的文件变更。
        - checkpoint N 的 diff = diff(checkpoint N, checkpoint N+1)
        - 最后一个 checkpoint 的 diff = diff(checkpoint N, 当前工作区)
        - 第一个 checkpoint 无特殊处理，同样按上述规则
        """
        try:
            self._ensure_init()
            meta = self._load_meta()
            if not meta:
                return []

            infos: list[CheckpointInfo] = []
            for i, d in enumerate(meta):
                try:
                    info = self._dict_to_info(d)
                except (KeyError, TypeError) as e:
                    logger.warning("[ShadowGit] 跳过损坏的 meta 条目 %d: %s", i, e)
                    continue
                if i + 1 < len(meta):
                    # 非最后一个：对比当前 commit 和下一个 commit
                    files, ins, dels = self._diff_stat(
                        d["commit_hash"], meta[i + 1]["commit_hash"]
                    )
                else:
                    # 最后一个：对比当前 commit 和工作区
                    files, ins, dels = self._diff_stat_worktree(d["commit_hash"])
                info = CheckpointInfo(
                    commit_hash=info.commit_hash,
                    timestamp=info.timestamp,
                    label=info.label,
                    langgraph_checkpoint_id=info.langgraph_checkpoint_id,
                    langgraph_parent_checkpoint_id=info.langgraph_parent_checkpoint_id,
                    files_changed=files,
                    insertions=ins,
                    deletions=dels,
                )
                infos.append(info)
            return infos
        except _GIT_FS_ERRORS:
            logger.error("[ShadowGit] 列出 checkpoint 失败", exc_info=True)
            return []

    def _diff_stat_worktree(self, from_hash: str) -> tuple[int, int, int]:
        """计算指定 commit 与当前工作区之间的 diff 统计。

        先暂存工作区变更，对比后恢复暂存区状态。

        Args:
            from_hash: 起始 commit hash

        Returns:
            (files_changed, insertions, deletions)
        """
        try:
            # 暂存所有工作区变更以便 diff 能检测到未追踪文件
            self._git("add", "-A")
            try:
                stat = self._git(
                    "diff", "--cached", "--shortstat", from_hash, check=False
                )
                return self._parse_shortstat(stat)
            finally:
                # 恢复暂存区到 HEAD 状态，不影响工作区文件
                self._git("reset", "HEAD", "--", ".", check=False)
        except _GIT_FS_ERRORS:
            logger.debug("[ShadowGit] worktree diff stat 失败", exc_info=True)
            return 0, 0, 0

    def _diff_stat(self, from_hash: str, to_hash: str) -> tuple[int, int, int]:
        """计算两个 commit 之间的 diff 统计。

        Args:
            from_hash: 起始 commit hash
            to_hash: 目标 commit hash

        Returns:
            (files_changed, insertions, deletions)
        """
        try:
            stat = self._git("diff", "--shortstat", from_hash, to_hash, check=False)
            return self._parse_shortstat(stat)
        except _GIT_FS_ERRORS:
            logger.debug("[ShadowGit] diff stat 失败", exc_info=True)
            return 0, 0, 0

    @staticmethod
    def _parse_shortstat(stat: str) -> tuple[int, int, int]:
        """解析 git diff --shortstat 输出。

        示例: " 3 files changed, 10 insertions(+), 5 deletions(-)"

        Returns:
            (files_changed, insertions, deletions)
        """
        import re

        if not stat.strip():
            return 0, 0, 0
        files = ins = dels = 0
        m = re.search(r"(\d+) file", stat)
        if m:
            files = int(m.group(1))
        m = re.search(r"(\d+) insertion", stat)
        if m:
            ins = int(m.group(1))
        m = re.search(r"(\d+) deletion", stat)
        if m:
            dels = int(m.group(1))
        return files, ins, dels

    # ── Checkpoint 恢复 ──

    def restore_checkpoint(self, commit_hash: str) -> bool:
        """恢复工作区到指定 checkpoint 的文件状态。

        使用 git checkout 将工作区文件恢复到指定 commit 的状态，
        并清理该 commit 之后新增的文件（包括未提交的工作区变更）。

        Args:
            commit_hash: 要恢复到的 checkpoint 的 git commit hash

        Returns:
            恢复成功返回 True
        """
        try:
            self._ensure_init()

            # 先暂存当前工作区所有变更，确保新增文件被 git 追踪
            self._git("add", "-A")

            # 列出目标 commit 之后新增的文件（对比工作区暂存区 vs 目标 commit）
            # git checkout 只能恢复目标 commit 中存在的文件，不会删除后续新增的文件，
            # 因此需要这一步获取新增文件列表，在 checkout 后手动删除
            files_to_delete: list[str] = []
            try:
                diff_output = self._git(
                    "diff",
                    "--cached",
                    "--name-only",
                    "--diff-filter=A",
                    commit_hash,
                    check=False,
                )
                if diff_output:
                    files_to_delete = [
                        p.strip() for p in diff_output.splitlines() if p.strip()
                    ]
            except _GIT_FS_ERRORS:
                logger.debug("[ShadowGit] 获取新增文件列表时出错", exc_info=True)

            # 用 git checkout 恢复已追踪文件到目标 commit 的状态
            self._git("checkout", commit_hash, "--", ".")

            # 逐文件删除目标 commit 中不存在的文件，单个文件失败不中止整体流程
            failed_deletes: list[str] = []
            for rel_path in files_to_delete:
                full_path = self._project_dir / rel_path
                try:
                    if full_path.exists() and full_path.is_file():
                        full_path.unlink()
                        logger.debug("[ShadowGit] 删除文件: %s", rel_path)
                except OSError:
                    logger.warning(
                        "[ShadowGit] 无法删除文件: %s", rel_path, exc_info=True
                    )
                    failed_deletes.append(rel_path)

            if failed_deletes:
                logger.error(
                    "[ShadowGit] %d 个文件在恢复时无法删除: %s",
                    len(failed_deletes),
                    failed_deletes,
                )

            # 更新 meta 和 HEAD（即使文件删除部分失败，也需保持 shadow git 一致）
            meta = self._load_meta()
            target_idx = None
            for i, d in enumerate(meta):
                if d["commit_hash"] == commit_hash:
                    target_idx = i
                    break

            if target_idx is not None:
                meta = meta[:target_idx]
                self._save_meta(meta)

            # 重置 shadow git HEAD 到目标 commit
            self._git("reset", "--soft", commit_hash)

            logger.info("[ShadowGit] 恢复到 checkpoint %s", commit_hash[:8])
            return True

        except _GIT_FS_ERRORS:
            logger.error("[ShadowGit] 恢复 checkpoint 失败", exc_info=True)
            return False

    # ── Meta 文件管理 ──

    def _load_meta(self) -> list[dict]:
        if not self._meta_path.exists():
            return []
        try:
            return json.loads(self._meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            logger.error("[ShadowGit] meta.json 损坏，已备份后重置", exc_info=True)
            # 备份损坏文件，防止数据彻底丢失
            try:
                backup = self._meta_path.with_suffix(".json.bak")
                self._meta_path.rename(backup)
            except OSError:
                pass
            return []

    def _save_meta(self, meta: list[dict]) -> None:
        """原子写入 meta.json（先写临时文件再 rename，防止 crash 导致损坏）"""
        import os

        data = json.dumps(meta, ensure_ascii=False, indent=2)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(self._repo_dir), suffix=".meta.tmp")
        os.close(tmp_fd)
        tmp = Path(tmp_path)
        try:
            tmp.write_text(data, encoding="utf-8")
            tmp.replace(self._meta_path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    @staticmethod
    def _info_to_dict(info: CheckpointInfo) -> dict:
        return {
            "checkpoint_id": info.checkpoint_id,
            "commit_hash": info.commit_hash,
            "timestamp": info.timestamp,
            "label": info.label,
            "langgraph_checkpoint_id": info.langgraph_checkpoint_id,
            "langgraph_parent_checkpoint_id": info.langgraph_parent_checkpoint_id or "",
        }

    @staticmethod
    def _dict_to_info(d: dict) -> CheckpointInfo:
        parent = d.get("langgraph_parent_checkpoint_id", "") or None
        return CheckpointInfo(
            commit_hash=d["commit_hash"],
            timestamp=d["timestamp"],
            label=d["label"],
            langgraph_checkpoint_id=d["langgraph_checkpoint_id"],
            langgraph_parent_checkpoint_id=parent,
        )
