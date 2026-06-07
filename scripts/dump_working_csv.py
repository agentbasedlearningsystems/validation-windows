#!/usr/bin/env python3
"""Produce a human-readable, topologically-sorted CSV dump of
data/Individual Relations.working.xlsx.

Layout rules:
- Rows grouped by output (all rows for one node are contiguous).
- Within a node: input/study rows first, definition row (input=NaN) last.
- Nodes in topological order: leaves (no parents) → ... → heads (diseases).
  Distal aggregators are placed immediately before the disease they feed,
  since they're parents of the disease in the DAG.
- Blank separator line between nodes for readability.
- First column is excel_row to let a reviewer jump back to the xlsx.
- Citation and comment columns preserved.

Output: paper/working_xlsx_dump.csv

Re-run after any xlsx change. Used for human review / debugging.
"""
import pandas as pd
import numpy as np
import csv
from pathlib import Path
from collections import defaultdict, deque

SRC = 'data/Individual Relations.working.xlsx'
OUT = 'paper/working_xlsx_dump.csv'

def main():
    df = pd.read_excel(SRC, sheet_name='all worksheet anom', header=0).reset_index(drop=True)
    df['excel_row'] = df.index + 2  # header is Excel row 1

    # Drop fully blank rows (ignore the excel_row col when deciding)
    content_cols = [c for c in df.columns if c != 'excel_row']
    df = df.dropna(axis='index', how='all', subset=content_cols).copy()

    useful_cols = [
        'excel_row', 'output', 'input', 'input values', 'Type',
        'Stat', 'Stat Value', 'Plus minus', 'RR Stat Value', 'RR plus minus',
        'control probability (needed if or,hr,smd, es, or wmd)',
        'control stdev (needed if ES or WMD)',
        'Sensitivity Stat Value', 'Sensitivity Plus Minus',
        'Specificity Stat Value', 'Specificity Plus Minus',
        'value1', 'index1', 'value2', 'index2',
        'value3', 'index3', 'value4', 'index4', 'value5', 'index5',
        'code', 'reverse',
        'study_n', 'study_design', 'study_population', 'verification_status',
        'citation', 'comment', 'comment2',
    ]
    useful_cols = [c for c in useful_cols if c in df.columns]

    # Build the parent/child graph. A node is anything that appears in
    # `output` OR as a non-JSON-string `input`. Filter out json-ish strings
    # that appear in some anomaly rows' `input` column (e.g. {"low":"4400"}).
    def is_real_node(name):
        if not isinstance(name, str):
            return False
        s = name.strip()
        return s and not s.startswith('{') and not s.endswith('}')

    node_parents = defaultdict(set)
    node_children = defaultdict(set)
    all_nodes = set()
    for _, row in df.iterrows():
        out = row.get('output')
        inp = row.get('input')
        if not is_real_node(out):
            continue
        all_nodes.add(out)
        if is_real_node(inp) and inp != out:
            node_parents[out].add(inp)
            node_children[inp].add(out)
            all_nodes.add(inp)

    # Kahn's topological sort - leaves (zero in-degree) first.
    in_degree = {n: len(node_parents[n]) for n in all_nodes}
    queue = deque(sorted(n for n in all_nodes if in_degree[n] == 0))
    topo_order = []
    visited = set()
    while queue:
        n = queue.popleft()
        if n in visited:
            continue
        visited.add(n)
        topo_order.append(n)
        for c in sorted(node_children[n]):
            in_degree[c] -= 1
            if in_degree[c] == 0:
                queue.append(c)
    # Any nodes left (shouldn't happen unless there's a cycle)
    for n in sorted(all_nodes - visited):
        topo_order.append(n)

    rank = {n: i for i, n in enumerate(topo_order)}

    def row_key(r):
        out = r.get('output')
        inp = r.get('input')
        topo_rank = rank.get(out, 10**9)
        is_def = 1 if (inp is None or (isinstance(inp, float) and pd.isna(inp))) else 0
        excel_row = r.get('excel_row', 10**9)
        return (topo_rank, is_def, excel_row)

    rows = sorted(df.to_dict('records'), key=row_key)

    final = []
    last_out = None
    for r in rows:
        out = r.get('output')
        if last_out is not None and out != last_out:
            final.append({c: '' for c in useful_cols})  # blank separator
        final.append({c: r.get(c, '') for c in useful_cols})
        last_out = out

    Path('paper').mkdir(exist_ok=True)
    with open(OUT, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(useful_cols)
        for r in final:
            w.writerow([r.get(c, '') for c in useful_cols])

    print(f"Wrote {OUT}  ({len(final)} rows including blank separators)")
    print(f"  nodes: {len(all_nodes)}  leaves (no parents): {sum(1 for n in all_nodes if not node_parents[n])}")
    print(f"  heads (no children): {sum(1 for n in all_nodes if not node_children[n])}")

if __name__ == '__main__':
    main()
