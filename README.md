# Validation Windows: reproduction code and data

Reproduction artifact for the paper "Validation Windows: Epistemic Uncertainty Produced by the Solver in a Literature-Derived Bayesian Network" (2nd Workshop on Epistemic Intelligence in Machine Learning @ ICML 2026).

Agent Based Learning Systems, San Luis Obispo, California, USA.

### 📄 Read the paper: [**validation_windows_eiml2026.pdf**](validation_windows_eiml2026.pdf)

The paper (EIML @ ICML 2026). Everything below — the window-reduction result and the full metric panel — is reported in it.

BayesExpert builds a Bayesian network from published epidemiological studies by solving a quadratic program over the polytope of conditional probability tables (CPTs) that satisfy every study's confidence interval, the law of total probability, and the CPT simplex bounds. For each solved edge the validation window `W` (between 0 and 1) is the width of that polytope along the study's axis. It is small when the literature and the population data agree on the edge, larger when they disagree, and largest when the solver has to move a study away from its own confidence interval to keep the whole network consistent.

## Quick reproduction (paper results, no build required)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 scripts/reproduce_paper.py
```

This loads the network the paper's results are computed on (`paper_results/bayesianNetworkProto_paper.pickle`) and reports the three headline results:

| Result | Value |
|---|---|
| Direction accuracy (literature vs query-RR) | 360/369 = 97.6% |
| Median validation window W̃ | 0.0156 |
| Joint fidelity within 5% of NHANES | 153/194 = 78.9% |

It also shows the abstention case on the low-base-rate pancreatic-cancer chain, where the polytope is direction-symmetric because the literature alone does not constrain the joint distribution.

The paper's headline result is an 87% drop in the median window as consistent literature edges are added. The starting network that drop is measured from — before those edges — is shipped as `bayesianNetworkProto_baseline.pickle` (median window about 0.125, versus 0.016 in the network above).

## Per-target predictive performance

Beyond the windows, the network is also a calibrated predictor. The current build (`bayesianNetworkProto_osteo.pickle`, 573 nodes — the build the paper's metric panel is computed on) discriminates held-out NHANES respondents, with each target's own NHANES code and its definitional-surrogate biomarkers excluded from the evidence. Reproduce with:

```bash
python3 scripts/observed_evidence_auc.py bayesianNetworkProto_osteo.pickle \
    bayesnet_config_osteo.json osteo 300 \
    --targets coronary_artery_disease depression diabetes insomnia sleep_apnea \
    stroke heart_attack copd cognitive_impairment hypertension osteoporosis cancer
```

Mean per-target AUC is **0.775** across 12 constructed disease targets (range 0.57 to 0.95) — several at or above the level of cohort-fitted clinical risk calculators (Framingham 10-year CVD and pooled ASCVD are about 0.71). The directly-observed `fall_history` outcome is excluded from the mean: it carries no constructed predictive structure, so it sits at its base rate.

### Full metric panel (current build, 573 nodes)

| measure | value |
|---|---|
| Mean per-target ROC AUC (12 constructed disease targets) | **0.775** (range 0.57–0.95) |
| Direction reproduced / washed out / reversed (445 cited edges) | 85.4% / 12.6% / 2.0% |
| Within a factor of two of the literature RR | 90% |
| Median validation window W̃ (mean) | 0.001 (0.086) |
| Calibration — ECE / MCE | 0.014 / 0.32 |
| Brier score | 0.070 |
| Prediction sharpness (variance) | 0.021 |
| Mean \|ρ-gap\| (network ρ vs NHANES ρ) | 0.091 |
| Sex-stratified AUC — male / female | 0.780 / 0.776 |
| Under-determined nodes (prediction reverts to prior) | 83 / 370 |
| Direction retained by chain length (1→5 hops) | 204/230 · 116/122 · 37/43 · 18/30 · 4/10 |

Direction accuracy and window here use a corrected reference-category metric, so they are not directly comparable to Table 1 above; the residual direction inversions concentrate in the rare-cancer, low-base-rate chains where the solver abstains.

### Per-target AUC

Each target's own NHANES code and its definitional-surrogate biomarkers are excluded from the evidence (see `validation_windows_eiml2026.pdf`, leak-guard appendix, for the per-target exclusion list).

| target | AUC | prevalence |
|---|---|---|
| Coronary artery disease | 0.95 | 0.057 |
| Depression | 0.89 | 0.063 |
| Diabetes | 0.85 | 0.070 |
| Insomnia | 0.85 | 0.160 |
| Sleep apnea | 0.81 | 0.037 |
| Stroke | 0.76 | 0.030 |
| Heart attack | 0.76 | 0.043 |
| COPD | 0.74 | 0.053 |
| Cognitive impairment | 0.73 | 0.160 |
| Hypertension | 0.70 | 0.280 |
| Osteoporosis | 0.69 | 0.070 |
| Cancer | 0.57 | 0.097 |
| **Mean** | **0.775** | |

Per-target AUC tracks whether the network's literature supplied informative risk-factor parents for that target.

## Continual improvement and community contributions

This network is maintained as a continually improving artifact, not a frozen snapshot:

- **[Continual Improvement Feed](IMPROVEMENT_LOG.md)** — a dated log of every net-improvement cycle, with the metric deltas (per-target AUC, validation window, direction accuracy, ρ-gap, calibration) and the literature added each cycle.
- **[Crowdsourcing](CROWDSOURCING.md)** — how anyone can submit a literature paper, manual change request, network-structure proposal, or question, plus the lists of adopted contributions.

## Repository contents

- `IMPROVEMENT_LOG.md`: the continual improvement feed (dated net-improvement cycles with metric deltas).
- `CROWDSOURCING.md`: the community-contribution process and the adopted-contribution lists.
- `validation_windows_eiml2026.pdf`: the paper.
- `data/relations.csv`: the literature network, one row per study or structural definition.
- `bayesianNetworkProto_osteo.pickle` + `bayesnet_config_osteo.json`: the current 573-node build the paper's metric panel (per-target AUC, calibration, direction, window) is computed on.
- `bayesianNetworkProto_paper.pickle`, `bayesianNetworkProto_baseline.pickle`: the network the paper's window result is computed on, and the starting network the 87% window reduction is measured from.
- `bayesnet_config_linear.json`: the compiled linear configuration.
- `paper_results/`: the committed result JSONs that `reproduce_paper.py` reads, plus the paper figures.
- `scripts/`: the build pipeline, the paper-grade tests, and `reproduce_paper.py`.
- `sn_bayes/`: the BayesExpert package (CSV parser, QP solver, NHANES preprocessor, query engine).
- `docs/bayesexpert_manual.md`: the construction manual, covering how to build a literature-grounded BayesExpert network in any domain.
- `data/preprocessed_nhanes.csv`: the NHANES reference cohort (about 116K respondents, 1999 to 2020) used for priors, joint fidelity, calibration, and the AUC test.

## Install

Python 3.10 or later. `pip install -r requirements.txt` (PyTorch, NumPy, SciPy, scikit-learn, pandas, protobuf). The QP solver runs on CPU.

## License

MIT License. See `LICENSE`.
