# paper_results/ - reproducible paper artifacts

These files let you verify the paper's numbers without running a 30-minute build.

## What's here

| File | What it is |
|---|---|
| `bayesianNetworkProto_paper.pickle` | The cycle-12 built network (7.6 MB). Loadable via `sn_bayes.utils.bayesInitialize`. |
| `bayesnet_config_linear_paper.json` | The linearised config the net was built from. |
| `results_paper.json` | Full five-core-metrics output (calibration, windows, joint fidelity, NHANES AUC, direction). |
| `objective_rr_comparison_paper.json` | Study-by-study direction accuracy test (369 studies). |
| `results_baseline.json` | Same metrics for the earlier cycle-9b build, for comparison. |
| `objective_rr_comparison_baseline.json` | Study-by-study test for cycle 9b. |
| `figures/polytope_schematic.pdf` | Figure 1: the feasible polytope diagram. |
| `figures/window_reduction.pdf` | Figure 2: window reduction across cycles. |

## Verify the paper's claims

Run the one-shot reproduction script:

```bash
python scripts/reproduce_paper.py
```

It loads the saved pickle + JSONs and prints:

1. Direction accuracy (paper: 97.6%)
2. Median window (paper: 0.016)
3. Joint fidelity (paper: 153/194 = 78.9%)
4. A direct query demonstrating the pancreatic-chain abstention (direction-reversed RR at low base rate)

Each line prints the reproduced number and whether it matches the paper's claim.

## Rebuild from scratch

If you want to verify the pickle itself was honestly produced, you can rebuild from the source spreadsheet:

```bash
python scripts/build_demo.py
```

This reads `data/Individual Relations.working.xlsx` and `data/preprocessed_nhanes.csv`, solves every CPT, and writes a fresh pickle. The full pipeline is roughly 30 minutes on a laptop for the 427-node demonstration. The saved pickle in this directory was built by the same `scripts/build_demo.py` invocation, applied to the same spreadsheet; rebuilding should yield direction accuracy, median window, and joint fidelity within sub-percent of the saved numbers (any drift is QP-solver non-determinism at the sub-second scale).

## Comparison across builds

`results_baseline.json` + `results_paper.json` show the progression reported in the paper:

| Metric | cycle 9b | cycle 12 |
|---|---|---|
| Direction accuracy | 362/369 (98.1%) | 360/369 (97.6%) |
| Median window W | 0.125 | 0.016 (87% reduction) |
| Joint fidelity within 5% | 137/178 (77.0%) | 153/194 (78.9%) |

The ~0.5 pp drop in direction accuracy from 9b to 12 is the cost of two connecting-study edges that introduced explaining-away; the ~87% window reduction is the benefit (the literature is now explicitly coherent across more edges).

## Why these files and not others

- Only cycles 9b and 12 are kept - the paper references these two only.
- The xlsx used to build cycle 12 is the current `data/Individual Relations.working.xlsx` (what a fresh build would use). Cycle 9b's xlsx differs by a few rows; if you want to verify cycle 9b separately, see the git history of the `bayesexpert` repo once public.
