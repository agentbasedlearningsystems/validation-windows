"""Coverage / CI diagnostic for a results_*.json file.

For every entry that carries (mean, ci95, solver_ci, coverage), checks:

  (1) Is the solver's point estimate inside the literature 95% CI?
      solver_point = midpoint(solver_ci);  expected: mean - ci95 <= solver_point <= mean + ci95
      An edge that fails this is a study whose CPT cell the solver had to
      move outside the literature band to keep the whole network feasible.
      That is the system's "forced-complaint" signal.

  (2) Is the reported coverage in [0, 1] and consistent with the window?
      coverage should equal erf(window * 1.96 / sqrt(2)) to within 1e-6.

Usage:

    python scripts/ci_coverage_check.py paper/results_paper.json

Exits non-zero if any edge fails the consistency check, or if the
fraction of edges whose solver point falls inside the literature 95% CI
drops below --min-within (default 0.95).

Also importable as a module:  from scripts.ci_coverage_check import run_checks

Note: "coverage" used to be called "p" pre Apr 24 2026; renamed to avoid
NHST-p naming collision. Old JSON files with the legacy "p" key are still
read (transparently aliased to coverage).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from math import erf, sqrt
from pathlib import Path

Z95 = 1.959963984540054


def expected_coverage(window: float) -> float:
    return max(0.0, min(1.0, erf(window * Z95 / sqrt(2.0))))


def midpoint(interval: list[float]) -> float:
    return 0.5 * (interval[0] + interval[1])


def get_coverage(entry: dict) -> float | None:
    """Return entry['coverage'] preferring the renamed key, falling back to 'p'."""
    if "coverage" in entry:
        return entry["coverage"]
    if "p" in entry:  # legacy
        return entry["p"]
    return None


def iter_edges(obj):
    """Yield every dict entry that has window + mean + ci95 + solver_ci + coverage/p."""
    if isinstance(obj, dict):
        if all(k in obj for k in ("window", "mean", "ci95", "solver_ci")) and get_coverage(obj) is not None:
            yield obj
        for v in obj.values():
            yield from iter_edges(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from iter_edges(x)


def run_checks(results_path: Path, min_within: float = 0.95):
    with open(results_path) as f:
        data = json.load(f)

    edges = list(iter_edges(data))
    if not edges:
        print(f"WARN: no edges with (mean, ci95, solver_ci, coverage) in {results_path}")
        return 0

    within_lit_ci = 0
    outside = []
    coverage_values = []
    inconsistent = []

    for e in edges:
        mean = e["mean"]
        hw = e["ci95"]
        sci = e["solver_ci"]
        w = e["window"]
        cov = get_coverage(e)

        lo_lit, hi_lit = mean - hw, mean + hw
        solver_point = midpoint(sci)
        if lo_lit - 1e-12 <= solver_point <= hi_lit + 1e-12:
            within_lit_ci += 1
        else:
            outside.append({
                "variable": e.get("variable", "?"),
                "solver_point": solver_point,
                "lit_ci": [lo_lit, hi_lit],
                "window": w,
                "coverage": cov,
            })

        coverage_values.append(cov)
        if abs(cov - expected_coverage(w)) > 1e-6:
            inconsistent.append({
                "variable": e.get("variable", "?"),
                "window": w,
                "coverage_reported": cov,
                "coverage_expected": expected_coverage(w),
            })

    n = len(edges)
    frac_within = within_lit_ci / n
    cov_med = statistics.median(coverage_values)
    cov_mean = statistics.fmean(coverage_values)

    print(f"{results_path}")
    print(f"  edges checked         : {n}")
    print(f"  within literature 95% : {within_lit_ci}/{n} ({frac_within:.1%})")
    print(f"  median coverage       : {cov_med:.3f}")
    print(f"  mean coverage         : {cov_mean:.3f}")
    print(f"  W->coverage consistency: {n - len(inconsistent)}/{n} pass")

    if outside:
        print(f"\n  edges OUTSIDE literature 95% CI ({len(outside)}):")
        for o in outside[:10]:
            print(f"    {o['variable']:40s}  solver_point={o['solver_point']:.3f}  "
                  f"lit=[{o['lit_ci'][0]:.3f}, {o['lit_ci'][1]:.3f}]  "
                  f"W={o['window']:.3f}  cov={o['coverage']:.3f}")
        if len(outside) > 10:
            print(f"    ... and {len(outside) - 10} more")

    if inconsistent:
        print(f"\n  consistency FAILURES ({len(inconsistent)}):")
        for f in inconsistent[:5]:
            print(f"    {f['variable']:40s}  W={f['window']:.3f}  "
                  f"reported={f['coverage_reported']:.4f}  expected={f['coverage_expected']:.4f}")

    exit_code = 0
    if inconsistent:
        print("\nFAIL: coverage inconsistent with window on some edges.")
        exit_code = 1
    if frac_within < min_within:
        print(f"\nFAIL: only {frac_within:.1%} within literature 95% CI "
              f"(threshold: {min_within:.0%}).")
        exit_code = 1
    if exit_code == 0:
        print("\nOK.")
    return exit_code


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results_json", type=Path, nargs="+",
                    help="one or more results_*.json files")
    ap.add_argument("--min-within", type=float, default=0.95,
                    help="minimum fraction of edges within literature 95% CI "
                         "to treat as OK (default: 0.95)")
    args = ap.parse_args()

    worst = 0
    for p in args.results_json:
        worst = max(worst, run_checks(p, min_within=args.min_within))
        print()
    return worst


if __name__ == "__main__":
    sys.exit(main())
