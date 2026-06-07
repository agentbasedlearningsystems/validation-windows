"""
Optimized distal MC simulation.

The original monte_carlo_rr_ci_distal calls _calculate_weighted_rr 500 times,
and each call filters NHANES DataFrames (117K rows) multiple times.
But the NHANES masks don't change between iterations - only the sampled RRs do.

This version precomputes all NHANES percentages once, then the MC loop
only does fast arithmetic with the sampled RRs.

Drop-in replacement: call fast_monte_carlo_rr_ci_distal with same args
as monte_carlo_rr_ci_distal.
"""

import itertools
from functools import reduce
from typing import Tuple, Optional, List

import numpy as np
import pandas as pd
from tqdm import tqdm


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
    from .distal import _calculate_weighted_rr
    condition_rrs_point = {v: d['rr'] for v, d in condition_rrs_dists.items()}
    _, value_mask_to_keep, nonna_mask_to_keep, prior_to_keep = _calculate_weighted_rr(
        unique_conditions, independent_groups, condition_rrs_point,
        value_masks, nonna_masks, priors, node_type, nhanes_data,
    )

    return lower, upper, value_mask_to_keep, nonna_mask_to_keep, prior_to_keep
