"""
NHANES prior calculation functions.

Extracted from config_creation.ipynb cells 6 and 13.
Computes prior probabilities from NHANES value counts for each variable.
"""

import pandas as pd

from .node_types import explicit_types, quartile_priors_types
from .utils import is_float


class PriorsError(Exception):
    pass


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


def calculate_priors_explicit(rule_row: pd.Series, nhanes_counts: dict):
    # 'discrete_nhanes_explicit', 'naive_0_nhanes_explicit', 'dependency_nhanes_explicit'
    value_ranges = extract_value_ranges(rule_row)
    nhanes_code = rule_row['code'].strip()
    counts = nhanes_counts[nhanes_code].reset_index(name='count').rename(columns={nhanes_code: 'value'})
    value_counts = {k: count_total(v, counts) for k, v in value_ranges.items()}
    total = sum(value_counts.values())
    frequencies = {k: v/total for k, v in value_counts.items()}
    return frequencies, value_ranges


def calculate_priors_quartile(rule_row: pd.Series, nhanes_counts: dict) -> dict:
    # 'discrete_nhanes_quartile', 'naive_0_nhanes_quartile',  'dependency_nhanes_quartile'
    
    priors = {}
    nhanes_code = rule_row['code'].strip()
    counts = nhanes_counts[nhanes_code].reset_index(name='count').rename(columns={nhanes_code: 'value'})
    if not pd.isna(rule_row['index1']):
        valid_ranges = sorted(extract_values(rule_row['index1']), key=sort_key)
        valid_ranges = collapse_ranges(valid_ranges)
    else:
        valid_ranges = [(0, counts['value'].dropna().max())]

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


def calculate_priors_nhanes(rule_row: pd.Series, nhanes_counts: dict) -> dict:
    try:
        if rule_row['Type'] in explicit_types:
            return calculate_priors_explicit(rule_row, nhanes_counts)
        elif rule_row['Type'] in quartile_priors_types | {'dependency_nhanes_quartile'}:
            return calculate_priors_quartile(rule_row, nhanes_counts)
    except KeyError as e:    # DEBUG, RAISE ERROR FURTHER 
        print(f"Row {rule_row.name+2} | KeyError occured during priors calculations from NHANES.")
        print(e)
        return 'MISSING IN NHANES', 'MISSING IN NHANES'
    except ZeroDivisionError as e:    # DEBUG REASE ERROR FURTHER 
        print(f"Row {rule_row.name+2} | ZeroDivisionError occured during priors calculations from NHANES.")
        print(e)
        priors = []
        for i in range(1, 6):
            if pd.isna(rule_row[f'value{i}']):
                break
            else:
                priors.append(rule_row[f'value{i}'])
        priors = {v: 1/len(priors) for v in priors}
        return priors, 'ERROR'