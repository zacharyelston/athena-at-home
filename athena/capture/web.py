# SPDX-License-Identifier: GPL-3.0-or-later
"""``athena capture describe``: a small form, on this computer only, for describing staged items from their slips.

It shows private records, so it is deliberately closed: it listens on 127.0.0.1, answers only requests addressed to
this computer (a web page elsewhere can't reach it through a borrowed name), changes nothing without the token it put
in its own form, loads nothing from anywhere else, runs no script, and logs no content.
"""
from __future__ import annotations

import io
import re
import secrets
from functools import lru_cache
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from starlette.exceptions import HTTPException as StarletteHTTPException

from athena.bundle import edtf, read_bundle
from athena.bundle.ulid import is_ulid

from . import images, staging
from .config import UNSORTED, CaptureConfig

LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
SIDES = ("recto", "verso", "spread")
KINDS = ("text", "photo", "handwriting", "mixed", "blank")
SIZES = {"thumb": 360, "view": 2000}
MAX_FORM_BYTES = 64 * 1024
HEADERS = {
    "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'self'; form-action 'self'; "
                               "base-uri 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
    # "same-origin", not "no-referrer": with the latter a browser sends "Origin: null" on the form's own POST, and
    # the save below couldn't tell the form from a stranger. Nothing is sent to any other site either way.
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")[:64]


@lru_cache(maxsize=512)
def _jpeg(path: str, mtime: float, edge: int) -> bytes:
    from PIL import Image

    with Image.open(path) as im:
        im.draft("RGB", (edge, edge))          # decodes a large JPEG at a fraction of its size
        im = im.convert("RGB")
        im.thumbnail((edge, edge))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82)
        return buf.getvalue()


def form_values(manifest: dict) -> dict:
    item = manifest["describe"]["item"]
    physical = item.get("physical", {})
    return {"series": manifest["describe"]["series"], "title": item.get("title", ""), "date": item.get("date", ""),
            "type": item.get("type", ""), "creator": item.get("creator", ""),
            "container": physical.get("container", ""), "format": physical.get("format", ""),
            "extent": physical.get("extent", ""), "condition": physical.get("condition", ""),
            "tags": ", ".join(item.get("tags", [])), "notes": item.get("notes", "")}


def read_form(fields: dict[str, str], manifest: dict) -> tuple[dict, dict, dict[int, dict], list[str]]:
    """What the form says, as (values to show again, the item description, page changes, problems)."""
    get = lambda k: fields.get(k, "").strip()  # noqa: E731
    values = {k: get(k) for k in ("series", "title", "date", "type", "creator", "container", "format", "extent",
                                  "condition", "tags", "notes")}
    errors = []
    item = dict(manifest["describe"]["item"])              # keeps fields this form doesn't show (language)
    for key in ("title", "creator", "notes"):
        item[key] = values[key]
    item["physical"] = {k: values[k] for k in ("container", "format", "extent", "condition")}

    values["series"] = slugify(values["series"])
    if not values["series"]:
        errors.append("Series: choose where this item belongs (or “unsorted”).")
    try:
        item["date"] = edtf.from_text(values["date"])
        if item["date"]:
            values["date"] = item["date"]
    except edtf.EdtfError as e:
        errors.append(f"Date: {e}")
    item["type"] = slugify(values["type"]) or None
    values["type"] = item["type"] or ""
    tags = []
    for raw in values["tags"].split(","):
        tag = slugify(raw)
        if tag and tag not in tags:
            tags.append(tag)
    item["tags"] = tags
    values["tags"] = ", ".join(tags)

    pages: dict[int, dict] = {}
    for p in manifest["pages"]:
        side, kind = fields.get(f"side_{p['n']}", p["side"]), fields.get(f"kind_{p['n']}", p["kind"])
        if side not in SIDES or kind not in KINDS:
            errors.append(f"Page {p['n']}: choose a side and what's on it from the lists.")
            continue
        pages[p["n"]] = {"side": side, "kind": kind}
    return values, item, pages, errors


def create_app(cfg: CaptureConfig) -> FastAPI:
    if cfg.staging is None:
        raise ValueError("no staging directory: set ATHENA_CAPTURE_STAGING, [capture] staging, or --staging")
    root = cfg.staging
    token = secrets.token_urlsafe(24)
    env = Environment(loader=PackageLoader("athena.capture", "templates"), autoescape=select_autoescape(["html"]),
                      undefined=StrictUndefined)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.headers.get("host", "")
        name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        if name not in LOCAL_HOSTS:
            return Response("This page is only available on the computer it runs on.", status_code=403,
                            media_type="text/plain", headers=HEADERS)
        response = await call_next(request)
        response.headers.update(HEADERS)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def plain_error(request: Request, exc: StarletteHTTPException):
        return Response(str(exc.detail), status_code=exc.status_code, media_type="text/plain")

    def page(name: str, status: int = 200, **ctx) -> HTMLResponse:
        return HTMLResponse(env.get_template(name).render(station=cfg.station, **ctx), status_code=status)

    def bundle_dir(bundle_id: str) -> Path:
        if not is_ulid(bundle_id) or not (root / bundle_id / "manifest.json").is_file():
            raise HTTPException(404, "No such item waiting here (it may have been sent already).")
        return root / bundle_id

    def inside(folder: Path, path: Path) -> Path:
        """``path`` if it really is a file within the bundle; a manifest can't point the form anywhere else."""
        if not path.is_file() or folder.resolve() not in path.resolve().parents:
            raise HTTPException(404, "No such image.")
        return path

    def series_choices(current: str) -> list[tuple[str, str]]:
        choices = [(slug, title) for slug, title, _ in cfg.known_series]
        if not choices:
            return []
        for extra in (UNSORTED, current):
            if extra not in {c[0] for c in choices}:
                choices.append((extra, extra))
        return choices

    def show(bundle_id: str, values: dict, errors: list[str], saved: bool = False, status: int = 200):
        b = read_bundle(bundle_dir(bundle_id))
        quality = staging.summary(b.path)["quality"]
        pages = [{"n": p["n"], "side": p["side"], "kind": p["kind"],
                  "flags": quality.get(str(p["n"]), {}).get("flags", [])} for p in b.manifest["pages"]]
        words = edtf.display(values["date"]) if saved and values["date"] else ""
        return page("item.html", status, id=bundle_id, form=values, errors=errors, saved=saved, date_words=words,
                    slip=bool(b.manifest.get("slip")), pages=pages, token=token, sides=SIDES, kinds=KINDS,
                    series_choices=series_choices(values["series"]), type_choices=list(cfg.types))

    @app.get("/")
    def index():
        items = []
        for d in staging.staged(root):
            s = staging.summary(d)
            s["date"] = edtf.display(s["item"]["date"]) if s["item"].get("date") else ""
            s["title"] = s["item"].get("title", "")
            items.append(s)
        waiting = [s["id"] for s in items if not s["item"].get("title")]
        return page("list.html", items=items, undescribed=len(waiting), next_id=waiting[0] if waiting else None)

    @app.get("/item/{bundle_id}")
    def item(bundle_id: str, saved: int = 0):
        b = read_bundle(bundle_dir(bundle_id))
        return show(bundle_id, form_values(b.manifest), [], saved=bool(saved))

    @app.post("/item/{bundle_id}")
    async def save(bundle_id: str, request: Request):
        d = bundle_dir(bundle_id)
        origin = request.headers.get("origin")
        if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "This form can only be sent from its own page.")
        body = await request.body()
        if len(body) > MAX_FORM_BYTES:
            raise HTTPException(413, "That is more text than this form takes.")
        fields = {k: v[-1] for k, v in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items()}
        if not secrets.compare_digest(fields.get("token", ""), token):
            raise HTTPException(403, "This form is out of date. Open the item again from the list.")
        manifest = read_bundle(d).manifest
        values, described, pages, errors = read_form(fields, manifest)
        if not errors:
            collection = cfg.collection_for(values["series"]) or manifest["describe"]["collection"]
            try:
                staging.redescribe(d, collection=collection, series=values["series"], item=described, pages=pages)
            except staging.DescribeError as e:
                errors.append(str(e))
        if errors:
            return show(bundle_id, values, errors, status=422)
        if fields.get("then") == "next":
            later = [b.name for b in staging.staged(root)
                     if b.name != bundle_id and not read_bundle(b).item.get("title")]
            return RedirectResponse(f"/item/{later[0]}" if later else "/", status_code=303)
        return RedirectResponse(f"/item/{bundle_id}?saved=1", status_code=303)

    @app.get("/item/{bundle_id}/page/{n}.jpg")
    def page_image(bundle_id: str, n: int, size: str = "thumb"):
        b = read_bundle(bundle_dir(bundle_id))
        match = [p for p in b.manifest["pages"] if p.get("n") == n]
        if not match or size not in SIZES:
            raise HTTPException(404, "No such page.")
        path = inside(b.path, b.path / str(match[0].get("page", "")))
        return Response(_jpeg(str(path), path.stat().st_mtime, SIZES[size]), media_type="image/jpeg")

    @app.get("/item/{bundle_id}/slip.jpg")
    def slip(bundle_id: str):
        b = read_bundle(bundle_dir(bundle_id))
        if not b.manifest.get("slip"):
            raise HTTPException(404, "This item has no slip.")
        path = inside(b.path, b.path / b.manifest["slip"])
        if images.extension(path) in ("heic", "heif") and not images.HEIF:
            raise HTTPException(404, "The slip can't be shown here.")
        return Response(_jpeg(str(path), path.stat().st_mtime, 1400), media_type="image/jpeg")

    @app.get("/static/describe.css")
    def css():
        text = resources.files("athena.capture").joinpath("static/describe.css").read_text("utf-8")
        return Response(text, media_type="text/css")

    return app


def serve(cfg: CaptureConfig, port: int = 8090) -> None:
    import uvicorn

    # No access log: request lines carry item ids, and nothing about the records belongs in a terminal's scrollback.
    uvicorn.run(create_app(cfg), host="127.0.0.1", port=port, log_level="warning", access_log=False)
