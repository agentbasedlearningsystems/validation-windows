"""In-memory DataFrame equivalents of scripts/data_checks.py spreadsheet checks.

Use when you've patched a DataFrame in-memory (e.g., variant scripts that
append connecting-study rows) and want to validate before calling
prepare_config - bypassing the on-disk xlsx.

Returns a list of issue dicts: {severity: 'FAIL'|'WARN', rule, message}.
Caller decides whether to stop or proceed on each severity level.
"""
import pandas as pd


def df_validate(df, severity_to_raise=None):
    """Run the in-memory equivalent of scripts/data_checks.py checks on df.

    Args:
      df: pandas DataFrame as returned by load_df() (i.e., post-strip,
          post-fixme-blank, with index1..5 stringified).
      severity_to_raise: if 'FAIL', raise ValueError on any FAIL; if 'WARN',
          raise on any FAIL or WARN; if None, just return the issue list.

    Returns:
      list of {'severity', 'rule', 'message', 'rows': [df_index, ...]}
    """
    issues = []

    def report(severity, rule, message, rows=None):
        issues.append({'severity': severity, 'rule': rule,
                        'message': message, 'rows': rows or []})

    all_outputs = set(df['output'].dropna().unique())
    all_inputs = set(df['input'].dropna().unique())

    # Rule 1: orphan inputs (input names not defined as any output).
    # Mirrors data_checks.py:366-377.
    orphan_inputs = []
    for inp in all_inputs - all_outputs:
        uses = df[(df['input'] == inp) & (~df['Type'].isin(['anomaly']) if 'Type' in df else True)]
        if len(uses) > 0:
            orphan_inputs.append(inp)
    if orphan_inputs:
        report('WARN', 'orphan_inputs',
               f'{len(orphan_inputs)} input names not defined as outputs: {orphan_inputs[:10]}',
               rows=[])

    # Rule 2: orphan outputs (NEW - not in data_checks.py).
    # A "definition row" is a row whose Type matches a known node-type from
    # sn_bayes.config_creation.node_types (any_of, all_of, dependency_*,
    # discrete_*, naive_*, dependency_distal, etc.). Connecting-study/edge
    # rows have Type set to a disease tag (e.g. 'diabetes'), which is NOT
    # in the node_types registry. Every output used as `output:` must have
    # at least one row whose Type is in the registry - else prepare_config
    # silently drops the row. (This was the overnight Phase 3 no-op bug.)
    try:
        from sn_bayes.config_creation.node_types import (
            prior_types, dependency_types, stats_recalculation_types
        )
        defn_type_set = (set(prior_types) | set(dependency_types)
                         | set(stats_recalculation_types))
    except Exception:
        defn_type_set = set()

    if 'Type' in df.columns and defn_type_set:
        defn_rows = df[df['output'].notna() & df['Type'].isin(defn_type_set)]
        outputs_with_definition = set(defn_rows['output'].dropna().unique())
        # Anomaly nodes are defined elsewhere (anomaly_data block), not in the
        # node_types registry; exclude rows whose Type is 'anomaly'.
        anomaly_outputs = set(df[df['Type'] == 'anomaly']['output'].dropna().unique()) if 'Type' in df else set()
        orphan_outputs = sorted(all_outputs - outputs_with_definition - anomaly_outputs)
        if orphan_outputs:
            report('FAIL', 'orphan_outputs',
                   f'{len(orphan_outputs)} output names used but no row '
                   f'has Type in node_types registry for them: '
                   f'{orphan_outputs[:10]}',
                   rows=[])

    # Rule 3: self-references. Row where output == input.
    self_refs = df[(df['output'].notna()) & (df['input'].notna())
                   & (df['output'] == df['input'])]
    if len(self_refs) > 0:
        report('FAIL', 'self_references',
               f'{len(self_refs)} rows where output == input: '
               f'{self_refs["output"].tolist()[:10]}',
               rows=self_refs.index.tolist())

    # Rule 4: gate study rows missing disease tag.
    # Mirror of data_checks.py:484. A row with input set + RR set + Type=disease-tag
    # should match its parent gate's disease.
    # Approximation: for any row with input not null AND RR Stat Value set,
    # the Type column should be a known disease tag (not blank, not 'any_of'/etc.).
    if 'RR Stat Value' in df.columns:
        gate_inputs = df[(df['input'].notna()) & (df['RR Stat Value'].notna())]
        non_disease_types = {'any_of', 'all_of', 'avg', 'if_then_else',
                             'distal', 'dependency_distal', 'discrete_priors'}
        missing_tag = gate_inputs[
            gate_inputs['Type'].isna()
            | gate_inputs['Type'].isin(non_disease_types)
        ]
        if len(missing_tag) > 0:
            report('WARN', 'missing_disease_tag',
                   f'{len(missing_tag)} input rows with RR but no/wrong disease tag',
                   rows=missing_tag.index.tolist()[:20])

    # Rule 5: input value missing.
    if 'input values' in df.columns:
        missing_iv = df[(df['input'].notna()) & (df['input values'].isna())]
        if len(missing_iv) > 0:
            report('WARN', 'missing_input_values',
                   f'{len(missing_iv)} input rows missing input values column',
                   rows=missing_iv.index.tolist()[:20])

    # Rule 6: duplicate (output, input, input_values) connecting-study rows.
    # Catches accidental double-appends.
    if all(c in df.columns for c in ('output', 'input', 'input values', 'RR Stat Value')):
        with_rr = df[df['RR Stat Value'].notna()]
        dups = with_rr[with_rr.duplicated(subset=['output', 'input', 'input values'], keep=False)]
        if len(dups) > 0:
            sample = dups[['output', 'input', 'input values']].head(5).to_dict('records')
            report('WARN', 'duplicate_connecting_rows',
                   f'{len(dups)} rows are duplicates of (output, input, input values)',
                   rows=dups.index.tolist()[:20])

    # Severity-driven raise
    fails = [i for i in issues if i['severity'] == 'FAIL']
    warns = [i for i in issues if i['severity'] == 'WARN']
    if severity_to_raise == 'FAIL' and fails:
        raise ValueError(f"df_validate found {len(fails)} FAIL issue(s):\n"
                          + '\n'.join(f"  - [{i['rule']}] {i['message']}" for i in fails))
    if severity_to_raise == 'WARN' and (fails or warns):
        all_ = fails + warns
        raise ValueError(f"df_validate found {len(all_)} issue(s):\n"
                          + '\n'.join(f"  - [{i['severity']}/{i['rule']}] {i['message']}" for i in all_))

    return issues


def print_validation_report(issues, header='df_validate'):
    """Pretty-print issues from df_validate."""
    if not issues:
        print(f"  [{header}] all checks passed")
        return
    fails = [i for i in issues if i['severity'] == 'FAIL']
    warns = [i for i in issues if i['severity'] == 'WARN']
    print(f"  [{header}] {len(fails)} FAIL, {len(warns)} WARN")
    for i in issues:
        print(f"    [{i['severity']}] {i['rule']}: {i['message']}")
