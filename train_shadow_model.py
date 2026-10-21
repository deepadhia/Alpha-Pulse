# -*- coding: utf-8 -*-
"""
train_shadow_model.py -- fit and save the logged-only shadow model (core/shadow_score.py).

Needs scikit-learn (the system python has it; the project .venv does not) and the event table made by research_event_model.py:
  .venv\\Scripts\\python.exe research_event_model.py --cache data.pkl --build-only      (writes reports/2026-10-17/event_model_events.csv)
  python train_shadow_model.py --events reports/2026-10-17/event_model_events.csv

The model is trained on ALL events, so only sessions AFTER train_end are genuine out-of-sample evidence; shadow_score.py --report respects that.
Writes models/shadow_gbm_v1.pkl (gitignored, regenerate any time).  Does not touch MongoDB or any scanner.
"""
import argparse

import pandas as pd

from core.shadow_score import MODEL_PATH, train_model


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--out", default=MODEL_PATH)
    a = ap.parse_args()
    ev = pd.read_csv(a.events, parse_dates=["date"])
    meta = train_model(ev, a.out)
    print("saved", a.out, meta)


if __name__ == "__main__":
    main()
