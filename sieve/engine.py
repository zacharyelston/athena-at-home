# SPDX-License-Identifier: GPL-3.0-or-later
# Derived from Athena@Home phase0/bench.py (DeepSeek loader), which was measured in docs/phase0-results.md.
"""DeepSeek-OCR-2 on Apple Silicon through mlx-vlm, in process.

Loading never runs the model repo's own Python (its config has an ``auto_map``): the processor and model classes
come from mlx-vlm, with ``trust_remote_code=False``. ``mlx_vlm.load()`` would route through ``AutoProcessor``, which
asks to run that code and fails non-interactively, so the pieces it would add are attached here by hand.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from . import guard

DEFAULT_MODEL = "mlx-community/DeepSeek-OCR-2-8bit"
MAX_TILES = 6

# The trailing space matters: it matches the model's chat template ("{{content}} "). The processor drops the final
# token in inference mode; without the space it drops the "." and the model stops after one token.
PROMPTS = {
    "markdown": "Convert the document to markdown.",
    "free": "Free OCR.",
}


def build_prompt(instruction: str) -> str:
    return f"<image>\n{instruction.strip()} "


@dataclass
class OcrResult:
    text: str
    tiles: int
    seconds: float
    gen_tokens: int | None
    image_tokens: int | None
    looped: bool
    hit_cap: bool
    grid: list[int] = field(default_factory=lambda: [0, 0])


class Engine:
    """One loaded DeepSeek-OCR-2 model. ``ocr()`` is serialized: MLX runs one generation at a time here."""

    def __init__(self, model_id: str = DEFAULT_MODEL):
        from pathlib import Path

        import mlx.core as mx
        from huggingface_hub import snapshot_download
        from mlx_vlm.models.deepseekocr_2 import DeepseekOCR2Processor
        from mlx_vlm.tokenizer_utils import load_tokenizer
        from mlx_vlm.utils import StoppingCriteria, load_model

        self.mx = mx
        self.model_id = model_id
        self._lock = threading.Lock()
        t0 = time.time()
        path = Path(snapshot_download(model_id))
        self.processor = DeepseekOCR2Processor.from_pretrained(str(path), trust_remote_code=False)
        self.model = load_model(path)
        # What mlx_vlm.utils.load_processor() adds after construction: streaming detokenizer + stop criteria.
        tok = self.processor.tokenizer
        self.processor.detokenizer = load_tokenizer(path, return_tokenizer=False)(tok)
        eos = getattr(self.model.config, "eos_token_id", None)
        eos_ids = eos if isinstance(eos, list) else ([eos] if eos is not None else None)
        tok.stopping_criteria = StoppingCriteria(eos_ids, tok)
        self.load_seconds = round(time.time() - t0, 1)

    def ocr(self, image, tiles: int = MAX_TILES, instruction: str = PROMPTS["markdown"],
            max_tokens: int = guard.DEFAULT_MAX_TOKENS) -> OcrResult:
        """OCR one page. ``image`` is a PIL image. ``tiles`` = max local 768 px tiles (0 = 1024 px overview only)."""
        from mlx_vlm.generate.dispatch import stream_generate

        tiles = max(0, min(MAX_TILES, int(tiles)))
        prompt = build_prompt(instruction)
        with self._lock:
            t0 = time.time()
            inputs = self.processor(text=prompt, images=[image.convert("RGB")],
                                    cropping=tiles > 0, min_patches=1, max_patches=max(tiles, 1))
            pieces, last = [], None
            for r in stream_generate(self.model, self.processor, prompt,
                                     input_ids=inputs["input_ids"], pixel_values=inputs["images"],
                                     images_seq_mask=inputs["images_seq_mask"],
                                     images_spatial_crop=self.mx.array(inputs["images_spatial_crop"]),
                                     max_tokens=max_tokens, temperature=0.0):
                pieces.append(r.text)
                last = r
            seconds = round(time.time() - t0, 2)

        text = "".join(pieces).strip()
        gen_tokens = getattr(last, "generation_tokens", None)
        grid = _as_list(inputs["images_spatial_crop"])
        grid = grid[0] if grid and isinstance(grid[0], list) else grid
        n_img = _as_list(inputs.get("num_image_tokens"))
        return OcrResult(
            text=text, tiles=tiles, seconds=seconds, gen_tokens=gen_tokens,
            image_tokens=int(n_img[0]) if n_img else None,
            looped=guard.looped(text),
            hit_cap=gen_tokens is not None and gen_tokens >= max_tokens,
            grid=[int(g) for g in grid[:2]] if tiles > 0 and len(grid) >= 2 else [0, 0],
        )


def _as_list(x):
    if hasattr(x, "tolist"):
        x = x.tolist()
    return x if isinstance(x, list) else ([] if x is None else [x])
