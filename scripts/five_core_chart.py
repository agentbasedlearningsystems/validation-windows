#!/usr/bin/env python3
"""Five-core metrics comparison chart per user spec (Apr 20).

Reads paper/results_{label}.json and paper/objective_rr_comparison_{label}.json
for each build and prints a consistent median / mean / max layout plus the
counting metrics (#/#) #%.

Usage:
    python3 scripts/five_core_chart.py [label1 label2 ...]

Default chain shows baseline → fixes added progressively.
"""
import sys, json, os

DEFAULT_BUILDS = [
    ('baseline_pre_drafts',  'baseline (pre-replacement, Apr 17)'),
    ('variant_A_inverted',   '+ Fransen inversion (Apr 19)'),
    ('mpv_anchor_full',      '+ MPV anchor (Apr 19)'),
    ('colon_composite_full', '+ colon BMI×CRP composite (Apr 20)'),
    ('no_df_ablation',       'PAPER-IDEAL: LS+mono+subset, w_df=0 (Apr 20)'),
    ('autofill_no_df',       '+ autofill NHANES-P0 on 61 rows (Apr 20)'),
    ('bmi_fix_no_df',        '+ blank 9 redundant bmi rows on cancers (Apr 20)'),
    ('overweight_fix_no_df', '+ blank 5 bmi_naive=overweight rows (Apr 20 pm)'),
    ('pc_flatten_no_df',     '+ pancreatic exposures → chemical_pc_risk direct (Apr 21)'),
    ('mpv_flatten_no_df',    '+ MPV_{digestion,glucose} aggregators bypassed (Apr 21)'),
    ('cycle4_no_df',         '+ blank ever_vaped→fatal_stroke (Apr 21)'),
    ('cycle6_no_df',         '+ co-parent edges for fasting_glucose (Apr 21 flight)'),
    ('cycle7_no_df',         '+ acm_inflammation flatten (Apr 21 flight)'),
    ('cycle8_no_df',         '+ ci_physical_activity flatten (Apr 21 flight)'),
    ('cycle9a_no_df',        '+ stomach Type swap + hysterectomy blank (Apr 21)'),
    ('cycle9b_no_df',        '+ widest_20 window attack (Apr 21)'),
    ('cycle10_no_df',        '+ pancreatic override + bmi/diabetes/CAD edges (Apr 21)'),
    ('cycle11_no_df',        '+ 16 medical-knowledge edges audit (Apr 21)'),
    ('cycle5_no_df',         '+ blank fasting_glucose ×2 + creatinine (Apr 21 5am)'),
]

OBJ_MAP = {
    'baseline_pre_drafts':  'baseline_pre_drafts',
    'variant_A_inverted':   'variant_A',
    'mpv_anchor_full':      'mpv_anchor_full',
    'colon_composite_full': 'colon_composite',
    'no_df_ablation':       'no_df_ablation',
    'autofill_no_df':       'autofill_no_df',
    'bmi_fix_no_df':        'bmi_fix_no_df',
    'overweight_fix_no_df': 'overweight_fix_no_df',
    'pc_flatten_no_df':     'pc_flatten_no_df',
    'mpv_flatten_no_df':    'mpv_flatten_no_df',
    'cycle4_no_df':         'cycle4_no_df',
    'cycle6_no_df':         'cycle6_no_df',
    'cycle7_no_df':         'cycle7_no_df',
    'cycle8_no_df':         'cycle8_no_df',
    'cycle9a_no_df':        'cycle9a_no_df',
    'cycle9b_no_df':        'cycle9b_no_df',
    'cycle10_no_df':        'cycle10_no_df',
    'cycle11_no_df':        'cycle11_no_df',
    'cycle5_no_df':         'cycle5_no_df',
}


def load(path):
    if not os.path.exists(path): return None
    with open(path) as f: return json.load(f)


def fmt_count(n, total):
    if total == 0 or total is None: return '     -'
    pct = 100.0 * n / total
    return f"({n}/{total}) {pct:.1f}%"


def fmt_num(x, spec='{:.4f}'):
    if x is None: return '   N/A'
    return spec.format(x)


def main():
    builds = [(b, d) for b, d in DEFAULT_BUILDS]
    if len(sys.argv) > 1:
        builds = [(a, a) for a in sys.argv[1:]]

    print("=" * 110)
    print("FIVE CORE METRICS - comparison across builds (Apr 20, 2026)")
    print("=" * 110)
    print()
    print("Builds in order (what each adds to the previous):")
    for b, d in builds: print(f"  - {b:28s}  {d}")
    print()
    print("Layout: each section shows median / mean / max where stored, plus a counting metric.")
    print("'N/A' = summary stats only stored median+mean, so max wasn't recorded.")
    print()

    data = {}
    for b, _ in builds:
        r = load(f'paper/results_{b}.json')
        obj_label = OBJ_MAP.get(b, b)
        o = load(f'paper/objective_rr_comparison_{obj_label}.json')
        data[b] = {'results': r, 'objective': o}

    # ----- 1. CALIBRATION -----
    print("#" * 110)
    print("# 1. CALIBRATION - per-node |net_marginal - NHANES_prior|")
    print("#    NOT EXTERNAL: priors come from NHANES. Measures QP's drift from its own prior input.")
    print("#" * 110)
    print(f"  {'Build':32s} {'nodes':>6s} {'median%':>9s} {'mean (MAE)':>12s} {'max':>8s} {'over-5%':>18s} {'over-10%':>18s}")
    print(f"  {'-'*32} {'-'*6} {'-'*9} {'-'*12} {'-'*8} {'-'*18} {'-'*18}")
    for b, _ in builds:
        r = data[b]['results']
        if not r: print(f"  {b:32s}   MISSING"); continue
        c = r.get('calibration', {})
        n = c.get('n_nodes', 0)
        median = c.get('median_pct_diff')
        mae = c.get('mae')
        # max not stored; show N/A
        print(f"  {b:32s} {n:>6d} {fmt_num(median, '{:>9.4f}'):>9s} {fmt_num(mae, '{:>12.4f}'):>12s} {'   N/A':>8s} "
              f"{fmt_count(c.get('over_5pct',0), n):>18s} {fmt_count(c.get('over_10pct',0), n):>18s}")
    print()

    # ----- 2. OBJECTIVE RR COMPARISON -----
    print("#" * 110)
    print("# 2. OBJECTIVE RR COMPARISON - clamp parent, query target, compare to study RR")
    print("#    MOST EXTERNAL: study RRs come from literature. Tests QP LS convergence + chain propagation.")
    print("#" * 110)
    print(f"  {'Build':32s} {'n':>5s} {'median err':>12s} {'mean err':>10s} {'max err':>9s} {'direction':>18s} {'within 25%':>18s} {'within 50%':>18s}")
    print(f"  {'-'*32} {'-'*5} {'-'*12} {'-'*10} {'-'*9} {'-'*18} {'-'*18} {'-'*18}")
    for b, _ in builds:
        o = data[b]['objective']
        if not o: print(f"  {b:32s}   MISSING"); continue
        s = o.get('summary', {})
        results = o.get('results', [])
        # compute max from per-row results if available
        max_err = None
        if results:
            errs = [x.get('pct_error') for x in results if isinstance(x.get('pct_error'), (int, float))]
            if errs: max_err = max(errs)
        n = s.get('n', 0)
        print(f"  {b:32s} {n:>5d} {fmt_num(s.get('median_pct_error'), '{:>11.1f}%'):>12s} "
              f"{fmt_num(s.get('mean_pct_error'), '{:>9.1f}%'):>10s} {fmt_num(max_err, '{:>8.1f}%'):>9s} "
              f"{fmt_count(s.get('dir_correct',0), n):>18s} "
              f"{fmt_count(s.get('within_25pct',0), n):>18s} "
              f"{fmt_count(s.get('within_50pct',0), n):>18s}")
    print()

    # ----- 3. VALIDATION WINDOW -----
    print("#" * 110)
    print("# 3. VALIDATION WINDOW - QP's stretch of each study's CI (upper−lower)")
    print("#    EXTERNAL-ISH: measures solver's compromise between literature and NHANES. Tighter = better.")
    print("#" * 110)
    print(f"  {'Build':32s} {'n':>6s} {'median':>8s} {'mean':>8s} {'max':>8s} {'over 0.05':>18s}")
    print(f"  {'-'*32} {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*18}")
    for b, _ in builds:
        r = data[b]['results']
        if not r: print(f"  {b:32s}   MISSING"); continue
        w = r.get('windows', {})
        n = r.get('calibration', {}).get('n_nodes', 0)
        print(f"  {b:32s} {n:>6d} {w.get('median',0):>8.4f} {w.get('mean',0):>8.4f} {w.get('max',0):>8.4f} "
              f"{fmt_count(w.get('over_005',0), n):>18s}")
    print()

    # ----- 4. JOINT FIDELITY -----
    print("#" * 110)
    print("# 4. JOINT FIDELITY - per-cell |net_joint - NHANES_joint|, fraction within 5%")
    print("#    NOT EXTERNAL: QP's DF term explicitly fits NHANES joint conditionals.")
    print("#" * 110)
    print(f"  {'Build':32s} {'n_nodes':>8s} {'n_cells':>8s} {'median diff':>12s} {'mean diff':>11s} {'max':>8s} {'within-5%':>18s}")
    print(f"  {'-'*32} {'-'*8} {'-'*8} {'-'*12} {'-'*11} {'-'*8} {'-'*18}")
    for b, _ in builds:
        r = data[b]['results']
        if not r: print(f"  {b:32s}   MISSING"); continue
        j = r.get('joint_fidelity', {})
        nc = j.get('n_cells_tested', 0)
        nn = j.get('n_nodes', 0)
        frac = j.get('within_005_frac', 0)
        within = int(round(frac * nc))
        print(f"  {b:32s} {nn:>8d} {nc:>8d} {j.get('median_diff',0):>12.4f} {j.get('mean_diff',0):>11.4f} "
              f"{'   N/A':>8s} {fmt_count(within, nc):>18s}")
    print()

    # ----- 5. NHANES AUC -----
    print("#" * 110)
    print("# 5. NHANES AUC - per-respondent disease prediction using observable features")
    print("#    NOT EXTERNAL: CPTs fit NHANES joint conditionals at build time.")
    print("#" * 110)
    print(f"  {'Build':32s} {'n_diseases':>11s} {'median':>8s} {'mean':>8s} {'max':>8s}")
    print(f"  {'-'*32} {'-'*11} {'-'*8} {'-'*8} {'-'*8}")
    for b, _ in builds:
        r = data[b]['results']
        if not r: print(f"  {b:32s}   MISSING"); continue
        n = r.get('nhanes_auc', {})
        print(f"  {b:32s} {n.get('n_diseases',0):>11d} {n.get('median_auc',0):>8.4f} "
              f"{n.get('mean_auc',0):>8.4f} {'   N/A':>8s}")
    print()

    print("=" * 110)
    print("Notes:")
    print("  - (#/#) #% = count / total as percentage")
    print("  - 'max' N/A for calibration / joint fidelity / NHANES AUC: extract_results summary stats didn't store per-node max.")
    print("    Objective RR max is computed from the per-row result list.")
    print("  - The 18-node joint-fidelity subset and 171-disease AUC subset are fixed, so those numbers move only")
    print("    when those specific nodes' CPTs change - our xlsx edits didn't touch them.")


if __name__ == '__main__':
    main()
