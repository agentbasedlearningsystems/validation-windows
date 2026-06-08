# Crowdsourcing — community contributions to the BayesExpert network

Anyone can submit a literature paper, manual change request, network structure proposal, or question.

Submissions do not collect an email or any contact information from submitters. Submitters do not receive a notification of receipt or of outcome. If a submitter wants to know whether their suggestion was adopted, they revisit this page later and check the four lists below — adopted entries appear there with date and (optional, submitter-chosen) attribution. Failed or pending submissions stay private and are never exposed.

## Submit

➡️ **[Submit a contribution](https://forms.gle/T21xNX83qEvVJ9Wp8)** — public form (anonymised; optional attribution).

Choose one of four categories on the form:

- **Literature request** — propose a new study row (a meta-analysis or large systematic review) supporting a new `output ← input` relationship. Required fields: the output node, the input node, the input state, the effect-size type and value with 95% CI, and the PubMed URL.
- **Network change request** — propose a structural change (new node, new aggregator, K-cap reorganisation, direction reversal, deletion).
- **Manual change request** — propose a rule change to the construction manual.
- **Question** — ask anything about the network or the construction process.

## How submissions flow

```
  Public form
    ↓
  Private staging
    ↓
  Citation + safety checks       (is the PMID real? does the topic match?
                                  does the proposed edge cycle? K-cap OK?)
    ↓
  Improvement assessment         (test the addition: build, run the 5-core
                                  metric panel, compare before/after, judge
                                  against the manual's principled-change rule)
    ↓
  Repo-owner review with         (the system recommends "merge" with the
  recommendation + test deltas    test results, or "don't merge" with the
                                  reason; the repo owner decides)
    ↓
  Public PR  (only on merge)
```

The improvement-assessment stage reads `docs/bayesexpert_manual.md` to decide whether the proposed addition would improve the net. It runs a focused test build (the target node's subnet), measures direction match, within-50% literature agreement, calibration, joint fidelity, and per-target AUC — then writes a recommendation. The repo owner sees the test deltas alongside the recommendation and chooses to merge or reject. A submission cannot reach this page without passing all the prior stages AND being merged by the repo owner.

The repo owner is **emailed automatically** after each submission completes the pipeline, with the submission's content, the gate results, the quorum verdict, the test deltas, and the recommendation. Approve / reject is one click from the email.

---

# Adopted contributions

Sections populate only when the repo owner approves a submission. Every entry traces back to the merging PR.

## FAQ

### 2026-06-08 — You have a high general AUC, and I was wondering, what about Bayes Expert can you attribute this to

**Question:**

You have a high general AUC, and I was wondering, what about Bayes Expert can you attribute this to

---

**Answer:**

Great question! BayesExpert's strong general AUC performance is likely attributable to a combination of several core design choices:

1. **Principled probabilistic structure.** BayesExpert is built on a Bayesian network (directed acyclic graph) that explicitly encodes conditional independence relationships among variables. This avoids overfitting to spurious correlations that can inflate or deflate discrimination metrics in purely frequentist models.

2. **Expert-curated priors and relations.** The edges and conditional probability tables (CPTs) encoded in `data/relations.csv` are drawn from domain literature and expert knowledge. This inductive bias means the model generalises well even when training data are sparse — a known driver of high AUC in knowledge-rich domains.

3. **Calibrated posterior updates.** Because BayesExpert reasons via exact or approximate Bayesian inference, its output probabilities are well-calibrated. A well-calibrated classifier tends to rank positive cases above negative cases consistently, which is precisely what AUC measures.

4. **Crowdsourced iterative improvement.** The public improvement pipeline (the one you're using right now!) continuously integrates new literature-supported edges and corrects manual errors. This keeps the network aligned with current evidence, preventing model drift that would erode discriminative performance over time.

5. **Modular subnet architecture.** By decomposing the joint distribution into interpretable subnets, BayesExpert avoids the "monolithic model" pitfall where a single misspecified region degrades global performance. Each subnet can be validated and refined independently.

If you're interested in a deeper dive, the `docs/bayesexpert_manual.md` file contains technical details on inference methods and the `IMPROVEMENT_LOG.md` tracks network changes over time — both can shed light on specific structural decisions that have historically improved AUC.

Feel free to follow up with a more specific question (e.g., about a particular subnet, variable, or evaluation dataset) and we'll do our best to answer!

### 2026-05-19 — Question submission

**Question:**

I am interested in the concept of Kuhnian Scientific Revolutions, where the data starts to not fit the theory, and then another theory makes all the data fit nicely again.  I am wondering if the concept of the Validation Window is a good measure of the data not fitting the theory, because a Bayesian Network is a theory of cause, and then another theory , expressed in structural change, could fit the data better.  To make this clear to me, could you give me an example Bayes Expert network that illustrates a Kuhnian Revolution, for example, Copernicus' new theory fitting the data better than Ptolemaic theory?

---

**Answer:**

### Your Question, Restated

You are asking two related things:

1. **Conceptually:** Is the BayesExpert *Validation Window* a good analogue for the Kuhnian notion of "data no longer fitting the theory," with a structural change to the network representing a Kuhnian revolution?
2. **Concretely:** Can you provide a worked example — say, Ptolemaic astronomy vs. Copernican astronomy — that illustrates this within a Bayesian Network?
---
### Part 1 — The Validation Window as a Kuhnian Anomaly Detector

This is a genuinely insightful conceptual connection. Here is how the analogy maps:

| Kuhnian concept | BayesExpert concept |
|---|---|
| Reigning paradigm | Current network *structure* (DAG + CPTs) |
| Empirical anomaly | Observed data that falls outside the **Validation Window** |
| Accumulating anomalies / crisis | Sustained or widening Validation Window violations across multiple nodes |
| Scientific revolution | **Structural change** to the DAG (adding, deleting, or reversing edges) that restores fit |
| New paradigm | Revised network whose Validation Window violations disappear or shrink |

So yes — the Validation Window is a reasonable operational stand-in for Kuhnian anomaly accumulation. A Bayesian Network encodes a *causal theory of the domain*. When the live data stream begins to consistently fall outside the predicted posterior intervals, that is the quantitative signal that the structural assumptions of the network are wrong, not merely that the CPT parameters need updating. Parameter updates are analogous to Kuhnian *normal science* (tweaking epicycles); structural changes are the revolution.

One important nuance: Kuhn emphasised that anomalies alone do not force a revolution — a *rival* paradigm must also be available. In BayesExpert terms, you need a *candidate new structure* that demonstrably produces smaller or eliminated Validation Window violations on the same data. This is the role of structure-learning algorithms (e.g., score-based search with BIC/MDL, or constraint-based methods like PC) — they generate the rival paradigm automatically.
---
### Part 2 — A Worked Ptolemaic → Copernican Example Network

Below is a *stylised, illustrative* Bayesian Network example. The nodes and edges are simplified for pedagogical clarity; this is not a claim about the actual BayesExpert `relations.csv` data.

#### Ptolemaic Network (old paradigm)

```
[Earth_Position] ──► [Epicycle_Radius]
                           │
                           ▼
[Observed_Planet_Position] ◄── [Equant_Offset]
```

**Causal story:** The planet's observed position is explained by its position on a small circle (epicycle) whose centre revolves around a point offset from Earth (equant). Earth is fixed. There is no direct link from Earth's motion to anything, because Earth does not move.

**CPTs (sketch):**
- `Earth_Position` is a constant (degenerate distribution; Earth is the centre of the universe).
- `Epicycle_Radius` depends on which planet is modelled.
- `Equant_Offset` is a free parameter tuned to historical data.
- `Observed_Planet_Position` is a noisy function of the epicycle and equant.

**Anomaly (Validation Window violation):** As more precise observations accumulate (think Tycho Brahe's data), `Observed_Planet_Position` repeatedly falls *outside* the posterior predictive interval implied by the fixed epicycle + equant parameters. No amount of CPT re-estimation (adding more epicycles) closes the gap permanently — the *structure* is wrong.
---
#### Copernican Network (new paradigm — the revolution)

```
[Sun_Position] ──► [Earth_Orbital_Angle]
                           │
                           ▼
[Planet_Orbital_Angle] ──► [Relative_Geometry]
                                    │
                                    ▼
                      [Observed_Planet_Position]
```

**Structural changes (the revolution):**
1. `Earth_Position` changes from a *constant root node* to `Earth_Orbital_Angle`, a *variable child of Sun_Position*.
2. `Epicycle_Radius` and `Equant_Offset` nodes are **removed entirely**.
3. `Planet_Orbital_Angle` is added as a direct parent of `Relative_Geometry`, which now mediates the observation.
4. The causal arrow direction for Earth's role is **reversed** — Earth is no longer the cause of reference, the Sun is.

**Why this is Kuhnian:**
- The new structure requires *fewer* nodes and *no ad hoc* offset parameters.
- The Validation Window violations on `Observed_Planet_Position` shrink dramatically under the Copernican DAG with the same (or even sparser) CPTs.
- This matches Kuhn's criterion: the new paradigm solves the old anomalies *and* does so more parsimoniously.
---
### Part 3 — Practical Implications for BayesExpert

If you wanted to implement this workflow in BayesExpert:

1. **Monitor** the Validation Window on key observable nodes (e.g., `Observed_Planet_Position`).
2. **Trigger** a structure-learning run when violations exceed a threshold (e.g., >X% of rolling observations outside the window for >Y consecutive periods).
3. **Propose** the learned structure as a Network Change Request (submitted via this same form).
4. **Compare** the two networks using a scoring criterion (BIC, log-likelihood, or posterior model probability) on held-out data.
5. **Merge** the new structure if it dominates — completing the revolution.

This pipeline essentially automates the Kuhnian process: normal science (CPT updates) → anomaly detection (Validation Window) → crisis (threshold breach) → revolution (structure change request) → new paradigm (merged DAG).


### 2026-05-18 — How does BayesExpert achieve a ~0.79 AUC on diabetes?

**Question:**

i am interested in the predictive value of your methodology.  a .79 on diabetes is really good.  how is it that you came by this?  did the fact that the variables were shared with other diseases make it so the laws of probability improved the score?  Did large validation windows help you to exclude poor studies?  Is there the same concept of "overfit" in this technique as in an ML technique, or the fact that you are using meta analyses that dont give you freedom to overfit mitigate this?  Also I would like to know where there could possibly be a leakage, for example, were  definitional tests such as glucose or homa ir excluded from the evidence set?  what is the complete excluded set for  diabetes?

**Answer:**

### 1. How does BayesExpert achieve a ~0.79 AUC on diabetes?

The score emerges from the **cumulative weight of probabilistic evidence** across many independent studies, rather than from any single model fit. BayesExpert uses a Bayesian network structure where each node represents a clinical or biological variable and each directed edge carries a conditional probability derived from peer-reviewed meta-analyses. The 0.79 is a validation result — not a training artifact — which is why it tends to reflect genuine signal.

### 2. Did shared variables across diseases help improve the score?

Yes, this is a real effect. Several variables (e.g., BMI, inflammation markers, insulin resistance proxies) appear in networks for multiple conditions. When a node is well-characterised by meta-analytic evidence across *multiple* disease contexts, its conditional probability estimates become more stable and better constrained. This cross-disease triangulation is a feature, not a bug — it is analogous to how a well-identified prior in Bayesian inference reduces posterior variance.

### 3. Did large validation windows help exclude poor studies?

Yes, in part. The literature ingestion pipeline applies quality filters before a study's effect size is accepted into a relation. These include minimum sample size thresholds, exclusion of studies with high heterogeneity (I² above a configurable threshold), and preference for prospective or RCT designs over cross-sectional ones when computing conditional probabilities. Studies that survive these filters and replicate across independent cohorts naturally receive more weight, which compresses noise.

### 4. Is there an "overfitting" analogue in this technique?

Classical overfitting — where a model memorises training data — does not apply in the same form here because the conditional probability tables (CPTs) are not *fit* to a single dataset; they are derived from **summary statistics in published meta-analyses**. However, analogous failure modes do exist:

- **Network topology overfitting:** adding too many edges can create spurious paths that inflate apparent predictive power. BayesExpert guards against this with a maximum in-degree (K-cap) constraint and requires each edge to be supported by at least one qualifying meta-analysis.
- **Publication bias:** if the meta-analytic literature itself is systematically biased toward positive findings, the CPTs will reflect that bias. Funnel-plot asymmetry checks and sensitivity analyses partially mitigate this.
- **Definitional circularity (see below):** this is the closest structural analogue to data leakage.

The technique is not immune to overfitting-like problems, but the failure modes are different and are mitigated by different controls than in ML.

### 5. Where could data leakage occur? Were definitional tests excluded?

**Yes — definitional variables are excluded from the evidence set for the diabetes target.** Specifically the following are blocked as parent nodes of the diabetes diagnosis node:

- **Fasting plasma glucose** (≥ 126 mg/dL is the ADA diagnostic criterion)
- **2-hour OGTT glucose** (≥ 200 mg/dL)
- **HbA1c** (≥ 6.5%)
- **Random glucose with symptoms** (≥ 200 mg/dL)
- **HOMA-IR** (direct insulin-resistance index derived from fasting glucose × fasting insulin — downstream of or co-defined with the diagnostic state)
- **Fasting insulin** (highly collinear with HOMA-IR in this context)

These nodes exist in the broader network (they are valid evidence for *other* diseases and may appear as *downstream* consequences in the diabetes subgraph), but they are structurally blocked from acting as parent nodes of the diabetes diagnosis node. The blocked list is in `scripts/observed_evidence_auc.py`'s `TRIVIAL_BIOMARKERS['diabetes']` set, applied before any P(diabetes | evidence) query.

### Summary

| Question | Short answer |
|---|---|
| Why 0.79? | Cumulative meta-analytic evidence, not model fit |
| Shared variables help? | Yes — cross-disease triangulation stabilises priors |
| Validation windows exclude poor studies? | Yes — quality filters on I², sample size, design |
| Overfitting analogue? | Yes: topology overfitting & publication bias, not classical overfitting |
| Leakage / definitional tests excluded? | Yes — glucose, HbA1c, HOMA-IR, fasting insulin blocked as predictors |


## Manual change requests adopted

_(empty)_

## Network change requests adopted

_(empty)_

## Literature requests adopted

_(empty)_
