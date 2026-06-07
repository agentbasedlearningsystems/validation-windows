from typing import (
    Tuple,
    Union,
    Optional,
    List,
)

import pandas as pd

def _would_create_cycle(child, parent, parsed, spreadsheet_data):
    """Check if adding child→parent edge would create a cycle in the DAG.

    Walks ancestors of parent in the spreadsheet to see if child is reachable.
    """
    visited = set()
    stack = [parent]
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        if node == child:
            return True
        # Find node's parents from spreadsheet input column
        node_inputs = spreadsheet_data[
            (spreadsheet_data['output'] == node) & (~spreadsheet_data['input'].isna())
        ]['input'].tolist()
        stack.extend(node_inputs)
    return False


def _create_value_mask(nhanes_data, code, ranges):
    """Create boolean mask for NHANES rows matching any of the given ranges."""
    mask = pd.Series(False, index=nhanes_data.index)
    for r in ranges:
        if isinstance(r, (tuple, list)):
            mask = mask | ((nhanes_data[code] >= r[0]) & (nhanes_data[code] <= r[1]))
        else:
            mask = mask | (nhanes_data[code] == r)
    return mask


from .node_types import (
    prior_types,
    naive_types,
    dependency_types,
    stats_recalculation_types,
    explicit_types,
    quartile_priors_types,
)
from .anomaly import available_anomaly_detectors, parse_anomaly_data
from .priors import (
    PriorsError,
    extract_priors,
    calculate_priors_nhanes,
)
from .statistics import (
    convert_ss_to_rr,
    calculate_sensitivity_specificity,
    extract_row_stats,
)
from .distal import(
    create_masks_priors,
    calculate_final_prior,
    calculate_rr_for_distal_var,
)


def create_outvars(row: pd.Series, nhanes_counts: pd.DataFrame) -> Tuple[Union[list, dict], Optional[dict]]:
    if row['Type'] in ['all_of', 'any_of', 'avg', 'if_then_else', 'dependency_distal']:
        outvars = []
        for i in range(1, 6):
            if pd.isna(row[f'value{i}']):
                break
            outvars.append(row[f'value{i}'].strip())
        return outvars, None

    elif row['Type'] in ['dependency_priors', 'discrete_priors']:
        outvars = extract_priors(row)
        return outvars, None

    elif row['Type'] in prior_types | {'dependency_nhanes_explicit', 'dependency_nhanes_quartile', 'dependency_nhanes_explicit_average'}:
        return calculate_priors_nhanes(row, nhanes_counts)


def get_priors_any_all(sub_dependencies: dict, nhanes_data: pd.DataFrame) -> dict:
    # Calculate priors for any_of and all_of from nhanes
    priors_to_use = []
    value_mask, nonna_mask, prior = create_masks_priors(nhanes_data, sub_dependencies, is_first=False)
    if value_mask is not None:
        priors_to_use.append((value_mask & nonna_mask).sum() / nonna_mask.sum())

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
        nhanes_counts: dict,
        raw_nhanes: pd.DataFrame,
        distal_to: Optional[str] = None,
        parsed_dependencies: dict = {}
) -> dict:
    #print('-'*100)
    #print('ROW', row.name+2, '|', 'PARSING FOR', row['output'], row['Type'], '|', 'DISTAL TO', distal_to)
    dependency = {}

    if not pd.isna(row['code']):
        dependency['CODE'] = row['code']
    
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
        # naive_0 disabled - always treat as discrete prior
        # TODO: re-enable after fixing interaction with cancer node QP solver
        if True:
            # Pure discrete prior - no parent, early return
            dependency['PRIORS'] = outvars
            parsed_dependencies[row['output']] = dependency
            return parsed_dependencies

        # Naive_0 with parent: verify DAG
        parent_name = row['input']
        if _would_create_cycle(row['output'], parent_name, parsed_dependencies, data):
            # Cycle detected - fall back to discrete prior
            dependency['PRIORS'] = outvars
            parsed_dependencies[row['output']] = dependency
            print(f"WARNING: naive_0 {row['output']} ← {parent_name} would create cycle, keeping as discrete")
            return parsed_dependencies

        # Naive_0 with parent: save priors as OUTVARS
        dependency['PRIORS'] = outvars
        dependency['OUTVARS'] = outvars
        # Change TYPE so bayesnet_creation processes it as dependency
        dependency['TYPE'] = row['Type'].replace('naive_0_', 'dependency_')

        # Compute P(child|parent) from NHANES joint counts
        parent_def_rows = data[
            (data['output'] == parent_name) & (data['Type'].isin(dependency_types | prior_types))
        ]
        if parent_def_rows.empty:
            # Parent not defined - keep as discrete
            dependency['TYPE'] = row['Type']
            dependency.pop('OUTVARS', None)
            parsed_dependencies[row['output']] = dependency
            return parsed_dependencies

        parent_def_row = parent_def_rows.iloc[0]
        if parent_name not in parsed_dependencies:
            parsed_dependencies = parse_dependency(
                row=parent_def_row, data=data, nhanes_counts=nhanes_counts,
                raw_nhanes=raw_nhanes, parsed_dependencies=parsed_dependencies,
            )

        parent_dep = parsed_dependencies[parent_name]
        parent_code = parent_dep.get('CODE')
        parent_vr = parent_dep.get('value_ranges')
        child_code = dependency.get('CODE')

        if not (parent_code and parent_vr and child_code and value_ranges
                and child_code in raw_nhanes.columns
                and parent_code in raw_nhanes.columns):
            # Can't compute from NHANES - keep as discrete
            dependency['TYPE'] = row['Type']
            dependency.pop('OUTVARS', None)
            parsed_dependencies[row['output']] = dependency
            return parsed_dependencies

        both_valid = raw_nhanes[child_code].notna() & raw_nhanes[parent_code].notna()
        if both_valid.sum() < 30:
            # Insufficient NHANES overlap - keep as discrete
            dependency['TYPE'] = row['Type']
            dependency.pop('OUTVARS', None)
            parsed_dependencies[row['output']] = dependency
            return parsed_dependencies

        child_first_val = list(outvars.keys())[0]
        child_marginal = outvars[child_first_val]
        child_mask = _create_value_mask(raw_nhanes, child_code, value_ranges[child_first_val])

        invar_entries = []
        for parent_val, parent_ranges in parent_vr.items():
            parent_mask = _create_value_mask(raw_nhanes, parent_code, parent_ranges)
            n_parent = (parent_mask & both_valid).sum()

            if n_parent < 10:
                rr = 1.0
            else:
                n_joint = (child_mask & parent_mask & both_valid).sum()
                conditional = n_joint / n_parent
                rr = conditional / child_marginal if child_marginal > 0 else 1.0

            if rr <= 0:
                rr = 0.01

            invar_entries.append({
                'VALUE': [parent_val],
                'STATS': {'relative_risk': float(rr), 'plus_minus': 0.1, 'ci': 95}
            })

        dependency['INVARS'] = {parent_name: invar_entries}
        dependency['INPUTS'] = {parent_name: parent_dep}
        parsed_dependencies[row['output']] = dependency
        return parsed_dependencies

    # PARSE INPUT DEPENDENCIES AND EXTRACT DATA FOR STATS CALCULATION
    as_output = data[(data['output'] == row['output']) & (~data['input'].isna()) & (~data['input values'].isna())]
    if distal_to:
        #print('DISTAL NODE', row['output'], distal_to)
        #print(as_output['Type'].values)
        # dependency_distal is transparent to the distal chain - preserve distal_to
        if row['Type'] != 'dependency_distal' and distal_to not in as_output['Type'].values:
            distal_to = None
    
    sub_dependencies = {}
    stats_to_recalculate = {}    # For sensitivity and specificity rows in distal cases
    if row['Type'] == 'avg':
        invars = []
    else:
        invars = {}
        
    for idx, inp_row in as_output.iterrows():
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
            if distal_to and inp_row['Type'] in {distal_to, 'distal', 'equivalent_distal'}:
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

        if row['Type'] == 'avg':
            invars.append(inp_row['input'])
            continue

        var_code = None
        if not pd.isna(input_var_definition_row['code']):
            var_code = input_var_definition_row['code']

        # EXTRACT INPUT VALUE
        if input_var_definition_row['Type'] in quartile_priors_types:
            #print(f'ROW {idx+2}', '|', 'OUTPUT', inp_row['output'], 'INPUT', inp_row['input'], 'INPUT TYPE', input_var_definition_row['Type'])
            parsed_input_values = [v.strip() for v in inp_row['input values'].split(',')]
            parsed_outvars = sub_dependencies[inp_row['input']]['PRIORS'].keys()
            input_values = find_quartile_name(
                parsed_input_values,
                parsed_outvars,
            )
            if not input_values:
                msg = f"Row {inp_row.name+2} | Input values '{parsed_input_values}' are not consistent with available input values: {parsed_outvars}."
                raise ValueError(msg)
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
                # For dependency_distal nodes: delegate distal RR computation
                # to the equivalent_distal parent (the node it wraps)
                input_dep = sub_dependencies[inp_row['input']]
                if input_dep.get('TYPE') == 'dependency_distal' and 'EQUIVALENT_DISTAL' in input_dep:
                    eq_name = input_dep['EQUIVALENT_DISTAL']
                    delegate = input_dep['INPUTS'][eq_name]
                else:
                    delegate = input_dep

                # For dependency_distal delegation: the wrapper's output names differ
                # from the delegate's (e.g., gate_metabolic_yes vs original_gate_metabolic_yes).
                # Compare by suffix (_yes/_no) or position, not exact name.
                delegate_first = delegate['OUTVARS'][0]
                input_first = input_values[0]
                if input_dep.get('TYPE') == 'dependency_distal':
                    # Both should end in _yes or _no - compare the suffix
                    reverse_rr = (input_first.endswith('_no') != delegate_first.endswith('_no'))
                else:
                    reverse_rr = (input_first != delegate_first)

                stats, value_mask, nonna_mask, prior = calculate_rr_for_distal_var(
                    sub_dependencies=delegate,
                    nhanes_data=raw_nhanes,
                    reverse_rr=reverse_rr,
                    output_var=row['output'],    # DEBUG
                    input_var=inp_row['input'],    # DEBUG
                )
                #print(f"ROW {idx+2} | CALCULATED DISTAL STATS |", 'OUTPUT', inp_row['output'], 'INPUT', inp_row['input'], '|', 'STATS', stats)
                #else:    # DEBUG
                #    print(inp_row['output'], inp_row['input'], 'DISTAL STATS CALCULATION PROBLEM. INPUT SUBDEPENDENCY IS NOT PARSED')
                #    stats = {'relative_risk': 1.0, 'BUG IN DATA': True}
            
        else:
            #input_values = tuple(input_values)
            if row['Type'] in ['all_of', 'any_of']:
                if not pd.isna(inp_row['RR Stat Value']):     # Distal case
                    stats = extract_row_stats(inp_row)
                else:
                    stats = {}
                
            elif inp_row['Type'] in stats_recalculation_types:
                # For equivalent_distal inside dependency_distal: inherit priors
                # from the equivalent parent and convert outvars list → dict
                if inp_row['Type'] == 'equivalent_distal' and row['Type'] == 'dependency_distal':
                    dependency['EQUIVALENT_DISTAL'] = inp_row['input']
                    eq_dep = sub_dependencies[inp_row['input']]
                    if eq_dep['TYPE'] in {'all_of', 'any_of'}:
                        eq_priors = get_priors_any_all(eq_dep, raw_nhanes)
                    elif 'OUTVARS' in eq_dep and isinstance(eq_dep['OUTVARS'], dict):
                        eq_priors = eq_dep['OUTVARS']
                    else:
                        eq_priors = eq_dep.get('PRIORS', {})
                    # Map equivalent parent priors to our output names by position
                    eq_values = list(eq_priors.values())
                    outvars = {outvars[i]: eq_values[i] if i < len(eq_values) else 0.5
                               for i in range(len(outvars))}

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
        skip_validation: bool = False,
):
    """Create bayesnet config from spreadsheet and wide-format NHANES data.

    nhanes should be a wide-format DataFrame where each column is a NHANES
    variable code (e.g. BPQ020, DMDEDUC2) and rows are respondents.

    Validates the DataFrame via sn_bayes.df_checks.df_validate before
    parsing - catches orphan outputs, self-references, etc. that would
    otherwise be silently dropped. Pass skip_validation=True to bypass
    (only do this if you've already validated upstream).
    """
    if not skip_validation:
        try:
            from sn_bayes.df_checks import df_validate
            issues = df_validate(spreadsheet_data)
            fails = [i for i in issues if i['severity'] == 'FAIL']
            if fails:
                msg = (f"prepare_config: df_validate found {len(fails)} FAIL "
                       f"issue(s) - pass skip_validation=True to bypass:\n"
                       + '\n'.join(f"  - [{i['rule']}] {i['message']}" for i in fails))
                raise ValueError(msg)
        except ImportError:
            pass  # df_checks module missing; skip rather than break

    config = {}
    config['anomaly_data'] = parse_anomaly_data(spreadsheet_data)

    nhanes_counts = {col: nhanes[col].value_counts().sort_index() for col in nhanes.columns}

    dependecy_output_var_rows = spreadsheet_data[spreadsheet_data['Type'].isin(dependency_types)]
    dependency_data = {}
    parsed_dependencies = {}
    for idx, row in dependecy_output_var_rows.iterrows():

        as_input_rows = spreadsheet_data[spreadsheet_data['input'] == row['output']]
        if as_input_rows.shape[0] > 0:    # Parse recursively output nodes that are not input to any
            continue

        parsed_dependencies = parse_dependency(
            row=row,
            data=spreadsheet_data,
            nhanes_counts=nhanes_counts,
            raw_nhanes=nhanes,
            parsed_dependencies=parsed_dependencies
        )
        dependency_data[row['output']] = parsed_dependencies[row['output']]

    config['dependency_data'] = dependency_data
    return config


# FOR LINEAR VERSION
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


def linearize_config(bayesnet_config: dict) -> dict:
    linear = {}

    for variable_name, dependency_data in bayesnet_config['dependency_data'].items():
        linear = add_subnodes(dependency_data['INPUTS'], linear)
        dependency_data.pop('INPUTS')
        linear[variable_name] = dependency_data
        
    linear_conf = {'anomaly_data': bayesnet_config['anomaly_data'], 'dependency_data': linear}
    return linear_conf