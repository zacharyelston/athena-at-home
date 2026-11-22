# SPDX-License-Identifier: GPL-3.0-or-later
"""A session folder becomes items and pages: walk it, put the frames in order, split them at separator sheets, look
at every page, and (for an import) write one capture bundle per item into staging.

How a folder is read:

- every directory that holds images is a run of frames, in capture-time order (or by file name when the files
  don't all carry a time);
- a separator sheet starts a new item, and its slip box becomes that item's slip;
- a directory with no separator sheets is one item (the folder-per-item way of working);
- a directory named after one of the library's series puts everything beneath it in that series.

Checking and importing share all of this, so the re-shoot list printed during a session is the same judgement the
import records in each bundle's ``quality.json``.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import athena
from athena.bundle import PageInput, write_bundle
from athena.bundle.manifest import OS_DEBRIS
from athena.bundle.ulid import new as new_ulid

from . import images
from .config import CaptureConfig
from .staging import WORK, Ledger

SOFTWARE = "athena-capture"


@dataclass
class Options:
    series: str | None = None        # for items no folder name places
    order: str = "auto"              # auto | time | name
    source: str | None = None        # camera | flatbed | import; default: from each file's metadata
    ppi: float | None = None         # declared resolution, for pages with no scale card and no scanner metadata
    rotate: int = 0                  # turn every frame clockwise by 0, 90, 180 or 270 degrees first
    duplex: bool = False             # pages alternate front, back
    jobs: int = 1


@dataclass
class Shot:
    path: Path
    rel: str
    facts: images.Facts
    sha256: str


@dataclass
class PageResult:
    shot: Shot
    n: int = 0
    kind: str = "text"
    width: int = 0
    height: int = 0
    ppi: float | None = None
    ppi_source: str = "unknown"
    metrics: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)       # reasons to shoot the page again
    notes: list[str] = field(default_factory=list)       # worth knowing, not worth a re-shoot
    page_file: str | None = None
    quick: list | None = None


@dataclass
class Item:
    number: int
    folder: str
    series: str
    separator: Shot | None = None
    slip_file: str | None = None
    pages: list[PageResult] = field(default_factory=list)
    status: str = "new"              # new | imported | conflict | unreadable
    detail: str = ""
    bundle: str | None = None        # the id it was written as (import), or already has


@dataclass
class Session:
    root: Path
    items: list[Item] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)     # (file, why it isn't a page)
    frames: int = 0
    separators: int = 0

    def reshoots(self) -> list[tuple[Item, PageResult]]:
        return [(i, p) for i in self.items for p in i.pages if p.flags]

    @property
    def problems(self) -> int:
        blocked = ("conflict", "unreadable")
        return (sum(1 for i, _ in self.reshoots() if i.status not in blocked)
                + sum(1 for i in self.items if i.status in blocked))


# --- looking at one frame (runs in a worker process) --------------------------------------------------------------

def judge(cfg: CaptureConfig, m: dict, kind: str, ppi: float | None, camera: bool, cropped: bool) -> tuple[list, list]:
    """Reasons to shoot a page again, and notes, from its measurements and the station's thresholds."""
    flags, notes = [], []
    if m.get("cut_off"):
        flags.append("the page runs off the edge of the frame")
    if kind in ("text", "mixed"):
        if m["sharpness"] < cfg.min_sharpness:
            flags.append(f"blurred (sharpness {m['sharpness']:.2f}, need {cfg.min_sharpness:g})")
        elif m["sharpness_min"] < cfg.min_sharpness / 2 and m.get("soft_where"):
            flags.append(f"soft at the {m['soft_where']} (sharpness {m['sharpness_min']:.2f} there, "
                         f"need {cfg.min_sharpness / 2:g})")
    elif kind == "photo" and m["sharpness"] < cfg.min_sharpness_photo:
        flags.append(f"blurred (sharpness {m['sharpness']:.2f}, need {cfg.min_sharpness_photo:g})")
    if camera and kind != "photo" and 0 < m.get("brightness", 0) < cfg.min_brightness:
        flags.append(f"too dark (paper at {m['brightness']:.0f} of 255, need {cfg.min_brightness:g})")
    if camera and m["glare"] > cfg.max_glare:
        flags.append(f"glare on {m['glare']:.0%} of the page (limit {cfg.max_glare:.0%})")
    if kind in ("text", "blank") and m["evenness"] < cfg.min_evenness:
        flags.append(f"uneven light (darkest part {m['evenness']:.0%} of the brightest, need {cfg.min_evenness:.0%})")
    if m.get("keystone", 0) > cfg.max_keystone:
        flags.append(f"camera not square to the page (one side {m['keystone']:.0%} longer than the other)")
    need = cfg.min_ppi_photo if kind == "photo" else cfg.min_ppi
    if ppi is None:
        notes.append("resolution not measured (no scale card in the frame)")
    elif ppi < need:
        flags.append(f"ppi {ppi:.0f} < {need:.0f}")
    if camera and not cropped and not m.get("cut_off"):
        notes.append("page edges not found; the whole frame was kept")
    return flags, notes


def _quick(rgb) -> list | None:
    """Apple Vision's lines for a page, or None where there's no Apple Vision."""
    try:
        import Vision  # noqa: F401
        from PIL import Image

        from sieve import tier0
        im = Image.fromarray(rgb)
        im.thumbnail((3000, 3000))
        return [{"text": ln.text, "x": round(ln.x, 4), "y": round(ln.y, 4), "w": round(ln.w, 4), "h": round(ln.h, 4),
                 "confidence": round(ln.confidence, 3)} for ln in tier0.recognize(im)]
    except Exception:
        return None


def examine(job: dict) -> dict:
    """Decode one frame and report what it is. ``job`` and the result are plain data, so this can run in another
    process. With ``job["work"]`` set, the page image (or the slip) is written there at full resolution."""
    import cv2
    import numpy as np

    from . import page, sheets

    cfg: CaptureConfig = job["cfg"]
    path, work, key = Path(job["path"]), job.get("work"), job["key"]
    try:
        rgb = images.load_rgb(path)
    except images.Unreadable as e:
        return {"error": str(e)}
    if job.get("rotate"):
        rgb = np.ascontiguousarray(np.rot90(rgb, k=-(job["rotate"] // 90)))
    corners = sheets.find_separator(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
    if corners is not None:
        out = {"separator": True}
        if work:
            out["slip"] = str(Path(work) / f"{key}.slip.jpg")
            images.save_jpeg(Path(out["slip"]), sheets.slip_from_separator(rgb, corners), quality=88)
        return out

    found, small = page.look(rgb)
    m = page.measure(small)
    ppi, source = page.measured_ppi(found, cfg.tag_mm * cfg.print_scale), "apriltag"
    if ppi is None and job.get("dpi"):
        ppi, source = job["dpi"], "scanner"
    if ppi is None and job.get("declared_ppi"):
        ppi, source = job["declared_ppi"], "declared"
    if ppi is None:
        source = "unknown"
    metrics = {"sharpness": m.sharpness, "sharpness_min": m.sharpness_min, "soft_where": m.soft_where,
               "glare": m.glare, "evenness": m.evenness, "brightness": m.brightness, "marks": m.marks,
               "laplacian_var": m.laplacian_var, "skew_deg": found.skew_deg,
               "keystone": found.keystone, "cropped": found.cropped, "cut_off": found.cut_off}
    out = {"separator": False, "kind": m.kind, "ppi": ppi, "ppi_source": source, "metrics": metrics,
           "width": found.size[0], "height": found.size[1], "quick": None, "page": None}
    if work:
        full = page.square_up(rgb, found)
        out["height"], out["width"] = full.shape[:2]
        out["page"] = str(Path(work) / f"{key}.jpg")
        images.save_jpeg(Path(out["page"]), full, cfg.jpeg_quality, job.get("icc"))
        if job.get("quick") and m.kind != "blank":
            out["quick"] = _quick(full)
            if m.kind == "photo" and out["quick"] and sum(len(ln["text"]) for ln in out["quick"]) > 200:
                out["kind"] = "mixed"                # a picture with a good deal of text on it
    out["flags"], out["notes"] = judge(cfg, metrics, out["kind"], ppi, job["camera"], found.cropped)
    return out


# --- reading the folder -------------------------------------------------------------------------------------------

def _natural(name: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _hidden(name: str) -> bool:
    return name.startswith(".") or name in OS_DEBRIS


def _runs(root: Path, order: str) -> tuple[list[tuple[str, list[Shot]]], list[tuple[str, str]]]:
    """Each directory's frames in order, and the files that aren't frames with the reason."""
    runs, skipped = [], []
    for here, dirs, files in os.walk(root):
        dirs[:] = sorted((d for d in dirs if not _hidden(d)), key=_natural)
        shots = []
        for name in sorted((f for f in files if not _hidden(f)), key=_natural):
            p = Path(here) / name
            rel = p.relative_to(root).as_posix()
            ext = images.extension(p)
            if ext in images.NOT_YET:
                skipped.append((rel, images.NOT_YET[ext]))
            if ext not in images.READABLE or p.is_symlink():
                continue                              # notes, videos, sidecar files: not ours to judge
            try:
                facts = images.facts(p)
            except images.Unreadable:
                facts = images.Facts(None, "", "", False, False, None, None)   # looking at it will say why
            shots.append(Shot(p, rel, facts, images.sha256(p)))
        if not shots:
            continue
        by_time = order == "time" or (order == "auto" and all(s.facts.taken for s in shots))
        if by_time:
            shots.sort(key=lambda s: (s.facts.taken is None, s.facts.taken or 0, _natural(s.path.name)))
        runs.append((Path(here).relative_to(root).as_posix(), shots))
    return runs, skipped


def _series_for(folder: str, cfg: CaptureConfig, opts: Options) -> str:
    known = cfg.series_slugs()
    for part in reversed(Path(folder).parts):
        if part.lower() in known:
            return part.lower()
    return opts.series or cfg.series


def survey(root: str | Path, cfg: CaptureConfig, opts: Options | None = None, work: Path | None = None) -> Session:
    """Read a session folder. With ``work``, page images and slips are written there for an import to pick up."""
    opts = opts or Options()
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a folder")
    ledger = Ledger(cfg.staging)
    runs, skipped = _runs(root, opts.order)
    session = Session(root, skipped=skipped, frames=sum(len(s) for _, s in runs))

    jobs = []
    for _, shots in runs:
        for s in shots:
            if s.sha256 in ledger.pages or s.sha256 in ledger.separators:
                continue
            jobs.append({"path": str(s.path), "key": s.sha256[:24], "cfg": cfg, "work": str(work) if work else None,
                         "rotate": opts.rotate, "dpi": s.facts.dpi, "declared_ppi": opts.ppi, "icc": s.facts.icc,
                         "camera": opts.source == "camera" if opts.source else s.facts.camera,
                         "quick": cfg.quick})
    if opts.jobs > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=opts.jobs) as pool:
            results = list(pool.map(examine, jobs))
    else:
        results = [examine(j) for j in jobs]
    seen = {Path(j["path"]): r for j, r in zip(jobs, results)}

    for folder, shots in runs:
        series = _series_for(folder, cfg, opts)
        current: Item | None = None
        for s in shots:
            r = seen.get(s.path)
            is_separator = s.sha256 in ledger.separators or bool(r and r.get("separator"))
            if is_separator:
                session.separators += 1
                current = Item(0, folder, series, separator=s, slip_file=(r or {}).get("slip"),
                               bundle=ledger.separators.get(s.sha256))
                session.items.append(current)
                continue
            if current is None:
                current = Item(0, folder, series)
                session.items.append(current)
            if r is None:                             # already in a bundle
                current.pages.append(PageResult(s))
                current.bundle = ledger.pages[s.sha256]
            elif "error" in r:
                current.pages.append(PageResult(s, flags=[r["error"]]))
                current.status, current.detail = "unreadable", f"{s.rel}: {r['error']}"
            else:
                current.pages.append(PageResult(
                    s, kind=r["kind"], width=r["width"], height=r["height"], ppi=r["ppi"],
                    ppi_source=r["ppi_source"], metrics=r["metrics"], flags=r["flags"], notes=r["notes"],
                    page_file=r["page"], quick=r["quick"]))

    session.items = [i for i in session.items if i.pages]      # a separator with nothing after it starts nothing
    for number, item in enumerate(session.items, start=1):
        item.number = number
        for n, p in enumerate(item.pages, start=1):
            p.n = n
        known = [ledger.pages.get(p.shot.sha256) for p in item.pages]
        if item.status == "unreadable":
            continue
        if all(known):
            item.status, item.bundle = "imported", known[0]
        elif any(known):
            ids = sorted({k for k in known if k})
            item.status = "conflict"
            item.detail = (f"{sum(1 for k in known if k)} of its {len(known)} pages are already in bundle "
                           f"{', '.join(ids)}; discard that bundle (athena capture discard) to import the item again")
    return session


# --- importing ------------------------------------------------------------------------------------------------------

def _source(item: Item, opts: Options) -> str:
    if opts.source:
        return opts.source
    facts = [p.shot.facts for p in item.pages]
    if all(f.camera for f in facts):
        return "camera"
    if all(f.dpi and not f.camera for f in facts):
        return "flatbed"
    return "import"


def _device(item: Item) -> dict | None:
    seen = {(p.shot.facts.make, p.shot.facts.model) for p in item.pages}
    if len(seen) != 1:
        return None
    make, model = seen.pop()
    return {k: v for k, v in (("make", make), ("model", model)) if v} or None


def quality_of(item: Item) -> dict:
    return {str(p.n): {**p.metrics, "kind_guess": p.kind, "flags": p.flags, "notes": p.notes} for p in item.pages}


def import_session(root: str | Path, cfg: CaptureConfig, opts: Options | None = None) -> Session:
    """Write one bundle per new item into ``cfg.staging``. Items already imported are left alone, so running this
    twice on the same folder (or on a folder that has grown) only adds what's new."""
    opts = opts or Options()
    if cfg.staging is None:
        raise ValueError("no staging directory: set ATHENA_CAPTURE_STAGING, [capture] staging, or --staging")
    if cfg.collection is None:
        raise ValueError("no collection: set [capture] collection or --collection")
    staging = cfg.staging
    for stale in (staging / WORK).glob("*"):          # left by an import that was interrupted more than a day ago
        if time.time() - stale.stat().st_mtime > 86400:
            shutil.rmtree(stale, ignore_errors=True)
    work = staging / WORK / new_ulid()
    work.mkdir(parents=True)
    try:
        session = survey(root, cfg, opts, work=work)
        ledger = Ledger(staging)
        station = {"name": cfg.station, "software": SOFTWARE, "version": athena.__version__}
        for item in session.items:
            if item.status != "new":
                continue
            pages = [PageInput(raw=p.shot.path, page=Path(p.page_file), width=p.width, height=p.height,
                               side="verso" if opts.duplex and p.n % 2 == 0 else "recto", kind=p.kind, ppi=p.ppi,
                               ppi_source=p.ppi_source, has_gps=p.shot.facts.has_gps, quick=p.quick)
                     for p in item.pages]
            collection = cfg.collection_for(item.series) or cfg.collection
            path = write_bundle(staging, station=station, source=_source(item, opts), operator=cfg.operator,
                                describe={"collection": collection, "series": item.series, "item": {}},
                                pages=pages, device=_device(item), quality=quality_of(item),
                                slip=Path(item.slip_file) if item.slip_file else None)
            item.bundle = path.name
            ledger.record(path.name, [p.shot.sha256 for p in item.pages],
                          item.separator.sha256 if item.separator else None, Path(root).name,
                          [p.shot.rel for p in item.pages])
        return session
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            (staging / WORK).rmdir()
        except OSError:
            pass


# --- the report -----------------------------------------------------------------------------------------------------

def report(session: Session, imported: bool = False) -> str:
    """The summary and the re-shoot list, as text for the terminal."""
    pages = sum(len(i.pages) for i in session.items)
    lines = [f"{session.root}: {session.frames} images, {session.separators} separator sheets, "
             f"{len(session.items)} items, {pages} pages"]
    for i in session.items:
        head = f"  item {i.number}: {len(i.pages)} page{'s' if len(i.pages) != 1 else ''}"
        if i.status != "imported":
            head += f", series {i.series}"
        if i.folder != ".":
            head += f", in {i.folder}/"
        if i.status == "imported":
            head += f" (already imported as {i.bundle})"
        elif i.status in ("conflict", "unreadable"):
            head += f"\n    NOT IMPORTED: {i.detail}"
        elif imported and i.bundle:
            head += f" -> {i.bundle}"
        lines.append(head)
    for rel, why in session.skipped:
        lines.append(f"  skipped {rel}: {why}")
    reshoots = [(i, p) for i, p in session.reshoots() if i.status != "unreadable"]
    if reshoots:
        lines.append(f"Re-shoot list: {len(reshoots)} page{'s' if len(reshoots) != 1 else ''}")
        for i, p in reshoots:
            lines.append(f"  {p.shot.rel}  item {i.number} page {p.n}: {'; '.join(p.flags)}")
    else:
        lines.append("Re-shoot list: nothing to shoot again")
    notes = [(i, p) for i in session.items for p in i.pages if p.notes]
    if notes:
        counts: dict[str, int] = {}
        for _, p in notes:
            for n in p.notes:
                counts[n] = counts.get(n, 0) + 1
        lines.append("Notes:")
        lines += [f"  {count} page{'s' if count != 1 else ''}: {note}" for note, count in counts.items()]
    return "\n".join(lines)


def as_data(session: Session) -> dict:
    """The same, for ``--json``."""
    return {
        "root": str(session.root), "frames": session.frames, "separators": session.separators,
        "skipped": [{"file": f, "reason": r} for f, r in session.skipped],
        "items": [{"number": i.number, "folder": i.folder, "series": i.series, "status": i.status,
                   "detail": i.detail, "bundle": i.bundle,
                   "pages": [{"n": p.n, "file": p.shot.rel, "kind": p.kind, "ppi": p.ppi,
                              "ppi_source": p.ppi_source, "flags": p.flags, "notes": p.notes,
                              "metrics": p.metrics} for p in i.pages]} for i in session.items],
    }

