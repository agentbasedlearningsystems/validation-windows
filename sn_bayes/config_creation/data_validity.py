import logging

import pandas as pd
from typing import List

from .node_types import (
    prior_types,
    stats_recalculation_types,
    dependency_types,
    quartile_priors_types,
)
from .priors import extract_value_ranges, PriorsError


def check_missing_inputs(data: pd.DataFrame) -> List[str]:    # FIX FOR ANOMALIES
    errors = []
    types_to_check = dependency_types.difference({'avg'})
    to_check = data[data['Type'].isin(types_to_check)]

    missing_input_values = data[
        (data['output'].isin(to_check['output'].unique())) \
        & (~data['input'].isna()) \
        & (data['input values'].isna()) \
        & (data['Type'] != 'anomaly')
    ]
    
    if not missing_input_values.empty:
        for n, row in missing_input_values.iterrows():
            errors.append(f"Row {n+2} | Missing input values for input variable '{row['input']}' and output variable '{row['output']}'.")
    return errors


def check_types(data: pd.DataFrame) -> List[str]:
    errors = []
    
    with_missing_types = data[
        (~data['output'].isna()) \
        &(data['input'].isna()) \
        &(data['Type'].isna()) \
    ]
    if not with_missing_types.empty:
        errors.append(f"Found rows with empty types: {[e+2 for e in with_missing_types.index]}.")

    nonna_types = data[~data['Type'].isna()]
    possible_types = prior_types | {'anomaly', 'distal'} | set(data['output'].unique()) | stats_recalculation_types | dependency_types
    with_unexpected_types = nonna_types[~nonna_types['Type'].isin(possible_types)]
    if not with_unexpected_types.empty:
        errors.append(f"Found rows with unexpected types: {[e+2 for e in with_unexpected_types.index]}")

    return errors


def check_priors(data: pd.DataFrame) -> List[str]:
    from .dependency import create_outvars
    errors = []
    priors_rows = data[data['Type'].isin({'dependency_priors', 'discrete_priors'})]
    for idx, row in priors_rows.iterrows():
        try:
            priors, value_ranges = create_outvars(row, None)
        except PriorsError as e:
            errors.append(str(e))
        else:
            total_prob = sum(priors.values())
            if total_prob > 1.0:
                errors.append(f"Row {_+2} | Priors sum {total_prob} is over 1.0.")
    return errors


def check_input_values(data: pd.DataFrame) -> List[str]:
    # Check valid input values for every dependency row
    errors = []
    types_to_check = dependency_types.difference({'avg'})
    dependecy_output_var_rows = data[data['Type'].isin(types_to_check)]
    for _, row in dependecy_output_var_rows.iterrows(): 
        as_output = data[(data['output'] == row['output']) & (~data['input'].isna()) & (~data['input values'].isna())]
        for _, inp_row in as_output.iterrows():
            input_var_definition_row = data[(data['output']==inp_row['input']) & (data['Type'].isin(dependency_types | prior_types))].squeeze()
            if isinstance(input_var_definition_row['Type'], pd.Series):
                pass
            if input_var_definition_row['Type'] not in quartile_priors_types | {'dependency_nhanes_quartile'}:
                found = False
                input_values = [v.strip() for v in inp_row['input values'].split(',')]
                for v in input_values:
                    found = False
                    for i in range(1, 6):
                        if v == input_var_definition_row[f'value{i}']:
                            found = True
                            break
                    if not found:
                        errors.append(f"Row {_+2} | Input value '{v}' not found for input variable '{inp_row['input']}'.")
    return errors


def check_missing_codes(data: pd.DataFrame) -> List[str]:
    errors = []
    missing_codes = data[(data['Type'].str.contains('nhanes')) & (data['code'].isna())]
    if not missing_codes.empty:
        for idx, row in missing_codes.iterrows():
            errors.append(f"Row {row.name+2} | Missing NHANES code for variable '{row['output']}'")
    return errors


def check_duplicated_value_columns(data: pd.DataFrame) -> List[str]:
    errors = []
    having_values = data[~data['value1'].isna()]
    for idx, row in having_values.iterrows():
        parsed_values = set()
        for i in range(1, 6):
            value = row[f'value{i}']
            if not pd.isna(value):
                if value in parsed_values:
                    errors.append(f"Row {row.name+2} | Found duplicated value{i}: '{value}'.")
                else:
                    parsed_values.add(value)
            else:
                continue
    return errors


def check_stats(data: pd.DataFrame) -> List[str]:
    errors = []

    sensitivity = data[~data['Sensitivity Stat Value'].isna()]
    bad_sens = sensitivity[(sensitivity['Sensitivity Stat Value'] >= 1) | (sensitivity['Sensitivity Stat Value'] < 0)]
    for idx, row in bad_sens.iterrows():
        errors.append(f"Row {idx+2} | Erroneous 'Sensitivity Stat Value' equal {row['Sensitivity Stat Value']} is found.")

    specificity = data[~data['Specificity Stat Value'].isna()]
    bad_spec = specificity[(specificity['Specificity Stat Value'] >= 1) | (specificity['Sensitivity Stat Value'] < 0)]
    for idx, row in bad_spec.iterrows():
        errors.append(f"Row {idx+2} | Erroneous 'Specificity Stat Value' equal {row['Sensitivity Stat Value']} is found.")

    rr_plus_minus = data[~data['RR plus minus'].isna()]
    bad_plus_minus = rr_plus_minus[rr_plus_minus['RR plus minus'] <= 0]
    for idx, row in bad_plus_minus.iterrows():
        errors.append(f"Row {idx+2} | Erroneous 'RR plus_minus' equal {row['RR plus minus']} is found.")

    return errors


def _get_precision(num: float) -> int:
    str_num = str(num).rstrip('0')
    if '.' in str_num:
        return len(str_num.split('.')[-1])
    else:
        return 0

def check_value_ranges_coverage(data: pd.DataFrame, raw_nhanes: pd.DataFrame) -> List[str]:
    errors = []
    nhanes_rows = data[~data['code'].isna()]
    missing_values = []
    for _, row in nhanes_rows.iterrows():
        code = row['code']
        value_ranges = extract_value_ranges(row)
        if value_ranges and code in raw_nhanes.columns:
            # Gather all intervals
            nonna_mask = ~(raw_nhanes[code].isna())
            nonna_rows = raw_nhanes[nonna_mask]
            
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
                    
            covered = raw_nhanes[nonna_mask & values_mask]
            full = raw_nhanes[nonna_mask & ((raw_nhanes[code] >= min_value) & (raw_nhanes[code] <= max_value))]

            difference = pd.concat([covered, full]).drop_duplicates(keep=False)

            if not difference.empty:
                precisions = [_get_precision(v) for v in raw_nhanes[code].dropna().values]
                max_precision = max(precisions)
                errors.append(f'Row {_+2} | Code {code}. Not covered values: {difference[code].unique()}, max precision {max_precision}.')
                missing_values.append((_+2, code, difference[code].unique(), max_precision))
                
    return errors


class PresentLoopException(Exception):
    pass

def check_paths(row: pd.Series, data: pd.DataFrame, path: list) -> list:
    errors = []
    to_add = (row.name+2, row['output'])
    path.append(to_add)
    for node in path[:-1]:
        if to_add == node:
            msg = f"Dependency loop is found in the spreadsheet: {' <- '.join([f'Row num {e[0]}, {e[1]}' for e in path])}"
            errors.append(msg)
            raise PresentLoopException(msg)
    
    if row['Type'] in prior_types:
        return

    as_output = data[data['output'] == row['output']]
    for idx, inp_row in as_output.iterrows():
        if not pd.isna(inp_row['input']):
            sub_path = path.copy()
            input_var_definition_row = data[(data['output']==inp_row['input']) & (data['Type'].isin(dependency_types | prior_types))].squeeze()
            check_paths(input_var_definition_row, data, sub_path)
            

def check_loops(data: pd.DataFrame) -> List[str]:
    errors = []
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
            errors.append(str(e))

    return errors

    
class SpreadsheetValidityError(Exception):
    pass

def check_data_validity(data: pd.DataFrame, nhanes: pd.DataFrame):
    errors = []
    errors.extend(check_missing_inputs(data))
    errors.extend(check_types(data))
    errors.extend(check_missing_inputs(data))
    errors.extend(check_input_values(data))
    errors.extend(check_missing_codes(data))
    errors.extend(check_stats(data))
    errors.extend(check_duplicated_value_columns(data))
    errors.extend(check_value_ranges_coverage(data, nhanes))

    for msg in errors:
        logging.error(msg)

    if errors:
        raise SpreadsheetValidityError('Errors found in the spreadsheet. Aborting.')