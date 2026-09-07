#!/usr/bin/env bash
set -e
pip install -r requirements.txt
python samples/make_samples.py
uvicorn backend.app:app --reload --port 8000
