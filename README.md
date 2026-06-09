# Validation Windows: reproduction code and data

Reproduction artifact for the paper "Validation Windows: Epistemic Uncertainty Produced by the Solver in a Literature-Derived Bayesian Network" (2nd Workshop on Epistemic Intelligence in Machine Learning @ ICML 2026).

Authors: Deborah Vakas Duong, Igor Yi. Agent Based Learning Systems, San Luis Obispo, California, USA.

### 📄 Read the paper: [**validation_windows_eiml2026.pdf**](validation_windows_eiml2026.pdf)

The camera-ready paper (EIML @ ICML 2026). Everything below — the window-reduction result and the full metric panel — is reported in it.

BayesExpert builds a Bayesian network from published epidemiological studies by solving a quadratic program over the polytope of conditional probability tables (CPTs) that satisfy every study's confidence interval, the law of total probability, and the CPT simplex bounds. For each solved edge the validation window `W` (between 0 and 1) is the width of that polytope along the study's axis. It is small when the literature and the population data agree on the edge, larger when they disagree, and largest when the solver has to move a study away from its own confidence interval to keep the whole network consistent.

## Quick reproduction (paper results, no build required)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 scripts/reproduce_paper.py
```

This loads the frozen paper-state network (`paper_results/bayesianNetworkProto_cycle12_no_df.pickle`) and reports the three headline results:

| Result | Reproduced | Paper |
|---|---|---|
| Direction accuracy (literature vs query-RR) | 360/369 = 97.6% | 97.6% |
| Median validation window W | 0.0156 | 0.016 |
| Joint fidelity within 5% of NHANES | 153/194 = 78.9% | 78.9% |

It also shows the abstention case on the low-base-rate pancreatic-cancer chain, where the polytope is direction-symmetric because the literature alone does not constrain the joint distribution.

The "before" network for the paper's 87% window-reduction result is shipped as `bayesianNetworkProto_cycle9b_no_df.pickle` (median W about 0.125).

## Re-derive from the CSV (optional, about 30 to 40 minutes)

```bash
python3 scripts/build_demo.py          # rebuilds the network from data/relations.csv
python3 scripts/data_checks.py spreadsheet   # pre-build CSV integrity checks
```

## Per-target predictive performance

Beyond the windows, the network is also a calibrated predictor. The improved network (`bayesianNetworkProto_improved.pickle`, 574 nodes — the build the paper's metric panel is computed on) discriminates held-out NHANES respondents, with each target's own NHANES code and its definitional-surrogate biomarkers excluded from the evidence. Reproduce with:

```bash
python3 scripts/observed_evidence_auc.py bayesianNetworkProto_improved.pickle \
    bayesnet_config_linear.json improved 300 \
    --targets heart_attack asthma malnutrition insomnia fall_history cancer \
    cognitive_impairment copd coronary_artery_disease diabetes hypertension \
    sleep_apnea sleep_disorder stroke depression osteoporosis
```

Mean per-target AUC is **0.716** across 16 clinical-event targets (range 0.48 to 0.94) — several at or above the level of cohort-fitted clinical risk calculators (Framingham 10-year CVD and pooled ASCVD are about 0.71).

### Full metric panel (improved network, 574 nodes)

| measure | value |
|---|---|
| Mean per-target ROC AUC (16 clinical events) | **0.716** (range 0.48–0.94) |
| Direction accuracy (query-RR sign vs literature RR) | 91.1% |
| Within a factor of two of the literature RR | 84.6% |
| Median validation window W̃ (mean) | 0.001 (0.086) |
| Calibration — ECE / MCE | 0.022 / 0.29 |
| Brier score | 0.092 |
| Prediction sharpness (variance) | 0.027 |
| Mean \|ρ-gap\| (network ρ vs NHANES ρ) | 0.097 |
| Sex-stratified AUC — male / female | 0.733 / 0.695 |
| Under-determined nodes (prediction reverts to prior) | 77 / 356 |
| Direction accuracy by chain length (1→5 hops) | 188/216 · 110/122 · 40/47 · 18/31 · 4/10 |

Direction accuracy and window here use a corrected reference-category metric, so they are not directly comparable to Table 1 above; the residual direction inversions concentrate in the rare-cancer, low-base-rate chains where the solver abstains.

### Per-target AUC

Each target's own NHANES code and its definitional-surrogate biomarkers are excluded from the evidence (see `validation_windows_eiml2026.pdf` App. F for the per-target exclusion list).

| target | AUC | prevalence |
|---|---|---|
| Coronary artery disease | 0.94 | 0.057 |
| Sleep apnea | 0.82 | 0.037 |
| Depression | 0.82 | 0.063 |
| Heart attack | 0.82 | 0.043 |
| Insomnia | 0.80 | 0.16 |
| Diabetes | 0.78 | 0.07 |
| Stroke | 0.76 | 0.03 |
| COPD | 0.73 | 0.053 |
| Cognitive impairment | 0.72 | 0.16 |
| Hypertension | 0.72 | 0.28 |
| Sleep disorder | 0.68 | 0.09 |
| Cancer | 0.63 | 0.097 |
| Malnutrition | 0.63 | 0.05 |
| Osteoporosis | 0.57 | 0.07 |
| Asthma | 0.56 | 0.50 |
| Fall history | 0.48 | 0.30 |
| **Mean** | **0.716** | |

Per-target AUC tracks whether the network's literature supplied informative risk-factor parents for that target.

## Continual improvement and community contributions

This network is maintained as a continually improving artifact, not a frozen snapshot:

- **[Continual Improvement Feed](IMPROVEMENT_LOG.md)** — a dated log of every net-improvement cycle, with the metric deltas (per-target AUC, validation window, direction accuracy, ρ-gap, calibration) and the literature added each cycle.
- **[Crowdsourcing](CROWDSOURCING.md)** — how anyone can submit a literature paper, manual change request, network-structure proposal, or question, plus the lists of adopted contributions.

## Repository contents

- `IMPROVEMENT_LOG.md`: the continual improvement feed (dated net-improvement cycles with metric deltas).
- `CROWDSOURCING.md`: the community-contribution process and the adopted-contribution lists.
- `validation_windows_eiml2026.pdf`: the camera-ready paper.
- `data/relations.csv`: the literature network, one row per study or structural definition.
- `bayesianNetworkProto_cycle12_no_df.pickle`, `bayesianNetworkProto_cycle9b_no_df.pickle`: the paper-state network and its "before" point.
- `bayesianNetworkProto_improved.pickle`: the improved network (the full metric panel / per-target AUC).
- `bayesnet_config_linear.json`: the compiled linear configuration.
- `paper_results/`: the committed cycle-12 result JSONs that `reproduce_paper.py` reads, plus the paper figures.
- `scripts/`: the build pipeline, the paper-grade tests, and `reproduce_paper.py`.
- `sn_bayes/`: the BayesExpert package (CSV parser, QP solver, NHANES preprocessor, query engine).
- `docs/bayesexpert_manual.md`: the construction manual, covering how to build a literature-grounded BayesExpert network in any domain.
- `data/preprocessed_nhanes.csv`: the NHANES reference cohort (about 116K respondents, 1999 to 2020) used for priors, joint fidelity, calibration, and the AUC test.

## Install

Python 3.10 or later. `pip install -r requirements.txt` (PyTorch, NumPy, SciPy, scikit-learn, pandas, protobuf). The QP solver runs on CPU.

## License

MIT License. See `LICENSE`.
