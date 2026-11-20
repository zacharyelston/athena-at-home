#!/bin/zsh
# SPDX-License-Identifier: GPL-3.0-or-later
# Phase 0 DeepSeek-OCR-2 matrix for one model build. Usage: ./run_matrix.sh mlx-community/DeepSeek-OCR-2-8bit ds-8bit
# full: DPI sweep (camera, 6 tiles), tile-budget sweep (camera 150, 0/2 tiles), clean scan (150, 6 tiles)
# lite: camera 150 at 6 and 0 tiles only (for comparing model precisions)
cd "${0:A:h}"
M=$1; T=$2; MODE=${3:-full}
run() { .venv/bin/python bench.py ds --model "$M" --tag "$T" "$@" 2>&1 | grep -v "^Added\|^Add \|<image>:\|pad▁\|Fetching"; }
if [[ $MODE == full ]]; then
  run --crops 6 --conds camera --dpis 100,150,200
  run --crops 0,2 --conds camera --dpis 150
  run --crops 6 --conds clean --dpis 150
else
  run --crops 6,0 --conds camera --dpis 150
fi
