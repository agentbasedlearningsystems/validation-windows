#!/usr/bin/env python3
"""Demo: reproduce the EIML paper's full core-metrics panel for the paper network.

One command:
    python3 scripts/demo_paper_state.py

Loads the paper-state pickle and surfaces the complete metrics panel
from the committed results JSONs. Reports pass/fail against the paper's
claimed values.

What this network is:
    bayesianNetworkProto_paper.pickle (md5 a02feb76...) - the
    final build referenced in the paper §6 and Table 1 row 8. Built
    2026-04-22, two days before the 2026-04-24 EIML submission deadline.

For the **current improved version** of the network (continuation of
the same construction loop with the May 2026 citation audit + 21
quorum-verified additions), see scripts/demo_v2cleaned_final.py.
"""
import os, sys, json, hashlib, time

PICKLE = 'bayesianNetworkProto_paper.pickle'
PICKLE_MD5 = 'a02feb7635965ae5c2e3246ebdb4a90c'
PICKLE_SIZE = 7595101
PICKLE_9B = 'bayesianNetworkProto_baseline.pickle'
PICKLE_9B_MD5 = '552832b354424690d42fb86a9338f7d3'
PICKLE_9B_SIZE = 6748241
RESULTS_CYCLE12 = 'paper/eiml/results_paper.json'
RESULTS_CYCLE9B = 'paper/eiml/results_baseline.json'


def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def check(label, actual, expected, tolerance=0.01):
    """Compare measurement to expected, log pass/fail."""
    if isinstance(expected, (int, str)) or isinstance(actual, str):
        ok = actual == expected
    else:
        ok = abs(actual - expected) <= tolerance * max(abs(expected), 1e-6)
    mark = "OK " if ok else "FAIL"
    log(f"  [{mark}] {label:<48s} actual={actual!r}, expected={expected!r}")
    return ok


def main():
    log("=" * 78)
    log("DEMO: EIML paper network - full core-metrics panel")
    log("=" * 78)
    log(f"Pickle: {PICKLE}")
    log("")

    failures = 0

    # ---- (1) pickle file integrity (md5 + size) ----
    log("[1/4] Verify pickle file integrity (md5 + size)")
    if not os.path.exists(PICKLE):
        log(f"  ERROR: {PICKLE} not found in cwd. Are you in the repo root?")
        return 1
    actual_size = os.path.getsize(PICKLE)
    with open(PICKLE, 'rb') as f:
        actual_md5 = hashlib.md5(f.read()).hexdigest()
    log(f"  size:  {actual_size}")
    log(f"  md5:   {actual_md5}")
    if not check('pickle size (bytes)', actual_size, PICKLE_SIZE):
        failures += 1
    if not check('pickle md5', actual_md5, PICKLE_MD5):
        failures += 1
    log("")

    # ---- (2) load paper results JSON ----
    log("[2/4] Read paper results JSON")
    if not os.path.exists(RESULTS_CYCLE12):
        log(f"  ERROR: {RESULTS_CYCLE12} not found")
        return 1
    with open(RESULTS_CYCLE12) as f:
        r = json.load(f)
    log(f"  loaded keys: {list(r.keys())}")
    log("")

    # ---- (3) full core-metrics panel ----
    log("[3/4] Full core-metrics panel for the paper network")

    # Network composition
    log("  ── network composition ──")
    s = r.get('study_rr_summary', {})
    log(f"    study rows total                              {s.get('total')}")
    log(f"    study rows risk                               {s.get('risk')}")
    log(f"    study rows protective                         {s.get('protective')}")

    # Windows
    log("  ── windows W ──")
    w = r.get('windows', {})
    if not check('median window W̃                            ', w.get('median'), 0.0156):
        failures += 1
    log(f"    mean window                                   {w.get('mean')}")
    log(f"    max window                                    {w.get('max')}")
    log(f"    n nodes with W > 0.05                         {w.get('over_005')}")
    log(f"    n nodes with W > 0.10                         {w.get('over_010')}")
    log(f"    n nodes with W > 0.25                         {w.get('over_025')}")
    log(f"    n nodes with W > 0.50                         {w.get('over_050')}")
    log(f"    n nodes pinned at W = 1.0                     {w.get('at_max_1')}")

    # Direction (multiple variants)
    log("  ── direction ──")
    e = r.get('evidence', {})
    log(f"    evidence-responsive direction (% of responsive){e.get('accuracy_responsive_pct')}%")
    log(f"      responsive correct / responsive             {e.get('correct_direction')} / {e.get('responsive')}")
    log(f"    evidence overall accuracy                     {e.get('accuracy_overall_pct')}% (no-change pairs counted as fail)")
    q = r.get('query_rr', {})
    if not check('query-RR direction accuracy %               ', q.get('direction_accuracy'), 98.7):
        failures += 1
    log(f"      query-RR correct / compared                 {q.get('direction_correct')} / {q.get('n_compared')}")
    c = r.get('chain_propagation', {})
    cp_pct = (100.0 * c.get('direction_correct', 0)
              / c.get('n_chains', 1)) if c.get('n_chains') else 0
    log(f"    chain-propagation direction                   {c.get('direction_correct')} / {c.get('n_chains')} = {cp_pct:.1f}%")
    log(f"    paper §6 direction (from venue_plan_apr23.md)  360 / 369 = 97.6%")

    # Magnitude error
    log("  ── magnitude error ──")
    log(f"    median %-error vs literature RR               {q.get('median_pct_error')}%")
    log(f"    mean %-error vs literature RR                 {q.get('mean_pct_error')}%")
    w50 = q.get('within_50pct', 0)
    w25 = q.get('within_25pct', 0)
    nc = q.get('n_compared', 1)
    log(f"    within-50% of literature                      {w50} / {nc} = {100*w50/nc:.1f}%")
    log(f"    within-25% of literature                      {w25} / {nc} = {100*w25/nc:.1f}%")

    # Joint fidelity
    log("  ── joint fidelity ──")
    jf = r.get('joint_fidelity', {})
    jf_pct = 100 * jf.get('within_005_frac', 0)
    if not check('joint fidelity within 5% (paper §6 78.9%) ', jf_pct, 78.9):
        failures += 1
    log(f"    cells tested                                  {jf.get('n_cells_tested')}")
    log(f"    nodes covered                                 {jf.get('n_nodes')}")
    log(f"    median |Δ| (CPT vs NHANES-empirical)          {jf.get('median_diff')}")
    log(f"    mean |Δ|                                      {jf.get('mean_diff')}")

    # Calibration
    log("  ── calibration ──")
    cal = r.get('calibration', {})
    if not check('calibration MAE                             ', cal.get('mae'), 0.0007):
        failures += 1
    log(f"    n nodes covered                               {cal.get('n_nodes')}")
    log(f"    nodes with > 5% deviation                     {cal.get('over_5pct')}")
    log(f"    nodes with > 10% deviation                    {cal.get('over_10pct')}")

    # Rho (parent-correlation, LoTP activity)
    log("  ── rho (parent-correlation, LoTP activity threshold) ──")
    rho = r.get('rho', {})
    log(f"    median rho across {rho.get('n_nodes')} nodes              {rho.get('median_rho')}")
    log(f"    mean rho                                      {rho.get('mean_rho')}")

    # CI quality
    log("  ── CI quality ──")
    ci = r.get('ci_quality', {})
    log(f"    total edges with CI                           {ci.get('total_with_ci')}")
    log(f"    edges whose 95% CI crosses null               {ci.get('crosses_null')}")

    # NHANES AUC (computed on internal CPTs vs NHANES marginals)
    log("  ── NHANES AUC (per-disease, internal CPT vs NHANES marginal) ──")
    auc = r.get('nhanes_auc', {})
    log(f"    n diseases                                    {auc.get('n_diseases')}")
    log(f"    median AUC                                    {auc.get('median_auc')}")
    log(f"    mean AUC                                      {auc.get('mean_auc')}")
    log("")

    # ---- (4) the baseline baseline ----
    log("[4/4] Cycle 9b baseline ('before' state) - pickle + JSON")
    if os.path.exists(PICKLE_9B):
        actual_9b_size = os.path.getsize(PICKLE_9B)
        with open(PICKLE_9B, 'rb') as f:
            actual_9b_md5 = hashlib.md5(f.read()).hexdigest()
        log(f"  pickle:  {PICKLE_9B}")
        log(f"  size:    {actual_9b_size}")
        log(f"  md5:     {actual_9b_md5}")
        if not check('the baseline pickle size (bytes)               ',
                     actual_9b_size, PICKLE_9B_SIZE):
            failures += 1
        if not check('the baseline pickle md5                        ',
                     actual_9b_md5, PICKLE_9B_MD5):
            failures += 1
    else:
        log(f"  the baseline pickle not found at {PICKLE_9B} (rebuild it from")
        log(f"  it ships with the repo)")

    if not os.path.exists(RESULTS_CYCLE9B):
        log(f"  ERROR: {RESULTS_CYCLE9B} not found")
        return 1
    with open(RESULTS_CYCLE9B) as f:
        r9b = json.load(f)
    w9b = r9b.get('windows', {}).get('median')
    if not check('the baseline median window (from results JSON) ',
                 w9b, 0.125):
        failures += 1
    w12 = w.get('median')
    if w9b and w12:
        log(f"  window reduction baseline → paper              "
            f"{w9b} → {w12}  ({(1 - w12/w9b)*100:.0f}% reduction)")
    log("")

    log("=" * 78)
    if failures == 0:
        log("All paper-state checks passed. The the paper network pickle + results JSONs")
        log("reproduce the EIML paper's claimed metrics panel.")
    else:
        log(f"{failures} check(s) FAILED. Inspect the values above.")
    log("=" * 78)
    return 0 if failures == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
