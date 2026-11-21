# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import contextlib
import importlib.util
import io
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import pytest
from PIL import Image

from sieve.engine import OcrResult
from sieve.tier0 import Line


def _have(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


HAVE_MLX = sys.platform == "darwin" and platform.machine() == "arm64" and _have("mlx_vlm")
HAVE_VISION = sys.platform == "darwin" and _have("Vision")


def pytest_collection_modifyitems(config, items):
    """Tests marked mlx or macos skip, with a reason, where the platform or its packages are missing."""
    skip_mlx = pytest.mark.skip(reason="needs Apple Silicon with mlx-vlm (pip install -e '.[sieve]')")
    skip_vision = pytest.mark.skip(reason="needs macOS with Apple Vision (pyobjc-framework-Vision)")
    for item in items:
        if "mlx" in item.keywords and not HAVE_MLX:
            item.add_marker(skip_mlx)
        if "macos" in item.keywords and not HAVE_VISION:
            item.add_marker(skip_vision)


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
def nats_server_running(max_payload: int, jetstream: bool = False):
    """A throwaway nats-server on a free port, localhost only (with JetStream in a temp dir if asked). Yields its
    URL."""
    exe = shutil.which("nats-server") or "/opt/homebrew/opt/nats-server/bin/nats-server"
    if not os.path.exists(exe):
        pytest.skip("nats-server not installed")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
        f.write(f"listen: 127.0.0.1:{port}\nmax_payload: {max_payload}\n")
        if jetstream:
            f.write(f"jetstream: {{store_dir: {json.dumps(tempfile.mkdtemp(prefix='js-'))}}}\n")
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


# --- PostgreSQL ----------------------------------------------------------------------------------------------
# Tests that need a database get a fresh one per test. With ATHENA_TEST_DATABASE_URL set (any database on a server
# the URL's user may create databases on), it's created there; otherwise a throwaway server is started with initdb
# for the session, the way nats_server_running starts nats-server. Without either, those tests skip.

def _pg_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    import glob
    for pattern in ("/opt/homebrew/opt/postgresql@*/bin/", "/usr/local/opt/postgresql@*/bin/",
                    "/usr/lib/postgresql/*/bin/"):
        hits = sorted(glob.glob(pattern + name))
        if hits:
            return hits[-1]
    return None


@pytest.fixture(scope="session")
def pg_admin_url(tmp_path_factory):
    url = os.environ.get("ATHENA_TEST_DATABASE_URL")
    if url:
        yield url
        return
    initdb, pg_ctl = _pg_bin("initdb"), _pg_bin("pg_ctl")
    if not (initdb and pg_ctl):
        pytest.skip("no PostgreSQL: set ATHENA_TEST_DATABASE_URL or install PostgreSQL (initdb, pg_ctl)")
    base = tmp_path_factory.mktemp("pg")
    data = base / "data"
    subprocess.run([initdb, "-D", str(data), "-U", "athena", "--auth=trust", "-E", "UTF8", "--no-locale"],
                   check=True, stdout=subprocess.DEVNULL)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    subprocess.run([pg_ctl, "-D", str(data), "-l", str(base / "log"), "-w", "start", "-o",
                    f"-p {port} -h 127.0.0.1 -c unix_socket_directories='' -c fsync=off -c full_page_writes=off"],
                   check=True, stdout=subprocess.DEVNULL)
    try:
        yield f"postgresql://athena@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run([pg_ctl, "-D", str(data), "-m", "immediate", "stop"], stdout=subprocess.DEVNULL)


@pytest.fixture
def pg_url(pg_admin_url):
    """A fresh, empty database for one test."""
    import uuid

    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    name = f"athena_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(pg_admin_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    params = conninfo_to_dict(pg_admin_url)
    params["dbname"] = name
    yield make_conninfo(**params)
    with psycopg.connect(pg_admin_url, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
