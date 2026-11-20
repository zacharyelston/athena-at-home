# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 0 OCR benchmark: Apple Vision vs DeepSeek-OCR-2 (MLX) on the synthetic test set.

  .venv/bin/python bench.py vision                       # Apple Vision on every image
  .venv/bin/python bench.py ds --model mlx-community/DeepSeek-OCR-2-8bit --crops 6 --conds camera --dpis 150

Appends one JSON row per (document, image, engine setting) to results/<tag>.jsonl and keeps raw outputs in
results/raw/. Scores: CER (character error rate vs ground truth, whitespace-collapsed), word F1 (order-free),
key-field recall (amounts, dates and IDs that must come out exactly), seconds/page, generated tokens, peak memory.
"""
import argparse
import html
import json
import os
import re
import time
from collections import Counter

from rapidfuzz.distance import Levenshtein

HERE = os.path.dirname(os.path.abspath(__file__))
TESTSET = os.path.join(HERE, "testset")
RESULTS = os.path.join(HERE, "results")
DOCS = ["bank_statement", "utility_bill", "insurance_letter", "receipt", "intake_form", "w2_form"]
# Same instruction as the HaberDash gpu_worker "markdown" mode. The trailing space matches the model's chat template
# ("{{content}} "): the processor drops the final token in inference mode, and it must be that space, not the ".".
PROMPT = "<image>\nConvert the document to markdown. "


# ---------------------------------------------------------------- scoring
def normalize(text):
    t = re.sub(r"<\|ref\|>.*?<\|/ref\|>", " ", text, flags=re.S)
    t = re.sub(r"<\|det\|>.*?<\|/det\|>", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)                       # HTML tables / tags
    t = html.unescape(t)
    t = re.sub(r"^\s*\|?\s*:?-{3,}.*$", " ", t, flags=re.M)  # markdown table separator rows
    t = re.sub(r"^\s*#+\s*", "", t, flags=re.M)           # headings
    t = t.replace("**", " ").replace("__", " ").replace("`", " ").replace("|", " ")
    t = t.replace("–", "-").replace("—", "-").replace("’", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"\\([$#_*%&])", r"\1", t)                 # LaTeX-style escapes like \$
    return re.sub(r"\s+", " ", t).strip()


def compact(s):
    return re.sub(r"\s+", "", s)


def score(pred_raw, gt_text, fields):
    pred, gt = normalize(pred_raw), normalize(gt_text)
    cer = Levenshtein.distance(gt, pred) / max(len(gt), 1)
    cer_ci = Levenshtein.distance(gt.lower(), pred.lower()) / max(len(gt), 1)
    pw, gw = Counter(pred.split()), Counter(gt.split())
    tp = sum((pw & gw).values())
    prec, rec = tp / max(sum(pw.values()), 1), tp / max(sum(gw.values()), 1)
    f1 = 2 * prec * rec / max(prec + rec, 1e-9)
    cp = compact(pred)
    missing = [f for f in fields if compact(f) not in cp]
    return {"cer": round(cer, 4), "cer_ci": round(cer_ci, 4), "word_f1": round(f1, 4),
            "field_recall": round(1 - len(missing) / max(len(fields), 1), 4), "fields_missing": missing}


def looped(text, min_repeats=4):
    """True if some line of >=8 chars repeats consecutively, a sign of a generation loop."""
    lines = [l.strip() for l in text.splitlines() if len(l.strip()) >= 8]
    run = 1
    for a, b in zip(lines, lines[1:]):
        run = run + 1 if a == b else 1
        if run >= min_repeats:
            return True
    return False


# ---------------------------------------------------------------- engines
def vision_ocr(path):
    import Vision
    from Foundation import NSURL
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(path), None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(str(err))
    lines = []
    for obs in req.results() or []:
        bb = obs.boundingBox()
        lines.append((round(1 - bb.origin.y - bb.size.height, 3), bb.origin.x, obs.topCandidates_(1)[0].string()))
    # group into rows by vertical position, then left-to-right, so table rows read as one line
    lines.sort()
    rows, cur, cur_y = [], [], None
    for y, x, s in lines:
        if cur_y is not None and abs(y - cur_y) > 0.006:
            rows.append(" ".join(t for _, t in sorted(cur)))
            cur = []
        cur.append((x, s))
        cur_y = y if cur_y is None or len(cur) == 1 else cur_y
    if cur:
        rows.append(" ".join(t for _, t in sorted(cur)))
    return "\n".join(rows), {}


class DeepSeek:
    def __init__(self, model_id):
        # Load through mlx-vlm's own DeepSeek-OCR-2 implementation. The model repo also ships custom Python
        # (auto_map); mlx_vlm.load() routes through AutoProcessor, which asks to run it. We never do:
        # trust_remote_code=False, and the model/processor classes come from mlx-vlm.
        from pathlib import Path
        import mlx.core as mx
        from huggingface_hub import snapshot_download
        from mlx_vlm.models.deepseekocr_2 import DeepseekOCR2Processor
        from mlx_vlm.utils import load_model
        self.mx = mx
        t0 = time.time()
        path = Path(snapshot_download(model_id))
        from mlx_vlm.tokenizer_utils import load_tokenizer
        from mlx_vlm.utils import StoppingCriteria
        self.processor = DeepseekOCR2Processor.from_pretrained(str(path), trust_remote_code=False)
        self.model = load_model(path)
        # what mlx_vlm.utils.load_processor() adds after construction: streaming detokenizer + stop criteria
        tok = self.processor.tokenizer
        self.processor.detokenizer = load_tokenizer(path, return_tokenizer=False)(tok)
        eos = getattr(self.model.config, "eos_token_id", None)
        tok.stopping_criteria = StoppingCriteria(eos if isinstance(eos, list) else [eos] if eos is not None else None,
                                                 tok)
        self.load_s = round(time.time() - t0, 1)

    def ocr(self, path, crops, max_tokens):
        from PIL import Image
        from mlx_vlm.generate.dispatch import stream_generate
        mx = self.mx
        img = Image.open(path).convert("RGB")
        kw = {"cropping": crops > 0, "min_patches": 1, "max_patches": max(crops, 1)}
        inputs = self.processor(text=PROMPT, images=[img], **kw)
        out, last = [], None
        for r in stream_generate(self.model, self.processor, PROMPT, input_ids=inputs["input_ids"],
                                 pixel_values=inputs["images"], images_seq_mask=inputs["images_seq_mask"],
                                 images_spatial_crop=mx.array(inputs["images_spatial_crop"]),
                                 max_tokens=max_tokens, temperature=0.0):
            out.append(r.text)
            last = r
        def as_list(x):
            if hasattr(x, "tolist"):
                x = x.tolist()
            return x if isinstance(x, list) else ([] if x is None else [x])
        grid = as_list(inputs["images_spatial_crop"])
        grid = grid[0] if grid and isinstance(grid[0], list) else grid
        nit = as_list(inputs.get("num_image_tokens"))
        meta = {"gen_tokens": getattr(last, "generation_tokens", None),
                "prompt_tokens": getattr(last, "prompt_tokens", None),
                "gen_tps": round(getattr(last, "generation_tps", 0) or 0, 1),
                "grid": [int(g) for g in grid[:2]] if crops > 0 and len(grid) >= 2 else [0, 0],
                "image_tokens": int(nit[0]) if nit else None}
        meta["hit_cap"] = meta["gen_tokens"] is not None and meta["gen_tokens"] >= max_tokens
        return "".join(out), meta


# ---------------------------------------------------------------- runner
def images_for(doc, conds, dpis):
    d = os.path.join(TESTSET, doc)
    for c in conds:
        for dpi in dpis:
            for ext in ("png", "jpg"):
                p = os.path.join(d, f"{c}_{dpi}.{ext}")
                if os.path.exists(p):
                    yield c, dpi, p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("engine", choices=["vision", "ds"])
    ap.add_argument("--model", default="mlx-community/DeepSeek-OCR-2-8bit")
    ap.add_argument("--crops", default="6", help="comma list of max tiles; 0 = global view only")
    ap.add_argument("--conds", default="clean,camera")
    ap.add_argument("--dpis", default="100,150,200")
    ap.add_argument("--docs", default=",".join(DOCS))
    ap.add_argument("--max-tokens", type=int, default=3000)
    ap.add_argument("--tag", default=None)
    a = ap.parse_args()

    conds, dpis, docs = a.conds.split(","), [int(x) for x in a.dpis.split(",")], a.docs.split(",")
    tag = a.tag or (a.engine if a.engine == "vision" else a.model.split("/")[-1])
    os.makedirs(os.path.join(RESULTS, "raw", tag), exist_ok=True)
    out_path = os.path.join(RESULTS, f"{tag}.jsonl")

    ds = DeepSeek(a.model) if a.engine == "ds" else None
    if ds:
        print(f"loaded {a.model} in {ds.load_s}s", flush=True)
    settings = [int(c) for c in a.crops.split(",")] if ds else [None]

    with open(out_path, "a") as fh:
        for doc in docs:
            gt = open(os.path.join(TESTSET, doc, "gt.txt")).read()
            fields = json.load(open(os.path.join(TESTSET, doc, "fields.json")))
            for cond, dpi, path in images_for(doc, conds, dpis):
                for crops in settings:
                    if ds:
                        ds.mx.reset_peak_memory()
                    t0 = time.time()
                    text, meta = ds.ocr(path, crops, a.max_tokens) if ds else vision_ocr(path)
                    secs = round(time.time() - t0, 2)
                    row = {"doc": doc, "cond": cond, "dpi": dpi, "engine": tag, "crops": crops, "seconds": secs,
                           "out_chars": len(text), "looped": looped(text), **meta, **score(text, gt, fields)}
                    if ds:
                        row["peak_gb"] = round(ds.mx.get_peak_memory() / 1e9, 2)
                    name = f"{doc}_{cond}_{dpi}" + (f"_c{crops}" if crops is not None else "")
                    with open(os.path.join(RESULTS, "raw", tag, name + ".txt"), "w") as rf:
                        rf.write(text)
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
                    print(f"{name:38s} {secs:6.1f}s  CER {row['cer']:.3f}  F1 {row['word_f1']:.3f}  "
                          f"fields {row['field_recall']:.2f}" + (f"  peak {row.get('peak_gb')}GB tok {meta.get('gen_tokens')}"
                                                                  if ds else "") + ("  LOOP" if row["looped"] else ""),
                          flush=True)


if __name__ == "__main__":
    main()
