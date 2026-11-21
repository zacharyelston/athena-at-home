# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import contextlib
import io
import os
import shutil
import socket
import subprocess
import tempfile
import time

import pytest
from PIL import Image

from sieve.engine import OcrResult
from sieve.tier0 import Line


class FakeEngine:
    """Stands in for the MLX model: records calls, returns canned text, can be told to fail."""
    model_id = "fake/deepseek-ocr-2"
    load_seconds = 0.0

    def __init__(self, text="# Page\nhello", fail=None):
        self.text, self.fail, self.calls = text, fail, []

    def ocr(self, image, tiles=6, instruction="", max_tokens=3000):
        self.calls.append({"size": image.size, "tiles": tiles, "instruction": instruction, "max_tokens": max_tokens})
        if self.fail:
            raise self.fail
        return OcrResult(text=self.text, tiles=tiles, seconds=0.01, gen_tokens=5, image_tokens=257,
                         looped=False, hit_cap=False)


def sparse_tier0(image):
    return [Line("Dear Jordan,", 0.1, 0.2, 0.2, 0.016, 1), Line("Thanks for being a customer.", 0.1, 0.25, 0.5, 0.016, 1)]


def dense_tier0(image):
    return [Line(f"09/{d:02d} PAYMENT 1,{d}00.00 5,{d}00.00", 0.1, 0.1 + d * 0.02, 0.8, 0.013, 1) for d in range(1, 15)]


@pytest.fixture
def page_b64():
    buf = io.BytesIO()
    Image.new("RGB", (850, 1100), "white").save(buf, "JPEG")
    return base64.b64encode(buf.getvalue()).decode()


@contextlib.contextmanager
def nats_server_running(max_payload: int):
    """A throwaway nats-server on a free port, localhost only. Yields its URL."""
    exe = shutil.which("nats-server") or "/opt/homebrew/opt/nats-server/bin/nats-server"
    if not os.path.exists(exe):
        pytest.skip("nats-server not installed")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
        f.write(f"listen: 127.0.0.1:{port}\nmax_payload: {max_payload}\n")
        conf = f.name
    proc = subprocess.Popen([exe, "-c", conf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
                break
            except OSError:
                time.sleep(0.1)
        yield f"nats://127.0.0.1:{port}"
    finally:
        proc.terminate()
        proc.wait(5)
        os.unlink(conf)


@pytest.fixture
def nats_server():
    """Small max_payload so the oversized-page path can be tested. Yields (url, max_payload)."""
    max_payload = 256 * 1024
    with nats_server_running(max_payload) as url:
        yield url, max_payload
