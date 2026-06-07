#!/usr/bin/env python3
"""Query the post-rebuild network for an extended UKBB validation set (50 pairs).
Uses the existing 20-pair backbone plus 14 additional pairs from UKBB cohort
papers and large meta-analyses with UKBB representation, mapping each
parent-outcome combination onto network nodes that exist in the post-rebuild
config.
"""
import sys, pickle, json
from sn_bayes.utils import smart_load_pickle
sys.path.insert(0, '.')

from sn_bayes.utils import bayesInitialize, query, get_var_val_positions

PICKLE = sys.argv[1] if len(sys.argv) > 1 else 'bayesianNetworkProto.pickle'

# 20 original + 14 additional = 34 candidate pairs.
# Each tuple: (idx, parent, parent_yes_value, target, target_yes, ukbb_mid, lit_rr, comment)
PAIRS = [
    # --- original 20 ---
    (1, 'sleep_anomaly', 'workday_sleep_under_5', 'hypertension', 'hypertension_yes', 1.20, 1.21, 'short_sleep→HTN'),
    (2, 'daily_servings_milk', 'daily_servings_milk_3_or_more', 'fracture_risk', 'fracture_risk_high', 1.09, 0.57, 'milk→fracture (controversial)'),
    (3, 'IGF1_ng_ml', 'IGF1_ng_ml_quartile_4_above_210', 'prostate_cancer', 'prostate_cancer_yes', 1.25, 1.29, 'igf1_q4→prostate'),
    (4, 'five_days_smoke_cigarettes', 'five_days_smoke_cigarettes_yes', 'metabolic_syndrome', 'metabolic_syndrome_yes', 1.73, 1.26, 'smoking→metsyn'),
    (5, 'frailty', 'frailty_yes', 'all_cause_mortality', 'all_cause_mortality_high', 2.7, 2.0, 'frailty→acm'),
    (6, 'long_sleep', 'long_sleep_yes', 'all_cause_mortality', 'all_cause_mortality_high', 1.16, 1.34, 'long_sleep→acm'),
    (7, 'vitamin_d_nmol_per_liter', 'vitamin_d_nmol_per_liter_deficiency_under_30', 'all_cause_mortality', 'all_cause_mortality_high', 1.20, 1.57, 'vit_d_def→acm'),
    (8, 'bmi', 'bmi_over_30_obesity', 'hypertension', 'hypertension_yes', 1.64, 1.71, 'obese→HTN'),
    (9, 'bmi', 'bmi_over_30_obesity', 'diabetes', 'diabetes_yes', 5.5, 4.56, 'obese→diabetes'),
    (10, 'five_days_smoke_cigarettes', 'five_days_smoke_cigarettes_yes', 'lung_cancer', 'lung_cancer_yes', 8.0, 8.43, 'smoking→lc'),
    (11, 'sleep_apnea', 'sleep_apnea_yes', 'hypertension', 'hypertension_yes', 1.56, 1.53, 'sleep_apnea→HTN'),
    (12, 'bmi', 'bmi_over_30_obesity', 'all_cause_mortality', 'all_cause_mortality_high', 1.21, 1.28, 'obese→acm'),
    (13, 'cardiovascular_minutes', 'cardiovascular_minutes_quartile_4_above_65.00', 'diabetes', 'diabetes_yes', 0.51, 0.65, 'cv_q4→diabetes'),
    (14, 'moderate_intensity_sports_minutes', 'moderate_intensity_sports_minutes_quartile_4_above_15.00', 'diabetes', 'diabetes_yes', 0.51, 0.68, 'sport_q4→diabetes'),
    (15, 'bmi', 'bmi_over_30_obesity', 'breast_cancer', 'breast_cancer_yes', 1.65, 1.65, 'obese→breast_cancer'),
    (16, 'dietary_fiber_gm', 'dietary_fiber_gm_quartile_1_8.20_and_below', 'cardiac_event', 'cardiac_event_yes', 1.13, 1.20, 'fiber_q1→cardiac'),
    (17, 'age_at_menarche', 'age_at_menarche_under_10_or_below', 'metabolic_syndrome', 'metabolic_syndrome_yes', 1.62, 1.62, 'menarche→metsyn'),
    (18, 'smokeless_tobacco_use', 'smokeless_tobacco_use_yes', 'cardiac_event', 'cardiac_event_yes', 1.37, 1.37, 'snus→cardiac'),
    (19, 'smokeless_tobacco_use', 'smokeless_tobacco_use_yes', 'heart_disease', 'heart_disease_yes', 1.37, 1.17, 'snus→hd'),
    (20, 'vegetarian', 'vegetarian_yes', 'fracture_risk', 'fracture_risk_high', 1.50, 1.50, 'vegetarian→fracture'),
    # --- additional 14: UKBB-cohort or strong UKBB-pooling meta-analyses ---
    (21, 'bmi', 'bmi_over_30_obesity', 'kidney_cancer', 'kidney_cancer_yes', 1.43, 1.30, 'obese→kidney (Bhaskaran UKBB-meta)'),
    (22, 'bmi', 'bmi_over_30_obesity', 'liver_cancer', 'liver_cancer_yes', 1.50, 1.31, 'obese→liver (Bhaskaran UKBB-meta)'),
    (23, 'bmi', 'bmi_over_30_obesity', 'colon_cancer', 'colon_cancer_yes', 1.20, 1.30, 'obese→colon (Renehan/UKBB)'),
    (24, 'bmi', 'bmi_over_30_obesity', 'oesophageal_cancer', 'oesophageal_cancer_yes', 1.40, 1.51, 'obese→oesophageal (UKBB-meta)'),
    (25, 'bmi', 'bmi_over_30_obesity', 'pancreatic_cancer', 'pancreatic_cancer_yes', 1.20, 1.10, 'obese→pancreatic (Bhaskaran)'),
    (26, 'bmi', 'bmi_over_30_obesity', 'stroke', 'stroke_yes', 1.13, 1.20, 'obese→stroke (Strazzullo meta)'),
    (27, 'bmi', 'bmi_over_30_obesity', 'heart_attack', 'heart_attack_yes', 1.40, 1.36, 'obese→MI (Iliodromiti UKBB)'),
    (28, 'sleep_anomaly', 'workday_sleep_under_5', 'diabetes', 'diabetes_yes', 1.30, 1.28, 'short_sleep→diabetes (Cappuccio)'),
    (29, 'sleep_anomaly', 'workday_sleep_under_5', 'all_cause_mortality', 'all_cause_mortality_high', 1.12, 1.12, 'short_sleep→acm (Cappuccio meta)'),
    (30, 'sleep_anomaly', 'workday_sleep_under_5', 'cardiac_event', 'cardiac_event_yes', 1.25, 1.48, 'short_sleep→cardiac (Cappuccio)'),
    (31, 'vitamin_d_nmol_per_liter', 'vitamin_d_nmol_per_liter_deficiency_under_30', 'diabetes', 'diabetes_yes', 1.30, 1.50, 'vit_d_def→diabetes (Forouhi UKBB)'),
    (32, 'vitamin_d_nmol_per_liter', 'vitamin_d_nmol_per_liter_deficiency_under_30', 'cognitive_impairment', 'cognitive_impairment_yes', 1.20, 1.20, 'vit_d_def→ci (Llewellyn UKBB)'),
    (33, 'sleep_apnea', 'sleep_apnea_yes', 'stroke', 'stroke_yes', 1.50, 1.45, 'sleep_apnea→stroke (Yaggi)'),
    (34, 'sleep_apnea', 'sleep_apnea_yes', 'diabetes', 'diabetes_yes', 1.40, 1.62, 'sleep_apnea→diabetes (Reichmuth)'),
    # --- additional 18: smoking, alcohol, depression, mediterranean, exercise on multiple targets ---
    (35, 'five_days_smoke_cigarettes', 'five_days_smoke_cigarettes_yes', 'cardiac_event', 'cardiac_event_yes', 1.50, 1.78, 'smoking→cardiac (USPSTF/UKBB)'),
    (36, 'five_days_smoke_cigarettes', 'five_days_smoke_cigarettes_yes', 'all_cause_mortality', 'all_cause_mortality_high', 2.00, 2.32, 'smoking→acm (Doll meta)'),
    (37, 'five_days_smoke_cigarettes', 'five_days_smoke_cigarettes_yes', 'stroke', 'stroke_yes', 1.50, 1.50, 'smoking→stroke (Shah meta)'),
    (38, 'five_days_smoke_cigarettes', 'five_days_smoke_cigarettes_yes', 'heart_attack', 'heart_attack_yes', 2.00, 2.50, 'smoking→MI (Mons UKBB)'),
    (39, 'five_days_smoke_cigarettes', 'five_days_smoke_cigarettes_yes', 'diabetes', 'diabetes_yes', 1.30, 1.40, 'smoking→diabetes (Willi meta)'),
    (40, 'five_days_smoke_cigarettes', 'five_days_smoke_cigarettes_yes', 'coronary_artery_disease', 'coronary_artery_disease_yes', 2.00, 2.30, 'smoking→CAD (Mons UKBB)'),
    (41, 'mediterranean_diet', 'mediterranean_diet_yes', 'cardiovascular_disease', 'cardiovascular_disease_yes', 0.70, 0.71, 'med_diet→CVD (Estruch RCT/UKBB)'),
    (42, 'mediterranean_diet', 'mediterranean_diet_yes', 'diabetes', 'diabetes_yes', 0.80, 0.81, 'med_diet→diabetes (Schwingshackl)'),
    (43, 'mediterranean_diet', 'mediterranean_diet_yes', 'all_cause_mortality', 'all_cause_mortality_high', 0.85, 0.92, 'med_diet→acm (Sofi meta)'),
    (44, 'heavy_alcohol_consumption_last_year', 'heavy_alcohol_consumption_days_4_or_5_drinks_last_year_daily', 'liver_cancer', 'liver_cancer_yes', 2.00, 2.07, 'heavy_alcohol→liver (Bagnardi)'),
    (45, 'heavy_alcohol_consumption_last_year', 'heavy_alcohol_consumption_days_4_or_5_drinks_last_year_daily', 'all_cause_mortality', 'all_cause_mortality_high', 1.40, 1.40, 'heavy_alcohol→acm (Wood UKBB)'),
    (46, 'depression', 'depression_yes', 'all_cause_mortality', 'all_cause_mortality_high', 1.60, 1.50, 'depression→acm (UKBB cohort)'),
    (47, 'depression', 'depression_yes', 'cardiac_event', 'cardiac_event_yes', 1.40, 1.30, 'depression→cardiac (Hare meta)'),
    (48, 'depression', 'depression_yes', 'diabetes', 'diabetes_yes', 1.40, 1.38, 'depression→diabetes (Mezuk meta)'),
    (49, 'cardiovascular_minutes', 'cardiovascular_minutes_quartile_4_above_65.00', 'all_cause_mortality', 'all_cause_mortality_high', 0.65, 0.78, 'cv_q4→acm (Boonpor UKBB)'),
    (50, 'cardiovascular_minutes', 'cardiovascular_minutes_quartile_4_above_65.00', 'cardiovascular_disease', 'cardiovascular_disease_yes', 0.70, 0.86, 'cv_q4→CVD (Aune meta)'),
    (51, 'bmi', 'bmi_over_30_obesity', 'cognitive_impairment', 'cognitive_impairment_yes', 1.30, 1.30, 'obese→ci (Pedditizi meta)'),
    (52, 'bmi', 'bmi_over_30_obesity', 'cardiovascular_disease', 'cardiovascular_disease_yes', 1.45, 1.50, 'obese→CVD (Khan UKBB)'),
]


def main():
    print(f"Loading {PICKLE}...")
    proto = smart_load_pickle(PICKLE)
    net = bayesInitialize(proto)
    vvp = get_var_val_positions(proto)

    results = []
    print(f"Querying {len(PAIRS)} pairs ...")
    for idx, parent, parent_val, target, target_yes, ukbb_mid, lit_rr, comment in PAIRS:
        if parent not in vvp:
            results.append({'idx': idx, 'parent': parent, 'target': target, 'status': f'parent ABSENT', 'comment': comment})
            print(f"{idx:<3} {parent[:25]:<25} {target[:25]:<25} parent ABSENT")
            continue
        if parent_val not in vvp[parent]:
            results.append({'idx': idx, 'parent': parent, 'target': target, 'status': 'parent_val ABSENT', 'comment': comment})
            print(f"{idx:<3} {parent[:25]:<25} {target[:25]:<25} val ABSENT")
            continue

        # baseline: query without evidence
        try:
            r0 = query(net, proto, {}, [target])
            r_yes = query(net, proto, {parent: parent_val}, [target])
        except Exception as e:
            results.append({'idx': idx, 'parent': parent, 'target': target, 'status': f'err {e}', 'comment': comment})
            continue

        if target not in r0 or target not in r_yes:
            results.append({'idx': idx, 'parent': parent, 'target': target, 'status': 'target ABSENT', 'comment': comment})
            continue
        if target_yes not in r0[target]:
            results.append({'idx': idx, 'parent': parent, 'target': target, 'status': f'target_yes ABSENT', 'comment': comment})
            continue

        base_p = r0[target][target_yes]
        ev_p = r_yes[target][target_yes]
        if base_p <= 0:
            results.append({'idx': idx, 'parent': parent, 'target': target, 'status': 'base_p=0', 'comment': comment})
            continue

        qrr = ev_p / base_p
        gap_lit = qrr - lit_rr
        gap_ukbb = qrr - ukbb_mid
        results.append({
            'idx': idx, 'parent': parent, 'target': target,
            'qrr_v2': qrr, 'ukbb': ukbb_mid, 'lit': lit_rr,
            'gap_lit': gap_lit, 'gap_ukbb': gap_ukbb, 'comment': comment,
        })
        print(f"{idx:<3} {parent[:25]:<25} {target[:25]:<25} {qrr:.3f}   {ukbb_mid:.2f}   {lit_rr:.2f}   {gap_lit:+.3f}   {gap_ukbb:+.3f}  {comment}")

    out = 'paper/v2_all_ukbb_qrr_50.json'
    with open(out, 'w') as f:
        json.dump(results, f, indent=2, default=float)
    print(f"\nSaved {out}")

    # Summary statistics
    valid = [r for r in results if 'qrr_v2' in r]
    if valid:
        n = len(valid)
        # direction match (sign of (UKBB-1) and (qrr-1))
        dir_match = sum(1 for r in valid if (r['ukbb'] > 1) == (r['qrr_v2'] > 1))
        # within 50% of UKBB
        within_50 = sum(1 for r in valid if abs(r['qrr_v2'] - r['ukbb']) / max(r['ukbb'], 0.01) <= 0.5)
        # within 50% of literature
        within_50_lit = sum(1 for r in valid if abs(r['qrr_v2'] - r['lit']) / max(r['lit'], 0.01) <= 0.5)
        print(f"\n=== Summary on {n} valid pairs ===")
        print(f"  Direction match: {dir_match}/{n} ({100*dir_match/n:.1f}%)")
        print(f"  Within 50% of UKBB: {within_50}/{n} ({100*within_50/n:.1f}%)")
        print(f"  Within 50% of lit: {within_50_lit}/{n} ({100*within_50_lit/n:.1f}%)")

if __name__ == '__main__':
    main()
