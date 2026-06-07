# %%
import json
import os
import time
import warnings
from typing import Optional, List, Tuple, Union

import pandas as pd
import numpy as np
import glob
from tqdm import tqdm

# %% [markdown]
# ## Spreadsheet validation functions

# %%
# SPREADSHEET VALIDITY CHECKS
def check_missing_inputs(data: pd.DataFrame) -> pd.DataFrame:
    not_include = {'naive_0_nhanes_quartile', 'naive_0_nhanes_explicit', 'anomaly', 'naive_0_nhanes_explicit_average', 'naive_0_nhanes_quartile_average'}
    
    missing_input_values = data[
        (~data['output'].isna()) \
        & (~data['input'].isna()) \
        & (data['input values'].isna()) \
        & (~data['Type'].isin(not_include)) \
        & (~data['output'].str.endswith('anomaly'))
    ]
    if not missing_input_values.empty:
        for n, row in missing_input_values.iterrows():
            print(f"Row {n+2} | Missing input values for input variable '{row['input']}' and output variable '{row['output']}'.")
    return missing_input_values


def check_types(data: pd.DataFrame):
    with_missing_types = data[
        (~data['output'].isna()) \
        &(data['input'].isna()) \
        &(data['Type'].isna()) \
    ]
    if not with_missing_types.empty:
        print(f"Found rows with empty types: {[e+2 for e in missing.index]}.")

    nonna_types = data[~data['Type'].isna()]
    possible_types = prior_types | {'anomaly', 'distal'} | set(data['output'].unique()) | stats_recalculation_types | dependency_types
    with_unexpected_types = nonna_types[~nonna_types['Type'].isin(possible_types)]
    if not with_unexpected_types.empty:
        print(f"Found rows with unexpected types: {[e+2 for e in with_unexpected_types.index]}")


class PriorsError(Exception):
    pass


def check_missing_priors(data: pd.DataFrame):
    priors_rows = data[data['Type'].isin({'dependency_priors', 'discrete_priors'})]
    for _, row in priors_rows.iterrows():
        try:
            priors, value_ranges = create_outvars(row, None)
        except PriorsError as e:
            print(str(e))


def check_input_values(data: pd.DataFrame) -> bool:
    # Check valid input values for every dependency row
    dependecy_output_var_rows = data[data['Type'].isin(dependency_types)]
    any_found = False
    for _, row in dependecy_output_var_rows.iterrows(): 
        as_output = data[data['output'] == row['output']]
        for _, inp_row in as_output.iterrows():
            if not pd.isna(inp_row['input']):
                input_var_definition_row = data[(data['output']==inp_row['input']) & (data['Type'].isin(dependency_types | prior_types))].squeeze()
                if input_var_definition_row['Type'] not in quartile_priors_types | {'dependency_nhanes_quartile'}:
                    found = False
                    input_values = [v.strip() for v in inp_row['input values'].split(',')]
                    for v in input_values:
                        found = False
                        for i in range(1, 6):
                            if v == input_var_definition_row[f'value{i}']:
                                found = True
                                any_found = True
                                break
                        if not found:
                            print(f"Row {_+2} | Input value '{v}' not found for input variable '{inp_row['input']}'.")
    return any_found


def get_precision(num):
    str_num = str(num).rstrip('0')
    if '.' in str_num:
        return len(str_num.split('.')[-1])
    else:
        return 0
        

def check_value_ranges_coverage(data: pd.DataFrame, raw_nhanes: pd.DataFrame):
    nhanes_rows = data[~data['code'].isna()]
    missing_values = []
    for _, row in nhanes_rows.iterrows():
        code = row['code']
        value_ranges = extract_value_ranges(row)
        if value_ranges and code in raw_nhanes.columns:
            # Gather all intervals
            nonna_mask = ~(raw_nhanes[code].isna())
            nonna_rows = pivoted[nonna_mask]
            
            min_value = nonna_rows[code].max()
            max_value = nonna_rows[code].min()
            
            values_mask = False
            single_values = []
            for r in value_ranges.values():
                for e in r:
                    if isinstance(e, tuple):
                        values_mask = values_mask | ((raw_nhanes[code] >= e[0]) & (raw_nhanes[code] <= e[1]))
                        if min_value > e[0]:
                            min_value = e[0]
                        if max_value < e[1]:
                            max_value = e[1]
                    elif isinstance(e, (float, int)):
                        single_values.append(e)
                        
            for value in single_values:
                if value <= max_value and value >= min_value:
                    values_mask = values_mask | (raw_nhanes[code] == value)
                    
            covered = pivoted[nonna_mask & values_mask]
            full = pivoted[nonna_mask & ((raw_nhanes[code] >= min_value) & (raw_nhanes[code] <= max_value))]

            difference = pd.concat([covered, full]).drop_duplicates(keep=False)

            if not difference.empty:
                precisions = [get_precision(v) for v in raw_nhanes[code].dropna().values]
                max_precision = max(precisions)
                message = f'Row {_+2} | Code {code}. Not covered values: {difference[code].unique()}, max precision {max_precision}.'
                print(message)
                missing_values.append((_+2, code, difference[code].unique(), max_precision))
                
    return missing_values


def check_single_values_ss_types(data: pd.DataFrame):

    def _amount_of_inputs(inputs: str):
        return len(inputs.split(','))
    
    ss_rows = data[data['Type'].isin(stats_recalculation_types)].copy()
    ss_rows['n_inputs'] = ss_rows['input values'].apply(lambda e: len(e.split(',')))
    more_than_one = ss_rows[ss_rows['n_inputs'] > 1]
    if not more_than_one.empty:
        row_numbers = [_+2 for _ in more_than_one.index]
        print(f"Multiple input values for single input variable with is_a, subsumes or equivalent_to type found for these rows: {row_numbers}.")

    return more_than_one.empty


class PresentLoopException(Exception):
    pass


def check_paths(row: pd.Series, data: pd.DataFrame, path: list) -> list:
    to_add = (row.name+2, row['output'])
    path.append(to_add)
    for node in path[:-1]:
        if to_add == node:
            msg = f"Dependency loop is found in the spreadsheet: {' <- '.join([f'Row num {e[0]}, {e[1]}' for e in path])}"
            raise PresentLoopException(msg)
    
    if row['Type'] in prior_types:
        return

    as_output = data[data['output'] == row['output']]
    for idx, inp_row in as_output.iterrows():
        if not pd.isna(inp_row['input']):
            sub_path = path.copy()
            input_var_definition_row = data[(data['output']==inp_row['input']) & (data['Type'].isin(dependency_types | prior_types))].squeeze()
            check_paths(input_var_definition_row, data, sub_path)
            

def check_loops(data: pd.DataFrame):
    dependecy_output_var_rows = data[data['Type'].isin(dependency_types)]

    for idx, row in dependecy_output_var_rows.iterrows():
        path = []
        as_input_rows = data[data['input'] == row['output']]
        if as_input_rows.shape[0] > 0:    # Parse recursively output nodes that are not input to any
            continue

        try:
            check_paths(
                row=row,
                data=data,
                path=path,
            )
        except PresentLoopException as e:
            print(f'Spreadsheet parse aborted. {str(e)}')
            return

    return None


def check_missing_codes(data: pd.DataFrame):
    missing_codes = data[(data['Type'].str.contains('nhanes')) & (data['code'].isna())]
    if not missing_codes.empty:
        for idx, row in missing_codes.iterrows():
            print('Row', row.name+2, '|', f"Missing NHANES code for variable '{row['output']}'")


def check_value_columns(data: pd.DataFrame):
    having_values = data[~data['value1'].isna()]
    for idx, row in having_values.iterrows():
        parsed_values = set()
        for i in range(1, 6):
            value = row[f'value{i}']
            if not pd.isna(value):
                if value in parsed_values:
                    print('Row', row.name+2, '|', f"Found duplicated value{i}: '{value}'.")
                else:
                    parsed_values.add(value)
            else:
                continue

# %% [markdown]
# ## NHANES priors calculation functions

# %%
def sort_key(elem):
    if isinstance(elem, (int, float)):
        return elem
    elif isinstance(elem, tuple):
        return elem[1]


def collapse_ranges(values: list):
    collapsed = []

    while values:
        value = values.pop(0)
        if isinstance(value, (int, float)):    # Remove values that are in ranges
            drop = False
            for subvalue in values:
                if isinstance(subvalue, tuple):
                    if value >= subvalue[0] and value <= subvalue[1]:
                        drop = True
                elif value == subvalue:
                    drop = True
                    break    # values are ordered by sort_key func
                    
            if not drop:
                collapsed.append(value)

        else:
            to_add = value
            for subvalue in values:    # Remove overlapping ranges
                if isinstance(subvalue, tuple):
                    if value[1] >= subvalue[0]:
                        if value[0] <= subvalue[0]:
                            to_add = (value[0], subvalue[1])
                            values.pop(0)
                        else:
                            to_add = None
                        break    # values are ordered by sort_key func
                        
            if to_add:
                collapsed.append(to_add)
    return collapsed


def filter_value(elem, values: list):
    for value in values:
        if isinstance(value, (float, int)):
            if elem == value:
                return True
        else:
            if elem <= value[1] and elem >= value[0]:
                return True
    return False
        

def extract_values(values_str: str) -> list:
    values_str = values_str.replace(' ', '')
    values = []
    for elem in values_str.split(','):
        if '-' in elem:
            values.append(tuple(float(e) for e in elem.split('-')))
        elif elem == 'missing':
            values.append(-1)
        else:
            values.append(float(elem))
    return values


def extract_value_ranges(rule_row: pd.Series) -> dict:
    value_ranges = {}
    for i in range(1, 6):
        if pd.isna(rule_row[f'value{i}']):
            break
        else:
            if isinstance(rule_row[f'index{i}'], (float, int)):
                if pd.isna(rule_row[f'index{i}']):    # DEBUG
                    value_ranges[rule_row[f'value{i}'].strip()] = 'null'
                else:
                    value_ranges[rule_row[f'value{i}'].strip()] = [rule_row[f'index{i}']]
            else:
                value_ranges[rule_row[f'value{i}'].strip()] = extract_values(rule_row[f'index{i}'])
    return value_ranges
    

def count_total(values: list, counts: pd.Series) -> int:
    count = 0
    for elem in values:
        if isinstance(elem, (float, int)):
            count += sum(counts[counts['value'] == elem]['count'])
        elif isinstance(elem, tuple):
            count += sum(counts[(counts['value'] >= elem[0]) & (counts['value'] <= elem[1])]['count'])
    return count


def calculate_priors_explicit(rule_row: pd.Series, nhanes_counts: pd.DataFrame):
    # 'discrete_nhanes_explicit', 'naive_0_nhanes_explicit', 'dependency_nhanes_explicit'
    value_ranges = extract_value_ranges(rule_row)
    nhanes_code = rule_row['code'].strip()
    counts = nhanes_counts.loc[nhanes_code].reset_index(name='count')
    value_counts = {k: count_total(v, counts) for k, v in value_ranges.items()}
    total = sum(value_counts.values())
    frequencies = {k: v/total for k, v in value_counts.items()}
    return frequencies, value_ranges


def calculate_priors_quartile(rule_row: pd.Series, nhanes_counts: pd.DataFrame) -> dict:
    # 'discrete_nhanes_quartile', 'naive_0_nhanes_quartile',  'dependency_nhanes_quartile'
    
    priors = {}
    nhanes_code = rule_row['code'].strip()
    counts = nhanes_counts.loc[nhanes_code].reset_index(name='count')
    if not pd.isna(rule_row['index1']):
        valid_ranges = sorted(extract_values(rule_row['index1']), key=sort_key)
        valid_ranges = collapse_ranges(valid_ranges)
    else:
        valid_ranges = [(0, counts['value'].max())]

    if isinstance(valid_ranges[-1], tuple):
        max_value = valid_ranges[-1][1]
    else:
        max_value = valid_ranges[-1]
    
    valid_counts = counts[counts['value'].apply(lambda e: filter_value(e, valid_ranges))].reset_index()    # reset_index to prevent copyvsview warning
    total = valid_counts['count'].sum()
    valid_counts['fraction'] = valid_counts['count'].cumsum() / total
    
    quantiles = []
    curr_quantile = 0.25
    for val, frac in valid_counts[['value', 'fraction']].values:
       if frac >= curr_quantile:
           quantiles.append(val)
           curr_quantile += 0.25

    value_ranges = {}
    for i, q in enumerate(quantiles, 1):
        if i == 1:
            strval = "{:.2f}".format(q)
            name = '_'.join([rule_row['output'], f'quartile_{i}', strval, "and_below"])
            priors[name] = 0.25
            prev_strval = strval
            value_ranges[name] = [(0, q)]

        elif i == len(quantiles):
            name = '_'.join([rule_row['output'], f'quartile_{i}', 'above', prev_strval])
            priors[name] = 0.25
            value_ranges[name] = [(quantiles[i-2], max_value)]

        else:
            strval = "{:.2f}".format(q)
            name = '_'.join([rule_row['output'], f'quartile_{i}', 'above', prev_strval, 'to', strval, "and_below"])
            prev_strval = strval
            priors[name] = 0.25
            value_ranges[name] = [(quantiles[i-2], q)]

    return priors, value_ranges


def calculate_priors_nhanes(rule_row: pd.Series, nhanes_counts: pd.DataFrame) -> dict:
    try:
        if rule_row['Type'] in explicit_types:
            return calculate_priors_explicit(rule_row, nhanes_counts)
        elif rule_row['Type'] in quartile_priors_types | {'dependency_nhanes_quartile'}:
            return calculate_priors_quartile(rule_row, nhanes_counts)
    except KeyError:
        return 'MISSING IN NHANES', 'MISSING IN NHANES'
    except ZeroDivisionError:
        return 'MISSING VALUES', 'MISSING IN NHANES'

# %% [markdown]
# ## Distal calculations

# %%
# DISTAL VARIABLES STATS
from functools import reduce
from typing import (
    Iterable,
    Tuple
)
import itertools

NHANES_THRESHOLD = 1000


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
            counts.append((comb, nhanes_data[nonna_mask].shape[0]))
        counts.sort(key=lambda e: e[1])
        
        if counts[-1][1] > threshold:
            largest_group = counts[-1][0]
            groups.append(largest_group)
            complement = {key: masks[key] for key in keys if key not in largest_group}
            for condition in largest_group:
                unique_conditions.remove(condition)
            #if complement:
            groups += create_independent_groups(complement, nhanes_data, unique_conditions, threshold)
            return groups

    if not groups:
        return [(elem,) for elem in unique_conditions]


def calculate_final_prior(priors: Iterable[float], dependency_type: str) -> float:
    # function to calculate positive prior in independent case
    
    if dependency_type == 'all_of':
        final_prior = reduce(lambda a, b: a*b, priors)
    
    if dependency_type == 'any_of':    # Inclusion-exclusion principle for independent variables
        neg_prior = 1.0
        for p in priors:
            neg_prior *= (1-p)
        final_prior = 1 - neg_prior
        
    return final_prior


def create_masks_priors(nhanes_data: pd.DataFrame, sub_dependencies: dict, var_values: List[str] = None, is_first: bool = True):
    if 'CODE' in sub_dependencies:
        code = sub_dependencies['CODE']
        #print('CREATING MASKS FOR', code)    # DEBUG
        nonna_mask = ~(nhanes_data[code].isna())

        # Filter out values that are not in index* columns
        valid_values_mask = False
        for value, ranges in sub_dependencies['value_ranges'].items():
            for r in ranges:
                if isinstance(r, tuple):
                    valid_values_mask = valid_values_mask | ((nhanes_data[code] >= r[0]) & (nhanes_data[code] <= r[1]))
                elif isinstance(r, (float, int)):
                    valid_values_mask = valid_values_mask | (nhanes_data[code] == r)
        nonna_mask = nonna_mask & valid_values_mask
        
        # Get mask for dependency input values
        value_mask = False
        for str_value in var_values:
            value_ranges = sub_dependencies['value_ranges'][str_value]
            for r in value_ranges:
                #print(str_value, r)    # DEBUG
                if isinstance(r, tuple):
                    value_mask = value_mask | ((nhanes_data[code] >= r[0]) & (nhanes_data[code] <= r[1]))
                elif isinstance(r, (float, int)):
                    value_mask = value_mask | (nhanes_data[code] == r)

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
                #print('UNIQUE CONDITIONS', unique_conditions)    # DEBUG
                groups = create_independent_groups(nonna_masks, nhanes_data, unique_conditions)
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
                            percentage = nhanes_data[value_mask & nonna_mask].shape[0] / nhanes_data[nonna_mask].shape[0]
                            g_priors.append(percentage)
                            #print(percentage)    # DEBUG
                        group_priors.append(calculate_final_prior(g_priors, sub_dependencies['TYPE']))

                    final_value_mask, final_nonna_mask = None, None
                    final_prior = calculate_final_prior(group_priors, sub_dependencies['TYPE'])
                    #print('COLLAPSED PRIOR FOR INDEPENDENT GROUPS', final_prior)    # DEBUG
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
    
    sub_rr = 0
    total_percentage = 0
    for comb in itertools.product([0, 1], repeat=len(unique_conditions)):
        if sum(comb) == len(unique_conditions) and node_type == 'all_of':
            group_percentages = []
            for group in independent_groups:
                prior, numerator = 1.0, 1.0
                value_mask, nonna_mask = True, True
                for condition in group:
                    numerator *= condition_rrs[condition]
                    
                    if condition in value_masks:
                        value_mask = value_mask & value_masks[condition]
                        nonna_mask = nonna_mask & nonna_masks[condition]
                    if condition in priors:
                        prior *= priors[condition]

                if isinstance(value_mask, pd.Series):
                    percentage = nhanes_data[value_mask & nonna_mask].shape[0] / nhanes_data[nonna_mask].shape[0]
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
                    percentage = nhanes_data[value_mask & nonna_mask].shape[0] / nhanes_data[nonna_mask].shape[0]
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
                    percentage *= nhanes_data[value_mask & nonna_mask].shape[0] / nhanes_data[nonna_mask].shape[0]
                percentage *= prior
                percentages.append(percentage)

            comb_percentage = reduce(lambda a, b: a*b, percentages)
            sub_rr += comb_percentage * rr

            total_percentage += comb_percentage

    if total_percentage > 1.0:    # DEBUG
        print('TOTAL PERCENTAGE', total_percentage)

    sub_rr = sub_rr / total_percentage    # Normalize percentages so they sum up to 1.0

    if node_type == 'all_of':
        final_rr = numerator / sub_rr
    elif node_type == 'any_of':
        final_rr = sub_rr / denominator

    return final_rr, value_mask_to_keep, nonna_mask_to_keep, prior_to_keep


def calculate_rr_for_distal_var(
        sub_dependencies: dict, 
        nhanes_data: pd.DataFrame,
        reverse_rr: bool,
        output_var,    # DEBUG 
        input_var,    # DEBUG
) -> dict:
    
    #print('CALCULATING DISTAL FOR', output_var, input_var, sub_dependencies['TYPE'])    # DEBUG
    value_masks, nonna_masks, priors = create_masks_priors(
        nhanes_data=nhanes_data,
        sub_dependencies=sub_dependencies,
    )
    #print('PRIORS', priors)    # DEBUG

    unique_conditions = list(set(list(value_masks.keys())+list(priors.keys())))
    if len(value_masks) > 1:
        groups = create_independent_groups(nonna_masks, nhanes_data, set(unique_conditions))
    else:
        groups = [(cond,) for cond in unique_conditions]
        
    #if len(groups) > 1:    # DEBUG
    #    #print('NUMBER OF GROUPS', len(groups))    # DEBUG
    #    #print('NO SEQN FOR THIS COMBINATION')
    #    #print(output_var, input_var, [key[0] for key in value_masks])
    #    #print('AMOUNT OF NONNA ROWS')
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
    #    #print('-'*50)    # DEBUG

    condition_rrs = {}
    condition_rrs_plus_minus = {}
    for condition in unique_conditions:
        for entry in sub_dependencies['INVARS'][condition[0]]:
            if list(condition[1]) == entry['VALUE']:
                if 'relative_risk' in entry['STATS']:    # DEBUG
                    rr = entry['STATS']['relative_risk']
                    condition_rrs[condition] = rr
                    condition_rrs_plus_minus[condition] = rr + entry['STATS']['plus_minus']
                else:
                    print('RELATIVE RISK NOT FOUND FOR CONDITION', condition, 'STATS', entry['STATS'])
                    condition_rrs[condition] = 1
                #print('CONDITION RR', condition, condition_rrs[condition])    # DEBUG
    #print('CONDITION RRS', condition_rrs)

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

    rr_plus_minus, _, _, _ = _calculate_weighted_rr(
        unique_conditions,
        groups,
        condition_rrs_plus_minus,
        value_masks,
        nonna_masks,
        priors,
        sub_dependencies['TYPE'],
        nhanes_data,
    )

    stats = {}
    if reverse_rr:
        #print('REVERSING RR FOR OUTPUT', output_var, 'AND INPUT', input_var)
        #stats['RELATIVE_RISK_REVERSED'] = True    # REMOVE ALL UNNECESSARY FIELDS AFTER DEBUG ?
        rr = 1 / rr
        rr_plus_minus = 1 / rr_plus_minus

        if value_mask_to_keep is not None:
            value_mask_to_keep = ~value_mask_to_keep
        if prior_to_keep is not None:
            prior_to_keep = 1 - prior_to_keep
    else:
        #stats['RELATIVE_RISK_REVERSED'] = False
        pass

    stats['relative_risk'] = rr
    stats['plus_minus'] = plus_minus = abs(rr-rr_plus_minus)
    stats['ci'] = CI

    #print(stats)    # DEBUG
    #print('-'*100)    # DEBUG 
    return stats, value_mask_to_keep, nonna_mask_to_keep, prior_to_keep

# %% [markdown]
# ## NHANES data preprocessing 

# %%
# NHANES PREPROCESS
ZERO_CUTOFF_THRESHOLD = 1e-16


def replace_missing(data: pd.DataFrame, nhanes: pd.DataFrame, replace_value: int = -1) -> pd.DataFrame:
    # Replace missing with -1 so after pivot we treat added NaN(NHANES category) and missing(data is not available for seqn) differently
    for i in range(1, 6):
        codes_with_missing = data[data[f'index{i}'].str.contains('missing', na=False)]['code'].unique()
        for code in codes_with_missing:
            nhanes.loc[(nhanes['var_code'] == code) & (nhanes['value'].isna()), 'value'] = replace_value
    return nhanes


def zero_cutoff_values(nhanes: pd.DataFrame, threshold: float = ZERO_CUTOFF_THRESHOLD):
    # Replace very small values with zero inplace
    nhanes['turn_to_zero'] = nhanes['value'].apply(lambda e: (e>0) & (e < threshold))
    nhanes.loc[nhanes['turn_to_zero'], 'value'] = 0
    nhanes.drop('turn_to_zero', axis=1, inplace=True)
    

def preprocess_nhanes(nhanes: pd.DataFrame, spreadsheet_data: pd.DataFrame) -> pd.DataFrame:
    print('Started NHANES data preprocessing..')
    start = time.time()
    
    var_codes = spreadsheet_data['code'].dropna().unique()
    nhanes = nhanes.loc[nhanes['var_code'].isin(var_codes), :].reset_index(drop=True)
    nhanes.drop(['variable_name', 'file_name'], axis='columns', inplace=True)
    
    nonna_types = spreadsheet_data[~spreadsheet_data['Type'].isna()]
    codes_average = nonna_types[nonna_types['Type'].str.endswith('average')]['code'].dropna().unique() 
    nhanes = replace_missing(spreadsheet_data, nhanes, replace_value=-1)
    zero_cutoff_values(nhanes)
    nhanes.drop(labels='file_name', axis='columns', inplace=True, errors='ignore')
    nhanes.dropna(axis='rows', subset='value', inplace=True)
    
    average_rows = nhanes[nhanes['var_code'].isin(codes_average)]
    averaged = average_rows.groupby(['seqn', 'var_code']).agg('mean').reset_index()
    
    non_average_rows = nhanes[~nhanes['var_code'].isin(codes_average)]
    non_average_unique = non_average_rows.groupby(['seqn', 'var_code']).agg(lambda e: e.mode().values[0]).reset_index()
    
    nhanes = pd.concat([averaged, non_average_unique], ignore_index=True)
    print(f'Finished preprocessing NHANES data. It took {time.time()-start} seconds.')
    return nhanes

# %% [markdown]
# ## Config creation functions

# %%
from bayesnet_gcn.node_types import (
    prior_types,
    dependency_types,
    stats_recalculation_types,
)


CI = 95
PLUS_MINUS = 0.05


explicit_types = {
    'discrete_nhanes_explicit',
    'naive_0_nhanes_explicit',
    'naive_0_nhanes_explicit_average',
    'dependency_nhanes_explicit',
}


quartile_priors_types = {
    'discrete_nhanes_quartile',
    'discrete_nhanes_quartile_average',
    'naive_0_nhanes_quartile',
    'naive_0_nhanes_quartile_average',
}

types_rename = {
    'dependency_nhanes_explicit': 'dependency',
    'dependency_nhanes_quartile': 'dependency',
    'dependency_priors': 'dependency',

    'discrete_priors': 'discrete',
    'discrete_nhanes_explicit': 'discrete',
    'naive_0_nhanes_explicit': 'discrete',
    'naive_0_nhanes_explicit_average': ' discrete',
    'discrete_nhanes_quartile': 'discrete',
    'naive_0_nhanes_quartile_average': 'discrete',
    'discrete_nhanes_quartile_average': 'discrete',
}

available_anomaly_detectors = {
    'LevelShiftAD',
    'InterQuartileRangeAD',
    'AutoregressionAD',
    'ThresholdAD',
    'QuantileAD',
}


def is_int(inp_str: str) -> bool:
    try:
        int(inp_str)
    except ValueError:
        return False
    return True


def is_float(inp_str: str) -> bool:
    try:
        float(inp_str)
    except ValueError:
        return False
    return True


def parse_anomaly_data(data: pd.DataFrame) -> dict:

    anomalies_config = {}
    anomalies = data[data['Type']=='anomaly']['output'].dropna().unique()

    for anomaly in anomalies:
        anomaly_data = {}
        anomaly_rows = data[data['output'] == anomaly]
        
        detectors = []
        for elem in anomaly_rows['input']:
            if elem in available_anomaly_detectors:
                detectors.append(elem)
            else:
                params = json.loads(elem)

        for param, value in params.items():
            if is_int(value):
                params[param] = int(value)
            elif is_float(value):
                params[param] = float(value)

        anomaly_data['detectors'] = detectors
        anomaly_data['parameters'] = params
        anomalies_config[anomaly] = anomaly_data

    for param, value in anomaly_data['parameters'].items():
        if is_int(value):
            anomaly_data['parameters'][param] = int(value)
        elif is_float(value):
            anomaly_data['parameters'][param] = float(value)

    return anomalies_config


def _ss_to_rr(sensitivity: float, specificity: float, output_prior: float) -> float:
    TP = sensitivity * output_prior
    TN = specificity * (1.0 - output_prior)
    FN = output_prior - TP
    FP = (1.0 - output_prior) - TN

    rr = (TP/(TP+FP)) / (FN/(FN+TN))
    return rr


def convert_ss_to_rr(ss_stats: dict, output_prior: float) -> dict:
    relative_risk = _ss_to_rr(ss_stats['sensitivity'], ss_stats['specificity'], output_prior)
    
    sens_plus_minus = ss_stats['sensitivity'] + ss_stats['sensitivity_plus_minus']
    spec_plus_minus = ss_stats['specificity'] + ss_stats['specificity_plus_minus']
    relative_risk_plus_minus = _ss_to_rr(sens_plus_minus, spec_plus_minus, output_prior)

    plus_minus = abs(relative_risk - relative_risk_plus_minus)
    stats = {
        'relative_risk': relative_risk,
        'plus_minus': plus_minus,
        'ci': CI,
    }
    return stats


def extract_priors(row):
    outvars = {}
    for i in range(1, 6):
        if pd.isna(row[f'value{i}']):
            break
        else:
            if pd.isna(row[f'index{i}']):
                msg = f"Row {row.name+2} | Missing prior for value '{row[f'value{i}']}'."
                raise PriorsError(msg)
            elif not is_float(row[f'index{i}']):
                msg = f"Row {row.name+2} | Prior for value '{row[f'value{i}']}' is not a floating point number."
                raise PriorsError(msg)
            else:
                outvars[row[f'value{i}'].strip()] = float(row[f'index{i}'])
    return outvars


def create_outvars(row: pd.Series, nhanes_counts: pd.DataFrame) -> Tuple[Union[list, dict], Optional[dict]]:
    if row['Type'] in ['all_of', 'any_of']:
            return [row['value1'].strip(), row['value2'].strip()], None

    elif row['Type'] in ['dependency_priors', 'discrete_priors']:
        outvars = extract_priors(row)
        return outvars, None

    elif row['Type'] in prior_types | {'dependency_nhanes_explicit', 'dependency_nhanes_quartile'}:
        return calculate_priors_nhanes(row, nhanes_counts)
    

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
        return _calc_sensitivity_specificity(a=a, b=b, c=0, d=1-a-b)
    elif dependency_type == 'equivalent_to':
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
            stats['relative_risk'] = 1
            stats['plus_minus'] = 0.05
            stats['ci'] = CI
            
    elif not pd.isna(row['Sensitivity Stat Value']):
        stats['sensitivity'] = row['Sensitivity Stat Value']
        stats['sensitivity_plus_minus'] = row['Sensitivity Plus Minus']
        
        stats['specificity'] = row['Specificity Stat Value']
        stats['specificity_plus_minus'] = row['Specificity Plus Minus']
        stats['ci'] = CI
        
    return stats


def get_priors_any_all(sub_dependencies: dict, nhanes_data: pd.DataFrame) -> dict:
    # Calculate priors for any_of and all_of from nhanes
    priors_to_use = []
    value_mask, nonna_mask, prior = create_masks_priors(nhanes_data, sub_dependencies, is_first=False)
    if value_mask is not None:
        priors_to_use.append(nhanes_data[value_mask & nonna_mask].shape[0] / nhanes_data[nonna_mask].shape[0])

    if prior is not None:
        priors_to_use.append(prior)
    final_prior = calculate_final_prior(priors_to_use, sub_dependencies['TYPE'])
    priors = {
        sub_dependencies['OUTVARS'][0]: final_prior,
        sub_dependencies['OUTVARS'][1]: 1-final_prior
    }
    return priors


def find_quartile_name(names: List[str], possible_names) -> str:
    found = []

    for name in names:
        for pos_name in possible_names:
            if pos_name.startswith(name):
                found.append(pos_name)
    return found
            

def parse_dependency(
        row: pd.Series,
        data: pd.DataFrame,
        nhanes_counts: pd.Series,
        raw_nhanes: pd.DataFrame,
        distal_to: Optional[str] = None,
        parsed_dependencies: dict = {}
) -> dict:
    #print('-'*100)
    #print('ROW', row.name+2, '|', 'PARSING FOR', row['output'], row['Type'], '|', 'DISTAL TO', distal_to)
    dependency = {}

    #if distal_to:
    #    print('DISTAL TO', distal_to)

    try:
        if not pd.isna(row['code']):
            dependency['CODE'] = row['code']
    except:
        print(f"ROW OUTPUT {row['output'].values}")
        sys.exit()
    
    #if row['Type'] in types_rename:
    #    dependency['TYPE'] = types_rename[row['Type']]
    #else:
    #    dependency['TYPE'] = row['Type']
    dependency['TYPE'] = row['Type']
    
    if not pd.isna(row['reverse']):
        dependency['REVERSE'] = row['reverse']

    outvars, value_ranges = create_outvars(row, nhanes_counts)
    if value_ranges is not None:
        dependency['value_ranges'] = value_ranges

    if row['Type'] in prior_types:
        dependency['PRIORS'] = outvars
        parsed_dependencies[row['output']] = dependency
        #print(f"FINISHED PARSING FOR {row['output']} {row['Type']}")
        #print('-'*50)
        return parsed_dependencies
    
    # PARSE INPUT DEPENDENCIES AND EXTRACT DATA FOR STATS CALCULATION
    as_output = data[data['output'] == row['output']]
    if distal_to:
        #print('DISTAL NODE', row['output'], distal_to)
        #print(as_output['Type'].values)
        if distal_to not in as_output['Type'].values:
            distal_to = None
    
    invars = {}
    sub_dependencies = {}
    stats_to_recalculate = {}    # For sensitivity and specificity rows in distal cases
    for idx, inp_row in as_output.iterrows():
        if not pd.isna(inp_row['input']):
            input_var_definition_row = data[(data['output']==inp_row['input']) & (data['Type'].isin(dependency_types | prior_types))].squeeze()
            #print('INPUT', inp_row['input'], input_var_definition_row['Type'])    # DEBUG
            if 'nhanes' in input_var_definition_row['Type']:    # Move this check to validity checks ? 
                if input_var_definition_row['code'] not in raw_nhanes.columns:
                    print(f"NO DATA FOUND IN NHANES FOR {input_var_definition_row['output']}, code {input_var_definition_row['code']}, type {input_var_definition_row['Type']}.")
                    print(f"SKIPPING THIS DEPENDENCY FOR {inp_row['output']}.")
                    continue
                    
            if inp_row['Type'] == 'distal':
                distal_to = row['output']
            
            if inp_row['input'] in parsed_dependencies:
                sub_dependencies[inp_row['input']] = parsed_dependencies[inp_row['input']]
            else:
                if distal_to and inp_row['Type'] in {distal_to, 'distal'}:
                    distal_nested = distal_to
                else:
                    distal_nested = None
                
                parsed_dependencies = parse_dependency(
                    row=input_var_definition_row,
                    data=data,
                    nhanes_counts=nhanes_counts,
                    raw_nhanes=raw_nhanes,
                    distal_to=distal_nested,
                    parsed_dependencies=parsed_dependencies,
                )
                sub_dependencies[inp_row['input']] = parsed_dependencies[inp_row['input']]

            var_code = None
            if not pd.isna(input_var_definition_row['code']):
                var_code = input_var_definition_row['code']

            # EXTRACT INPUT VALUE
            if input_var_definition_row['Type'] in quartile_priors_types:
                #print(f'ROW {idx+2}', '|', 'OUTPUT', inp_row['output'], 'INPUT', inp_row['input'], 'INPUT TYPE', input_var_definition_row['Type'])
                parsed_values = [v.strip() for v in inp_row['input values'].split(',')]
                input_values = find_quartile_name(
                    parsed_values,
                    sub_dependencies[inp_row['input']]['PRIORS'].keys(),
                )
            else:
                input_values = [v.strip() for v in inp_row['input values'].split(',')]

            # CALCULATE STATS
            if distal_to and inp_row['Type'] in {distal_to, 'distal'}:
                if not pd.isna(inp_row['RR Stat Value']):
                    stats = extract_row_stats(inp_row)
                    
                elif not pd.isna(inp_row['Sensitivity Stat Value']):
                    stats_to_keep = {}
                    stats_to_keep['sensitivity'] = inp_row['Sensitivity Stat Value']
                    stats_to_keep['sensitivity_plus_minus'] = inp_row['Sensitivity Plus Minus']
                    
                    stats_to_keep['specificity'] = inp_row['Specificity Stat Value']
                    stats_to_keep['specificity_plus_minus'] = inp_row['Specificity Plus Minus']
                    
                    stats_to_recalculate[(inp_row['input'], tuple(input_values))] = stats_to_keep
                    stats = {}
                else:
                    #value_mask = None     # TODO: SAVE THESE TO REMOVE RELALCULATIONS
                    #nonna_mask = None
                    #prior = None
                    
                    if inp_row['input'] in sub_dependencies:    # DEBUG
                        reverse_rr = (input_values[0] != sub_dependencies[inp_row['input']]['OUTVARS'][0])
                        stats, value_mask, nonna_mask, prior = calculate_rr_for_distal_var(
                            sub_dependencies=sub_dependencies[inp_row['input']],
                            nhanes_data=raw_nhanes,
                            reverse_rr=reverse_rr,
                            output_var=row['output'],    # DEBUG
                            input_var=inp_row['input'],    # DEBUG
                        )
                        #print(f"ROW {idx+2} | CALCULATED DISTAL STATS |", 'OUTPUT', inp_row['output'], 'INPUT', inp_row['input'], '|', 'STATS', stats)
                    else:    # DEBUG
                        print(inp_row['output'], inp_row['input'], 'DISTAL STATS CALCULATION PROBLEM. INPUT SUBDEPENDENCY IS NOT PARSED')
                        stats = {'relative_risk': 1.0, 'BUG IN DATA': True}
                
            else:
                #input_values = tuple(input_values)
                if row['Type'] in ['all_of', 'any_of']:
                    if not pd.isna(inp_row['RR Stat Value']):     # Distal case
                        stats = extract_row_stats(inp_row)
                    else:
                        stats = {}
                    
                elif inp_row['Type'] in stats_recalculation_types:
                    if sub_dependencies[inp_row['input']]['TYPE'] in {'all_of', 'any_of'}:
                        input_priors = get_priors_any_all(sub_dependencies[inp_row['input']], raw_nhanes)
                    elif 'OUTVARS' in sub_dependencies[inp_row['input']]:
                        input_priors = sub_dependencies[inp_row['input']]['OUTVARS']
                    else:
                        input_priors = sub_dependencies[inp_row['input']]['PRIORS']
                    
                    try:
                        input_prior = 0
                        for value in input_values:
                            input_prior += input_priors[value]
                            
                        stats = calculate_sensitivity_specificity(
                            dependency_type=inp_row['Type'],
                            output_prior=outvars[row['value1']],
                            input_prior=input_prior,
                        )

                    except TypeError:
                        print('-'*25)
                        print('MISSING OUTVARS', outvars, '|', 'ROW', inp_row.name+2, 'OTUPUT', row['output'], 'INPUT', inp_row['input'])
                        stats = {}
                        
                    except ZeroDivisionError:     # DEBUG
                        print('-'*25)
                        print('ZERO DIVISION WHILE CALCULATING SENSITIVITY AND SPECIFCITIY FOR', row['output'], inp_row['input'])
                        print('INPUT PRIORS', input_priors, '|', 'TYPE', sub_dependencies[inp_row['input']]['TYPE'])
                        print('OUTPUT PRIORS', outvars, 'TYPE', row['Type'])
                        print('-'*25)
                        continue

                    if pd.isna(stats['sensitivity']) or pd.isna(stats['specificity']):    # DEBUG
                        print('STATS RECALCULATION TYPE PROBLEM', inp_row['output'], inp_row['input'])
                        print('INPUT PRIORS', input_priors, '|', 'TYPE', sub_dependencies[inp_row['input']]['TYPE'])
                        print('OUTPUT PRIORS', outvars, 'TYPE', row['Type'])
                        print('-'*25)
                        stats = {'sensitivity': 0.5, 'specificity': 0.5, 'BUG IN DATA': True}
                        
                    elif stats['sensitivity'] > 1.0 or stats['specificity'] > 1.0:     # DEBUG
                        print('ROW', idx+2)
                        print('RECALCULATED STATS FOR', inp_row['output'], inp_row['input'], '|', 'TYPE:', inp_row['Type'], '|', 'STATS:', stats)
                        print('INPUT PRIORS', input_priors, '|', 'TYPE', sub_dependencies[inp_row['input']]['TYPE'])
                        print('OUTPUT PRIORS', outvars, 'TYPE', row['Type'])
                        print('-'*25)
                        
                else:
                    stats = extract_row_stats(inp_row)

            input_var_data = {}
            input_var_data['VALUE'] = input_values
            if stats:
                input_var_data['STATS'] = stats
                
            if inp_row['input'] in invars:    # MULTIPLE INPUTS VALUES FOR SAME VARIABLE CASE
                invars[inp_row['input']].append(input_var_data)
            else:
                invars[inp_row['input']] = [input_var_data]

    dependency['INVARS'] = invars
    dependency['OUTVARS'] = outvars
    dependency['INPUTS'] = sub_dependencies
    
    for (invar, inp_values), stats in stats_to_recalculate.items():
        output_priors = get_priors_any_all(dependency, raw_nhanes)
        output_prior = output_priors[row['value1']]
        calculated_stats = convert_ss_to_rr(stats, output_prior)
        for entry in invars[invar]:
            if entry['VALUE'] == list(inp_values):
                entry['STATS'] = calculated_stats

    parsed_dependencies[row['output']] = dependency
    #print(f"FINISHED PARSING FOR {row['output']} {row['Type']}")
    #print('='*100)
    return parsed_dependencies


def prepare_config(
        spreadsheet_data: pd.DataFrame,
        nhanes: pd.DataFrame,
        nhanes_preprocessed: bool = False,
):

    config = {}
    
    config['anomaly_data'] = parse_anomaly_data(spreadsheet_data)

    if not nhanes_preprocessed:
        start = time.time()
        nhanes = preprocess_nhanes(nhanes, spreadsheet_data)

    nhanes_counts = nhanes.groupby(['var_code', 'value']).size()
    pivoted = nhanes.pivot_table(columns='var_code', index=['seqn'])
    pivoted.columns = pivoted.columns.droplevel(level=0)
    
    dependecy_output_var_rows = data[data['Type'].isin(dependency_types)]
    dependency_data = {}
    parsed_dependencies = {}
    for idx, row in dependecy_output_var_rows.iterrows():

        as_input_rows = data[data['input'] == row['output']]
        if as_input_rows.shape[0] > 0:    # Parse recursively output nodes that are not input to any
            continue

        parsed_dependencies = parse_dependency(
            row=row,
            data=spreadsheet_data,
            nhanes_counts=nhanes_counts,
            raw_nhanes=pivoted,
            parsed_dependencies=parsed_dependencies
        )
        dependency_data[row['output']] = parsed_dependencies[row['output']]

    config['dependency_data'] = dependency_data
    return config

# %% [markdown]
# ## Prepare spreadsheet

# %% [markdown]
# #### Spreadsheet is expected to be in ./data subfolder under the name 'Individual Relations.xlsx' by default. Adjust it if needed by changing 'path' variable.

# %%
def _upper(e):
    if not pd.isna(e):
        return e.upper()
    return e

from os.path import exists

def create_spreadsheet_data(spreadsheet_path = './data/Individual Relations.xlsx'):
    data = pd.read_excel(spreadsheet_path, sheet_name='all worksheet', header=0)

    #data.drop(labels=['index', 'counter'], axis='columns', inplace=True)
    data.dropna(axis='index', how='all', inplace=True)
    #data = data[~data['output'].isna()]

    data_inputnotna = data[~data['input'].isna()]
    data.loc[data_inputnotna[data_inputnotna['input'].str.startswith('fixme')].index, 'input'] = np.nan

    data = data.applymap(lambda e: e.strip() if isinstance(e, str) else e)
    data['code'] = data['code'].apply(_upper)

    # %%
    check_missing_inputs(data)
    check_types(data)
    check_missing_priors(data)
    check_input_values(data)
    check_value_ranges_coverage(data, pivoted)
    #check_single_values_ss_types(data)
    check_loops(data)
    check_missing_codes(data)
    check_value_columns(data)

    return data

def create_nhanes(spreadsheet_data, nhanes_path="./data/nhanes/",
                created_file_path="./data/out/preprocessed_nhanes.csv"): 
    
    #if you want the file created and it exists, you have to go in and delete it
    if not exists(created_file_path):

    # %% [markdown]
    # ### Load NHANES data
    # #### Currently NHANES data consists of multiple tables that we need to merge together. Since NHANES data is too big for git it's stored in that drive: https://drive.google.com/drive/u/1/folders/14wN8pkLgwjpGzgtW1X5jYFGONkpZFslf
    # #### In order to run the code download all '.csv' and '.parquet' files and put them './data/nhanes/' subfolder.

    # %%

        dfs = []
        for fname in os.listdir(nhanes_path):
            df = None
            if fname.endswith('csv'):
                df = pd.read_csv(os.path.join(nhanes_path, fname))
            elif fname.endswith('parquet'):
                df = pd.read_parquet(os.path.join(nhanes_path, fname))
            if df is not None:
                df.rename(lambda e: e.lower(), axis=1, inplace=True)
                dfs.append(df)

        nhanes = pd.concat(dfs, axis=0)

        #var_codes = data['code'].dropna().unique()
        #nhanes = nhanes[nhanes['var_code'].isin(var_codes)]
        #nhanes.drop('variable_name', axis='columns', inplace=True)

        # %% [markdown]
        # #### Preprocess NHANES outside config creation function to save time on consequent reruns

        # %%
        # TO SAVE TIME ON RERUNS, IT'S TIME CONSUMING
        nhanes = preprocess_nhanes(nhanes, spreadsheet_data)

        # %%
        nhanes.to_csv(created_file_path, index=False)

    # %%
    nhanes = pd.read_csv(created_file_path)

    return nhanes

  

def create_pivoted(nhanes): 
    pivoted = nhanes.pivot_table(columns='var_code', index=['seqn'])
    pivoted.columns = pivoted.columns.droplevel(level=0)

    return pivoted


# %% [markdown]
# ### Create and save config from spreadsheet
# #### Note that since nhanes is already preprocessed we need to pass 'nhanes_preprocessed' as True

# %%

def create_config (spreadsheet_data, nhanes, linearized_config_path ='./bayesnet_config_linear.json'):
    bayesnet_config = prepare_config(spreadsheet_data, nhanes, nhanes_preprocessed=True)

    # %%
    #with open('./bayesnet_config.json', 'w') as f:
        #json.dump(bayesnet_config, f, allow_nan=False)
# %%
    dependency_data = bayesnet_config['dependency_data'].copy()
    linearized = linearize_config(dependency_data)
    linear_conf = {'anomaly_data': bayesnet_config['anomaly_data'], 'dependency_data': linearized}

    # %%
    with open(linearized_config_path, 'w') as f:
        json.dump(linear_conf, f, allow_nan=False)

    # %%
    #len(linear_conf['dependency_data'])

# %% [markdown]
# ### Linearize config

# %%
def add_subnodes(dependency_data: dict, added: dict):
    for name, data in dependency_data.items():
        if name in added:
            continue
        if 'INPUTS' not in data:
            added[name] = data
        else:
            added = add_subnodes(data['INPUTS'], added)
            data.pop('INPUTS')
            added[name] = data
    return added

def linearize_config(config: dict):
    added = {}

    for variable_name, dependency_data in config.items():
        added = add_subnodes(dependency_data['INPUTS'], added)
        dependency_data.pop('INPUTS')
        added[variable_name] = dependency_data
    return added


# %% [markdown]
# ## Some debugging stuff you don't need

# %%
def multiple_input_values_nodes(data: pd.DataFrame):
    dependecy_output_var_rows = data[data['Type'].isin(dependency_types)]

    for idx, row in dependecy_output_var_rows.iterrows():
        as_output = data[data['output'] == row['output']]
        node_type = as_output[as_output['input'].isna()]['Type'].values[0]
        input_variables = {}
        for idx_, inp_row in as_output.iterrows():
            if not pd.isna(inp_row['input']):
                if inp_row['input'] in input_variables:
                    print(f"Row {idx_+2} | Multiple inputs for input variable {inp_row['input']} | NODE TYPE {node_type}")
                else:
                    input_variables[inp_row['input']] = inp_row['input values']

# %%
multiple_input_values_nodes(data)

# %%
def different_ss_plus_minus(data: pd.DataFrame):
    has_ss = data[(~data['Sensitivity Stat Value'].isna()) & (~data['Specificity Stat Value'].isna())]

    for idx, row in has_ss.iterrows():
        if pd.isna(row['Sensitivity Plus Minus']):
            print(f"Row {idx+2} | Sensitivity plus minus is missing.")
            continue
        
        if pd.isna(row['Specificity Plus Minus']):
            print(f"Row {idx+2} | Specificity plus minus is missing.")
            continue

        if row['Specificity Plus Minus'] != row['Sensitivity Plus Minus']:
            print(f"Row {idx+2} | Speficity and sensitivity have different plus minus.")

# %%
different_ss_plus_minus(data)

# %%
has_ss = data[(~data['Sensitivity Stat Value'].isna()) & (~data['Specificity Stat Value'].isna())]

# %%
has_ss

# %%
data[(~data['Specificity Stat Value'].isna())]

# %%
total = 0 
for inp, dependency_data in linear_conf['dependency_data'].items():
    if dependency_data['TYPE'] in prior_types | {'dependency_priors', 'discrete_priors'}:
        total += 1

# %%
total

# %%
len(linearized)

# %%
with open('./bayesnet_config_linear.json', 'w') as f:
    json.dump(linear_conf, f)

# %%
# SUBSUMES

input_prior = 0.063
output_prior = 0.32

a = output_prior
b = input_prior - a
c = 0
d = 1 - a - b

values = {
    'SENSITIVITY': a/(a+c),
    'SPECIFICITY': d/(b+d),
}
values

# %%
# IS_A

input_prior = 0.32
output_prior = 0.25

a = input_prior
b = 0
c = output_prior - a
d = 1 - a - c
values = {
    'SENSITIVITY': a/(a+c),
    'SPECIFICITY': d/(b+d),
}
values

# %%
def get_nonna_mask(nhanes, code):
    return ~(pivoted[code].isna())

# %%
code = 'LBXHSCRP'

# %%
nonna_mask = ~(pivoted['PAQ677'].isna()) & ~(pivoted['PAD440'].isna()) & ~(pivoted['CVDFITLV'].isna())

# %%
codes = ['PAQ650', 'PAD645', 'PAD680']

value_ranges = {
    'PAQ650': (1,2),
    'PAD645': (0, 1440),
    'PAD680': (0, 10000),
}

nonna_mask = True
for code in codes:
    nonna_mask = nonna_mask & (~pivoted[code].isna())
    nonna_mask = nonna_mask & ((pivoted[code] >= value_ranges[code][0]) & (pivoted[code] <= value_ranges[code][1]))

print(pivoted[nonna_mask].shape[0])

# %%
exercise_nonna_mask = (get_nonna_mask(pivoted, 'PAQ677') & (pivoted['PAQ677']>=0) & (pivoted["PAQ677"] <=7) &
                       get_nonna_mask(pivoted, 'PAD440') & (pivoted['PAD440'] <=2) & (pivoted['PAD440'] >=1) &
                       get_nonna_mask(pivoted, 'CVDFITLV') & (pivoted['CVDFITLV'] <=3) & (pivoted['CVDFITLV'] >=1))

# %%
# EXERCISE
cardiovascular_days_nonna_mask = get_nonna_mask(pivoted, 'PAQ677') & (pivoted['PAQ677']>=0) & (pivoted["PAQ677"] <=7)
cardiovascular_days_mask = (pivoted['PAQ677'] >=5) & (pivoted['PAQ677'] <= 7)
cardiovascular_prior = pivoted[cardiovascular_days_mask&cardiovascular_days_nonna_mask].shape[0] / pivoted[cardiovascular_days_nonna_mask].shape[0]

strengthening_exercises_nonna_mask = get_nonna_mask(pivoted, 'PAD440') & (pivoted['PAD440'] <=2) & (pivoted['PAD440'] >=1)
strengthening_exercises_mask = (pivoted['PAD440'] == 1)
strengthening_exercises_prior = pivoted[strengthening_exercises_mask&strengthening_exercises_nonna_mask].shape[0] / pivoted[strengthening_exercises_nonna_mask].shape[0]

vo2max_cardiovascular_fitness_nonna_mask = get_nonna_mask(pivoted, 'CVDFITLV') & (pivoted['CVDFITLV'] <=3) & (pivoted['CVDFITLV'] >=1)
vo2max_cardiovascular_fitness_mask = (pivoted['CVDFITLV'] == 3)
vo2max_cardiovascular_fitness_prior = pivoted[vo2max_cardiovascular_fitness_mask&vo2max_cardiovascular_fitness_nonna_mask].shape[0] / pivoted[vo2max_cardiovascular_fitness_nonna_mask].shape[0]

exercise_prior = cardiovascular_prior*strengthening_exercises_prior*vo2max_cardiovascular_fitness_prior

# %%
# FRAILTY_LIFESTYLE

heavy_alcohol_cons_nonna_mask = (get_nonna_mask(pivoted, 'ALQ142') & ((pivoted['ALQ142']==1) | (pivoted['ALQ142'] == 2) | (pivoted['ALQ142'] == 3) | (pivoted['ALQ142'] == 4) | (pivoted['ALQ142'] == 0) | ((pivoted['ALQ142'] >=5) & (pivoted['ALQ142'] <=10))))
heavy_alcohol_cons_mask = (pivoted['ALQ142']==2) | (pivoted['ALQ142'] ==1)
heavy_alcohol_cons_prior = pivoted[heavy_alcohol_cons_mask&heavy_alcohol_cons_nonna_mask].shape[0] / pivoted[heavy_alcohol_cons_nonna_mask].shape[0]

five_days_smoke_nonna_mask = get_nonna_mask(pivoted, 'SMQ681') & ((pivoted['SMQ681'] ==2) | (pivoted['SMQ681'] ==1))
five_days_smoke_mask = (pivoted['SMQ681'] ==1)
five_days_smoke_prior = pivoted[five_days_smoke_mask&five_days_smoke_nonna_mask].shape[0] / pivoted[five_days_smoke_nonna_mask].shape[0]

frailty_lifestyle_prior = 1 - exercise_prior*(1-heavy_alcohol_cons_prior)*(1-five_days_smoke_prior)

# %%
frailty_weight_nonna_mask = (get_nonna_mask(pivoted, 'BMXWAIST') & (pivoted['BMXWAIST']>=0) & (pivoted["BMXWAIST"] <=1000) &
       get_nonna_mask(pivoted, 'BMXBMI') & (pivoted['BMXBMI'] <=1000) & (pivoted['BMXBMI'] >=0) &
       get_nonna_mask(pivoted, 'FSD032C') & ((pivoted['FSD032C'] == 1) | (pivoted['FSD032C']==2) | (pivoted['FSD032C'] == 3)))

frailty_weight_mask = (get_nonna_mask(pivoted, 'BMXWAIST') & (pivoted['BMXWAIST']>=0) & (pivoted["BMXWAIST"] <=1000) &
       get_nonna_mask(pivoted, 'BMXBMI') & (pivoted['BMXBMI'] <=1000) & (pivoted['BMXBMI'] >=0) &
       get_nonna_mask(pivoted, 'FSD032C') & ((pivoted['FSD032C'] == 1) | (pivoted['FSD032C']==2) | (pivoted['FSD032C'] == 3)) &
       ((pivoted['BMXWAIST']<=1000) & (pivoted['BMXWAIST'] >=89)) |
       (((pivoted['BMXBMI'] <=39.999) & (pivoted['BMXBMI']>=30)) | ((pivoted['BMXBMI']>=40) & (pivoted['BMXBMI'] <=1000))) |
       ((pivoted['BMXBMI']<=29.999) & (pivoted['BMXBMI'] >=25)) |
       ((pivoted['BMXBMI']>=0) & (pivoted['BMXBMI'] <= 18.999)) |
       (pivoted['FSD032C']==1))

# %%
frailty_healty_diet_nonna_mask = (get_nonna_mask(pivoted, 'DR1TPROT') & (pivoted['DR1TPROT']>=0) & (pivoted["DR1TPROT"] <=869.49) &
       get_nonna_mask(pivoted, 'LBXVIDMS') & (pivoted['LBXVIDMS'] <=1000) & (pivoted['LBXVIDMS'] >=0))

frailty_healthy_diet_mask = ~(((pivoted['DR1TPROT']<=92.2) & (pivoted['DR1TPROT'] >=0)) & ((pivoted['LBXVIDMS'] <= 124.999) & (pivoted['LBXVIDMS']>=50)))

# %%
1 - frailty_lifestyle_prior*pivoted[frailty_weight_nonna_mask&frailty_healty_diet_nonna_mask&frailty_healthy_diet_mask&frailty_weight_mask].shape[0]/pivoted[frailty_healty_diet_nonna_mask&frailty_weight_nonna_mask].shape[0]

# %%
# PROCESS CFDCIR VARIABLE FILES

first = pd.read_csv(os.path.join(nhanes_path, 'CFQ_G.csv'))
second = pd.read_csv(os.path.join(nhanes_path, 'CFQ_H.csv'))

first = first.loc[:, ['SEQN', 'CFDCIR']]
second = second.loc[:, ['SEQN', 'CFDCIR']]

code_data = pd.concat([first, second], ignore_index=True)
code_data['var_code'] = 'CFDCIR'
code_data.rename(columns={'CFDCIR': 'value'}, inplace=True)
code_data = code_data.loc[:, ['SEQN', 'var_code', 'value']]
code_data.to_csv(os.path.join(nhanes_path, 'CFDCIR.csv'), index=False)

# %%
data.loc[1072].name

# %%
with open('test_config.json', 'w') as f:
    json.dump(d, f, allow_nan=False)

# %%
nhanes_counts = nhanes.groupby(['var_code', 'value']).size()

# %%
def remove_keys(d: dict):
    
    for k, v in d.items():
        

# %%
# EXTRACT MIN-MAX RANGES 
def remove_prefix(text, prefix):
    if text.startswith(prefix):
        return text[len(prefix):]
    return text

columns = ['code', 'bayes_var', 'type', 'answers', 'min', 'max']
rows = []
for k, v in a.items():
    if isinstance(v['value_ranges'], dict):
        row = [v['code']]
        row.append(k)
        row.append(v['type'])
        to_sort = [(k_, v_) for k_, v_ in v['value_ranges'].items()]
        to_sort.sort(key=_key)
        v_r = []
        for e in to_sort:
            v_r.append((remove_prefix(e[0], k).lstrip('_'), e[1]))

        if v['code'] not in nhanes_meta.index:
            row.append(v_r)
            row.append(None)
            row.append(None)
        else:
            
        rows.append(row)

# %%
survey_keys = pd.read_csv('data/survey_questions_with_json_keys.csv', encoding='unicode_escape')

# %%
import re
def parse_d(str_dict):
    str_dict = re.sub(r"(?<!\")'(?!\")", '"', str_dict)
    str_dict = str_dict.replace("\\","")
    return json.loads(str_dict)

# %%
df = pd.DataFrame.from_records(rows, columns=columns)

# %%
# NHANES PREPROCESS

nonna_types = data[~data['Type'].isna()]
codes_average = nonna_types[nonna_types['Type'].str.endswith('average')]['code'].dropna().unique()
nhanes = replace_missing(data, nhanes, replace_value=-1)
nhanes.drop(labels='file_name', axis='columns', inplace=True, errors='ignore')
nhanes.dropna(axis='rows', subset='value', inplace=True)
average_rows = nhanes[nhanes['var_code'].isin(codes_average)]
averaged = average_rows.groupby(['seqn', 'var_code']).agg('mean').reset_index()

non_average_rows = nhanes[~nhanes['var_code'].isin(codes_average)]
non_average_unique = non_average_rows.groupby(['seqn', 'var_code']).agg(lambda e: e.mode().values[0]).reset_index()

nhanes = pd.concat([averaged, non_average_unique], ignore_index=True)

# %%
# DEAL WITH AVERAGE IF WE AVERAGE FOR ALL UNIQUE seqnXvarcodeXfile_name entry

nhanes['value'] = nhanes.groupby(['seqn', 'var_code', 'file_name'])['value'].transform('mean')
nhanes.drop_duplicates(['seq', 'var_code', 'file_name'])

# %%
nhanes_types = {    
    'discrete_nhanes_explicit',
    'discrete_nhanes_quartile',
    'dependency_nhanes_explicit',
    'dependency_nhanes_quartile',
    'naive_0_nhanes_explicit',
    'naive_0_nhanes_quartile',
}

nhanes_codes = nhanes['var_code'].unique()

data_nhanes_codes = data[data['Type'].isin(nhanes_types)]['code'].unique()

problem_vars = {}

for var in data_nhanes_codes:
    var_name = data[data['code']==var]['output'].unique()[0]
    _type = data[data['code']==var]['Type'].unique()[0]
    if var.upper() not in nhanes_codes and var not in nhanes_codes:
        for i in range(len(var)):
            if var[:-i].upper() in nhanes_codes or var[:-i] in nhanes_codes:
                problem_vars[(var, var_name, _type)] = var[:-i]
        if (var, var_name, _type) not in problem_vars:
            problem_vars[(var, var_name, _type)] = 'MISSING IN NHANES'

for k, v in problem_vars.items():
    print(k, v)

# %%
nhanes[nhanes['var']]

# %%
var_code = 'MCQ160c'
nhanes_codes = nhanes['var_code'].unique()
var_code.upper() in nhanes_codes

# %%
upper_codes = set([code.upper() for code in data_nhanes_codes])

# %%
nhanes_upper = nhanes[nhanes['var_code'].isin(upper_codes)]

# %%
import datetime

def check_type(di, dtype):
    if isinstance(di, dict):
        for k, v in di.items():
            is_type = check_type(v, dtype)
            if is_type:
                print(di)
    elif isinstance(di, dtype):
        return True
    else:
        return False

def check_pd_na(di):
    if isinstance(di, dict):
        for k, v in di.items():
            is_na = check_pd_na(v)
            if is_na:
                print(di)
    elif isinstance(di, list):
        for elem in di: 
            return check_pd_na(elem)
            #if is_na:
                #print(di)
    else:
        if pd.isna(di):
            return True
        else:
            return False

# %%
check_pd_na(d)


