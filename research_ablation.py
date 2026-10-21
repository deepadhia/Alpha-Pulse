# -*- coding: utf-8 -*-
"""
research_ablation.py -- which divergence / gate costs how much (research tooling, read-only)

  A. each fidelity switch ALONE from the legacy backtest
  B. the live-faithful configuration with ONE switch reverted to its legacy value
  C. live entry sub-gate ablation on top of the live-faithful configuration

  python research_ablation.py --cache data.pkl
"""
import argparse
import logging
import pickle
from dataclasses import replace

logging.disable(logging.CRITICAL)

from core.honest_backtest import BRIDGE_STEPS, LEGACY, LIVE_FAITHFUL, Params
from run_honest_backtest import fmt_row, run_all, summarize


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    a = ap.parse_args()
    listing_map, candle_map = pickle.load(open(a.cache, "rb"))
    P = Params.from_live()

    def go(label, cfg, p=P):
        s = summarize(run_all(candle_map, listing_map, cfg, p))
        print(fmt_row(label, s) + f"  [L={s.get('n_listing', 0)} S={s.get('n_shelf', 0)} B={s.get('n_base', 0)}]", flush=True)

    print("=== A. each switch ALONE from legacy ===")
    for field, value, _ in BRIDGE_STEPS:
        go(f"only {field}={value}", replace(LEGACY, **{field: value}))
    print("\n=== B. live-faithful with ONE switch reverted to legacy ===")
    go("live-faithful (all on)", LIVE_FAITHFUL)
    for field, value, _ in BRIDGE_STEPS:
        go(f"live-faithful but {field}={getattr(LEGACY, field)}", replace(LIVE_FAITHFUL, **{field: getattr(LEGACY, field)}))
    print("\n=== C. entry_path=live sub-gate ablation (on top of LIVE_FAITHFUL) ===")
    for label, over in [
        ("no post-confirm >=1.5% rule", dict(min_post_confirm_pct=0.0)),
        ("no leader-score gate", dict(min_leader_score=-99)),
        ("no price floor", dict(min_entry_price=0.0)),
        ("no turnover floor", dict(turnover_floor_cr=0.0)),
        ("no circuit-day gate", dict(circuit_day_threshold=999)),
        ("no Tier B (base) entries", dict(tier_b_enabled=False)),
        ("ALL live entry sub-gates off", dict(min_post_confirm_pct=0.0, min_leader_score=-99, min_entry_price=0.0,
                                             turnover_floor_cr=0.0, circuit_day_threshold=999)),
    ]:
        go(label, LIVE_FAITHFUL, replace(P, **over))


if __name__ == "__main__":
    main()
