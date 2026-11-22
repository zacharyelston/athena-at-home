# Phase 0: OCR benchmark

Apple Vision vs DeepSeek-OCR-2 (MLX) on synthetic household documents with exact ground truth.
Results and conclusions: [../docs/phase0-results.md](../docs/phase0-results.md).

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python mlx-vlm pyobjc-framework-Vision pyobjc-framework-Quartz pillow rapidfuzz opencv-python-headless
.venv/bin/python gen_testset.py                                   # renders testset/ (git-ignored)
.venv/bin/python bench.py vision                                  # Apple Vision, all images
./run_matrix.sh mlx-community/DeepSeek-OCR-2-8bit ds-8bit full    # DPI and tile-budget sweeps
./run_matrix.sh mlx-community/DeepSeek-OCR-2-bf16 ds-bf16 lite    # precision comparison
.venv/bin/python summarize.py                                     # markdown tables
```

Results append to `results/<tag>.jsonl`; raw OCR text goes to `results/raw/<tag>/`. Models download to the
Hugging Face cache (8-bit 4.0 GB, bf16 6.8 GB). `bench.py` never runs the model repo's custom code.

`gen_testset.py` also renders five pages in historical styles (mimeograph, typewriter, newsprint columns, a typeset
program with a photograph, handwriting) for the capture pilot ([../docs/pilot.md](../docs/pilot.md)). `bench.py`
doesn't read them, so the Phase 0 results above stand as measured.
