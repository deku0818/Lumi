import asyncio
import base64
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from lumi.remote.daemon import Client, InstanceLock
from lumi.remote.protocol import CHUNK_BYTES, MAX_FRAME, encode_frame, read_frame


class MemoryWriter:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


async def test_fragmented_unicode_frames_and_multiple_messages():
    reader = asyncio.StreamReader()
    frames = [{"op": "runtime", "text": "你好 🌟"}, {"id": 2, "result": {}}]
    wire = b"".join(map(encode_frame, frames))

    async def feed():
        for byte in wire:
            reader.feed_data(bytes([byte]))
            await asyncio.sleep(0)
        reader.feed_eof()

    task = asyncio.create_task(feed())
    assert [await read_frame(reader), await read_frame(reader)] == frames
    await task
    with pytest.raises(asyncio.IncompleteReadError):
        await read_frame(reader)


@pytest.mark.parametrize(
    "wire", [struct.pack("!I", 0), struct.pack("!I", MAX_FRAME + 1), b"\0\0\0\2[]"]
)
async def test_invalid_frame_is_rejected(wire):
    reader = asyncio.StreamReader()
    reader.feed_data(wire)
    with pytest.raises(ValueError):
        await read_frame(reader)


def test_lifetime_lock_is_exclusive_and_released_without_deleting_data(tmp_path):
    root = tmp_path / "remote-runtime"
    first = InstanceLock(root)
    marker = root / "agent.sock"
    marker.write_text("owned socket")
    try:
        with pytest.raises(BlockingIOError):
            InstanceLock(root)
        assert marker.read_text() == "owned socket"
        assert (root / "instance.lock").stat().st_mode & 0o777 == 0o600
    finally:
        first.close()
    second = InstanceLock(root)
    second.close()
    assert marker.exists()


def test_chunked_upload_download_and_failed_upload_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lumi.utils.config.global_manager.uploads_dir", lambda: tmp_path / "uploads"
    )
    client = Client(tmp_path, None, MemoryWriter())
    payload = b"\x00\xff\x80" * (CHUNK_BYTES // 3 + 20)
    identifier = client.file_command({"op": "upload_begin", "name": "../../中文.bin"})[
        "upload"
    ]
    for offset in range(0, len(payload), CHUNK_BYTES):
        client.file_command(
            {
                "op": "upload_chunk",
                "upload": identifier,
                "data": base64.b64encode(
                    payload[offset : offset + CHUNK_BYTES]
                ).decode(),
            }
        )
    path = Path(
        client.file_command({"op": "upload_finish", "upload": identifier})["path"]
    )
    assert path.name == "中文.bin" and path.read_bytes() == payload
    assert path.stat().st_mode & 0o777 == 0o600
    assert client.file_command({"op": "file_stat", "path": str(path)})["size"] == len(
        payload
    )
    result = client.file_command(
        {"op": "file_read", "path": str(path), "offset": CHUNK_BYTES, "length": 500}
    )
    assert base64.b64decode(result["data"]) == payload[CHUNK_BYTES : CHUNK_BYTES + 500]
    with pytest.raises(ValueError, match="range"):
        client.file_command(
            {"op": "file_read", "path": str(path), "length": CHUNK_BYTES + 1}
        )
    failed = client.file_command({"op": "upload_begin", "name": "failed.bin"})["upload"]
    with pytest.raises(ValueError, match="limit"):
        client.file_command(
            {
                "op": "upload_chunk",
                "upload": failed,
                "data": base64.b64encode(b"a" * (CHUNK_BYTES + 1)).decode(),
            }
        )
    assert not (path.parent.parent / failed).exists()
    assert path.read_bytes() == payload, (
        "Failed transfer must not delete another upload"
    )


async def test_disconnect_cleans_partial_upload_and_closes_writer(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "lumi.utils.config.global_manager.uploads_dir", lambda: tmp_path / "uploads"
    )
    reader, writer = asyncio.StreamReader(), MemoryWriter()
    client = Client(tmp_path, reader, writer)
    identifier = client.file_command({"op": "upload_begin", "name": "partial.txt"})[
        "upload"
    ]
    reader.feed_eof()
    await client.run()
    assert writer.closed and not client.uploads
    assert not (tmp_path / "uploads" / identifier).exists()


async def test_file_errors_return_status_without_closing_other_channels(tmp_path):
    writer = MemoryWriter()
    client = Client(tmp_path, None, writer)
    await client.dispatch(
        {"op": "file_stat", "id": 1, "path": str(tmp_path / "absent")}
    )
    await client.dispatch({"op": "runtime", "id": 2})
    reader = asyncio.StreamReader()
    reader.feed_data(writer.data)
    assert (await read_frame(reader))["error"]["status"] == 404
    assert (await read_frame(reader))["result"]["transport"] == "ssh-stdio"


async def test_cancel_open_replies_to_both_requests_and_keeps_other_channels(
    tmp_path, monkeypatch
):
    entered = asyncio.Event()
    bridge_closed = asyncio.Event()

    class SlowBridge:
        async def initialize(self, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        async def close(self):
            bridge_closed.set()

    monkeypatch.setattr("lumi.gateway.bridge.AgentBridge", SlowBridge)
    incoming, outgoing = asyncio.StreamReader(), asyncio.StreamReader()

    class Writer(MemoryWriter):
        def write(self, data):
            super().write(data)
            outgoing.feed_data(data)

    writer = Writer()
    client = Client(tmp_path, incoming, writer)
    other = SimpleNamespace(
        handle_frame=AsyncMock(), should_detach=lambda: False, aclose=AsyncMock()
    )
    client.sessions["other"] = other
    running = asyncio.create_task(client.run())
    try:
        assert (await read_frame(outgoing))["type"] == "hello"
        incoming.feed_data(encode_frame({"id": 1, "op": "open", "channel": "slow"}))
        await asyncio.wait_for(entered.wait(), 1)
        opening = client.opening["slow"]
        incoming.feed_data(encode_frame({"id": 2, "op": "close", "channel": "slow"}))
        replies = [await asyncio.wait_for(read_frame(outgoing), 1) for _ in range(2)]
        assert [reply["id"] for reply in replies] == [1, 2]
        assert replies[0]["error"]["message"] == "Remote session opening was cancelled"
        assert replies[1]["result"] == {}
        assert bridge_closed.is_set()
        assert opening.cancelled()
        assert not client.opening and "slow" not in client.sessions
        assert not writer.closed and not running.done()

        frame = {"id": 3, "method": "list_sessions"}
        incoming.feed_data(
            encode_frame({"op": "frame", "channel": "other", "frame": frame})
        )
        incoming.feed_data(encode_frame({"id": 4, "op": "runtime"}))
        assert (await asyncio.wait_for(read_frame(outgoing), 1))["id"] == 4
        other.handle_frame.assert_awaited_once_with(frame)
        other.aclose.assert_not_awaited()
    finally:
        incoming.feed_eof()
        await asyncio.wait_for(running, 2)
    other.aclose.assert_awaited_once()


async def test_viewer_write_failure_does_not_abort_agent_output():
    from lumi.remote.daemon import SessionChannel

    class LostWriter:
        async def send(self, frame):
            raise BrokenPipeError("viewer disconnected")

    await SessionChannel(LostWriter(), "first").send({"method": "event"})
