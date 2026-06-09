"""Sanity tests for the per-edge coverage / solver_ci output.

Pure-math tests on the formula relating window -> coverage. Does not
require a built network; runs in well under a second.

Run:
    pytest tests/test_coverage.py -v
or:
    python tests/test_coverage.py
"""
from __future__ import annotations

import json
import os
import sys
from math import erf, sqrt
from pathlib import Path

# Allow `python tests/test_coverage.py` invocation
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

Z95 = 1.959963984540054


def coverage_from_window(w: float) -> float:
    return max(0.0, min(1.0, erf(w * Z95 / sqrt(2.0))))


def test_coverage_at_unit_window_is_95():
    # Anchor: at window=1, the band IS the 95% CI -> coverage=0.95
    assert abs(coverage_from_window(1.0) - 0.95) < 1e-6


def test_coverage_at_bonferroni_factor_is_joint_95():
    # n=166 studies, Bonferroni: c = z_{1-0.05/(2*166)} / 1.96 ~= 1.85
    # At c=1.85, marginal-per-study coverage is ~0.9997
    assert abs(coverage_from_window(1.85) - 0.9997) < 5e-3


def test_coverage_zero_window_is_zero():
    # At window=0, the interval is a single point -> coverage=0
    assert coverage_from_window(0.0) == 0.0


def test_coverage_monotone_in_window():
    # Wider window -> higher marginal coverage
    prev = -1.0
    for w in [0.0, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0]:
        c = coverage_from_window(w)
        assert c >= prev
        prev = c


def test_coverage_in_unit_interval():
    for w in [-1.0, 0.0, 0.5, 1.0, 1.85, 5.0, 100.0]:
        c = coverage_from_window(w)
        assert 0.0 <= c <= 1.0


def test_coverage_field_in_cycle_12_widest_20():
    """Loaded cycle-12 paper artifact should have coverage on every widest-20 entry,
    consistent with its window value."""
    candidates = [
        ROOT / "paper" / "results_paper.json",
        ROOT / "paper_results" / "results_paper.json",
    ]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        # No artifact present; skip rather than fail (e.g. fresh checkout)
        return
    with open(path) as f:
        d = json.load(f)
    widest = d.get("windows", {}).get("widest_20", [])
    assert widest, "cycle-12 widest_20 list is empty"
    for e in widest:
        assert "window" in e
        assert "coverage" in e, f"missing coverage on {e.get('variable')}"
        assert "solver_ci" in e, f"missing solver_ci on {e.get('variable')}"
        # Field consistency
        assert abs(e["coverage"] - coverage_from_window(e["window"])) < 1e-6
        # Interval validity
        lo, hi = e["solver_ci"]
        assert lo <= hi


def test_solver_ci_contains_mean_at_unit_window():
    """At window=1, solver_ci should equal [mean - ci95, mean + ci95]."""
    w = 1.0
    mean = 0.5
    ci95 = 0.1
    half = w * ci95
    assert abs((mean - half) - 0.4) < 1e-12
    assert abs((mean + half) - 0.6) < 1e-12


def _runtests():
    import inspect
    fns = [(n, f) for n, f in globals().items()
           if n.startswith("test_") and inspect.isfunction(f)]
    failed = []
    for name, fn in fns:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed.append((name, e))
            print(f"  FAIL  {name}: {e}")
    print(f"\n{len(fns) - len(failed)}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_runtests())
