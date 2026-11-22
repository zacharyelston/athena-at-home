# SPDX-License-Identifier: GPL-3.0-or-later
"""The capture pilot's scorer, runner and report, on made-up historical pages with a stand-in for the engines.

These test the measuring, not the engines: a page of "handwriting" is read badly here on purpose.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("cv2")
pytest.importorskip("rapidfuzz")
pytest.importorskip("psycopg", reason="the pilot sends pages the way the library does (library extra)")

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "phase0", Path(__file__).parent / "fixtures"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import gen_testset as gen  # noqa: E402
import synthetic_pilot  # noqa: E402
from conftest import FakeEngine, dense_tier0, sparse_tier0  # noqa: E402

import pilot  # noqa: E402
from pilot import report, run, score  # noqa: E402
from sieve import service  # noqa: E402


# --- the scorer -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(gen.HISTORICAL))
def test_a_perfect_reading_scores_perfectly_on_every_style(name):
    layout, _, _ = gen.render_historical(name)
    truth = "\n".join(layout.lines)
    exact = score.score(truth, truth, layout.fields)
    assert (exact["cer"], exact["word_f1"], exact["keys_exact"]) == (0.0, 1.0, len(layout.fields))
    # the same words as the model writes them: a heading, bold, a table, a grounding tag, different line breaks
    words = truth.split()
    dressed = "# " + " ".join(words[:4]) + "\n\n**" + " ".join(words[4:9]) + "**\n\n| " + " | ".join(words[9:]) + " |\n|---|---|\n"
    dressed += "<|ref|>figure<|/ref|><|det|>[[1,2,3,4]]<|/det|>"
    again = score.score(dressed, truth, layout.fields)
    assert (again["cer"], again["word_f1"]) == (0.0, 1.0)


def test_scores_fall_with_what_is_wrong():
    layout, _, _ = gen.render_historical("mimeo_newsletter")
    truth = "\n".join(layout.lines)
    dropped = "\n".join(layout.lines[:-3])                        # the last paragraph is missing
    garbled = truth.replace("e", "c").replace("a", "o")
    one_wrong = truth.replace("$86.40", "$36.40")
    a, b, c = (score.score(t, truth, layout.fields) for t in (dropped, garbled, one_wrong))
    assert 0.6 < a["word_f1"] < 1 and a["cer"] > 0.1 and "$86.40" in a["keys_missing"]
    assert b["word_f1"] < 0.6 < a["word_f1"] and b["cer"] > 0.1 and b["keys_exact"] <= 3
    assert c["word_f1"] > 0.97 and c["keys_missing"] == ["$86.40"] and c["keys_exact"] == len(layout.fields) - 1
    assert score.score("", truth, layout.fields)["word_f1"] == 0.0 and score.score("", "")["word_f1"] == 1.0


def test_key_items_ignore_spacing_and_case_does_not_hide_an_error():
    truth = "Flower Show, May 12, at the Grange Hall. Cleared $86.40."
    read = "Flower Show, May  12, at the Grange\nHall. Cleared $ 86.40."
    assert score.score(read, truth, ["May 12", "Grange Hall", "$86.40"])["keys_exact"] == 3
    lower = score.score(truth.lower(), truth, ["Grange Hall"])
    assert lower["keys_exact"] == 0 and lower["cer"] > 0 and lower["cer_ci"] == 0.0
    assert "keys" not in score.score(read, truth)                  # no key list typed: nothing claimed about keys


def test_agreement_between_two_readings():
    a, b = "The meeting was called to order at 2:15 p.m.", "## The meeting was called to order at 2:15 p.m."
    assert score.agreement(a, b) == 1.0 == score.agreement(b, a)
    assert score.agreement(a, "The meeting was called to order") < 1
    assert score.agreement(a, "something else entirely") == 0.0


# --- a synthetic pilot ------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def made(tmp_path_factory) -> dict:
    return synthetic_pilot.make_pilot(tmp_path_factory.mktemp("pilot"), methods=("camera-a", "camera-b", "flatbed"),
                                      ppi=110, blur={"camera-b": 2.5})


def reader(made: dict, calls: list | None = None):
    """Stands in for Sieve: reads each page as well as its material deserves, so the scores are known in advance."""
    def ask(req: dict) -> dict:
        page = made["pages"][req["id"]]
        text, material = page["text"], page["material"]
        if calls is not None:
            calls.append((req["id"], req["tiles"], req["tier0"]))
        tiles = 6 if req["tiles"] == 6 or material in ("newsprint", "typeset") else 0
        if material == "handwriting":
            text = text.replace("e", "c").replace("a", "o").replace("t", "l")
        elif material == "mimeograph" and tiles == 0:
            text = "\n".join(text.splitlines()[:-4])               # the overview misses the faint last lines
        elif material == "typewritten":
            text = text.replace("$214.37", "$214.87")              # one wrong digit in a key item
        if page["method"] == "camera-b":
            text = text.replace("the", "tbe")                      # the blurred method reads worse everywhere
        reply = {"text": "# " + text, "model": "fake/reader", "tiles": tiles,
                 "tile_reason": "requested" if req["tiles"] == 6 else "stand-in", "seconds": 4.0 + tiles,
                 "gen_tokens": len(text) // 4, "image_tokens": 257, "looped": False,
                 "hit_cap": material == "newsprint" and tiles == 0, "tier0": None, "id": req["id"]}
        if req["tier0"]:
            reply["tier0"] = {"text": page["text"].replace("Mrs.", "Mrs"), "chars": len(page["text"])}
        return reply
    return ask


@pytest.fixture(scope="module")
def results(made, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("results")
    calls: list = []
    counts = run.run(made["bundles"], out, reader(made, calls))
    assert counts == {"pages": 15, "ocr": 15, "skipped": 0, "errors": 0, "already": 0}
    # one request with Sieve's own tile choice and tier 0; a second at 6 tiles only where the first chose fewer
    assert sum(1 for _, tiles, tier0 in calls if tiles == "auto" and tier0) == 15
    assert sum(1 for _, tiles, tier0 in calls if tiles == 6 and not tier0) == 9
    done = score.score_run(out, made["truth"])
    assert done == {"pages": 15, "with_truth": 15, "engines": ["ds-6", "ds-auto", "vision"]}
    return out


def test_the_page_list_says_what_to_transcribe(made, results):
    pages = pilot.read_jsonl(results / "pages.jsonl")
    assert len(pages) == 15 and {p["method"] for p in pages} == {"camera-a", "camera-b", "flatbed"}
    assert {p["material"] for p in pages} == {"mimeograph", "typewritten", "newsprint", "typeset", "handwriting"}
    camera = next(p for p in pages if p["method"] == "camera-a")
    scan = next(p for p in pages if p["method"] == "flatbed")
    assert camera["ppi_source"] == "apriltag" and camera["taken"] and camera["comparison"] and "sharpness" in camera["quality"]
    assert (scan["ppi"], scan["ppi_source"], scan["taken"]) == (110.0, "scanner", None)
    assert (made["truth"] / camera["truth"]).is_file()             # the name the list gives is the name scored
    lines = (results / "pages.tsv").read_text().splitlines()
    assert lines[0].split("\t")[-1] == "transcription file" and len(lines) == 16 and camera["truth"] in lines[1]


def test_rows_and_texts(made, results):
    rows = pilot.read_jsonl(results / "ocr.jsonl")
    assert len(rows) == 45 and not [r for r in rows if "error" in r]
    reused = [r for r in rows if r.get("same_as")]
    assert len(reused) == 6 and all(r["engine"] == "ds-6" and r["tiles"] == 6 for r in reused)
    page = next(p for p, v in made["pages"].items() if v["material"] == "mimeograph" and v["method"] == "camera-a")
    auto, six = (pilot.text_file(results, page, e).read_text() for e in ("ds-auto", "ds-6"))
    assert len(auto) < len(six) and pilot.text_file(results, page, "vision").read_text().count("Mrs.") == 0
    scores = {(r["page"], r["engine"]): r for r in pilot.read_jsonl(results / "scores.jsonl")}
    assert scores[(page, "ds-6")]["word_f1"] == 1.0 > scores[(page, "ds-auto")]["word_f1"]
    assert 0.9 < scores[(page, "ds-6")]["agrees_with_vision"] < 1 and "agrees_with_vision" not in scores[(page, "vision")]


def test_running_again_does_nothing_and_a_failure_is_retried(made, results, tmp_path):
    def never(req):
        raise AssertionError("everything was done already")
    again = run.run(made["bundles"], results, never)
    assert (again["ocr"], again["already"]) == (0, 15)

    out = tmp_path / "flaky"
    good = reader(made)
    seen: list = []

    def flaky(req):
        seen.append(req["id"])
        return {"error": "TimeoutError: no reply"} if len(seen) == 1 else good(req)

    first = run.run(made["bundles"], out, flaky, limit=2)
    assert (first["ocr"], first["errors"]) == (2, 1)
    assert [r for r in pilot.read_jsonl(out / "ocr.jsonl") if "error" in r][0]["engine"] == "ds-auto"
    second = run.run(made["bundles"], out, good, limit=2)
    assert (second["ocr"], second["already"], second["errors"]) == (1, 1, 0)
    assert not [r for r in pilot.read_jsonl(out / "ocr.jsonl") if "error" in r]


def test_report_tables_and_what_the_criteria_say(made, results):
    data = report.load(results)
    rows = {(r["material"], r["engine"]): r for r in report.by_material(data)}
    assert rows[("typeset", "ds-auto")]["pages"] == 3 and rows[("typeset", "ds-auto")]["six_tiles"] == 1.0
    assert rows[("mimeograph", "ds-auto")]["six_tiles"] == 0.0 and rows[("newsprint", "ds-auto")]["caps"] == 0
    assert rows[("handwriting", "ds-auto")]["f1"] < 0.5 < rows[("typeset", "vision")]["f1"]
    assert rows[("typewritten", "ds-auto")]["keys_exact"] == pytest.approx(7 / 8)

    verdicts = {p["material"]: p for p in report.policy(report.by_material(data))}
    assert verdicts["handwriting"]["policy"] == "image only"
    assert verdicts["typewritten"]["policy"] == "shown, labelled uncorrected" and "key items 88%" in verdicts["typewritten"]["why"]
    assert verdicts["mimeograph"]["six_tiles_worth_it"] and not verdicts["typeset"]["six_tiles_worth_it"]
    assert verdicts["typeset"]["policy"] in ("text of record", "shown, labelled uncorrected")

    methods, tagged = report.by_method(data)
    by = {m["method"]: m for m in methods}
    assert tagged and set(by) == {"camera-a", "camera-b", "flatbed"} and all(m["pages"] == 5 for m in methods)
    assert (by["camera-a"]["seconds"], by["camera-b"]["seconds"], by["flatbed"]["seconds"]) == (36, 62, None)
    assert by["camera-a"]["pages_per_hour"] == 100                 # page to page, separator sheets included
    assert by["flatbed"]["ppi"] == 110 and by["camera-b"]["soft_pages"] >= 3 > by["camera-a"]["soft_pages"]
    assert by["camera-b"]["f1"] < by["camera-a"]["f1"] - 0.02
    chosen, notes = report.pick_method(methods)
    assert chosen is None and any(n.startswith("camera-b") and "soft or blurred" in n for n in notes)
    assert all("under 400" in n for n in notes)                   # nothing in this tiny pilot reaches 400 ppi

    text = report.render(data, total_pages=2500, today="2026-11-21")
    assert "| handwriting | **image only** |" in text and "For 2,500 pages:" in text and "Over the pages tagged" in text
    assert "no method qualifies" in text and "- flatbed: no capture times in the files" in text
    assert "| camera-a | 25.0 | 10 |" in text                     # 2,500 pages at 100 an hour


def test_the_report_carries_no_content(made, results):
    text = report.render(report.load(results), total_pages=2500)
    for title in made["titles"]:
        assert title not in text
    for page, facts in made["pages"].items():
        assert page.split("/")[0] not in text                      # no bundle ids
        for key in facts["keys"]:
            if not key.isdigit():                                  # ("1928" could be any number in a table)
                assert key not in text, key
    for word in ("Whitcombe", "Dunmore", "phlox", "IMG_", ".jpg", ".txt", "/"):
        assert word not in text.replace("s/page", "").replace("Pages/hour", "").replace("MB/page", ""), word
    assert report.label("Mimeograph — see Mrs. Whitcombe's note, 12 Elm Street, about the 1962 show") == \
        "mimeograph  see mrs whit"
    assert report.label("") == "not set" and report.label(None) == "not set"


def test_method_choice_and_time_per_page():
    def page(n, method, ppi, taken, kind="text", flags=()):
        return {"page": f"B{method}/{n}", "bundle": f"B{method}", "n": n, "material": "typeset", "method": method,
                "comparison": True, "kind": kind, "ppi": ppi, "raw_bytes": 4_000_000, "page_bytes": 3_000_000,
                "taken": taken, "quality": {"sharpness": 3.0}, "flags": list(flags)}

    pages, scores = {}, {}
    for method, ppi, gap, f1 in (("stand", 520, 20, 0.97), ("scanner", 600, None, 0.98), ("webcam", 180, 8, 0.90)):
        for n in range(1, 5):
            taken = None if gap is None else f"2026-12-03T10:{(n * gap) // 60:02d}:{(n * gap) % 60:02d}"
            if method == "stand" and n == 4:
                taken = "2026-12-03T11:30:00"                      # a tea break is not handling time
            p = page(n, method, ppi, taken, flags=["ppi 180 < 400"] if method == "webcam" else ())
            pages[p["page"]] = p
            scores[(p["page"], "ds-auto")] = {"word_f1": f1, "agrees_with_vision": 0.95}
    pages["Bstand/5"] = page(5, "stand", 520, None, kind="photo")
    data = {"pages": pages, "ocr": {}, "scores": scores}
    methods, _ = report.by_method(data)
    by = {m["method"]: m for m in methods}
    assert by["stand"]["seconds"] == 20 and by["stand"]["pages_per_hour"] == 180 and by["scanner"]["seconds"] is None
    assert by["stand"]["photo_ppi_min"] == 520 and by["stand"]["documents"] == 4
    chosen, notes = report.pick_method(methods)
    assert chosen == "stand"                                       # both qualify; the timed one is the faster known
    assert notes == ["webcam: lowest page 180 ppi, under 400; OCR 0.900 against 0.980 for the best method"]
    text = report.render(data, total_pages=1800)
    assert "for documents: **stand**" in text and "Photographs with stand: does not meet 600 ppi" in text
    assert "| stand | 10.0 | 4 | 12.6 GB |" in text               # 1,800 pages at 180 an hour, 7 MB a page


# --- plumbing ---------------------------------------------------------------------------------------------------------

def test_the_request_is_the_one_sieve_serves(made, tmp_path):
    """The real request handler with a stand-in model: the runner speaks ``ocr.page`` as the worker does."""
    engine = FakeEngine(text="# Page\nRIVERSIDE GARDEN SOCIETY")
    tier0 = iter([dense_tier0, sparse_tier0] * 8)
    counts = run.run(made["bundles"], tmp_path / "out", lambda req: service.handle(req, engine, next(tier0)), limit=2)
    assert counts["ocr"] == 2 and counts["errors"] == 0
    rows = pilot.read_jsonl(tmp_path / "out" / "ocr.jsonl")
    auto = [r for r in rows if r["engine"] == "ds-auto"]
    assert [r["tiles"] for r in auto] == [6, 0] and "table-like" in auto[0]["tile_reason"]
    assert {r["engine"] for r in rows} == {"ds-auto", "ds-6", "vision"} and len(engine.calls) == 3
    sent = engine.calls[0]["size"]
    assert max(sent) <= run.OCR_MAX_EDGE and sent[0] < sent[1]     # the page as the library would send it


def test_results_stay_out_of_the_repository(made, tmp_path, capsys):
    inside = ROOT / "pilot-results-should-never-exist"
    with pytest.raises(pilot.PilotError, match="inside the repository"):
        run.run(made["bundles"], inside, None)
    assert not inside.exists()
    assert run.main([str(made["bundles"]), "--out", str(inside), "--list"]) == 2
    assert "inside the repository" in capsys.readouterr().err
    assert score.main([str(ROOT / "pilot"), "--truth", str(tmp_path)]) == 2
    assert report.main([str(tmp_path / "empty")]) == 2 and "run python -m pilot.run first" in capsys.readouterr().err
    with pytest.raises(pilot.PilotError, match="no capture bundles"):
        run.run(tmp_path, tmp_path / "out", None)


def test_command_line_list_score_report(made, results, tmp_path, capsys):
    out = tmp_path / "listed"
    assert run.main([str(made["bundles"]), "--out", str(out), "--list"]) == 0
    assert "15 pages listed" in capsys.readouterr().err and (out / "pages.tsv").is_file()
    assert not (out / "ocr.jsonl").exists()
    assert score.main([str(results)]) == 0                         # no transcriptions: agreement only
    assert "0 with a transcription" in capsys.readouterr().err
    assert all("word_f1" not in r for r in pilot.read_jsonl(results / "scores.jsonl"))
    assert "not decided" in report.render(report.load(results))
    assert score.main([str(results), "--truth", str(made["truth"])]) == 0
    capsys.readouterr()
    assert report.main([str(results), "--total-pages", "2500", "--out", str(tmp_path / "report.md")]) == 0
    written = (tmp_path / "report.md").read_text()
    assert written.startswith("# Capture pilot: report (") and "## What the capture tool measured" in written
    assert json.loads((results / "pages.jsonl").read_text().splitlines()[0])["page"]
