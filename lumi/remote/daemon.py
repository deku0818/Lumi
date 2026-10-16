"""Resident remote agent: private Unix socket, one process per data directory."""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import fcntl
import json
import mimetypes
import os
import shutil
import signal
import stat
import sys
import uuid
from pathlib import Path

from lumi.remote.protocol import CHUNK_BYTES, PROTOCOL, FrameWriter, read_frame


class InstanceLock:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = (root / "instance.lock").open("a+")
        os.fchmod(self.file.fileno(), 0o600)
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.file.close()
            raise

    def close(self) -> None:
        self.file.close()


def runtime(data: Path) -> dict:
    from lumi import __version__

    return {
        "transport": "ssh-stdio",
        "protocol": PROTOCOL,
        "version": __version__,
        "pid": os.getpid(),
        "executable": sys.executable,
        "data_dir": str(data),
    }


class SessionChannel:
    def __init__(self, output: FrameWriter, channel: str) -> None:
        self.output, self.channel = output, channel

    async def send(self, frame: dict) -> None:
        # Losing a viewer must not fail the running agent before Client detaches it.
        with contextlib.suppress(ConnectionError):
            await self.output.send(
                {"type": "frame", "channel": self.channel, "frame": frame}
            )


async def release_session(session) -> None:
    from lumi.gateway.session_registry import registry

    if session.should_detach():
        displaced = session.detach(registry)
        if displaced is not None:
            await displaced.aclose()
    else:
        await session.aclose()


class Client:
    def __init__(
        self, data: Path, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.data, self.reader, self.writer = data, reader, writer
        self.output = FrameWriter(writer)
        self.sessions: dict = {}
        self.opening: dict[str, asyncio.Task] = {}
        self.tasks: set[asyncio.Task] = set()
        self.uploads: dict = {}
        self.file_lock = asyncio.Lock()

    async def open_session(self, message: dict) -> dict:
        from lumi.gateway.bridge import AgentBridge
        from lumi.gateway.broadcast import hub
        from lumi.gateway.projects import touch_project
        from lumi.gateway.session import GatewaySession
        from lumi.gateway.session_registry import registry

        channel = message["channel"]
        if channel in self.sessions or channel in self.opening:
            raise ValueError("Channel is already open")
        self.opening[channel] = asyncio.current_task()
        session = None
        bridge = None
        try:
            output = SessionChannel(self.output, channel)
            thread = message.get("thread", "")
            session = registry.take(thread) if thread else None
            if session is not None:
                self.sessions[channel] = session
                await session.reattach(output)
            else:
                bridge = AgentBridge()
                await bridge.initialize(project_dir=message.get("workspace", ""))
                if bridge.workspace_bound:
                    touch_project(bridge.workspace_dir)
                session = GatewaySession(bridge, output, hub)
                self.sessions[channel] = session
                await session.start()
            return {"channel": channel}
        except BaseException:
            self.sessions.pop(channel, None)
            if session is not None:
                await release_session(session)
            elif bridge is not None:
                await bridge.close()
            raise
        finally:
            self.opening.pop(channel, None)

    async def close_session(self, channel: str) -> dict:
        task = self.opening.get(channel)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        session = self.sessions.pop(channel, None)
        if session is not None:
            await release_session(session)
        return {}

    def cancel_upload(self, identifier: str) -> None:
        upload = self.uploads.pop(identifier, None)
        if upload is not None:
            upload["file"].close()
            shutil.rmtree(upload["path"].parent)

    def file_command(self, message: dict) -> dict:
        from lumi.gateway.uploads import MAX_UPLOAD_BYTES
        from lumi.utils.config.global_manager import uploads_dir

        op = message["op"]
        if op in {"file_stat", "file_read"}:
            path = Path(message["path"]).expanduser().resolve()
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise FileNotFoundError(str(path))
            if info.st_size > 128 * 1024 * 1024:
                raise ValueError("File exceeds the 128 MiB preview limit")
            if op == "file_stat":
                return {
                    "size": info.st_size,
                    "mime": mimetypes.guess_type(path)[0] or "application/octet-stream",
                }
            offset = message.get("offset", 0)
            length = message.get("length", CHUNK_BYTES)
            if (
                not isinstance(offset, int)
                or offset < 0
                or not isinstance(length, int)
                or not 0 < length <= CHUNK_BYTES
            ):
                raise ValueError("Invalid file range")
            with path.open("rb") as source:
                source.seek(offset)
                return {"data": base64.b64encode(source.read(length)).decode()}
        if op == "upload_begin":
            name = Path(message["name"]).name
            if not name or name in {".", ".."} or "\x00" in name:
                raise ValueError("Invalid upload filename")
            identifier = uuid.uuid4().hex
            folder = uploads_dir() / identifier
            folder.mkdir(parents=True, mode=0o700)
            path = folder / name
            file = path.open("xb")
            path.chmod(0o600)
            self.uploads[identifier] = {"path": path, "file": file, "size": 0}
            return {"upload": identifier}
        if op == "upload_cancel":
            self.cancel_upload(message["upload"])
            return {}
        upload = self.uploads[message["upload"]]
        if op == "upload_chunk":
            data = base64.b64decode(message["data"], validate=True)
            if len(data) > CHUNK_BYTES or upload["size"] + len(data) > MAX_UPLOAD_BYTES:
                self.cancel_upload(message["upload"])
                raise ValueError("Upload exceeds its size limit")
            upload["file"].write(data)
            upload["size"] += len(data)
            return {}
        if op == "upload_finish":
            upload["file"].close()
            self.uploads.pop(message["upload"])
            return {"path": str(upload["path"])}
        raise ValueError("Unknown file operation")

    async def dispatch(self, message: dict) -> None:
        rid = message.get("id")
        try:
            op = message["op"]
            if op == "open":
                result = await self.open_session(message)
            elif op == "close":
                result = await self.close_session(message["channel"])
            elif op == "runtime":
                result = runtime(self.data)
            elif op.startswith("file_") or op.startswith("upload_"):
                async with self.file_lock:
                    operation = asyncio.create_task(
                        asyncio.to_thread(self.file_command, message)
                    )
                    try:
                        result = await asyncio.shield(operation)
                    except asyncio.CancelledError:
                        await operation
                        raise
            else:
                raise ValueError("Unknown remote operation")
            await self.output.send({"id": rid, "result": result})
        except asyncio.CancelledError:
            # Closing a channel must also settle its pending open RPC.
            if message.get("op") == "open":
                with contextlib.suppress(ConnectionError):
                    await self.output.send(
                        {
                            "id": rid,
                            "error": {
                                "message": "Remote session opening was cancelled",
                                "status": 400,
                            },
                        }
                    )
            raise
        except Exception as error:
            if isinstance(error, (FileNotFoundError, NotADirectoryError)):
                status = 404
            elif isinstance(error, PermissionError):
                status = 403
            else:
                status = 400
            with contextlib.suppress(ConnectionError):
                await self.output.send(
                    {"id": rid, "error": {"message": str(error), "status": status}}
                )

    async def run(self) -> None:
        try:
            await self.output.send({"type": "hello", **runtime(self.data)})
            while True:
                message = await read_frame(self.reader)
                if message.get("op") == "frame":
                    session = self.sessions.get(message["channel"])
                    if session is not None:
                        await session.handle_frame(message["frame"])
                else:
                    task = asyncio.create_task(self.dispatch(message))
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            pass
        finally:
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            for channel in list(self.sessions):
                await self.close_session(channel)
            for upload in list(self.uploads):
                self.cancel_upload(upload)
            self.writer.close()
            with contextlib.suppress(ConnectionError):
                await self.writer.wait_closed()


async def serve(data: Path) -> None:
    from lumi.gateway.bootstrap import drain_runs, gateway_process
    from lumi.gateway.channels.manager import channels_runtime

    root = data / "remote-runtime"
    lock = InstanceLock(root)
    socket_path = root / "agent.sock"
    clients: set[asyncio.Task] = set()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)

    async def accept(reader, writer) -> None:
        task = asyncio.current_task()
        clients.add(task)
        try:
            await Client(data, reader, writer).run()
        finally:
            clients.discard(task)

    try:
        # The lifetime lock grants ownership of the socket, including stale cleanup.
        socket_path.unlink(missing_ok=True)
        async with gateway_process(), channels_runtime():
            server = await asyncio.start_unix_server(accept, path=socket_path)
            socket_path.chmod(0o600)
            from lumi.remote.bootstrap import write_private

            write_private(root / "instance.json", json.dumps(runtime(data)))
            async with server:
                await stopped.wait()
                server.close()
                await server.wait_closed()
                await drain_runs()
                for task in clients:
                    task.cancel()
                await asyncio.gather(*clients, return_exceptions=True)
    finally:
        socket_path.unlink(missing_ok=True)
        lock.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="~/.lumi")
    data = Path(parser.parse_args().data_dir).expanduser().resolve()
    os.environ["LUMI_CONFIG_DIR"] = str(data)
    data.mkdir(parents=True, exist_ok=True)
    os.chdir(data)
    try:
        asyncio.run(serve(data))
    except BlockingIOError:
        print("Lumi is already running for this data directory", file=sys.stderr)


if __name__ == "__main__":
    main()
