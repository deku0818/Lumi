"""Relay an SSH command's stdin/stdout to the resident Lumi Unix socket."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
from pathlib import Path


def attach(data: Path) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(
            str(data.expanduser().resolve() / "remote-runtime/agent.sock")
        )

        def upload() -> None:
            try:
                while chunk := os.read(sys.stdin.fileno(), 65536):
                    connection.sendall(chunk)
                connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass

        threading.Thread(target=upload, daemon=True).start()
        while chunk := connection.recv(65536):
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="~/.lumi")
    attach(Path(parser.parse_args().data_dir))
