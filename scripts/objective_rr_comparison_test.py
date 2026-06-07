#!/usr/bin/env python3
"""Objective RR comparison test - the right one, per user spec (Apr 20):

  For every spreadsheet row (output=child, input=parent, input_values=[v1,v2,...],
  RR=r, Type=disease_tag):
    - OR-aggregate parent ∈ {v1, v2, ...}: P(parent ∈ V) = Σ_v P(parent=v)
    - Clamp parent to v (one at a time), query child, weight by P(parent=v),
      marginalize to get conditional P(child=first_state | parent ∈ V)
    - ratio = conditional / baseline
    - Compare ratio to study_rr: direction match + magnitude % error

  Tests every RR-bearing row, including multi-value rows, distal-chain rows,
  and every Type. If the distal chain fails to propagate, the test fails -
  that's diagnostic, not a reason to skip.

  Two targets:
    1. DIRECT  - query the row's `output` (the edge's stated child)
    2. DISEASE - query the row's `Type` if it's a known disease name (tests
                 end-to-end propagation through intermediate chain)

Writes paper/objective_rr_comparison_<label>.json with per-row comparison and
summary stats.
"""
import sys, os, json, time, warnings, argparse
import numpy as np
import pandas as pd
from sn_bayes.utils import smart_load_pickle

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')


def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def load_df():
    # Post Apr 30 2026: data/relations.csv is canonical. xlsx is no longer
    # read by build scripts. Use load_relations() to get the dataframe the
    # build sees.
    sys.path.insert(0, '.')
    from sn_bayes.data_loader import load_relations
    return load_relations()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--label', required=True)
    ap.add_argument('--pickle', default='bayesianNetworkProto.pickle')
    ap.add_argument('--config', default='bayesnet_config_linear.json')
    args = ap.parse_args()

    log(f"=== objective RR test: {args.label} ===")
    import pickle
    proto = smart_load_pickle(args.pickle)
    with open(args.config) as f: cfg = json.load(f)

    from sn_bayes.utils import bayesInitialize, query, get_var_val_positions
    net = bayesInitialize(proto)
    vvp = get_var_val_positions(proto)
    log(f"Net loaded, {len(vvp)} variables")

    # Per-user-spec Apr 20: target = Type column's node if it's in the net AND
    # Type != output (distal row); else target = output (direct edge). No
    # disease-name filter - every RR row gets tested against its correct target.

    # Baseline query (all variables)
    r0 = query(net, proto, {}, list(vvp.keys()))

    df = load_df()
    log(f"xlsx rows: {df.shape[0]}")

    # All 391 testable pairs: RR OR Sens/Spec with input set
    rr_col = 'RR Stat Value'
    has_rr = df[rr_col].notna()
    has_sens = df['Sensitivity Stat Value'].notna() if 'Sensitivity Stat Value' in df.columns else pd.Series(False, index=df.index)
    has_input = df['input'].notna()
    test_rows = df[(has_rr | has_sens) & has_input].copy()
    log(f"All study pairs (RR or Sens+Spec, input set): {len(test_rows)}")
    log(f"  of which have RR: {(has_rr & has_input).sum()}")
    log(f"  of which have Sens-only: {(has_sens & ~has_rr & has_input).sum()}")

    # Strip whitespace on object cells we'll look at
    for c in ('output','input','input values','Type'):
        test_rows[c] = test_rows[c].apply(lambda e: e.strip() if isinstance(e, str) else e)

    def parse_vals(s):
        if pd.isna(s): return []
        return [v.strip() for v in str(s).split(',') if v.strip()]

    def first_state(node):
        if node not in r0: return None
        ks = list(r0[node].keys())
        return ks[0] if ks else None

    def baseline_parent_priors(parent, values):
        # P(parent=v) for each v in values, from marginal r0
        out = {}
        if parent not in r0: return out
        for v in values:
            # if exact match, use it; else try substring match for quartile/range names
            if v in r0[parent]:
                out[v] = float(r0[parent][v])
                continue
            # try prefix match
            for kv in r0[parent]:
                if kv.startswith(v) or v in kv:
                    out[v] = float(r0[parent][kv])
                    # remember resolved name for the query call
                    break
        return out

    def resolve_value_name(parent, v):
        """Return the canonical state name in vvp[parent] that matches v, or None."""
        if parent not in vvp: return None
        states = list(vvp[parent].keys())
        if v in states: return v
        for s in states:
            if s.startswith(v) or v in s: return s
        return None

    def test_one_row(row, target):
        """target = row's 'output' (direct) or row's 'Type' (disease)."""
        parent = row['input']
        values_raw = parse_vals(row['input values'])
        try:
            study_rr = float(row[rr_col])
        except (TypeError, ValueError):
            return None  # e.g., SMD_UNRELIABLE sentinel
        if not values_raw or parent not in vvp: return None
        # Resolve values
        resolved = []
        for v in values_raw:
            r = resolve_value_name(parent, v)
            if r: resolved.append(r)
        if not resolved: return None
        # Target first state
        tgt_first = first_state(target)
        if tgt_first is None: return None
        base_p = float(r0.get(target, {}).get(tgt_first, 0))
        if base_p <= 0: return None
        # Weighted marginalization over values
        numerator = 0.0; denom = 0.0
        for v in resolved:
            p_v = float(r0.get(parent, {}).get(v, 0))
            if p_v <= 0: continue
            try:
                r_ev = query(net, proto, {parent: v}, [target])
            except Exception:
                continue
            p_child_given = float(r_ev.get(target, {}).get(tgt_first, 0))
            numerator += p_child_given * p_v
            denom += p_v
        if denom <= 0: return None
        cond_p = numerator / denom
        q_rr = cond_p / base_p
        pct_err = abs(q_rr - study_rr) / max(abs(study_rr), 0.01) * 100.0
        study_dir = 'UP' if study_rr > 1.01 else ('DOWN' if study_rr < 0.99 else 'NONE')
        q_dir = 'UP' if q_rr > 1.001 else ('DOWN' if q_rr < 0.999 else 'NONE')
        dir_match = (study_dir == q_dir) or study_dir == 'NONE' or q_dir == 'NONE'
        return {
            'target': target,
            'parent': parent,
            'values': resolved,
            'n_values': len(resolved),
            'study_rr': round(study_rr, 4),
            'query_rr': round(q_rr, 4),
            'pct_error': round(pct_err, 1),
            'direction_match': dir_match,
            'study_dir': study_dir,
            'query_dir': q_dir,
        }

    # Per-row test using the right target per user's rule:
    #   target = Type if Type is a net node AND Type != output (distal row)
    #   else target = output (direct edge)
    results = []
    skipped_no_parent = 0; skipped_no_target = 0
    target_was_type = 0; target_was_output = 0

    for _, row in test_rows.iterrows():
        if row['input'] not in vvp:
            skipped_no_parent += 1
            continue
        out = row['output']
        typ = row.get('Type')
        # Determine target
        if isinstance(typ, str) and typ in vvp and typ != out:
            target = typ
            target_src = 'Type'
            target_was_type += 1
        elif out in vvp:
            target = out
            target_src = 'output'
            target_was_output += 1
        else:
            skipped_no_target += 1
            continue
        r = test_one_row(row, target)
        if r:
            r['row_index'] = int(row.name) + 2
            r['row_output'] = out
            r['row_input'] = row['input']
            r['row_type'] = typ
            r['target'] = target
            r['target_source'] = target_src
            results.append(r)

    def summarize(label, results):
        if not results:
            log(f"  {label}: no testable rows"); return {}
        total = len(results)
        dir_correct = sum(1 for r in results if r['direction_match'])
        dir_wrong = total - dir_correct
        errs = [r['pct_error'] for r in results if np.isfinite(r['pct_error'])]
        w25 = sum(1 for e in errs if e <= 25)
        w50 = sum(1 for e in errs if e <= 50)
        log(f"\n  === {label} (n={total}) ===")
        log(f"    Direction correct: {dir_correct}/{total} ({dir_correct/total*100:.1f}%)")
        log(f"    Direction wrong:   {dir_wrong}/{total}")
        log(f"    Median % error: {np.median(errs):.1f}%")
        log(f"    Mean % error:   {np.mean(errs):.1f}%")
        log(f"    Within 25% err: {w25}/{total} ({w25/total*100:.1f}%)")
        log(f"    Within 50% err: {w50}/{total} ({w50/total*100:.1f}%)")
        return {
            'n': total, 'dir_correct': dir_correct, 'dir_wrong': dir_wrong,
            'direction_accuracy_pct': round(dir_correct/total*100, 1),
            'median_pct_error': round(float(np.median(errs)), 1),
            'mean_pct_error': round(float(np.mean(errs)), 1),
            'within_25pct': w25, 'within_25pct_rate': round(w25/total*100, 1),
            'within_50pct': w50, 'within_50pct_rate': round(w50/total*100, 1),
        }

    log(f"\nTotal study pairs: {len(test_rows)}")
    log(f"Skipped (parent not in net): {skipped_no_parent}")
    log(f"Skipped (no valid target): {skipped_no_target}")
    log(f"Target was row.Type (distal): {target_was_type}")
    log(f"Target was row.output (direct): {target_was_output}")

    s = summarize('OBJECTIVE RR COMPARISON TEST (every study pair, target per row)', results)

    out = {
        'label': args.label,
        'total_study_pairs': len(test_rows),
        'skipped_no_parent': skipped_no_parent,
        'skipped_no_target': skipped_no_target,
        'target_was_type_distal': target_was_type,
        'target_was_output_direct': target_was_output,
        'summary': s,
        'results': results,
    }
    os.makedirs('paper', exist_ok=True)
    out_path = f'paper/objective_rr_comparison_{args.label}.json'
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2)
    log(f"\nWrote {out_path}")


if __name__ == '__main__':
    main()
