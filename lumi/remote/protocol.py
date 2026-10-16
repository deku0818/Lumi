"""Length-prefixed JSON frames shared by the Unix listener and SSH byte relay."""

from __future__ import annotations

import asyncio
import json
import struct

PROTOCOL = 1
MAX_FRAME = 16 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


def encode_frame(frame: dict) -> bytes:
    data = json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode()
    if len(data) > MAX_FRAME:
        raise ValueError("Remote frame is too large")
    return struct.pack("!I", len(data)) + data


async def read_frame(reader: asyncio.StreamReader) -> dict:
    size = struct.unpack("!I", await reader.readexactly(4))[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError("Invalid remote frame size")
    frame = json.loads(await reader.readexactly(size))
    if not isinstance(frame, dict):
        raise ValueError("Remote frame must be an object")
    return frame


class FrameWriter:
    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        self.lock = asyncio.Lock()

    async def send(self, frame: dict) -> None:
        data = encode_frame(frame)
        async with self.lock:
            self.writer.write(data)
            await self.writer.drain()
