"""
Distal variable calculation functions.

Extracted from config_creation.ipynb cell 9.
Handles NHANES mask creation, independent group detection,
weighted RR calculation, and Monte Carlo CI estimation.

Includes mask cache for performance and fast_monte_carlo_rr_ci_distal
that precomputes NHANES percentages outside the MC loop.
"""

from functools import reduce
from typing import (
    Iterable,
    Tuple,
    List,
    Optional,
)
import itertools

import numpy as np
import pandas as pd
from tqdm import tqdm

# DISTAL VARIABLES STATS
from functools import reduce
from typing import (
    Iterable,
    Tuple
)
import itertools
from multiprocessing import Pool, cpu_count

CI = 95
NHANES_THRESHOLD = 0
DISTAL_MC_SIMULATIONS = 500
DEBUG_PRINT = False


# Cache for NHANES mask computations
_mask_cache = {}

def _clear_mask_cache():
    """Clear the mask cache."""
    global _mask_cache
    _mask_cache = {}

def _get_cached_masks(nhanes_data, code, value_ranges):
    """Get or compute nonna_mask and per-value masks for a CODE variable."""
    cache_key = (code, tuple(sorted(
        (k, tuple(tuple(x) if isinstance(x, (list, tuple)) else (x,) for x in v))
        for k, v in value_ranges.items()
    )))
    if cache_key in _mask_cache:
        return _mask_cache[cache_key]

    nonna_mask = ~(nhanes_data[code].isna())
    valid_values_mask = False
    for value, ranges in value_ranges.items():
        for r in ranges:
            if isinstance(r, (tuple, list)):
                valid_values_mask = valid_values_mask | ((nhanes_data[code] >= r[0]) & (nhanes_data[code] <= r[1]))
            elif isinstance(r, (float, int)):
                valid_values_mask = valid_values_mask | (nhanes_data[code] == r)
    nonna_mask = nonna_mask & valid_values_mask

    per_value_masks = {}
    for val_name, ranges in value_ranges.items():
        vm = False
        for r in ranges:
            if isinstance(r, (tuple, list)):
                vm = vm | ((nhanes_data[code] >= r[0]) & (nhanes_data[code] <= r[1]))
            elif isinstance(r, (float, int)):
                vm = vm | (nhanes_data[code] == r)
        per_value_masks[val_name] = vm

    result = (nonna_mask, per_value_masks)
    _mask_cache[cache_key] = result
    return result



def create_independent_groups(masks: dict, nhanes_data: pd.DataFrame, unique_conditions: set, threshold: int = NHANES_THRESHOLD) -> list:
    if len(masks) <= 1:
        return [(elem,) for elem in unique_conditions]
    
    groups = []
    total = len(masks)
    keys = masks.keys()

    for n in range(total, 0, -1):
        # If n == 1 return here?
        
        counts = []
        for comb in itertools.combinations(keys, n):
            nonna_mask = True
            for key in comb:
                nonna_mask = nonna_mask & masks[key]
            counts.append((comb, nonna_mask.sum() if isinstance(nonna_mask, pd.Series) else len(nhanes_data)))
        counts.sort(key=lambda e: e[1])
        
        if counts[-1][1] > threshold:
            largest_group = counts[-1][0]
            groups.append(largest_group)
            complement = {key: masks[key] for key in keys if key not in largest_group}
            for condition in largest_group:
                unique_conditions.remove(condition)
            groups += create_independent_groups(complement, nhanes_data, unique_conditions, threshold)
            return groups

    if not groups:
        return [(elem,) for elem in unique_conditions]


def calculate_final_prior(priors: Iterable[float], dependency_type: str) -> float:
    # function to calculate positive prior in independent case
    
    if dependency_type == 'all_of':
        final_prior = reduce(lambda a, b: a*b, priors)
    
    if dependency_type in ('any_of', 'dependency_distal'):    # Inclusion-exclusion principle for independent variables
        neg_prior = 1.0
        for p in priors:
            neg_prior *= (1-p)
        final_prior = 1 - neg_prior
        
    return final_prior


def create_masks_priors(nhanes_data: pd.DataFrame, sub_dependencies: dict, var_values: List[str] = None, is_first: bool = True):
    if 'CODE' in sub_dependencies:
        code = sub_dependencies['CODE']
        nhanes_id = id(nhanes_data)

        # Build hashable key for nonna_mask cache
        vr_key = tuple(
            (vname, tuple(r) if isinstance(r, (tuple, list)) else r)
            for vname, ranges in sub_dependencies['value_ranges'].items()
            for r in ranges
        )
        nonna_cache_key = (nhanes_id, code, vr_key)

        if nonna_cache_key in _mask_cache:
            nonna_mask = _mask_cache[nonna_cache_key]
        else:
            nonna_mask = ~(nhanes_data[code].isna())
            valid_values_mask = False
            for value, ranges in sub_dependencies['value_ranges'].items():
                for r in ranges:
                    if isinstance(r, (tuple, list)):
                        valid_values_mask = valid_values_mask | ((nhanes_data[code] >= r[0]) & (nhanes_data[code] <= r[1]))
                    elif isinstance(r, (float, int)):
                        valid_values_mask = valid_values_mask | (nhanes_data[code] == r)
            nonna_mask = nonna_mask & valid_values_mask
            _mask_cache[nonna_cache_key] = nonna_mask

        # Build hashable key for value_mask cache
        val_ranges_key = tuple(
            (sv, tuple(r) if isinstance(r, (tuple, list)) else r)
            for sv in var_values
            for r in sub_dependencies['value_ranges'][sv]
        )
        value_cache_key = (nhanes_id, code, val_ranges_key)

        if value_cache_key in _mask_cache:
            value_mask = _mask_cache[value_cache_key]
        else:
            value_mask = False
            for str_value in var_values:
                value_ranges = sub_dependencies['value_ranges'][str_value]
                for r in value_ranges:
                    if isinstance(r, (tuple, list)):
                        value_mask = value_mask | ((nhanes_data[code] >= r[0]) & (nhanes_data[code] <= r[1]))
                    elif isinstance(r, (float, int)):
                        value_mask = value_mask | (nhanes_data[code] == r)
            _mask_cache[value_cache_key] = value_mask

        return value_mask, nonna_mask, None
        
    elif sub_dependencies['TYPE'] in {'discrete_priors', 'dependency_priors'} and not is_first:
        prior = 0
        for value in var_values:
            if 'OUTVARS' in sub_dependencies:
                prior += sub_dependencies['OUTVARS'][value]
            else:
                prior += sub_dependencies['PRIORS'][value]
        return None, None, prior
        
    else:
        priors = {}
        value_masks = {}
        nonna_masks = {}
        for invar, var_data in sub_dependencies['INVARS'].items():
            for entry in var_data:
                condition_key = (invar, tuple(entry['VALUE']))    # We can have multiple inputs for the same variable with different stats
                if 'value_mask' in entry or 'prior' in entry:
                    if 'value_mask' in entry:
                        value_masks[condition_key] = entry['value_mask']
                        nonna_masks[condition_key] = entry['value_mask']
                    if 'prior' in entry:
                        priors[condition_key] = entry['prior']
                else:
                    if DEBUG_PRINT:
                        if sub_dependencies['INPUTS'][invar]['TYPE'] in {'all_of', 'any_of'}:    # DEBUG
                            print('VARIABLE', invar)   # DEBUG
                            print('-'*50)    # DEBUG
                    value_mask, nonna_mask, prior = create_masks_priors(
                        nhanes_data=nhanes_data,
                        sub_dependencies=sub_dependencies['INPUTS'][invar],
                        var_values=entry['VALUE'],
                        is_first=False,
                    )

                    if sub_dependencies['INPUTS'][invar]['TYPE'] in {'all_of', 'any_of'}:    # Inversing mask and priors if input value is not in value1 col. Do we need this check? 
                        if entry['VALUE'][0] != sub_dependencies['INPUTS'][invar]['OUTVARS'][0]:
                            #print('REVERSING MASK AND PRIORS FOR', invar, entry['VALUE'])    # DEBUG
                            if value_mask is not None:
                                value_mask = ~value_mask
                            if prior is not None:
                                prior = 1 - prior
                                #print('REVERSED PRIOR', prior)
                    
                    if value_mask is not None:
                        value_masks[condition_key] = value_mask
                        nonna_masks[condition_key] = nonna_mask
                    
                    if prior is not None:
                        priors[condition_key] = prior

        # Collapsing all masks and priors into one for NESTED calls
        if is_first:
            return value_masks, nonna_masks, priors
        else:
            #print('COLLAPSING MASKS AND PRIORS, TYPE', sub_dependencies['TYPE'])    # DEBUG
            if value_masks:
                unique_conditions = list(set(list(value_masks.keys())+list(priors.keys())))
                #if DEBUG_PRINT:
                #print('UNIQUE CONDITIONS', unique_conditions)    # DEBUG
                groups = create_independent_groups(nonna_masks, nhanes_data, unique_conditions)
                #print('GROUPS', groups)

                if DEBUG_PRINT:
                    for i, group in enumerate(groups):    # DEBUG
                        nonna_mask_debug = True
                        priors_debug = {}
                        for cond in group:
                            if cond in value_masks:
                                nonna_mask_debug = nonna_mask_debug & nonna_masks[cond]
                            else:
                                priors_debug[cond] = priors[cond]
    
                        print(f'GROUP {i}', group)
                        if isinstance(nonna_mask_debug, pd.Series):
                            print('NUMBER OF SEQNs', nhanes_data[nonna_mask_debug].shape[0])
                        else:
                            print('PRIORs', priors[cond])
                        print('-'*50)
                
                if len(groups) > 1:
                    #print('NUMBER OF GROUPS', len(groups))    # DEBUG
                    #print(groups)    # DEBUG 
                    group_priors = []
                    for group in groups:
                        #print(group)    # DEBUG
                        g_priors = []
                        value_mask = False if sub_dependencies['TYPE'] == 'any_of' else True
                        nonna_mask = True 
                        for condition in group:
                            if condition in value_masks:
                                nonna_mask = nonna_mask & nonna_masks[condition]
                                if sub_dependencies['TYPE'] == 'all_of':
                                    value_mask = value_mask & value_masks[condition]
                                if sub_dependencies['TYPE'] == 'any_of':
                                    value_mask = value_mask | value_masks[condition]
                                
                            if condition in priors:
                                g_priors.append(priors[condition])

                        if isinstance(value_mask, pd.Series):
                            percentage = (value_mask & nonna_mask).sum() / nonna_mask.sum()
                            g_priors.append(percentage)
                            #print(percentage)    # DEBUG
                        group_priors.append(calculate_final_prior(g_priors, sub_dependencies['TYPE']))

                    final_value_mask, final_nonna_mask = None, None
                    final_prior = calculate_final_prior(group_priors, sub_dependencies['TYPE'])
                    #print('COLLAPSED PRIOR FOR INDEPENDENT GROUPS', final_prior)    # DEBUG
                    #print('GROUP PRIORS', group_priors)
                    return final_value_mask, final_nonna_mask, final_prior
                    
                else:
                    final_value_mask = False if sub_dependencies['TYPE'] == 'any_of' else True
                    final_nonna_mask = True 
                    for condition, mask in value_masks.items():
                        final_nonna_mask = final_nonna_mask & nonna_masks[condition]
                        if sub_dependencies['TYPE'] == 'all_of':
                            final_value_mask = final_value_mask & mask
                        if sub_dependencies['TYPE'] == 'any_of':
                            final_value_mask = final_value_mask | mask
            else:
                final_value_mask, final_nonna_mask = None, None

            if priors:
                final_prior = calculate_final_prior(priors.values(), sub_dependencies['TYPE'])
                #print('COLLAPSED PRIOR', final_prior)    # DEBUG
            else:
                final_prior = None

            #if final_value_mask is not None:   # DEBUG
               #print('COLLAPSED MASK PERCENTAGE', nhanes_data[final_value_mask & final_nonna_mask].shape[0], nhanes_data[final_nonna_mask].shape[0], nhanes_data[final_value_mask & final_nonna_mask].shape[0] / nhanes_data[final_nonna_mask].shape[0])    # DEBUG
                
            return final_value_mask, final_nonna_mask, final_prior


def _calculate_weighted_rr(
    unique_conditions: list,
    independent_groups: list,
    condition_rrs: dict,
    value_masks: dict,
    nonna_masks: dict,
    priors: dict,
    node_type: str,
    nhanes_data: pd.DataFrame,
) -> Tuple[float, Optional[pd.Series], Optional[pd.Series], Optional[float]]:

    #print_debug = False
    #if ((('polypharmacy', ('polypharmacy_yes',)) in condition_rrs) & (('lung_disease', ('lung_disease_yes',)) in condition_rrs)) or ('non_alcoholic_fatty_liver_disease', ('non_alcoholic_fatty_liver_disease_yes',)) in condition_rrs:
    #    #print(condition_rrs)
    #    print_debug = False
    # Defensive init: single-condition any_of/all_of groups with no NHANES-backed
    # leaves skip the inner isinstance(value_mask, pd.Series) branch and leave these
    # undefined, crashing at the return (UnboundLocalError).
    value_mask_to_keep = None
    nonna_mask_to_keep = None
    prior_to_keep = None
    numerator = 1.0
    denominator = 1
    final_rr = 1.0
    sub_rr = 0
    total_percentage = 0
    for comb in itertools.product([0, 1], repeat=len(unique_conditions)):
        if sum(comb) == len(unique_conditions) and node_type == 'all_of':
            group_percentages = []
            numerator = 1.0 
            for group in independent_groups:
                prior = 1.0
                value_mask, nonna_mask = True, True
                for condition in group:
                    numerator *= condition_rrs[condition]
                    
                    if condition in value_masks:
                        value_mask = value_mask & value_masks[condition]
                        nonna_mask = nonna_mask & nonna_masks[condition]
                    if condition in priors:
                        prior *= priors[condition]

                if isinstance(value_mask, pd.Series):
                    n_nonna = nonna_mask.sum()
                    percentage = (value_mask & nonna_mask).sum() / n_nonna if n_nonna > 0 else 0
                    percentage *= prior
                else:
                    percentage = prior
                group_percentages.append(percentage)

            # Keep positive mask and prior for 'all_of' to use it further for nested variables
            if len(independent_groups) == 1:
                if isinstance(value_mask, pd.Series):
                    value_mask_to_keep = value_mask
                    nonna_mask_to_keep = nonna_mask
                
                if prior != 1.0:
                    prior_to_keep = prior
                else:
                    prior_to_keep = None
            else:
                value_mask_to_keep = None
                nonna_mask_to_keep = None
                prior_to_keep = reduce(lambda a, b: a*b, group_percentages)
            
        elif sum(comb) == 0 and node_type == 'any_of':
            # We calculate mask anb priors to save it for further calculations
            group_percentages = []
            for group in independent_groups:
                prior = 1.0
                value_mask, nonna_mask = True, True
                for condition in group:
                    if condition in value_masks:
                        value_mask = value_mask & (~value_masks[condition])
                        nonna_mask = nonna_mask & nonna_masks[condition]
                    if condition in priors:
                        prior *= (1-priors[condition])

                if isinstance(value_mask, pd.Series):
                    n_nonna = nonna_mask.sum()
                    percentage = (value_mask & nonna_mask).sum() / n_nonna if n_nonna > 0 else 0
                    percentage *= prior
                else:
                    percentage = prior
                group_percentages.append(percentage)

            denominator = 1

            # Invert negative mask and prior to keep POSITIVE ones for 'any_of' to use it further for nested variables
            if len(independent_groups) == 1:
                if isinstance(value_mask, pd.Series):
                    value_mask_to_keep = ~value_mask
                    nonna_mask_to_keep = nonna_mask
                
                if prior != 1.0:
                    prior_to_keep = 1 - prior
                else:
                    prior_to_keep = None
            else:
                value_mask_to_keep = None
                nonna_mask_to_keep = None
                prior_to_keep = 1 - reduce(lambda a, b: a*b, group_percentages)
            
        else:
            curr_value_masks = {}
            curr_priors = {}
            curr_rrs = {}
            #print('COMB', comb)
            for flag, condition in zip(comb, unique_conditions):
                if condition in value_masks:
                    if flag:
                        curr_value_masks[condition] = value_masks[condition]
                    else:
                        curr_value_masks[condition] = ~value_masks[condition]
                        
                if condition in priors:
                    if flag:
                        curr_priors[condition] = priors[condition]
                    else:
                        curr_priors[condition] = 1 - priors[condition]

                if flag:
                    curr_rrs[condition] = condition_rrs[condition]

            percentages = []
            rrs = []
            for group in independent_groups:
                percentage, prior, rr = 1.0, 1.0, 1.0
                value_mask, nonna_mask = True, True

                for condition in group:
                    if condition in curr_value_masks:
                        value_mask = value_mask & curr_value_masks[condition]
                        nonna_mask = nonna_mask & nonna_masks[condition]
                    if condition in curr_priors:
                        prior *= curr_priors[condition]
                    if condition in curr_rrs:
                        rr *= curr_rrs[condition]

                if isinstance(value_mask, pd.Series):
                    n_nonna = nonna_mask.sum()
                    percentage *= (value_mask & nonna_mask).sum() / n_nonna if n_nonna > 0 else 0
                percentage *= prior
                percentages.append(percentage)
                rrs.append(rr)

            #if print_debug:
            #    print(comb)
            #    print('percentages')
            #    print(percentages)
            #    print('rrs')
            #    print(rrs)

            #print('PERCENTAGES', percentages)
            comb_percentage = reduce(lambda a, b: a*b, percentages)
            comb_rr = reduce(lambda a, b: a*b, rrs)
            #print('COMB_PERCENTAGE', comb_percentage)
            sub_rr += comb_percentage * comb_rr
            #print('COMB_PERCENTAGE*RR', comb_percentage * comb_rr)
            total_percentage += comb_percentage
            
            #if print_debug:
            #    print('COMB_PERCENTAGE', comb_percentage)
            #    print('COMB RR', comb_rr)
            #    print('SUB_RR PART', comb_percentage * comb_rr)
            #    print('TOTAL_PERCENTAGE', total_percentage)
            #    print('SUB_RR', sub_rr)

    #if print_debug:
    #    print('TOTAL PERCENTAGE', total_percentage)

    if total_percentage > 1.0:    # DEBUG
        print('TOTAL PERCENTAGE', total_percentage)

    sub_rr = sub_rr / total_percentage    # Normalize percentages so they sum up to 1.0

    if node_type == 'all_of':
        final_rr = numerator / sub_rr
    elif node_type == 'any_of':
        final_rr = sub_rr / denominator

    return final_rr, value_mask_to_keep, nonna_mask_to_keep, prior_to_keep


def monte_carlo_rr_ci_distal(
        condition_rrs_dists: dict,
        unique_conditions: list,
        independent_groups: list,
        value_masks: dict,
        nonna_masks: dict,
        priors: dict,
        node_type: str,
        nhanes_data: pd.DataFrame,
        reverse_rr: bool,
        n_simulations: int = DISTAL_MC_SIMULATIONS,
):
    combined_rrs = []

    #print_debug = False
    #if ((('polypharmacy', ('polypharmacy_yes',)) in condition_rrs_dists) & (('lung_disease', ('lung_disease_yes',)) in condition_rrs_dists)):
    #    print_debug = True
    # Monte Carlo simulation
    for i in tqdm(range(n_simulations), ncols=100):
        # Sample from each input RR distribution
        condition_rrs = {}
        for variable, params in condition_rrs_dists.items():
            # Sample from log-normal distribution
            log_rr_sample = np.random.normal(params['mean'], params['se'])
            condition_rrs[variable] = np.exp(log_rr_sample)

        # Use your calculate_weighted_rr function with sampled RRs
        combined_rr, value_mask, nonna_mask, prior = _calculate_weighted_rr(
            unique_conditions,
            independent_groups,
            condition_rrs,
            value_masks,
            nonna_masks,
            priors,
            node_type,
            nhanes_data,
        )
        if reverse_rr:
            combined_rrs.append(1/combined_rr)
        else:
            combined_rrs.append(combined_rr)

        #if print_debug:
        #    print('ITER', i)
        #    print('SAMPLED RELATIVE RISKS')
        #    for v, d in condition_rrs.items():
        #        print(v)
        #        print(d)
        #    print('ITERATION RR')
        #    if reverse_rr:
        #        print(1/combined_rr)
        #    else:
        #        print(combined_rr)
        #    print('-'*50)

    # Calculate statistics from the simulation results
    combined_rrs = np.array(combined_rrs)
    mean_rr = np.mean(combined_rrs)
    
    lower = np.percentile(combined_rrs, 5.0)
    upper = np.percentile(combined_rrs, 95.0)
    #plus_minus = min(mean_rr-lower, upper-mean_rr)

    return lower, upper, value_mask, nonna_mask, prior


def calculate_rr_for_distal_var(
        sub_dependencies: dict, 
        nhanes_data: pd.DataFrame,
        reverse_rr: bool,
        output_var,    # DEBUG 
        input_var,    # DEBUG
) -> dict:

    value_masks, nonna_masks, priors = create_masks_priors(
        nhanes_data=nhanes_data,
        sub_dependencies=sub_dependencies,
    )

    unique_conditions = list(set(list(value_masks.keys())+list(priors.keys())))
    if len(value_masks) > 1:
        groups = create_independent_groups(nonna_masks, nhanes_data, set(unique_conditions))
    else:
        groups = [(cond,) for cond in unique_conditions]

    #if len(groups) > 1:    # DEBUG
    #    print('NUMBER OF GROUPS', len(groups))    # DEBUG
    #    print('NO SEQN FOR THIS COMBINATION')
    #    print(output_var, input_var, [key[0] for key in value_masks])
    #    print('AMOUNT OF NONNA ROWS')
    #    if value_masks:
    #        for g in groups:
    #            m = True
    #            for e in g:
    #                if e in nonna_masks:
    #                    m = m & nonna_masks[e]
    #            if isinstance(m, pd.Series):
    #                print(g, nhanes_data[m].shape[0])    # DEBUG
    #            else:
    #                print(g, 'PRIORS')    # DEBUG
    #    print('-'*50)    # DEBUG

    condition_rrs = {}
    condition_rr_dists = {}
    for condition in unique_conditions:
        for entry in sub_dependencies['INVARS'][condition[0]]:
            if list(condition[1]) == entry['VALUE']:
                rr = entry['STATS']['relative_risk']
                condition_rrs[condition] = rr
                ci_lower = rr - entry['STATS']['plus_minus']
                ci_upper = rr + entry['STATS']['plus_minus']
                
                log_mean = np.log(rr)
                log_se = (np.log(ci_upper) - np.log(ci_lower)) / (2 * 1.96)
                condition_rr_dists[condition] = {'mean': log_mean, 'se': log_se, 'rr': rr, 'lower': ci_lower, 'upper': ci_upper}

    #if ((('polypharmacy', ('polypharmacy_yes',)) in unique_conditions) & (('lung_disease', ('lung_disease_yes',)) in unique_conditions)) or ('non_alcoholic_fatty_liver_disease', ('non_alcoholic_fatty_liver_disease_yes',)) in unique_conditions:
    #    print('OUTPUT', output_var)
    #    print('INPUT', input_var)
    #    for invar, d in sub_dependencies['INVARS'].items():
    #        print(invar)
    #        print(d)
#
    #    for i, d in condition_rr_dists.items():
    #        print(i)
    #        print(d)
        #import sys; sys.exit()
    #print('CALCULATING DISTAL STATS')
    #print('N UNIQUE_CONDITIONS', len(unique_conditions))
    #print('CONDITION_RR_DISTS')
    #print(condition_rr_dists)
    from .distal_fast import fast_monte_carlo_rr_ci_distal
    lower, upper, value_mask_to_keep, nonna_mask_to_keep, prior_to_keep = fast_monte_carlo_rr_ci_distal(
        condition_rr_dists,
        unique_conditions,
        groups,
        value_masks,
        nonna_masks,
        priors,
        sub_dependencies['TYPE'],
        nhanes_data,
        reverse_rr,
        n_simulations=DISTAL_MC_SIMULATIONS,
    )

    rr, value_mask_to_keep, nonna_mask_to_keep, prior_to_keep = _calculate_weighted_rr(
        unique_conditions,
        groups,
        condition_rrs,
        value_masks,
        nonna_masks,
        priors,
        sub_dependencies['TYPE'],
        nhanes_data,
    )

    #if 'mean_platelet_volume_fL_digestion' in sub_dependencies['INVARS'] or 'frailty_chronic_illness' in sub_dependencies['INVARS']:
    #    print(condition_rr_dists)
    #    print(rr, rr_plus_minus)
    #    print('-'*50)

    stats = {}
    if reverse_rr:
        #print('REVERSING RR FOR OUTPUT', output_var, 'AND INPUT', input_var)
        #stats['RELATIVE_RISK_REVERSED'] = True    # REMOVE ALL UNNECESSARY FIELDS AFTER DEBUG ?
        rr = 1 / rr
        #rr_plus_minus = 1 / rr_plus_minus

        if value_mask_to_keep is not None:
            value_mask_to_keep = ~value_mask_to_keep
        if prior_to_keep is not None:
            prior_to_keep = 1 - prior_to_keep

    #print('RR', rr)
    #print('LOWER', lower, '|', 'UPPER', upper)
    rr_plus_minus = min(rr-lower, upper-rr)    # already reversed
    stats['relative_risk'] = rr
    stats['plus_minus'] = rr_plus_minus
    stats['ci'] = CI

    #print(stats)    # DEBUG
    #print('-'*100)    # DEBUG
    #import sys; sys.exit()
    return stats, value_mask_to_keep, nonna_mask_to_keep, prior_to_keep

# === FAST MC (precomputed NHANES percentages) ===

def _precompute_percentages(
    unique_conditions, independent_groups, value_masks, nonna_masks, priors,
    node_type, nhanes_data,
):
    """Precompute all NHANES-derived percentages once.

    Returns a dict keyed by combination tuple (0/1 flags for each condition),
    containing the percentage and group structure needed for RR computation.
    """
    precomputed = {}

    for comb in itertools.product([0, 1], repeat=len(unique_conditions)):
        comb_data = {'group_percentages': [], 'group_priors': []}

        for group in independent_groups:
            prior = 1.0
            value_mask, nonna_mask = True, True

            for condition in group:
                cond_idx = unique_conditions.index(condition)
                flag = comb[cond_idx]

                if condition in value_masks:
                    if flag:
                        value_mask = value_mask & value_masks[condition]
                    else:
                        value_mask = value_mask & (~value_masks[condition])
                    nonna_mask = nonna_mask & nonna_masks[condition]

                if condition in priors:
                    if flag:
                        prior *= priors[condition]
                    else:
                        prior *= (1 - priors[condition])

            if isinstance(value_mask, pd.Series):
                n_nonna = nonna_mask.sum()
                if n_nonna > 0:
                    percentage = (value_mask & nonna_mask).sum() / n_nonna
                else:
                    percentage = 0
                percentage *= prior
            else:
                percentage = prior

            comb_data['group_percentages'].append(percentage)
            comb_data['group_priors'].append(prior)

        precomputed[comb] = comb_data

    # Also precompute the special cases for all_of and any_of
    # (the numerator/denominator and mask-keeping logic)
    if node_type == 'all_of':
        all_ones = tuple([1] * len(unique_conditions))
        precomputed['_all_ones'] = all_ones
    elif node_type == 'any_of':
        all_zeros = tuple([0] * len(unique_conditions))
        precomputed['_all_zeros'] = all_zeros

    return precomputed


def _fast_weighted_rr(
    unique_conditions, independent_groups, condition_rrs,
    precomputed, node_type,
):
    """Fast version of _calculate_weighted_rr using precomputed percentages."""

    sub_rr = 0
    total_percentage = 0

    for comb in itertools.product([0, 1], repeat=len(unique_conditions)):
        comb_data = precomputed[comb]
        group_percentages = comb_data['group_percentages']

        if sum(comb) == len(unique_conditions) and node_type == 'all_of':
            numerator = 1.0
            for group in independent_groups:
                for condition in group:
                    numerator *= condition_rrs[condition]

        elif sum(comb) == 0 and node_type == 'any_of':
            denominator = 1

        else:
            curr_rrs = {}
            for flag, condition in zip(comb, unique_conditions):
                if flag:
                    curr_rrs[condition] = condition_rrs[condition]

            rrs = []
            for gi, group in enumerate(independent_groups):
                rr = 1.0
                for condition in group:
                    if condition in curr_rrs:
                        rr *= curr_rrs[condition]
                rrs.append(rr)

            comb_percentage = reduce(lambda a, b: a * b, group_percentages)
            comb_rr = reduce(lambda a, b: a * b, rrs)
            sub_rr += comb_percentage * comb_rr
            total_percentage += comb_percentage

    if total_percentage == 0:
        return 1.0

    sub_rr = sub_rr / total_percentage

    if node_type == 'all_of':
        final_rr = numerator / sub_rr if sub_rr != 0 else 1.0
    elif node_type == 'any_of':
        final_rr = sub_rr / denominator if denominator != 0 else 1.0
    else:
        final_rr = sub_rr

    return final_rr


def fast_monte_carlo_rr_ci_distal(
    condition_rrs_dists, unique_conditions, independent_groups,
    value_masks, nonna_masks, priors, node_type, nhanes_data,
    reverse_rr, n_simulations=50000,
):
    """Drop-in replacement for monte_carlo_rr_ci_distal.

    Precomputes NHANES percentages once, then MC loop only samples RRs.
    """
    # Precompute all NHANES-derived percentages
    precomputed = _precompute_percentages(
        unique_conditions, independent_groups, value_masks, nonna_masks,
        priors, node_type, nhanes_data,
    )

    combined_rrs = []

    for i in tqdm(range(n_simulations), ncols=100):
        # Sample from each input RR distribution
        condition_rrs = {}
        for variable, params in condition_rrs_dists.items():
            log_rr_sample = np.random.normal(params['mean'], params['se'])
            condition_rrs[variable] = np.exp(log_rr_sample)

        # Fast RR computation (no NHANES access)
        combined_rr = _fast_weighted_rr(
            unique_conditions, independent_groups, condition_rrs,
            precomputed, node_type,
        )

        if reverse_rr:
            combined_rrs.append(1 / combined_rr)
        else:
            combined_rrs.append(combined_rr)

    combined_rrs = np.array(combined_rrs)

    lower = np.percentile(combined_rrs, 5.0)
    upper = np.percentile(combined_rrs, 95.0)

    # Compute masks from one call to original _calculate_weighted_rr
    # (needed for nested distal variables)
    
    condition_rrs_point = {v: d['rr'] for v, d in condition_rrs_dists.items()}
    _, value_mask_to_keep, nonna_mask_to_keep, prior_to_keep = _calculate_weighted_rr(
        unique_conditions, independent_groups, condition_rrs_point,
        value_masks, nonna_masks, priors, node_type, nhanes_data,
    )

    return lower, upper, value_mask_to_keep, nonna_mask_to_keep, prior_to_keep
