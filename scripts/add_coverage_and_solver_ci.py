"""Post-process results_*.json files in place: add coverage and solver_ci per edge.

For every dict entry that has a `window` field, compute:

  coverage  = 2 * Phi(window * 1.96) - 1   (= erf(window * 1.96 / sqrt(2)))
              marginal per-edge CI coverage under the normal approximation.
              window=1 -> coverage=0.95;  window=1.85 -> coverage~0.9997
              (Bonferroni n=166).

  solver_ci = [mean - window * ci95, mean + window * ci95]
              the interval itself, in the same units as the entry's mean.
              At window=1 this is the study's original 95% CI; at smaller
              windows it is the coherence-tightened interval.

Idempotent: safe to re-run. Will overwrite any existing coverage / solver_ci
fields. Strips legacy `p` fields (renamed to `coverage` Apr 24 2026 to
avoid NHST-p collision).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from math import erf, sqrt

Z95 = 1.959963984540054


def enrich(entry: dict) -> None:
    w = entry.get("window")
    if not isinstance(w, (int, float)):
        return
    entry["coverage"] = max(0.0, min(1.0, erf(w * Z95 / sqrt(2.0))))
    # Strip the legacy "p" key (renamed to "coverage" Apr 24 2026).
    if "p" in entry:
        del entry["p"]
    m = entry.get("mean")
    hw = entry.get("ci95")
    if isinstance(m, (int, float)) and isinstance(hw, (int, float)):
        half = w * abs(hw)  # abs() guards against unphysical negative ci95
        entry["solver_ci"] = [m - half, m + half]


def walk(obj) -> None:
    if isinstance(obj, dict):
        if "window" in obj:
            enrich(obj)
        for v in obj.values():
            walk(v)
    elif isinstance(obj, list):
        for x in obj:
            walk(x)


def main(paths: list[str]) -> None:
    for p in paths:
        if not os.path.exists(p):
            print(f"SKIP (missing): {p}")
            continue
        shutil.copy(p, p + ".pre_coverage_backup")
        with open(p) as f:
            d = json.load(f)
        walk(d)
        with open(p, "w") as f:
            json.dump(d, f, indent=2)
        print(f"OK: {p}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: add_coverage_and_solver_ci.py <results.json> [...]")
        sys.exit(1)
    main(sys.argv[1:])
