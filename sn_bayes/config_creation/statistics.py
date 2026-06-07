"""
Statistics conversion functions.

Extracted from config_creation.ipynb cell 13.
Converts between OR, HR, SMD, sensitivity/specificity and RR.
"""

import numpy as np
import pandas as pd

CI = 95
PLUS_MINUS = 0.05

def simulate_rr_ci(sens_mean, sens_ci, spec_mean, spec_ci, output_prior, n_sim=30000):
    # Assume normal approximation for sensitivity and specificity
    sens_se = (sens_ci[1] - sens_ci[0]) / (2 * 1.96)
    spec_se = (spec_ci[1] - spec_ci[0]) / (2 * 1.96)

    # Simulate
    sens_samples = np.random.normal(sens_mean, sens_se, n_sim)
    spec_samples = np.random.normal(spec_mean, spec_se, n_sim)

    # Constrain to [0,1]
    sens_samples = np.clip(sens_samples, 0, 1)
    spec_samples = np.clip(spec_samples, 0, 1)

    rr_samples = [_ss_to_rr(s, sp, output_prior) for s, sp in zip(sens_samples, spec_samples)]

    rr_mean = np.mean(rr_samples)
    ci_lower = np.percentile(rr_samples, 2.5)
    ci_upper = np.percentile(rr_samples, 97.5)

    return rr_mean, (ci_lower, ci_upper)


def _ss_to_rr(sensitivity: float, specificity: float, output_prior: float) -> float:
    TP = sensitivity * output_prior
    TN = specificity * (1.0 - output_prior)
    FN = output_prior - TP
    FP = (1.0 - output_prior) - TN

    rr = (TP/(TP+FP)) / (FN/(FN+TN))
    return rr


def convert_ss_to_rr(ss_stats: dict, output_prior: float) -> dict:
    sens_ci = [
        ss_stats['sensitivity'] - ss_stats['sensitivity_plus_minus'],
        ss_stats['sensitivity'] + ss_stats['sensitivity_plus_minus']
    ]
    spec_ci = [
        ss_stats['specificity'] - ss_stats['specificity_plus_minus'],
        ss_stats['specificity'] + ss_stats['specificity_plus_minus']
    ]
    relative_risk, (lower_ci, upper_ci) = simulate_rr_ci(
        sens_mean=ss_stats['sensitivity'], sens_ci=sens_ci,
        spec_mean=ss_stats['specificity'], spec_ci=spec_ci,
        output_prior=output_prior, n_sim=100000,
    )

    plus_minus = min(relative_risk-lower_ci, upper_ci-relative_risk)
    stats = {
        'relative_risk': relative_risk,
        'plus_minus': plus_minus,
        'ci': CI,
    }
    return stats


def _calc_sensitivity_specificity(a: int, b: int, c: int, d: int) -> dict:
    try:
        sensitivity = a/(a+c)
        specificity = d/(b+d)
        values = {
            'sensitivity': sensitivity,
            'sensitivity_plus_minus': PLUS_MINUS * sensitivity,
            'specificity': specificity,
            'specificity_plus_minus': PLUS_MINUS * specificity,
            'ci': CI,
        }
    except ZeroDivisionError as e:
        print('ZERODIVISIONERROR DURING SPECIFICITY AND SENSITIVITY CALCULATIONS')
        raise e
    return values


def calculate_sensitivity_specificity(
        dependency_type: str, 
        output_prior: float,
        input_prior: float) -> dict:

    if dependency_type == 'is_a':
        a = input_prior
        c = output_prior - a
        return _calc_sensitivity_specificity(a=a, b=0, c=c, d=1-a-c)
    elif dependency_type == 'subsumes':
        a = output_prior
        b = input_prior - a
        #print('a', a)
        #print('b', b)
        #print('d', 1-a-b)
        return _calc_sensitivity_specificity(a=a, b=b, c=0, d=1-a-b)
    elif dependency_type in ('equivalent_to', 'equivalent_distal'):
        a = input_prior
        return _calc_sensitivity_specificity(a=a, b=0, c=0, d=1-a)


def extract_row_stats(row: pd.Series) -> dict:
    stats = {}
    
    if not pd.isna(row['RR Stat Value']):
        if isinstance(row['RR Stat Value'], (int, float)):
            stats['relative_risk'] = row['RR Stat Value']
            stats['plus_minus'] = row['RR plus minus']
            stats['ci'] = CI
        else:
            print('SOME PROBLEM WITH RR STATS VALUE', row['output'], row['input'])
            #stats['relative_risk'] = 1
            #stats['plus_minus'] = 0.05
            #stats['ci'] = CI
            
    elif not pd.isna(row['Sensitivity Stat Value']):
        stats['sensitivity'] = row['Sensitivity Stat Value']
        stats['sensitivity_plus_minus'] = row['Sensitivity Plus Minus']
        
        stats['specificity'] = row['Specificity Stat Value']
        stats['specificity_plus_minus'] = row['Specificity Plus Minus']
        stats['ci'] = CI
        
    return stats

