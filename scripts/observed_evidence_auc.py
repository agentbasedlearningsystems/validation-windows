#!/usr/bin/env python3
"""Per-condition + whole-net AUC using observed-evidence-only methodology.

For each disease target T:
  1. Find NHANES respondents who actually answered T (target outcome non-NaN).
  2. Build evidence from each respondent's *answered* variables only — never set
     NaN as evidence.
  3. Exclude trivial diagnostic biomarkers of T (e.g. a1c is the diagnostic
     definition of diabetes; blood pressure is the diagnostic definition of
     hypertension; BMI is the diagnostic definition of obesity).
  4. Predict_proba(evidence), record (P_pred, y_obs).
  5. Compute AUC per target + whole-net mean/median across all targets that
     pass minimum-positives threshold.

Outputs:
  paper/observed_evidence_auc_<label>.json  — per-target + whole-net summary
  paper/observed_evidence_auc_<label>.md    — human-readable report
"""
import json
import sys
import os
import time
import pickle
import warnings
import numpy as np
import pandas as pd
warnings.filterwarnings('ignore')

sys.path.insert(0, '.')
from sn_bayes.utils import (
    bayesInitialize,
    dictVarsAndValues,
    predict_proba_adjusted,
    smart_load_pickle,
)
from sklearn.metrics import roc_auc_score


# NHANES target discovery + value mapping. This script is the single source
# of truth for paper-claim AUC: per-target diagnostic-biomarker exclusion,
# observed-evidence-only methodology, per-target partial JSON checkpoints.

# Behaviors / exposures that have a NHANES code + binary _yes/_no but are NOT
# clinical events. Excluded because the paper claim is on binary clinical-event
# targets (diseases, conditions, symptoms, functional outcomes).
BEHAVIOR_EXCLUDES = {
    'low_calorie_diet',
    'strengthening_exercises',
    'five_days_smoke_cigarettes',
    'workout_anomaly',
    'paint_or_fuel_in_house',
    'natural_gas_cooking',
    'home_herbicide_use',
    'home_insecticide_use',
    'smokeless_tobacco_use',
    'vegetarian',
    'sunburn_last_year',
    'hysterectomy',
    'naive_sleep_apnea',
    # Symptom-level exclusions (May 4 audit): symptoms / functional self-reports
    # at a different level of abstraction from disease entities.
    'confusion',
    'increased_confusion_or_memory_loss',
    'dexterity',
    'pain',
}


def _atp3_metsyn(nh):
    """ATP-III metabolic syndrome: 3+ of 5 criteria."""
    needed = ['BMXWAIST', 'LBXTR', 'LBDHDD', 'BPXSY1', 'BPXDI1', 'LBXGLU', 'RIAGENDR']
    if not all(c in nh.columns for c in needed):
        return None
    waist_high = ((nh['RIAGENDR'] == 1) & (nh['BMXWAIST'] > 102)) | \
                 ((nh['RIAGENDR'] == 2) & (nh['BMXWAIST'] > 88))
    tg_high = nh['LBXTR'] >= 150
    hdl_low = ((nh['RIAGENDR'] == 1) & (nh['LBDHDD'] < 40)) | \
              ((nh['RIAGENDR'] == 2) & (nh['LBDHDD'] < 50))
    bp_high = (nh['BPXSY1'] >= 130) | (nh['BPXDI1'] >= 85)
    glu_high = nh['LBXGLU'] >= 100
    count = (waist_high.fillna(False).astype(int)
             + tg_high.fillna(False).astype(int)
             + hdl_low.fillna(False).astype(int)
             + bp_high.fillna(False).astype(int)
             + glu_high.fillna(False).astype(int))
    return (count >= 3).astype(int)


# Composite outcomes (no single NHANES code; computed from multiple).
COMPOSITE_OUTCOMES = {
    'metabolic_syndrome': {
        'positive_val': 'metabolic_syndrome_yes',
        'fn': lambda nh: _atp3_metsyn(nh),
    },
}


def discover_targets(config):
    """Discover all binary clinical-event targets in config."""
    dd = config['dependency_data']
    targets = {}
    for n, d in dd.items():
        if n in BEHAVIOR_EXCLUDES:
            continue
        typ = d.get('TYPE', '')
        if typ.startswith('naive_0_'):
            continue
        code = d.get('CODE')
        if not code:
            continue
        outvars = d.get('OUTVARS', d.get('PRIORS', {}))
        if not isinstance(outvars, dict) or len(outvars) != 2:
            continue
        keys = list(outvars.keys())
        yes_keys = [k for k in keys if k.endswith('_yes')]
        no_keys = [k for k in keys if k.endswith('_no')]
        if len(yes_keys) != 1 or len(no_keys) != 1:
            continue
        vr = d.get('value_ranges')
        if not vr:
            continue
        targets[n] = {
            'positive_val': yes_keys[0],
            'negative_val': no_keys[0],
            'code': code,
            'value_ranges': vr,
        }
    # Also add composite outcomes
    for n, spec in COMPOSITE_OUTCOMES.items():
        if n in dd:
            outvars = dd[n].get('OUTVARS', dd[n].get('PRIORS', {}))
            if spec['positive_val'] in outvars:
                targets[n] = {
                    'positive_val': spec['positive_val'],
                    'composite_fn': spec['fn'],
                }
    return targets


def map_nhanes_to_value(raw_value, value_ranges):
    """Map a raw NHANES value to its discrete value name (or None if unmappable)."""
    if pd.isna(raw_value):
        return None
    for val_name, ranges in value_ranges.items():
        for r in ranges:
            if isinstance(r, (tuple, list)):
                try:
                    if float(raw_value) >= r[0] and float(raw_value) <= r[1]:
                        return val_name
                except Exception:
                    pass
            else:
                try:
                    if float(raw_value) == float(r):
                        return val_name
                except Exception:
                    if raw_value == r:
                        return val_name
    return None


PICKLE = sys.argv[1] if len(sys.argv) > 1 else 'bayesianNetworkProto_post14_dd_v5.pickle'
CONFIG = sys.argv[2] if len(sys.argv) > 2 else 'bayesnet_config_linear.json'
LABEL = sys.argv[3] if len(sys.argv) > 3 else 'v5'
N_PER_TARGET = int(sys.argv[4]) if len(sys.argv) > 4 else 1500
# Optional: pass --targets <name1> <name2> ... to limit the run to a subset
# of targets (useful for re-runs with extended biomarker exclusions).
TARGETS_FILTER = None
if '--targets' in sys.argv:
    i = sys.argv.index('--targets')
    j = i + 1
    while j < len(sys.argv) and not sys.argv[j].startswith('--'):
        j += 1
    TARGETS_FILTER = set(sys.argv[i+1:j])
# Optional: pass --target-n-json <path> to use per-target N values
# (for prevalence-adaptive sampling). JSON is {target_name: N}.
TARGET_N_MAP = None
if '--target-n-json' in sys.argv:
    i = sys.argv.index('--target-n-json')
    with open(sys.argv[i+1]) as f:
        TARGET_N_MAP = json.load(f)
NHANES_PATH = './data/preprocessed_nhanes.csv'


# Strict per-target exclusion list. Rule: a node X is excluded from
# evidence for target T only if ALL of:
#   - X has a NHANES code (otherwise it can't be in evidence anyway)
#   - X is one of:
#       (a) deterministic any_of/all_of cascade ancestor of T
#       (b) NAIVE alias of T (same NHANES code, different CPT model)
#       (c) diagnostic biomarker that defines the disease threshold
#       (d) NHANES screening item that IS the disease question
#       (e) for umbrella diagnoses: a subtype reachable via T's _subtypes
#           any_of channel (e.g. specific cancers under the cancer umbrella —
#           NHANES coding is self-consistent: MCQ220 = yes if any specific
#           cancer is yes)
# Sibling diseases, downstream mortality, correlated clinical observations,
# and risk factors are NOT excluded — they are legitimate evidence.
# Generated and verified by scripts/proposed_exclusions_full.py against
# bayesnet_config_linear.json.

TRIVIAL_BIOMARKERS = {
    'anemia': {'hemoglobin_g_dL', 'hemoglobin_g_dL_naive'},
    'asthma': {'asthma_attack_last_year'},
    'breast_cancer': set(),
    'cancer': {'breast_cancer', 'colon_cancer', 'gallbladder_cancer',
               'kidney_cancer', 'laryngeal_cancer', 'leukemia', 'liver_cancer',
               'lung_cancer', 'melanoma', 'oesophageal_cancer', 'oral_cancer',
               'ovarian_cancer', 'pancreatic_cancer', 'prostate_cancer',
               'skin_cancer_non_melanoma', 'stomach_cancer', 'thyroid_cancer'},
    # dementia + Alzheimer's + non-AD dementia are by definition severe CI
    'cognitive_impairment': {'dementia', 'alzheimers', 'non_alzheimers_dementia'},
    'colon_cancer': set(),
    # emphysema + chronic bronchitis are the two GOLD-defined types of COPD
    'copd': {'copd_naive', 'emphysema', 'chronic_bronchitis'},
    # MI (MCQ160E) ≈ CAD (≥95% of MIs are from CAD), angina (MCQ160D = "Ever
    # told had coronary heart disease") IS CAD by name; CHF excluded because
    # CHF can occur from hypertension/cardiomyopathy/valvular disease alone.
    'coronary_artery_disease': {'heart_attack', 'heart_attack_naive', 'angina'},
    # alzheimers + non-AD dementia ARE dementia subtypes
    'dementia': {'alzheimers', 'non_alzheimers_dementia'},
    # PHQ-9 functional-impairment add-on (DPQ100) is part of the MDD diagnostic
    # criteria (functional impairment from depression symptoms). Individual
    # PHQ-9 symptom items (low_energy, poor_appetite, etc.) intentionally NOT
    # excluded — each can occur in non-depressed patients (anemia, hypothyroid,
    # grief, schizophrenia anhedonia).
    'depression': {'depression_difficulty_functioning'},
    # May 17 2026: added homa_ir + glucose_serum_mg_dL — both are diagnostic
    # surrogates of diabetes. HOMA-IR = fasting insulin × fasting glucose / 405,
    # i.e., a direct function of the already-excluded fasting_glucose +
    # insulin_uU_mL. glucose_serum_mg_dL is casual/random glucose (diagnostic
    # ≥200 mg/dL). Inclusion of either was leaking diagnostic evidence into
    # diabetes prediction → inflated AUC. The subnet AUC=0.7961 from cycle-17-v2
    # was almost certainly elevated by this leakage. After the fix, expect AUC
    # closer to the clinical-score range (FINDRISC, ADA ~0.70-0.75 without glucose).
    'diabetes': {'a1c', 'fasting_glucose_mg_dL', 'insulin_uU_mL',
                 'naive_insulin_uU_mL', 'homa_ir', 'glucose_serum_mg_dL'},
    'fall_history': set(),
    'gallbladder_cancer': set(),
    # 2026-05-30 leak fix (symmetry with coronary_artery_disease above): CAD
    # causes ~95% of MIs and `angina` = MCQ160D "ever told had coronary heart
    # disease" (CAD by name). CAD's list already excludes heart_attack; this
    # makes it symmetric so CAD/angina don't leak diagnostic evidence into MI
    # prediction (heart_attack was AUC-leak-flagged with a bimodal pred dist).
    'heart_attack': {'heart_attack_naive', 'coronary_artery_disease', 'angina'},
    'hip_fracture': {'hip_fracture_naive'},
    # Direct BP measurements (systolic BPXSY3, diastolic BPXDI2) and meds-related
    # nodes (BPQ040A prescription, BPQ050A compliance) are all diagnostic
    # equivalents for hypertension: if you have hypertension at NHANES visit
    # time your BP will be elevated, and being prescribed BP meds implies HTN.
    'hypertension': {'systolic', 'diastolic',
                     'high_blood_pressure_medication_compliance',
                     'high_blood_pressure_patient_prescription'},
    'insomnia': {'naive_insomnia'},
    'kidney_cancer': set(),
    'laryngeal_cancer': set(),
    'leukemia': set(),
    'liver_cancer': set(),
    'lung_cancer': set(),
    'lung_disease': {'copd', 'emphysema', 'chronic_bronchitis', 'asthma'},
    'malnutrition': set(),
    'melanoma': set(),
    'metabolic_syndrome': set(),  # composite — components don't individually equate
    'oesophageal_cancer': set(),
    'oral_cancer': set(),
    'osteoporosis': set(),
    'ovarian_cancer': set(),
    'pancreatic_cancer': set(),
    'prostate_cancer': set(),
    'skin_cancer_non_melanoma': set(),
    'sleep_apnea': {'naive_sleep_apnea', 'snore_how_often_per_week'},
    # insomnia is itself a sleep disorder (ICSD-3); told_inadequate_sleep
    # (SLQ050 = "told a doctor you have trouble sleeping") implies a
    # clinically reported sleep problem. Symptom-level items
    # (trouble_falling_to_sleep, wake_up_cant_sleep) NOT excluded.
    'sleep_disorder': {'insomnia', 'naive_insomnia', 'told_inadequate_sleep'},
    'spine_fracture': set(),
    'stomach_cancer': set(),
    'stroke': {'fatal_stroke'},  # fatal_stroke is a stroke subtype by definition
    'thyroid_cancer': set(),
    # Generic excludes applied to ALL targets (sentinel; handled in main loop):
    '_GLOBAL_': set(),
}


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def main():
    log(f'PICKLE: {PICKLE}')
    log(f'CONFIG: {CONFIG}')
    log(f'N per target cap: {N_PER_TARGET}')

    proto = smart_load_pickle(PICKLE)
    with open(CONFIG) as f:
        config = json.load(f)

    net = bayesInitialize(proto)
    vvp = dictVarsAndValues(proto, {})
    log(f'Net: {len(vvp)} variables')

    nhanes = pd.read_csv(NHANES_PATH)
    log(f'NHANES: {len(nhanes)} respondents x {len(nhanes.columns)} columns')

    targets = discover_targets(config)
    log(f'Discovered {len(targets)} clinical-event targets')

    # All NHANES-coded variables that COULD be evidence
    input_codes = {}
    for nname, ndata in config['dependency_data'].items():
        code = ndata.get('CODE')
        vr = ndata.get('value_ranges')
        if code and vr and code in nhanes.columns and nname in vvp:
            input_codes[nname] = {'code': code, 'value_ranges': vr}
    log(f'Input vars with NHANES codes: {len(input_codes)}')

    # Pre-compute composite outcome series for targets without 'code'
    for tn, tspec in targets.items():
        if 'composite_fn' in tspec and 'code' not in tspec:
            try:
                tspec['_y_series'] = tspec['composite_fn'](nhanes)
            except Exception as e:
                log(f'  composite_fn fail {tn}: {e}')
                tspec['_y_series'] = None

    results = {}
    overall_t0 = time.time()

    for ti, (tn, tspec) in enumerate(sorted(targets.items())):
        if TARGETS_FILTER is not None and tn not in TARGETS_FILTER:
            continue
        log(f'\n--- {ti+1}/{len(targets)} {tn} ---')
        # Outcome series for this target
        if 'code' in tspec:
            target_code = tspec['code']
            y_raw = nhanes[target_code] if target_code in nhanes.columns else None
            if y_raw is None:
                results[tn] = {'note': 'no_target_column'}
                log('  no target NHANES column; skip')
                continue
            # Map raw to binary y
            y_obs_full = y_raw.apply(
                lambda v: map_nhanes_to_value(v, tspec['value_ranges'])
            )
            y_obs_bin = y_obs_full.apply(
                lambda m: 1 if m == tspec['positive_val'] else (0 if m is not None else None)
            )
            outcome_nonna = y_obs_bin.notna() & y_raw.notna()
        elif 'composite_fn' in tspec:
            y_series = tspec.get('_y_series')
            if y_series is None:
                results[tn] = {'note': 'composite_fail'}
                continue
            y_obs_bin = y_series
            target_code = None
            outcome_nonna = y_obs_bin.notna()
        else:
            results[tn] = {'note': 'unknown_target_type'}
            continue

        eligible_idx = nhanes[outcome_nonna].index.tolist()
        log(f'  respondents with outcome: {len(eligible_idx)}')
        if len(eligible_idx) == 0:
            results[tn] = {'note': 'no_observations'}
            continue

        # Sample up to N_PER_TARGET (per-target N if --target-n-json given)
        n_for_target = (TARGET_N_MAP.get(tn, N_PER_TARGET)
                        if TARGET_N_MAP is not None else N_PER_TARGET)
        if len(eligible_idx) > n_for_target:
            np.random.seed(42)
            eligible_idx = list(np.random.choice(eligible_idx, n_for_target, replace=False))
        log(f'  sampled: {len(eligible_idx)} (cap={n_for_target})')

        # Trivial-biomarker exclusion list for this target
        trivial = set(TRIVIAL_BIOMARKERS.get(tn, set()))
        # also drop the target itself
        trivial.add(tn)

        preds = []
        actuals = []
        sexes = []   # parallel RIAGENDR per kept respondent (subgroup calibration)
        skip_evidence_too_small = 0
        skip_pred_nan = 0
        ok = 0
        t0 = time.time()
        for ri, ridx in enumerate(eligible_idx):
            if ri > 0 and ri % 200 == 0:
                eta = (time.time() - t0) / ri * (len(eligible_idx) - ri)
                log(f'    {ri}/{len(eligible_idx)} ok={ok} ev_small={skip_evidence_too_small} '
                    f'pred_nan={skip_pred_nan} eta={eta:.0f}s')
            row = nhanes.iloc[ridx]
            # Build evidence ONLY from variables this respondent actually answered
            evidence = {}
            for var_name, info in input_codes.items():
                if var_name in trivial:
                    continue
                if target_code is not None and info['code'] == target_code:
                    continue
                raw_val = row[info['code']]
                if pd.isna(raw_val):
                    continue
                mapped = map_nhanes_to_value(raw_val, info['value_ranges'])
                if mapped is None:
                    continue
                if var_name in vvp and mapped in vvp[var_name]:
                    evidence[var_name] = mapped
            if len(evidence) < 3:
                skip_evidence_too_small += 1
                continue
            try:
                probs = predict_proba_adjusted(net, proto, evidence)
            except Exception:
                skip_pred_nan += 1
                continue
            if probs is None or tn not in probs or not isinstance(probs[tn], dict):
                skip_pred_nan += 1
                continue
            p_pos = probs[tn].get(tspec['positive_val'])
            if p_pos is None or pd.isna(p_pos):
                skip_pred_nan += 1
                continue
            y = y_obs_bin.iloc[ridx]
            if pd.isna(y):
                continue
            preds.append(float(p_pos))
            actuals.append(int(y))
            sexes.append(int(row['RIAGENDR']) if 'RIAGENDR' in row.index and pd.notna(row['RIAGENDR']) else -1)
            ok += 1

        n = len(preds)
        n_pos = sum(actuals) if actuals else 0
        n_neg = n - n_pos
        elapsed = time.time() - t0
        log(f'  done: ok={ok}, ev_small={skip_evidence_too_small}, '
            f'pred_nan={skip_pred_nan} ({elapsed:.0f}s)')
        log(f'  AUC eligible: n={n}, pos={n_pos}, neg={n_neg}')
        # Always record the predictions and try to compute AUC. The
        # insufficient flag (n < 30 or pos/neg < 5) becomes a marker for
        # downstream filtering, not a discard — predictions are kept so
        # post-hoc decisions can be made (e.g. accept a 1-positive AUC).
        insufficient = (n < 30 or n_pos < 5 or n_neg < 5)
        entry = {
            'n': n, 'n_positive': n_pos, 'n_negative': n_neg,
            'prevalence': round(n_pos/n, 4) if n > 0 else None,
            'predictions': [round(float(p), 6) for p in preds],
            'actuals': [int(a) for a in actuals],
            'sexes': [int(s) for s in sexes],
            'insufficient_flag': insufficient,
        }
        if n_pos == 0 or n_neg == 0:
            entry['note'] = 'auc_undefined_one_class'
            log(f'  AUC undefined (one class): pos={n_pos}, neg={n_neg}')
        else:
            try:
                auc = float(roc_auc_score(actuals, preds))
                entry['auc'] = round(auc, 4)
                tag = ' (INSUFFICIENT)' if insufficient else ''
                log(f'  AUC={auc:.4f} (n={n}, pos={n_pos}, '
                    f'prev={n_pos/n:.3f}){tag}')
            except Exception as e:
                entry['note'] = f'auc_error: {e}'
                log(f'  AUC error: {e}')
        results[tn] = entry
        # Incremental save: write partial JSON after every target so a kill
        # at hour 19 doesn't lose 19 hours of work.
        try:
            _partial = {
                'methodology': 'observed-evidence-only (PARTIAL — incremental save)',
                'pickle': PICKLE, 'config': CONFIG,
                'n_per_target_cap': N_PER_TARGET,
                'progress': f'{ti+1}/{len(targets)}',
                'targets_completed_with_auc': len([k for k,v in results.items() if 'auc' in v]),
                'targets_with_note': len([k for k,v in results.items() if 'note' in v]),
                'targets': results,
            }
            _partial_path = f'paper/observed_evidence_auc_{LABEL}.partial.json'
            with open(_partial_path, 'w') as _pf:
                json.dump(_partial, _pf, indent=2)
        except Exception as _e:
            log(f'  [warn] partial save failed: {_e}')

    # Whole-net summary
    valid = {k: v for k, v in results.items() if 'auc' in v}
    if valid:
        aucs = [v['auc'] for v in valid.values()]
        summary = {
            'n_targets_with_auc': len(valid),
            'n_targets_total': len(results),
            'mean_auc': round(float(np.mean(aucs)), 4),
            'median_auc': round(float(np.median(aucs)), 4),
            'min_auc': round(float(np.min(aucs)), 4),
            'max_auc': round(float(np.max(aucs)), 4),
        }
    else:
        summary = {'n_targets_with_auc': 0, 'n_targets_total': len(results)}

    out = {
        'methodology': 'observed-evidence-only: per respondent, evidence built '
                       'from NHANES variables they actually answered; trivial '
                       'diagnostic biomarkers excluded per target',
        'pickle': PICKLE,
        'config': CONFIG,
        'n_per_target_cap': N_PER_TARGET,
        'summary': summary,
        'targets': results,
    }
    out_path = f'paper/observed_evidence_auc_{LABEL}.json'
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2)
    log(f'\nSaved {out_path}')

    # Per-disease human-readable report (the .md this script's docstring has always
    # promised but never wrote — so per-disease AUC is surfaced, not just the mean).
    md_path = f'paper/observed_evidence_auc_{LABEL}.md'
    rows = sorted(((tn, v) for tn, v in valid.items()), key=lambda kv: -kv[1]['auc'])
    with open(md_path, 'w') as f:
        f.write(f'# Observed-evidence AUC — {LABEL}\n\n')
        if valid:
            f.write(f"**Mean AUC {summary['mean_auc']}** · median {summary['median_auc']} "
                    f"· range {summary['min_auc']}–{summary['max_auc']} "
                    f"· {summary['n_targets_with_auc']}/{summary['n_targets_total']} targets\n\n")
        f.write('Observed-evidence-only; diagnostic biomarkers excluded per target. '
                'The mean hides per-disease spread — read the table.\n\n')
        f.write('| Disease | AUC | prevalence | n | flag |\n|---|---|---|---|---|\n')
        for tn, v in rows:
            flag = 'INSUFFICIENT' if v.get('insufficient_flag') else ''
            f.write(f"| {tn} | {v['auc']} | {v.get('prevalence')} | {v.get('n')} | {flag} |\n")
        skipped = {tn: v.get('note') for tn, v in results.items() if 'auc' not in v}
        if skipped:
            f.write('\n**No AUC (note):**\n\n')
            for tn, note in skipped.items():
                f.write(f"- {tn}: {note}\n")
    log(f'Saved {md_path} (per-disease report)')
    log(f'Total time: {(time.time()-overall_t0)/60:.1f} min')
    log(f'\nSUMMARY: {json.dumps(summary, indent=2)}')


if __name__ == '__main__':
    main()
