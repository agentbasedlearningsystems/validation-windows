# `data/` directory

## Contents

- `Individual Relations.working.xlsx` - the demonstration network's literature spreadsheet. 427 nodes, 166 study rows (72% meta-analyses, pooled ~4.17×10⁸ subjects across literature).

## NHANES (not included)

The builder requires a preprocessed NHANES reference CSV as the population-frequency source. The file is ~85 MB and is not tracked in the repository. To regenerate it:

1. Download NHANES 2017-March 2020 (pre-pandemic combined cycle) from https://wwwn.cdc.gov/nchs/nhanes/. You need Demographic, Examination, Laboratory, Questionnaire, and Dietary files for the cycle.
2. Run `sn_bayes/config_creation/nhanes_preprocess.py` with the raw XPT files in a source directory. The script produces `preprocessed_nhanes.csv` with one row per respondent (~116K rows).
3. Place the result at `data/preprocessed_nhanes.csv`.

The spreadsheet references NHANES variable codes (e.g., `BMXBMI` for body mass index) in its `code` column; the preprocessor keeps exactly those columns.

## Build artifacts

Running the builder produces the following files at the repository root (they are `.gitignore`'d):

- `bayesianNetworkProto.pickle` - the assembled Bayesian network in protobuf format (loadable via `sn_bayes.utils.bayesInitialize`).
- `bayesnet_config_linear.json` - the linearized configuration (parent/child structure, distal-computed stats, etc).
- `builds/cpt_cache/<label>/` - per-node CPT cache (safely deletable; regenerated on rebuild).

## The spreadsheet in human-readable form

`scripts/dump_working_csv.py` produces a topologically-sorted CSV view (leaves first, disease heads last, rows grouped by output). Handy when you want to scan the net without opening Excel.
