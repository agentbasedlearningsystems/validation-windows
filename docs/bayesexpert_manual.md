# BayesExpert Construction Manual

How to build a Bayesian expert network from epidemiological studies using the BayesExpert system. This manual is written for both humans and LLMs.

> **Note for new Claudes (or any Claude resuming after compaction / new session):** READ THIS MANUAL FIRST before making any structural edits. Specifically read these sections, in order:
>
> 1. **§7.quinquies "Subnet workflow rules (May 1 v2)"** - the clean 8-step subnet attack process. **THE most important section if doing iterative net optimization.** Anti-patterns enumerated. Quick checklist included. Read end-to-end.
> 2. **§7.sexies "Subnet result audit - systematic organization (May 1 v3)"** - what to do AFTER subnets complete. Run `scripts/audit_subnet_wins.py` to enumerate wins, cross-check ghost wins against cycle 14 INVARS, resolve conflicts. The audit TSV (`paper/subnet_audit_log.tsv`) is the durable record across compactions. **Includes the validation rule (local tier-eval ≠ confirmed global win, run 5-core panel) AND the first-principles override (causally-honest changes promote even if a secondary metric briefly regresses).**
> 3. **§7.septies "Applying `dependency_distal` to multipath / washout candidates (May 2)"** - operational guide for the proper structural fix. Two regimes (multipath under-determined cells, aggregator washout) treated by the same 3-row block. Verified pattern from `diabetes_healthy_diet` row 542. Decision rule, priority ordering (frailty family first), validation (UKBB shift + tier transition), stop conditions. **Read this BEFORE adding any dep_distal candidate.**
> 3. **§7.decies "Saturation, polarity, and threshold patterns (May 6)"** - two leaf-wiring anti-patterns (rare-protective-as-NO trigger, lenient-threshold) that produce most of the saturated gates. Polarity flip = both input value AND inverted RR. Connection to AUC bias via independence assumption. Read before auditing any gate with baseline P(gate=yes) > 0.85.
> 4. **§8 "Building a New Network - Systematic Procedure (Any Domain)"** - domain-agnostic 9-step procedure for constructing a BayesExpert net on any topic (health, economics, politics, sports, etc.). Inputs: literature corpus + reference dataset + domain knowledge. Bakes in the four wiring rules and common-cause latent design. Read this if starting a new net or porting the methodology to a new domain.
> 3. **§0.2 K ≤ 6 rule + bypass-fix awareness** - direct K-bumping edges often exist because prior cycles added them to defeat aggregator attenuation. Read the `comment` column before blanking. K rule is a warning, not a constraint; K up to 7 permitted (May 1 directive).
> 3. **§3 Feature Types** - when to use `dependency_distal` vs `is_a` vs `equivalent_to` vs `any_of` vs `all_of`.
> 4. **§3 "How to Write a Distal in the Spreadsheet"** - the three-row pattern for distal aggregation.
> 5. **§3 "Explaining-Away and Connecting Studies (dependency_distal)"** - the wrapper pattern that adds correlation between siblings WITHOUT raising K_DAG.
> 6. **§7.quinquies (rest)** - CSV canonicality, auto-κ Bonferroni, tier-promotion-via-blanking findings, what works / what doesn't.
> 7. **§7.quater + §7.ter + §7.bis** - earlier Apr 28-29 patterns.
>
> **Hard rules - violating any of these has caused failures in past sessions:**
>
> - **The single source of truth for net state is the published-paper baseline build** (currently `builds/cpt_cache/cycle14/`). All subnet attacks validate against THIS baseline's `config_linear.json`, NOT against current CSV state. Subnets enumerated from current CSV produce ghost wins on rows that don't exist in baseline.
> - **One rebuild per cycle.** Multi-cycle sequential rebuilds (cycle 25 → 26 → 27 → 28 each adding one untested change) cause untrackable drift. Apply ALL validated wins to baseline in ONE patched config, then build once.
> - **Don't run subnets and a full-net build concurrently** - CPU contention stretches each subnet 3-5×. Kill the build, fire subnets, build with winners.
> - **Don't trust subnet `OK/FLAT/WRONG`** as tier verdict - that's leaf-level direction count. Use `subnet_tier_eval` JSON output for tier classification.
> - **Cross-check every subnet win against baseline INVARS** before applying. If the (target, parent) tuple isn't in baseline's `dependency_data[target][INVARS]`, the win is a ghost (row was post-baseline addition).
> - **Don't edit** `data/Individual Relations.working.xlsx` (post Apr 30: CSV is canonical). Use `scripts/apply_blanking_to_csv.py` for CSV edits if needed (most subnet workflow doesn't need CSV edits - patch the baseline `config_linear.json` directly).
> - **Don't blank rows just to satisfy a metric or rule** without checking the `comment` column for prior intent. Direct K-bumping rows are often intentional bypass-fixes; convert to `dependency_distal` rather than blank (per §0.2). Apr 30 K-restore mistake: blanked 14 intentional bypass-fixes, had to restore. Don't repeat.
> - **Don't kill running processes when the user says "stop"** - that means "stop new actions, just listen". Killing is irreversible. (See `<memory>/feedback_dont_kill_processes.md`.)
>
> Real case studies preserved as cautionary tales:
> - `paper/K_RESTRUCTURE_PLAN.md` and `paper/K_RESTORE_APR30.md` - Apr 30: 14 bypass-fixes blanked, then restore-via-`dependency_distal` had to be designed.
> - May 1 session: cycle 26 was a 3-change rebuild that drifted diabetes from descrK3 → abstain-m, hypertension tier_eval-detected as descrK3 (LoTP detection gap), ACM regression. Cycle 27 inherited this. Cycle 28 should have been cycle 14 + winners only - got rebuilt three times before that became the policy. Pattern formalized in §7.quinquies v2.
>
> **Before "fixing" bugs in `sn_bayes/config_creation/{dependency,distal}.py`:** read [`docs/FIXES_LEDGER.md`](FIXES_LEDGER.md). Several OUTVARS / PRIORS / STATS / value_ranges dict-shape bugs have regressed once already; before patching, grep for the same access pattern at other call sites in the same function - these bugs come in clusters, and fixing one tolerantly often exposes the next site immediately. Add an entry to the ledger when you land a fix.

---

## Introduction - what this net is and isn't

### What the net does
BayesExpert builds a Bayesian network from published epidemiological studies (meta-analyses). Given risk factors and diseases with their study-reported relative risks (RR), the network produces a full joint probability distribution over all variables. You can query it: "given this person has X, Y, Z, what's their probability of disease D?"

### What makes this different from just running a regression
A regression requires having data on all variables simultaneously. Published studies typically only report bivariate effects (e.g., "BMI → diabetes RR=2.5"). The Bayesian network combines many such bivariate studies into a single coherent multi-variable model - using literature, not raw data.

The QP solver (`sn_bayes/dependency_v2.py`) takes each study's RR as a **constraint** on the CPT and solves for CPT cells that (a) honor all study RRs jointly, (b) satisfy subset consistency bounds (probability theory identities), (c) preserve NHANES-derived priors.

### Who it's for
- **Lifestyle / clinical application developers** - need a population-scale risk model that responds correctly to user-entered evidence.
- **Researchers** - need a reference model combining the literature, to test hypothesis about which interventions help most.
- **Paper authors demonstrating LLM-assisted model construction** - the construction process itself is the scientific contribution.

### What it's NOT for
- Not a substitute for trial data. The net's predictions are as good as the underlying studies.
- Not a causal inference tool. Observational RRs are the inputs; the net doesn't do back-door adjustment beyond what studies themselves reported.

---

## Intake Guide - building a net for a new domain

If a human has a new domain they want a BayesExpert net for (e.g., not longevity, but mental health, or pediatric development), ask these questions before starting construction:

### 1. What are the target outcomes (diseases)?
- List 5-20 primary outcomes the user wants to model.
- Are they prevalent (≥1%) or rare (<1%)? Rare outcomes suffer distal-edge saturation (see §7.bis).
- Are they binary (yes/no) or ordinal (severity tiers)? Binary is simpler.

### 2. What is the target population?
- NHANES (general US adult)? A clinical cohort? A specific age/sex group?
- You need a **reference dataset** for priors and for NHANES AUC testing. In this repo, `preprocessed_nhanes.csv` is that reference. For a new domain, identify what will play that role.

### 3. What risk factors will be inputs?
- Which behaviors/biomarkers/comorbidities are relevant?
- For each, does the reference dataset measure it (so priors come from data)? Or is it defined only via studies (priors come from literature)?

### 4. What studies will you bring?
- Meta-analyses (preferred): largest sample, most robust.
- Single cohorts (acceptable with note): small, may have population bias.
- Expert consensus (acceptable): last-resort, flag as low-quality.

### 5. Intervention scenarios
- What actions/conditions do users want to evaluate? (See §8.bis scenario design - target prevalent states, not extremes.)

### 6. Expected use pattern
- Interactive user queries? Batch population forecasting? Ablation studies?
- Affects optimization priorities (direction-first vs magnitude-first).

### 7. Soundness constraints
- Is causal interpretation required? (If yes, enable deconfounding - but see §0.3 caveats.)
- Must predictions match a specific published model? (If yes, hold out studies for external validation.)

With these answers, the LLM/human can fill in the input CSV (see below) as a plan-of-record, then iterate.

---

## Walkthrough -- one literature finding from PDF to spreadsheet to query

This section walks through adding a single literature finding end-to-end,
so a reader can see exactly what each column contains for a concrete case.
It is the first thing to read when building in a new domain; it shows the
full data path from "I read this paper" to "the model gives a query
result that matches the paper."

**The paper.** Lubin & Caporaso (2006, *J Natl Cancer Inst*) report
RR = 8.43 (95% CI [6.8, 10.4]) for lung cancer in current heavy
smokers vs. never-smokers, pooled from cohort meta-analysis.

### Step 1: identify the (output, input, input_value) triple

- `output` = dependent variable = the node whose risk you are predicting.
  Here: `lung_cancer`.
- `input` = independent variable = the node carrying the exposure. Here:
  `five_days_smoke_cigarettes` (a binary node "smoked in past 5 days").
- `input values` = the *state* of the input that the study's exposure
  group corresponds to. Here: `five_days_smoke_cigarettes_yes`.

If both nodes already have definition rows in the spreadsheet, you only
need to add the study row (Step 3). If `five_days_smoke_cigarettes` is
new, add its definition row first (Step 2).

### Step 2: definition row (only when the input or output node is new)

A definition row has `input` blank. It declares the node's Type, its
state names, and how those states map to the reference survey. Example:

| Column | Value | Note |
|---|---|---|
| `output` | `five_days_smoke_cigarettes` | The node name |
| `input` | (blank) | Definition row |
| `input values` | (blank) | -- |
| `Stat` ... `RR plus minus` | (blank) | Definition rows have no statistic |
| `Type` | `dependency_nhanes_explicit` | See §3 for choice |
| `value1` | `five_days_smoke_cigarettes_yes` | First state name |
| `index1` | `1` | NHANES code value that maps to value1 |
| `value2` | `five_days_smoke_cigarettes_no` | Second state name |
| `index2` | `2` | NHANES code value that maps to value2 |
| `code` | `SMQ680` | NHANES variable name |
| `reverse` | (blank) | Set to `Y` only if higher index = lower risk |
| `citation` | (blank) | Definitions usually have none |
| `comment` | "Whether respondent smoked cigarettes in past 5 days. NHANES SMQ680: 1=yes, 2=no." | Free text |

For an ordinal node (e.g., `bmi_naive` with quartiles), you'd have
`value1`, `index1`, ..., `value5`, `index5`, with each `index` either a
single code value or a range like `30-1000` (everything >=30, e.g., for
severe obesity).

### Step 3: study row

A study row has both `output` and `input` filled in.

| Column | Value | Note |
|---|---|---|
| `P0` (col A) | 0.064 | Output node base rate from NHANES; **autofilled** by `scripts/autofill_p0_sd.py`, do not hand-edit |
| `P0_sd` (col B) | (autofilled) | Used for ES/WMD/SMD; left blank for RR/OR/HR |
| `Stat Value` (col C) | `8.43` | Literature point estimate |
| `Plus minus` (col D) | `0.8` | Half the 95% CI half-width on the *original* scale; for log-RR scale (RR/OR/HR), pre-converted by the user. Standard rule of thumb: `D = (CI_high - CI_low) / 4` |
| `Stat` (col E) | `RR` | One of `RR`, `OR`, `HR`, `SMD`, `ES`, `WMD` |
| `output` (col F) | `lung_cancer` | Dependent variable |
| `input` (col G) | `five_days_smoke_cigarettes` | Independent variable |
| `input values` (col H) | `five_days_smoke_cigarettes_yes` | The exposure state. For multi-state OR-aggregated rows, comma-separated |
| `RR Stat Value` (col M) | `=` formula or `8.43` | Auto-computed from C/D/E using the conversion formula (next section). For `Stat=RR` it equals C |
| `RR plus minus` (col N) | `=` formula or `0.8` | Auto-computed; for `Stat=RR` equals D |
| `Type` (col O) | (blank for direct) or `lung_cancer` (for distal-routed inside an aggregator) | See §3 |
| `citation` (col AB) | `<science><title>Lubin JH, Caporaso NE. ...</title></science>` | Scientifically-valid source. The `<science>` wrapper is a marker meaning the source is peer-reviewed and the LLM has verified scientific validity. Required: free text + this tag |
| `comment` (col AC) | `[Apr 28] Direct from Lubin & Caporaso 2006 cohort meta.` | Provenance: bracketed date + reason for any adjustment |

**Rule:** every study row gets a citation wrapped with `<science>`. The
tag is the LLM's commitment that the source is peer-reviewed,
methodologically valid, and the cited statistic is what the row claims.
Non-`<science>` sources (preprints, blogs, expert opinion) should not
populate study rows.

### Step 4: rebuild and query

```bash
source venv/bin/activate
python scripts/apply_ci_scale_185.py    # full rebuild (~30-50 min)
```

After the build:

```python
import pickle
from sn_bayes.utils import bayesInitialize, query
proto = pickle.load(open('bayesianNetworkProto.pickle','rb'))
net = bayesInitialize(proto)

# Query: P(lung_cancer | smoking=yes) / P(lung_cancer | smoking=no)
r1 = query(net, proto, {'five_days_smoke_cigarettes':'five_days_smoke_cigarettes_yes'}, ['lung_cancer'])
r0 = query(net, proto, {'five_days_smoke_cigarettes':'five_days_smoke_cigarettes_no'}, ['lung_cancer'])
print('model RR =', r1['lung_cancer'] / r0['lung_cancer'])
print('study RR = 8.43')
```

If `model RR / study RR` is within 50% with the same direction, the row
is in the descriptive tier. To check tier membership across the whole
net (one run per build):

```bash
python scripts/objective_rr_comparison_test.py --label <label>
```

Writes `paper/objective_rr_comparison_<label>.json` with per-row results
(`pct_error`, `direction_match`) and a header summary
(`direction_accuracy_pct`, `within_50pct_rate`, `median_pct_error`). The
per-target tier breakdown is in `reproduce_paper.py`'s output (or the
JSON's `results` array grouped by `target`).

---

## Building the reference dataset for a new domain

The longevity demonstration uses NHANES; for a new domain (mental
health, education outcomes, macroeconomics), the LLM has to produce an
analogous CSV that plays the same role. The schema requirements are:

1. **One row per respondent / observation.** Whatever the unit of
   analysis is in the new domain.

2. **One column per node** that the spreadsheet declares with a `code`.
   The column header in the CSV must match the `code` cell of the
   definition row exactly (case-sensitive). Continuous variables are
   numeric columns; categorical variables are integer-encoded matching
   the `index1`/`index2`/... cells of the definition row.

3. **Missing values** as empty strings or `NaN`. The autofill script
   (`scripts/autofill_p0_sd.py`) computes priors from the non-missing
   subset.

4. **File location.** `data/preprocessed_<domain>.csv`. The build code
   reads from `preprocessed_nhanes.csv` by default; for a new domain,
   point at the new file in the build pipeline (in
   `scripts/apply_ci_scale_185.py` and `scripts/autofill_p0_sd.py`,
   replace the path).

**Recipe for producing the CSV from raw survey data:**

```python
import pandas as pd
# Load raw survey waves (e.g., NHANES is a folder of .XPT files; many
# domains have similar multi-table layouts).
demo = pd.read_sas('demo.xpt')
exposure = pd.read_sas('exposure_questionnaire.xpt')
biomarkers = pd.read_sas('lab_results.xpt')

# Merge on respondent ID:
df = demo.merge(exposure, on='SEQN').merge(biomarkers, on='SEQN')

# Rename to match the spreadsheet's `code` column:
df = df.rename(columns={'BMXBMI':'BMXBMI', 'SMQ680':'SMQ680', ...})

# Re-encode if the survey codes don't match your node states. For
# example, if NHANES codes Yes=1/No=2 but you want a 3-state node
# Yes/No/Unknown with index 1/2/9, leave it as-is and use the
# definition row's index1=1, index2=2, index3=9.

# Filter to the analytic universe (e.g., adults only, complete cases on
# the key biomarkers). Document this in a comment file alongside the
# CSV.

df.to_csv('data/preprocessed_<domain>.csv', index=False)
```

**What the reference dataset is used for:**

1. **Per-node prior P0** (col A) -- via `autofill_p0_sd.py`.
2. **Per-node sd P0_sd** (col B) -- for ES/WMD/SMD effect-size conversion.
3. **Joint-fidelity test** (§6.bis test 4) -- exhaustive over every
   conditional cell with >=30 matching respondents.
4. **NHANES AUC test** (§6.bis test 5) -- per-respondent disease
   prediction.

If the new domain doesn't have a per-respondent dataset (only summary
statistics), tests 3 and 4 cannot run; the build still works using
literature priors only (set `dependency_priors` instead of
`dependency_nhanes_explicit` on definition rows).

---

## The input CSV - what it communicates

The network is built from a single spreadsheet `data/Individual Relations.working.xlsx` (also exported as `data/relations.csv`). Each row is either:

- A **definition row** (input blank) - declares a node, its Type, its states, its NHANES code if any.
- A **study row** (input set) - represents one literature study: `output` is the dependent variable, `input` is the exposure, `input values` is the exposure state clamped, `Stat`/`Stat Value`/`Plus minus` are the reported effect and its CI, `citation` is the source.

This spreadsheet is BOTH:
1. **Data**: what the QP reads to build the CPTs.
2. **Communication artifact**: what the human reviews to see what the LLM has built. Each row is auditable - the comment column shows what was adjusted and why.

See §2 for full column meanings. Key points for intake:

- **Node types** (§3): chose carefully. `dependency_nhanes_explicit` needs a NHANES code + study parents. `dependency_priors` uses literature priors. `any_of`/`all_of` are aggregators. See §7.bis for which types suffer saturation at low disease prevalence.
- **Type='cardiovascular_disease'**, **Type='cognitive_impairment'**, etc. on a study row: these are **distal chain tags** - they tell the pipeline "this RR applies to the ultimate disease, routed through the intermediate chain". Critical for multi-level architectures.
- **Index1/Index2 columns**: these define *how the node state maps to NHANES values*. E.g., `bmi` has index1=`30-1000` for the obesity state. This is how priors are computed from data.
- **Comment column**: mandatory provenance record when any row is adjusted. See §6.bis mandatory provenance rule.

A human reviewing the CSV should see, for each node:
- Definition row with clean Type + value1/2 + index1/2.
- Study rows with specific citations and effect sizes.
- Comments documenting any adjustment the LLM made.

If a row's comment says "[autofill] P0 updated 0.22→0.07 per Apr 20 NHANES-P0 convention" the human knows the LLM rescored that row; if comment says nothing, the LLM left it alone.

---

## 0. Key Design Decisions (and Why)

These are load-bearing decisions. Change them only after reading *why*.

### 0.1 Marginal study RRs are constraints; full CPT is QP-solved
Each published study gives a bivariate effect (RR, OR, HR). The full conditional P(Y | X₁,...,X_K) is never directly measured. We treat each study's RR as a *constraint* on the CPT's sub-marginal and solve a quadratic program for the remaining degrees of freedom. This is the only way to synthesize K-way joint behavior from bivariate evidence.

### 0.2 K ≤ 7 parents per dependency node (May 1 directive) - and DO NOT blank K-bumping rows to satisfy this

Degrees of freedom in a binary CPT with K parents = 2^K − K − 1. K=3→4 DOF, K=4→11, K=5→26, K=6→57, K=7→120. Higher K means more CPT cells but the QP solver handles K up to 10 (cycle 14 ACM had K=10, paper's published baseline). The K rule in `data_checks.py:273` is a **warning**, not a build constraint - it triggers at K > 7 (May 1 directive softened it from K > 5 → K > 6 → K > 7). Per memory `feedback_use_subnets_for_theory_tests.md`: subnet-test the consequences instead of blanking-on-warning.

**Critical (Apr 30 lesson): direct K-bumping rows often exist for a reason.** Prior cycles may have added direct disease ← leaf edges to BYPASS aggregator-attenuation (saturating any_of, multi-hop chain dilution, dependency_distal that fails to fire). Those direct edges raise K but recover signal that otherwise dies in the aggregator chain. Their `comment` column will say things like "[cycle N] promoted from <gate> to <disease> direct" or "bypass saturating any_of" or "fixes M-hop chain dilution". **Do NOT blank these blindly to satisfy the K-rule.** Read the comment first.

**Real consequences of blanking K-bumpers blindly (Apr 30):** 14 intentional bypass-fix rows were blanked across 5 targets (ACM K=10→5, cog_imp K=7→5, hypertension K=6→4, diabetes K=7→3, lung_cancer K=6→4). All five regressed. The diabetes regression (descrK3 → abstain-m, max%err 47% → 54%) was inherited by every downstream cycle and contaminated the cycle 26 baseline used for May 1 v2 work. Restoration via `dependency_distal` was attempted but the bypass signal couldn't be fully recovered. **Do not repeat.**

**Correct fix when K > 7 (or you genuinely want to reduce K):** convert direct edges into `dependency_distal` wrappers per §3 lines 787-823. The dependency_distal pattern was created EXPLICITLY to add correlation/signal recovery WITHOUT raising K_DAG. Bundle K-bumping leaves into a (or existing) aggregator, wrap it with `dependency_distal` carrying a connecting study from a correlated sibling, and the disease takes ONE distal-typed edge to the wrapper instead of K direct edges to the leaves. **But subnet-test before applying** - bypass rows often work better than the dependency_distal substitute because the structure that produced the over-K K wasn't accidental.

**Restructure recipe (`paper/K_RESTRUCTURE_PLAN.md` is the worked example):**
1. Identify K_DAG of every node above 6.
2. For each over-K node, check `comment` column on the direct rows that push it over.
3. If comments show "[cycle N] promoted from X to Y direct" / "bypass" / "fixes ... chain": those are bypass-fixes. Bundle them under their original gate (or a new aggregator), wrap with `dependency_distal` adding a correlated-sibling connecting study, route via Type=`distal` from the disease.
4. Verify via subnet test that the restructured target's tier doesn't regress vs the K-bumping direct version.

### 0.3 Observational RRs are the target, NOT causal direct effects
Studies report associational/observational RRs that already include confounding through shared ancestors. The QP target should be the observational RR exactly as published. **Deconfounding is disabled by default** because subtracting indirect-through-mediator effects from already-confounded observational RRs double-adjusts and introduces inversions. (Part 1, 6, 7)

### 0.4 Multiple paths from X to Y are Bayesian-correct
The old "don't let the same signal reach a node through two paths" rule was wrong. Multiple paths reflect real causal mechanisms (smoking → DNA damage → lung cancer; smoking → inflammation → lung cancer). The problem is only when correlated parents are treated as independent. Fix with `dependency_distal` at the correlated siblings, not by removing paths. (Part 21)

### 0.5 Single-parent query tests can flip from explaining-away - that's valid Bayesian inference
When A and B are correlated shared ancestors of D, setting only A can make D *decrease* via explaining-away of B. This is correct inference on the current network; it reveals that A and B are treated as independent when they shouldn't be. Fix: add the correlation edge. Don't mistake explaining-away for a bug. (Part 9)

### 0.6 `any_of`/`all_of` gates are deterministic truth tables
Gates themselves don't attenuate signal - they're logical functions. Signal "attenuation" in FLAT cases is caused by:
(a) `all_of` with K≥3 where P(all K active) ≈ 0, killing signal at the prior level;
(b) the distal RR aggregation, which averages leaf RRs weighted by NHANES frequency (correlated leaves can cancel);
(c) the disease CPT treating gate parents as independent when they aren't (fix: `dependency_distal`). (Parts 12, 21)

### 0.7 `equivalent_to` for identity, not mere similarity
`equivalent_to` has sensitivity=specificity=1 (perfect correlation). When applied to things that are related but not truly equivalent (e.g., sarcopenia and frailty), the equivalent parent *dominates* the disease CPT, explaining away distal inputs. Use `is_a` for subsets, `dependency` for risk - never `equivalent_to` for "close enough." (Part 20, 21)

### 0.8 `is_a` breaks for common events (P > 0.25)
`is_a` encodes sensitivity = P(A)/P(B). When P(B) is high, this pushes P(B|A) to 1.0 too easily, saturating B. Only use `is_a` for P(B) < 0.05. For common events use `any_of`. (Part 11)

### 0.9 Mixed-direction inputs in one gate cause FLAT, not WARN
When a gate has both risk (RR>1) and protective (RR<1) leaves, the distal aggregate RR pulls toward 1.0 and the gate becomes FLAT for every leaf. This is a structural bug, not a style warning. `data_checks` escalated this from WARN to FAIL on Apr 16 2026 (commit `6def3db`). Three legitimate resolutions (only one of which is a true fix; the others are documented exceptions):

- **(a) Structural split** (the real fix). Separate the risk-direction inputs into one sub-gate and the protective-direction inputs into another, then combine at the parent level. The gate's value1/value2 semantics become unambiguous in each sub-gate. See `frailty_behaviors` split for a worked example.

- **(b) Literature-established waiver** (`data_checks.py` `mixed_waivers`). When the mixed direction reflects a known clinical concept that *should* mix (e.g., "abnormal weight change in elderly" includes both weight loss as risk and weight gain as protective per the obesity paradox), add the gate to `mixed_waivers` with a comment citing the literature. Only 2 gates are currently waived (`frailty_weight`, `frailty_healthy_diet`) and the waivers should be re-examined if the clinical reasoning changes.

- **(c) Study audit** (the one you do FIRST before either other option). The mix may not be a real semantic pattern - it may be miscited or semantically-flipped study rows. The Apr 18 2026 audit of `original_diabetes_healthy_diet` found that its 1r+2p mix came from 3 Claude-inserted rows with either (i) cross-outcome disease-RR inversions (rows 538, 539) or (ii) numbers not in the cited paper (row 542, attributed to Poortinga 2007 which doesn't report that pairwise OR). Blanking the 3 removed the flag, at the cost of losing the signal. The lesson: before splitting a gate or adding a waiver, check whether the mixed direction is a data-entry artifact. Manual §4 "Connecting Study Selection" has the audit checklist. (Part 21, Part 25)

### 0.10 Gateway → sub-gate → bypass hierarchy
Three architectural patterns exist to manage K and correlations:
- **Gateway**: intermediate node collecting related leaves to reduce K at the child (e.g., `bmi_pc_risk`→pancreatic_cancer).
- **Sub-gate**: separate correlated leaves into their own sub-group (e.g., sleep quality vs. sleep onset).
- **Bypass**: direct leaf→disease path skipping a deep chain. **Tested and rejected** - creates new explaining-away at higher K. Don't use. (Part 12, 20)

### 0.11 `naive_0` is a workaround, currently disabled
Two nodes pointing to the same NHANES code (bmi and bmi_naive both → BMXBMI) are perfectly correlated in the data, which produces degenerate CPTs. `naive_0` was intended to fix this but breaks 12 cancer nodes. Keep disabled until the proto-state issue is resolved. (Parts 15, 16)

### 0.12 Build-order is not fully deterministic
`dependency()` calls `bayesInitialize()` during CPT construction, which changes proto state for subsequent nodes. Different build orderings can produce slightly different CPTs. This limits reproducibility claims; addressing it requires decoupling CPT construction from network baking. (Parts 15, 16)

### 0.13 Test at every level (leaf / gate / connecting study)
The current paper-grade direction test, `objective_rr_comparison_test.py`, runs both **DIRECT** mode (clamp parent, query the row's `output`) and **DISEASE** mode (clamp parent, query the disease named in the row's `Type`). The DISEASE mode is what catches gate-level explaining-away - when a row's literature claim about a disease doesn't survive the chain it walks through. Connecting-study tests for `dependency_distal` are also covered when the row's Type points at the disease it serves. (Part 21)

### 0.14 Run ALL checks after every change
Not just direction - also calibration MAE, validation windows, empty CPTs, NaN, DAG check, DD/CPT counts, mixed-direction warnings. Fast checks catch silent errors. Use `data_checks.py spreadsheet --xlsx <path>` then the full pipeline. (Part 21)

### 0.15 Literature RR > NHANES-computed RR for connecting studies
NHANES gate-level correlation computation is unreliable when gates include multi-value nodes (age, gender) that make the gate near-certain. Always prefer a published meta-analysis of the concept-to-concept association. Mark unverified RRs as `verification_status=placeholder`. (Part 21)

### 0.16 Connecting studies only help at prevalence P(gate) ∈ [0.2, 0.8]
At saturated gates (P≈1) the connecting study RR can't move the gate higher; at near-zero prevalence it has nothing to propagate. Target sub-gates in the moderate-prevalence range. (Part 21)

### 0.17 Simpson's paradox can require CPT inversions
Monotonicity is a *preference*, not a truth. For some correlation structures, a risk-factor CPT cell going backwards is correct under Simpson's paradox. The `monotonicity penalty` is a soft regularizer; don't drive it to zero blindly. (Part 1)

### 0.18 `use_subset` constraints tighten feasibility via pairwise rho
The paper's core contribution: subset-consistency constraints derived from law of total probability add hard bounds of width (1-ρ)/ρ per pair (the "pairwise bound"), reducing QP infeasibility and inversions at K≥3. Use with `use_subset=True`. (Paper abstract)

**Subset constraints are always on** - they are not an ablation dial. They encode probability-theory identities (law of total probability, sub-marginal decomposition), so turning them off means letting the QP produce CPTs that are mathematically impossible given the pairwise data. The only valid reasons to disable `use_subset` are (a) the implementation is buggy or mathematically unsound for a specific case, or (b) debugging an isolated test of a different component.

**When the subset bounds reject a study target, the BayesExpert response is to find a better study, not loosen the constraint.** A study whose marginal RR is outside the feasible region derived from correct probability math is telling you that the study's population or conditioning doesn't match the network's NHANES base rates. The correct actions are: (1) find a study with tighter CI, (2) find a study in a population that better matches the target, (3) add a connecting study that models the correlation the study was implicitly conditioning on, or (4) flag the edge for crowd-sourced re-verification. This rejection signal is a **feature**, not a bug - it's precisely what Bayes expert networks are supposed to do with inconsistent evidence.

**The objective weights (`w_ls`, `w_df`, `w_mono`) ARE heuristic and ARE safe to ablate.** They choose AMONG feasible CPTs (after subset carves out the feasible set). Ablating `w_df` or `w_mono` is a design choice about which feasible CPT to prefer. Ablating `w_ls` makes the solver ignore study marginals, so that's usually not what you want.

### 0.19 Separate the network topology from the numeric fit
Topology (which nodes have which edges) encodes causal knowledge from the construction manual. Numeric fit (CPT values) comes from the QP solver. If the network is giving WRONG answers, first check topology (missing edges, wrong gate types), then the fit (constraints, solver). (Part 11, 12)

### 0.20 The `avg` and `if_then_else` node types
`avg` is not used in the current network. `if_then_else` is used rarely. Document but don't expand their use - standard `dependency` + gates cover all cases.

---

## 1. Overview

BayesExpert converts published epidemiological study results (relative risks, odds ratios, hazard ratios) into a Bayesian network for clinical decision support. The network takes patient evidence (lab values, lifestyle factors, demographics) and computes disease probabilities.

### Pipeline
1. **Spreadsheet** - all studies, features, and relationships in an Excel file
2. **prepare_config()** - parses spreadsheet into nested config dict
3. **linearize_config()** - flattens to ~N node entries
4. **create_bayesnet_proto_linear()** - builds Bayesian network using QP solver
5. **bayesInitialize() + bake()** - compiles for inference
6. **query()** - probabilistic inference with evidence

### Core Formula
```
P(Disease | parent=v) = prevalence × RR_v / Σ(prior_v × RR_v)
```
The QP solver builds CPTs satisfying this formula subject to feasibility constraints.

---

## 0.bis. Bayesian framing of what the system does

These are the precise statements of what each moving part means in Bayesian terms. Use them when writing the paper, when teaching, and whenever a reader objects "that's not Bayesian."

### 0.bis.1 The CPT is the factor; the network is the joint
A Bayesian network is a factorization:
```
P(X_1, ..., X_n) = Π_i P(X_i | parents(X_i))
```
BayesExpert builds each CPT `P(X_i | parents)` separately from study evidence. The joint distribution is whatever the factorization implies. No global likelihood is optimized - each CPT is a local problem.

### 0.bis.2 Study RRs are hard constraints; NHANES joint data is likelihood
In the QP formulation for one CPT:
- Each study RR becomes a **hard constraint** on a sub-marginal of the CPT (must be satisfied within the validation window).
- The objective is a **likelihood** of the CPT given data: either least-squares (uniform prior on cells) or NHANES-conditioned data fit (empirical joint prior).
- Monotonicity is a **soft prior** (slack-variable penalty) encoding the domain belief "risk factors don't decrease disease probability" - except where Simpson's paradox says otherwise (§0.17).

**This is constrained maximum a posteriori (MAP) estimation per CPT.** The `w` weight is the prior-vs-likelihood balance.

### 0.bis.3 CPT marginal ≠ query marginal - and that is not a bug
- **CPT marginal** = deterministic sum: `Σ_combo CPT(D|combo)·P(combo)`, where `P(combo)` is the unconditional parent joint. This is the quantity the QP fits against study RRs. It reproduces study RRs within 0-2% error - this is the consistency check.
- **Query marginal** = belief-propagation result: `P(D | evidence)`, computed by the inference engine. Uses `P(parents | evidence)` instead of the unconditional joint, so it includes explaining-away and other structural effects.
- Both are correct; they answer different questions (reproduce-the-study vs. predict-for-this-patient).
- Historical note: many earlier MAPE numbers (30-60%) were computed against a multiplicative-odds "truth" that isn't in our feasible set. Those were wrong measurements, not real errors.

### 0.bis.4 Feasible range is set-identification, not posterior uncertainty
Given the study RRs as constraints, multiple CPTs satisfy them. The LP-computed **feasible range** = {min, max} of each CPT cell across the set of admissible solutions.

This is an **identified set**, like in partial identification econometrics - not a posterior credible interval. A narrow feasible range means "the constraints pin down this cell to a small interval"; it does NOT mean "we are 95% confident the true value is in this interval." Those are different kinds of uncertainty and should not be conflated in the paper.

### 0.bis.5 ρ is observed correlation; edges propagate ρ but don't create it
For independent parents A, B, any_of(A,B) is statistically independent of any_of(C,D) if {A,B,C,D} are mutually independent (proven by factorization). Therefore deterministic gates **do not create new correlations** between their outputs from independent inputs. They propagate existing correlations via shared ancestors or explicit edges.

To create ρ between two intermediate nodes, you need **one of**:
- a shared ancestor (some upstream node affects both), or
- a direct edge (e.g., a connecting study captured as `dependency_distal`).

**The pairwise bound `range = (1−ρ)/ρ` only bites when ρ exists.** A network with median ρ ≈ 0.17 is under-connected; the paper's subset constraints need more ρ to produce clinically dependable feasible ranges.

### 0.bis.6 Multi-path is Bayesian-correct (Design rule 5 restated)
If risk factor R affects disease D through multiple mechanisms (R→M₁→D and R→M₂→D), the correct Bayesian network has multiple paths. Treating M₁ and M₂ as independent parents of D (when they are not) is the bug, not the multiplicity of paths. The fix is a `dependency_distal` capturing the M₁-M₂ correlation - see §5 and the connecting-study verification process.

### 0.bis.7 The network is observational; interventions require do-calculus
All CPTs, all queries, and all RRs here describe `P(D | observe X = x)`. Clinical questions like "if we *treat* to lower X, what happens?" are interventional: `P(D | do(X = x))`. For effects without unmeasured confounders, the two coincide; otherwise, do-calculus is needed. This is intentionally out of scope for the current paper but must be noted for clinical deployment and future work.

---

## 2. Spreadsheet Structure

The spreadsheet (`Individual Relations.working.xlsx`, sheet `all worksheet anom`) has one row per study input or node definition.

### Columns
| Column | Name | Purpose |
|--------|------|---------|
| A | control probability | P0 - baseline prevalence from NHANES (needed for OR, HR, SMD, ES, WMD conversion) |
| B | control stdev | Standard deviation (needed for ES, WMD) |
| C | Stat Value | The study's reported statistic |
| D | Plus minus | 95% CI half-width |
| E | Stat | Statistic type: RR, OR, HR, SMD, ES, WMD |
| F | output | The node this row belongs to |
| G | input | The parent variable (empty for definition rows) |
| H | input values | Which value of the parent activates this link |
| I-L | Sensitivity/Specificity | For is_a, subsumes, equivalent_to relationships |
| M | RR Stat Value | **Formula**: Converts Stat Value to RR using P0 |
| N | RR plus minus | **Formula**: Converts Plus minus to RR scale |
| O | Type | Feature type (see Section 3) |
| P | value1 | First output value name (typically the "risk" state) |
| Q | index1 | Prior probability for value1 |
| R | value2 | Second output value name |
| S | index2 | **Formula**: Prior for value2 (typically =1-index1) |
| T-Y | value3-5, index3-5 | Additional output values (for multi-valued nodes) |
| Z | code | NHANES variable code (uppercase) |
| AA | reverse | 1 if value1 is the "good" state (goes on definition row only) |
| AB | citation | Study reference |
| BE | study_n | Sample size |
| BF | study_design | Study design (meta-analysis, cohort, etc.) |
| BG | study_population | Population description |

### Row Types
- **Definition row**: Has Type and value1/value2, no input. Last row of each node's block.
- **Study input row (direct)**: Has input, input values, Stat, Stat Value, Plus minus. Type is empty. Used for `dependency_*` and plain-gate inputs.
- **Leaf study row inside an aggregator that feeds a distal chain**: Has input, input values, Stat, Stat Value, Plus minus, **and `Type = <disease_name>`** (literally the disease the aggregator ultimately feeds; e.g., `diabetes`, `cardiovascular_disease`). The disease-name tag is what routes this leaf's RR into the distal aggregation for that disease. Verified in `config_parsing.py:1231` (`if distal_to and inp_row['Type'] in {distal_to, 'distal'}`).
- **Distal link row**: Has `input=<aggregator>`, `Type='distal'`. Connects an aggregator to a disease. All Stat / RR columns **should be blank** (the distal code aggregates the leaf RRs). If `RR Stat Value` is set, that value overrides the aggregation (rare; used only when the aggregator→disease RR is known from a dedicated study and the leaf aggregation would be wrong).
- **is_a/subsumes/equivalent_to row**: Has input, Type, Sensitivity/Specificity values.
- **Blank separator row**: Between node blocks, for readability.

### Conventions
- Rows for the same node are contiguous
- Definition row is last in each block, immediately after its input rows
- Blank row between each node's block
- Parents must appear before children in the spreadsheet
- Formulas only reference their own row. Up to **6 known exceptions**
  exist in columns M/N (hand-added formulas in otherwise-blank cells).
  `data_checks.py` enforces `cells_with_cross_row_refs ≤ 6`. The older
  "same formula in every row of a column" convention is NOT enforced:
  value1/value2 and other columns legitimately have hand-added formulas
  that differ row-to-row.
- **Do not append new nodes at the end of the sheet.** When adding a new
  gate or dependency, **insert** its rows into the logical cluster near
  existing related nodes so a human reader can see the whole node's
  context without scrolling past unrelated content. `data_checks.py`
  warns about scattered blocks (input rows > N rows away from their
  definition row).

---

## 3. Feature Types

| Feature | Type Column | Bayesian role | Example |
|---------|------------|---------------|---------|
| **dependency** | `dependency_nhanes_explicit`, `dependency_priors`, etc. | CPT P(Y\|X₁..X_K) learned by QP from study RRs | smoking → lung_cancer (RR=2.5) |
| **dependency_distal** | `dependency_distal` | Dependency node inside a distal chain that wraps a gate and adds a connecting-study parent for a correlated sibling | See §Connecting Studies below |
| **is_a** | `is_a` | Definitional subset A⊂B via sensitivity=P(A)/P(B), specificity=1. Encodes P(B\|A)=1 | lung_cancer is_a cancer |
| **subsumes** | `subsumes` | Definitional superset (inverse of is_a) | cancer subsumes lung_cancer |
| **equivalent_to** | `equivalent_to` | Identity relation sensitivity=specificity=1. Encodes P(A)=P(B) | bmi = bmi_naive |
| **equivalent_distal** | `equivalent_distal` | equivalent_to inside a distal chain; the dependency_distal inherits its distal RR from this parent | Used only with dependency_distal |
| **any_of** | `any_of` | Deterministic OR-gate CPT (truth table). Output=1 iff any input=1 | digestive_cancer = colon OR liver |
| **all_of** | `all_of` | Deterministic AND-gate CPT. Output=1 iff all inputs=1 | central_obesity = male AND waist>102cm |
| **if_then_else** | `if_then_else` | Deterministic switch-based CPT | used rarely |
| **distal** | `distal` (link type) | Routing: RR for aggregator→disease is computed by aggregating the aggregator's leaf study RRs inside the gate using NHANES co-occurrence frequencies (Monte Carlo over leaf CIs for the disease-edge CI). If `RR Stat Value` is set on this row, that explicit RR overrides the aggregation. | diet_risk → diabetes |
| **naive_0** | `naive_0_*` | **Currently disabled.** Workaround for perfectly-correlated NHANES codes (bmi + bmi_naive both → BMXBMI). | re-enable after resolving cancer empty-CPT issue |

### When to Use Each

**dependency**: The default for study-backed relationships. Use when you have an RR, OR, or HR from a published study linking a risk factor to a disease.

**is_a**: Only for definitional subsets where P(parent|child) ≈ 1.0. Works for rare events (P<0.05). Breaks for common events (P>0.25) because it pushes sensitivity too high. Example: lung_cancer is_a cancer (correct), regular_exercise is_a physical_activity (wrong - too common).

**subsumes**: Reverse of is_a. B subsumes A means every A is also a B.

**equivalent_to**: For identical concepts with different names or measurement methods.

**any_of**: For grouping independent risk factors. "Any ONE of these increases risk." Use when having ONLY one active input still increases disease risk. Example: digestive_cancer = colon_cancer OR liver_cancer. Each independently contributes.

**all_of**: For concepts where ALL inputs must be present. Use sparingly - P(all K active) ≈ 0 for K≥3, killing signal. Only use for true AND logic: central_obesity = male AND large_waist. Ask: "does having ONLY one input still increase risk?" If yes → any_of, not all_of.

**distal**: For computed RRs through aggregator chains. The distal code walks into the aggregator, finds each leaf's (Stat, Stat Value, Plus minus), converts to RR form, then combines them into a single aggregator→disease RR by weighting each leaf combination by its NHANES co-occurrence frequency. Use when an aggregator feeds into a disease and the aggregator→disease RR should be derived from the studies inside the aggregator rather than from a separate study.

### How to Write a Distal in the Spreadsheet (verified against code)

The code (`sn_bayes/config_creation/distal.py` + `config_parsing.py:1194-1262`) expects a **three-kind-of-row** structure for every distal chain:

1. **Distal link row** - declares that an aggregator feeds a disease.
   ```
   output=<disease>  input=<aggregator>  input values=<aggregator>_yes  Type=distal
   ```
   **All Stat / Stat Value / Plus minus / RR Stat Value / RR plus minus columns should be blank.** The distal code computes the edge RR from the aggregator's leaves. (If you *do* set `RR Stat Value`, it overrides the aggregation - rare, use only with explicit justification.)

2. **Leaf study rows inside the aggregator** - each leaf study that contributes to the aggregator→disease edge.
   ```
   output=<aggregator>  input=<leaf>  input values=<leaf>_yes  Type=<disease>  Stat=OR|HR|RR|SMD|...  Stat Value=...  Plus minus=...  RR Stat Value=...  RR plus minus=...
   ```
   **`Type` is the disease name** (not `distal`, not blank). The disease-name tag is what activates the distal branch in the parser - it tells the code "this leaf's RR feeds the distal aggregation for that disease." A leaf can itself be a sub-aggregator (any_of / all_of / dependency_distal); the distal code recurses and uses its computed sub-RR.

3. **Aggregator definition row** - the aggregator's own logical type.
   ```
   output=<aggregator>  input=<blank>  Type=any_of  value1=<aggregator>_yes  value2=<aggregator>_no
   ```
   Typically `any_of` (any one leaf active → aggregator active). Use `all_of` only if every leaf truly must be present (signal shrinks to near zero for K≥3, so `all_of` is rarely appropriate inside a distal chain).

**Worked example - diabetes_signs feeding diabetes:**

```
# (1) Leaf studies inside the aggregator - Type is the disease name
output=diabetes_signs  input=telomere            input values=telomere_short       Type=diabetes  Stat=RR  Stat Value=2.77  Plus minus=0.4
output=diabetes_signs  input=age_at_menarche     input values=early_menarche       Type=diabetes  Stat=HR  Stat Value=1.22  Plus minus=0.1
output=diabetes_signs  input=diabetes_belly_fat  input values=diabetes_belly_fat_yes Type=diabetes  (blank stats - this leaf is itself a sub-aggregator, its RR is computed recursively)

# (2) Aggregator definition
output=diabetes_signs  input=<blank>             Type=any_of  value1=diabetes_signs_yes  value2=diabetes_signs_no

# (3) Distal link
output=diabetes        input=diabetes_signs      input values=diabetes_signs_yes   Type=distal  (all Stat columns BLANK)

# Disease definition (unrelated to distal, shown for context)
output=diabetes        input=a1c                 input values=a1c_high             Stat=RR  Stat Value=2.3  (direct study, Type blank)
output=diabetes        input=<blank>             Type=dependency_priors            value1=diabetes_yes  value2=diabetes_no
```

**Common corruption patterns (what NOT to do):**
- Setting `Stat Value` / `RR Stat Value` on the distal link row without meaning to override - the code honors it and silently skips the leaf aggregation (`config_parsing.py:1231-1233`). This is the bug in cycle 10.
- Leaving Type blank on a leaf inside a distal aggregator - the parser skips it out of the distal branch; the leaf's RR is absorbed into the aggregator's truth-table math and does NOT reach the disease via distal aggregation.
- Setting `Type='distal'` on a leaf - wrong shape; the distal branch is entered but no aggregator sub-structure is parsed underneath.
- Renaming or moving leaves into a different aggregator without updating `Type=<disease>` - if the new aggregator doesn't feed the same disease, the tag is now referring to a non-existent distal chain and `data_checks` will flag it.

### Gate input with a study RR when the gate is not in a distal chain

There is a fourth pattern beyond the above. A gate may be used as a **direct** `any_of`/`all_of` input to a disease (i.e., the `disease ← gate` row has `Type=None`, **not** `Type=distal`). In that case the parser (`dependency.py:304-357`) never enters the distal branch for this gate: `distal_to` stays `None` when recursing into the gate's inputs, and the Type column on those inputs is ignored by the distal logic. RRs on the gate's input rows are still captured via `extract_row_stats` (line 355) and stored as INVARS stats, aggregated through the gate's truth-table semantics rather than distal math.

This produces an inconsistent audit state: `data_checks` flags the RR-bearing gate-input row as "missing disease tag" under the "Gate RR inputs missing disease tag" rule, but the Type value would be functionally ignored even if set.

**Resolution (choose one):**
- **(a) Promote the gate to a distal chain.** Set the `disease ← gate` row's Type to `distal`, then tag the gate's RR-bearing input rows with the disease name. This activates distal aggregation for the gate.
- **(b) Move the RR-bearing leaf to a gate that is already in a distal chain.** Leaves the original gate as a pure truth-table trigger.
- **(c) Accept the inconsistency.** The RR still participates via the gate's `any_of`/`all_of` math, just not through distal aggregation. In this case the disease tag is a documentation convention only; the `data_checks` rule could be relaxed for this case.

**Historical example (row 783):** `healthy_lifestyle ← daily_time_sitting` with RR=1.12 (Biswas 2015) was moved from `cardiovascular_disease ← daily_time_sitting` into the `healthy_lifestyle` gate in commit `c389dee` to eliminate explaining-away. The `cardiovascular_disease ← healthy_lifestyle` row (row 781) is `Type=None`, so healthy_lifestyle is not a distal chain for CVD. Setting row 783's `Type=cardiovascular_disease` is semantically wrong because no distal chain exists for that tag to refer to. The fix is (a), (b), or (c) per the list above.

### Worked Example: Adding a New Disease Node

Suppose you want to add "liver_fibrosis" as a new disease with 3 study inputs.

**Step 1: Find studies.** You find:
- Obesity increases liver fibrosis risk: RR=2.1, CI=[1.8, 2.4], meta-analysis n=15,000
- Heavy alcohol increases risk: OR=3.5, CI=[2.8, 4.2], cohort n=8,000, P0=0.05
- Diabetes increases risk: HR=1.8, CI=[1.5, 2.1], cohort n=12,000, P0=0.07

**Step 2: Check study quality.**
- CI crosses null? No (all CIs exclude 1.0) ✓
- OIS: For RR=2.1 with P0=0.05, required n ≈ 400. Actual n=15,000 ✓
- All are large studies with clear effects ✓

**Step 3: Add spreadsheet rows** (parents must already exist in the spreadsheet):
```
| P0   | stdev | Stat | PM  | Stat | output         | input           | input values           | ... | Type             | value1              | value2              |
|------|-------|------|-----|------|----------------|-----------------|------------------------|-----|------------------|---------------------|---------------------|
|      |       | 2.1  | 0.3 | RR   | liver_fibrosis | bmi             | bmi_over_30_obesity    |     |                  |                     |                     |
| 0.05 |       | 3.5  | 0.7 | OR   | liver_fibrosis | heavy_alcohol   | heavy_alcohol_yes      |     |                  |                     |                     |
| 0.07 |       | 1.8  | 0.3 | HR   | liver_fibrosis | diabetes        | diabetes_yes           |     |                  |                     |                     |
|      |       |      |     |      | liver_fibrosis |                 |                        |     | dependency_priors | liver_fibrosis_yes  | liver_fibrosis_no   |
```

- Study input rows: no Type (the disease node's Type is on the definition row)
- P0 needed for OR and HR conversion (column A)
- Definition row: Type=dependency_priors, value1/value2 name the output states
- index1 (column Q) on definition row: NHANES prevalence of liver_fibrosis (e.g., 0.05)
- Blank row before and after this block

**Step 4: K check.** K=3 inputs ✓ (under limit of 5)

**Step 5: Build and test.** Run pipeline, check 0 empty CPTs (`data_checks.py post_run`), then `objective_rr_comparison_test.py` to verify all 3 inputs show correct direction (DIRECT and DISEASE modes).

### Worked Example: Using Gates to Group Risk Factors

Suppose liver_fibrosis has 8 study inputs (too many - K=8 causes inversions). Group into gates:

```
# Gate 1: metabolic factors
Row: output=fibrosis_metabolic, input=bmi,        input_values=bmi_over_30_obesity,       Type=liver_fibrosis, Stat=RR, StatValue=2.1, PM=0.3
Row: output=fibrosis_metabolic, input=diabetes,    input_values=diabetes_yes,              Type=liver_fibrosis, Stat=HR, StatValue=1.8, PM=0.3, P0=0.07
Row: output=fibrosis_metabolic, input=homa_ir,     input_values=homa_ir_high_above_2.9,    Type=liver_fibrosis, Stat=RR, StatValue=1.6, PM=0.2
Row: output=fibrosis_metabolic, input=NaN,         Type=any_of, value1=fibrosis_metabolic_yes, value2=fibrosis_metabolic_no

# Gate 2: behavioral factors  
Row: output=fibrosis_behavioral, input=heavy_alcohol, input_values=heavy_alcohol_yes,      Type=liver_fibrosis, Stat=OR, StatValue=3.5, PM=0.7, P0=0.05
Row: output=fibrosis_behavioral, input=hepatitis_c,   input_values=hepatitis_c_yes,        Type=liver_fibrosis, Stat=RR, StatValue=4.2, PM=0.8
Row: output=fibrosis_behavioral, input=NaN,          Type=any_of, value1=fibrosis_behavioral_yes, value2=fibrosis_behavioral_no

# Distal links to disease
Row: output=liver_fibrosis, input=fibrosis_metabolic,  Type=distal
Row: output=liver_fibrosis, input=fibrosis_behavioral, Type=distal
Row: output=liver_fibrosis, input=NaN,                 Type=dependency_priors, value1=liver_fibrosis_yes, value2=liver_fibrosis_no
```

Now K=2 at the disease level (two gates). Each gate has K=3-4. The `Type=liver_fibrosis` on study rows tells the distal code to compute each gate's RR using the liver_fibrosis formula. The `Type=any_of` means any one metabolic factor independently increases risk.

### Worked Example: Connecting Studies for Explaining-Away

After building, `objective_rr_comparison_test.py` shows:
```
WRONG: diabetes study_RR=1.800 query_RR=0.950  (goes DOWN instead of UP)
```

This means setting diabetes=yes makes liver_fibrosis go DOWN - explaining-away. The network thinks "bmi already explains fibrosis, so diabetes must not be contributing."

**Fix:** Add a connecting study between bmi and diabetes:
```
Row: output=liver_fibrosis, input=diabetes, input_values=diabetes_yes, Stat=RR, StatValue=1.8, PM=0.3, P0=0.07
                                                        ↑ this row already exists

# ADD this connecting study:
Row: output=bmi, input=diabetes, input_values=diabetes_yes, Stat=RR, StatValue=4.56, PM=0.5
                                 (from a study showing diabetes is associated with higher BMI)
```

This tells the network that diabetes and bmi are correlated, preventing explaining-away. The RR=4.56 comes from a real study - don't make up connecting study values.

### Worked Example: Fixing FLAT with Gate Flip

`objective_rr_comparison_test.py` shows:
```
FLAT: dietary_fiber study_RR=1.190 query_RR=1.002  (signal barely reaches disease)
FLAT: vegetarian    study_RR=1.099 query_RR=1.002
```

These inputs go through `acm_unhealthy_diet` which is `all_of`. P(all 4 inputs active) ≈ 0.007 - signal death.

**Diagnosis:** Ask "does having ONLY one unhealthy diet factor increase mortality risk?" Yes - eating out frequently alone increases risk, regardless of fiber intake. These are independent risk factors.

**Fix:** Change `acm_unhealthy_diet` Type from `all_of` to `any_of`. Result: FLAT → OK for all inputs.

---

## 3.bis. Effect-size conversion formulas

Literature reports effect sizes in five common metrics: relative risk
(RR), odds ratio (OR), hazard ratio (HR), standardized mean difference
(SMD), and effect size / weighted mean difference (ES, WMD). The QP
solves on the *RR* scale, so non-RR statistics are converted before they
become CPT-cell constraints. The conversion uses the output node's base
rate `P0` (col A, autofilled from the reference survey) and -- for
ES/WMD -- the output node's standard deviation `P0_sd` (col B).

The xlsx encodes these conversions as Excel formulas in cols M ("RR Stat
Value") and N ("RR plus minus"). For an LLM building from scratch
without those formulas pre-baked, the math is:

**RR (no conversion needed)**

`RR_value = Stat_Value`

**OR -> RR** (Zhang & Yu 1998 transformation, exact under rare-disease
assumption; usable up to P0 ~ 0.3 with bias < 10%):

`RR_value = OR / ((1 - P0) + P0 * OR)`

**HR -> RR** (constant-hazard approximation over the cohort follow-up,
treating P0 as cumulative incidence):

`RR_value = (1 - exp(HR * ln(1 - P0))) / P0`

**SMD -> RR** (probit/logistic-link approximation; the constant 1.81 is
`pi / sqrt(3)` from converting standard-normal to logistic effect-size):

```
log_term = exp(1.81 * min(SMD, 1.5))
RR_value = log_term / ((1 - P0) + P0 * log_term)
```

The `min(SMD, 1.5)` cap prevents the conversion from blowing up at
implausible SMD magnitudes; combined with the `if P0 < 0.1 and result >
5` guard, it sets `RR_value = "SMD_UNRELIABLE"` when the combination of
small base rate and large SMD makes the conversion meaningless. Rows
flagged `SMD_UNRELIABLE` are dropped from the QP fit; treat them as a
signal to find a non-SMD source for the same edge.

**ES / WMD -> RR** (continuous outcomes, normalised by the output's
standard deviation):

```
log_term = exp(1.81 * ES / P0_sd)
RR_value = log_term / ((1 - P0) + P0 * log_term)
```

`P0_sd` (col B) is required for ES/WMD rows; for RR/OR/HR/SMD it is
unused and can be left blank.

**CI half-width conversion.** The same transformation applied to
`Stat_Value - Plus_minus` gives the lower edge of the converted band;
the converted half-width on the RR scale is
`RR_value - converted_lower`. The xlsx formula in col N implements this
case-by-case. The `SMD_UNRELIABLE` / non-RR cap rules apply identically.

**Direction note.** A protective effect (RR < 1) is encoded as
`Stat_Value < 1` for RR / OR / HR. For SMD/ES/WMD a *lower* outcome is
typically protective, so a *negative* `Stat_Value` corresponds to RR <
1. The conversion formulas above produce RR < 1 in those cases without
sign-handling tricks.

**Reference implementation.** `sn_bayes/rr_formulas.py` contains the
Python port of every Excel formula in cols M/N, used by
`scripts/autofill_p0_sd.py` to recompute RR cells when P0 or
Stat_Value changes. Read that file when you need the canonical edge
cases (e.g., zero CI on a meta-analysis pool).

---

## 4. Design Rules

### Architecture Rules

**1. Never use dependency for definitional relationships.**
The QP formula can't express certainty. Use is_a instead.
- Wrong: lung_cancer → cancer as dependency (RR=10). Result: P(cancer|lung_cancer) = 0.54.
- Correct: lung_cancer → cancer as is_a. Result: P(cancer|lung_cancer) = 1.0.
- What goes wrong: the network says there's only a 54% chance that someone with lung cancer has cancer. Clinically nonsensical. Undermines all downstream reasoning.

**2. K up to 7 inputs per dependency node (per May 1 directive); the K rule in data_checks.py is a warning.**
Inversions increase with K but the QP solver handles K up to 10 (cycle 14 ACM was K=10 - paper baseline). Group inputs into sub-gates if K really exceeds the warning threshold AND the CPT is showing inversions in subnet tests; otherwise leave it. **Do NOT blank rows just to satisfy the warning** - see §0.2 case study where blanking caused diabetes regression that propagated through 3 cycles. Use `dependency_distal` to bundle leaves under an aggregator (preserves signal) if you want to genuinely reduce K.
- What goes wrong if you blank to satisfy the warning: load-bearing bypass rows disappear, signal is lost through aggregator chain dilution, target tier regresses (descrK3 → abstain-m).

**3. Keep same-direction factors together in gates.**
Mixing risk (RR>1) and protective (RR<1) in one gate cancels signal.
- What goes wrong: a gate with protein (RR=0.90, protective) and vitamin_d (RR=1.12, risk) produces near-zero net effect. The gate output barely moves when either input changes. Both studies' signals are wasted.

**4. is_a works for rare events (P<0.05), breaks for common (P>0.25).**
Use any_of for common events instead.
- What goes wrong: is_a for regular_exercise (P=0.35) → physical_activity pushes P(physical_activity) to near 1.0. The node saturates and can't transmit any more signal downstream. Everything that depends on physical_activity becomes flat.

**5. When the same signal reaches a node through multiple paths, model the correlation with dependency_distal.**
Multiple paths from a risk factor to a disease are correct from Bayesian theory - they represent real causal pathways. The problem arises only when correlated parents are treated as independent. Fix: add `dependency_distal` nodes between correlated siblings at each level of the chain.
- What goes wrong WITHOUT the dependency: diet_fruits_and_vegetables feeds into cardiovascular_disease through two gate parents. The network treats them as independent, causing explaining-away → WRONG direction.
- Fix: use `dependency_distal` to capture the correlation. The connecting study RR comes from a published study or NHANES: P(sibling_A=yes | sibling_B=yes) / P(sibling_A=yes).
- **Deeper dependencies = stronger signal.** One dependency_distal at the disease level fixes direction but may not fix magnitude. Adding dependency_distal nodes deeper in the chain (between sub-gates, between sub-sub-gates) propagates evidence sideways to correlated siblings, building stronger signal at each level.

**6. The reverse flag goes on the gate definition row.**
It tells the system value1 is the good state. Never put reverse on input rows.
- What goes wrong: moving inputs from a reverse gate (where value1=good) to a non-reverse gate (where value1=bad) flips the meaning of all inputs. "vitamin_d normal" now reads as "increases frailty risk" instead of "protects against frailty." Silent error - the build succeeds but the network gives backwards answers.

**7. Same input variable with different values is NOT redundant.**
bmi_naive overweight (RR=0.93) and bmi_naive underweight (RR=1.45) are different study findings.
- What goes wrong: deleting the "underweight" row because it looks like a duplicate of the "overweight" row loses the finding that underweight increases frailty risk. The network can no longer distinguish these different risk profiles.

**8. all_of = "all must be present." any_of = "grouped independent risks."**
Using all_of for grouping causes signal death: P(all K conditions) ≈ 0.
- What goes wrong: cause_cancer_mortality as all_of with 5 inputs (tumor marker, inflammation, diet, sedentariness, sleep). P(all 5 active simultaneously) ≈ 0.001. Setting any single input has negligible effect on the gate output. All 5 studies' signals are completely lost - 0% of their information reaches the disease. Looks like FLAT in diagnosis.

### Explaining-Away and Connecting Studies (dependency_distal)

**9. Correlated parents cause explaining-away. Fix with dependency_distal.**
When parents A and B are correlated in reality but independent in the network, setting A=risk makes the network think "A explains the disease, so B is less needed" → P(disease|B=risk) drops.

Fix: use `dependency_distal` to model the correlation. The adjusted gate wraps the original gate (via `equivalent_distal`) and adds a connecting study from the correlated sibling.

**Spreadsheet pattern:**
```
# Original gate (renamed, but keeps SAME output value names)
original_gate_metabolic | diabetes     | diabetes_yes    | disease_name | RR=1.5
original_gate_metabolic |              | any_of          | value1=gate_metabolic_yes, value2=gate_metabolic_no

# Adjusted gate (dependency_distal - replaces original in the disease's inputs)
gate_metabolic | original_gate_metabolic | gate_metabolic_yes | equivalent_distal
gate_metabolic | gate_cardio             | gate_cardio_yes    | (blank type) | RR=2.18 | citation=connecting study
gate_metabolic |                         | dependency_distal  | value1=gate_metabolic_yes, value2=gate_metabolic_no

# Disease (uses adjusted gate, NOT original)
disease | gate_cardio    | gate_cardio_yes    | distal
disease | gate_metabolic | gate_metabolic_yes | distal
disease |                | dependency_priors
```

**Critical naming rule:** The original gate's value1/value2 MUST match the wrapper's value1/value2. If they differ, the reverse_rr check inverts the RR (1/RR instead of RR).

**Connecting study RR:** The RR must come from a published meta-analysis or large cohort study measuring the association between the two concepts. Always prefer literature over NHANES computation (NHANES gate-level computation is unreliable because gates contain complex multi-value nodes that don't reduce cleanly to binary risk/no-risk).

**10. dependency_distal can appear anywhere in the distal chain.**
Not just between disease-level gate parents, but between sub-gates, sub-sub-gates - wherever two correlated siblings feed into the same parent. Deeper dependencies propagate evidence sideways to more siblings, building stronger signal through the chain.

**11. Connecting studies are most effective at moderate-prevalence gates (0.2-0.8).**
When a gate's prevalence is near 1.0 (saturated), a connecting study barely moves it - P(gate=yes) is already ~1 regardless of the sibling. When near 0, the connecting study has nothing to propagate. Target sub-gates in the 0.2-0.8 range. Check gate prevalences before adding connecting studies.

**12. Sub-gates for correlated leaf priors.**
Can't add connecting studies between NHANES leaf variables (they have no parents). Group correlated leaves into sub-gates instead.

### Study Quality Rules

**11. Check if the study's CI crosses 1.0.**
If [RR - pm, RR + pm] contains 1.0, the study can't determine risk vs protective. Prefer studies with CIs that don't cross the null.

**12. Check optimal information size (OIS).**
Small effects need large samples. Required n = 7.84 × (P0(1-P0) + P1(1-P1)) / (P0-P1)² where P1 = P0 × RR. If actual n < required n, the study is underpowered - find a larger meta-analysis.

**13. Don't use CPT marginals to check study RR reproduction.**
Marginalizing over other parents requires correct prevalences and independence. Use query-based checking: set evidence, query disease, compute RR = P(disease|evidence) / P(disease|baseline).

### Connecting Study Selection and Verification

Connecting studies (used in `dependency_distal` nodes) link two correlated sibling concepts. They differ from regular studies: a regular study measures "risk factor → disease," while a connecting study measures "concept A ↔ concept B" (the association between two intermediate nodes).

**Why they matter for the paper.** Correlations between parents do *two* things simultaneously: (a) they correct mixture - when two correlated parents both have risk signal, the CPT needs the correlation to distribute the joint effect without double-counting; (b) they prevent explaining-away - when one parent is set to risk, the other should also update toward risk in proportion to their correlation. Both failures come from treating correlated parents as independent. A valid connecting study is the fix for both.

#### What the RR Represents

The connecting study RR is: **P(concept_A=yes | concept_B=yes) / P(concept_A=yes)**

This is the association between the two sibling concepts in the general population - how much knowing concept_B is active increases your belief that concept_A is active. It is NOT the shared effect of both on a disease.

#### Common Miscitation (DO NOT DO)

Disease-risk studies report `P(disease | risk_factor) / P(disease)` - a different RR entirely. They cannot be substituted for a connecting-study RR by any of the following (all observed in the Apr 2026 review of rows 538/539/542):

1. **Taking the disease-risk RR as-is on a behavior→behavior edge.** The scale is wrong; the reference population is wrong; the conditional is wrong.
2. **Inverting the disease-risk RR** (e.g., RR=0.787 = 1/1.27 from an SSB→T2D meta) **to encode the "protective" direction.** The inverse of a disease RR is not a behavior-correlation RR. A study reporting "high sugar → diabetes RR=1.27" tells you almost nothing about P(healthy_diet | low_sugar) - that requires separate data on the joint behavior distribution.
3. **Citing a real clustering paper but using a number not in it.** Even if the paper is the right *kind* of study (e.g., Poortinga 2007 PMID 17157369, a real 4-factor clustering study on the 2003 Health Survey for England, n=11,492), the specific pairwise OR must come from the paper's reported numbers. If the abstract or tables don't report the exact pairwise OR, the value is unsupported - do not invent a plausible-looking number.

**Required check before inserting a connecting study:**

- [ ] The cited study's outcome is the paired *concept*, not a downstream disease.
- [ ] The reported estimate is a pairwise `OR`/`RR`/`HR` between the two concepts at the population level.
- [ ] The number in `Stat Value` matches a number reported in the paper (quote the table/text).
- [ ] If the number was derived from the paper (e.g., dividing two prevalences), document the derivation in `verification_status`.
- [ ] Connecting studies inside a `dependency_distal` wrapper must satisfy all four criteria; do not use the wrapper to justify weak provenance.

When a valid connecting study doesn't exist for a pair, three legitimate fallbacks:

- (a) **NHANES-derived joint estimate.** Compute the pairwise OR from the NHANES wide-format table directly; cite "NHANES-derived, cycles YYYY-YYYY, n=X" in `verification_status`. Unbiased under NHANES's sampling.
- (b) **Latent-class / lifestyle-cluster paper** that reports the joint. UK Biobank, ELSA, HRS, or the Health Survey for England have papers that give pairwise prevalences.
- (c) **Leave the edge correlation-free.** The QP solver will fall back to independence. This is worse than a correct connecting study but better than a wrong one.

#### Within-outcome vs cross-outcome inversion

A subtle distinction the Apr 2026 audit turned up: **within-outcome inversion of an OR is mathematically valid; cross-outcome inversion is not.**

- **Within-outcome (valid):** If a paper reports `OR = 0.86` for "high diet quality → poor sleep quality", then `OR = 1/0.86 = 1.16` is the same OR expressed from the flipped reference group (`low diet quality → poor sleep quality`). Same endpoint, same comparison, just the exposure reference reversed. The Guo 2025 → row 469 example.
- **Cross-outcome (invalid):** If a paper reports `RR = 1.27` for "high sugar intake → type 2 diabetes," then `1/1.27 = 0.787` is *not* a valid RR for "low sugar intake → healthy diet adherence." That's a different endpoint (diet adherence, not T2D), a different conditional, requires different data. The Li 2023 → row 539 example.

When a row's `verification_status` documents an inversion, it must specify which kind and why the within-outcome version applies.

#### Audit checklist (applied to a row with citation + stat value)

1. **The cited paper exists.** Look up PMID on PubMed; confirm author / title / journal match the citation text.
2. **The outcome in the paper matches the gate/disease being modeled.** Disease-risk papers (exposure → disease) cannot be repurposed as behavior-clustering papers (concept A ↔ concept B).
3. **The specific OR/RR/HR in the row appears in the paper.** Abstract or tables must quote the exact number; don't infer plausibility.
4. **The population in the paper matches the target (NHANES, or the network's intended application).** Occupational cohorts, clinical cohorts, and single-disease registries do not transport to general-population NHANES-conditioned CPTs without additional adjustment.
5. **If the row inverts the paper's OR, confirm within-outcome:** the paper's reference group and the row's reference group must refer to the *same* endpoint.
6. **Record the outcome of steps 1-5 in `verification_status`.** "VERIFIED YYYY-MM-DD: ..." for a successful audit; blanking + a comment documenting why for a failed audit.

Rows with blank `verification_status` should be treated as not-yet-audited; see §5 build-time data_checks for how many such rows currently exist in the net.

#### Study Selection Criteria

**Evidence hierarchy** (same as regular studies, adapted for cross-concept associations):

1. **Meta-analysis measuring the cross-concept association** (preferred) - e.g., "meta-analysis of unhealthy lifestyle and multimorbidity clustering." Must report OR, RR, or HR with CI.

2. **Large population-based cohort** (n>10,000) - measuring co-occurrence of the two concepts in a general population sample.

3. **Mendelian randomization** - provides causal evidence that concept_B causes concept_A (or vice versa). Useful for confirming directionality.

**Inclusion requirements** (all must be met):

| Criterion | Check | Fail action |
|-----------|-------|-------------|
| CI does not cross 1.0 | [RR-pm, RR+pm] excludes 1.0 | Find a stronger study |
| Passes OIS | study_n ≥ required n for the effect size | Find a larger study |
| General population | NOT from disease registries, clinical cohorts, or case-control of the disease node | Replace - collider bias |
| Adjusts for confounders | OR better: adjusts for age, sex, SES at minimum | Note if unadjusted |
| Measures the right concepts | Maps to the gate-level concepts, not individual leaves | Verify operationalization |

**Exclusion criteria** (specific to connecting studies):

- **Disease-conditioned studies**: A study measuring "behaviors and conditions among frailty patients" has collider bias - both are causes of frailty, so conditioning on frailty creates spurious association. Use general-population studies only.
- **In vitro / cell culture**: Same as regular studies - no population-level RR available.
- **Narrative reviews**: Cannot be primary source for effect sizes.
- **Ecological studies**: Population-level correlations (country-level) don't give individual-level RRs.

#### Spreadsheet Documentation

Every connecting study row must have these columns filled:

| Column | Required | Content |
|--------|----------|---------|
| RR Stat Value (M) | Yes | The association RR (converted if needed) |
| RR plus minus (N) | Yes | 95% CI half-width |
| citation (AB) | Yes | "Author Year PMID:NNNNNNN" |
| comment (AC) | Yes | Semantic justification: why these concepts are correlated |
| study_n (BE) | Yes | Sample size |
| study_design (BF) | Yes | meta-analysis, cohort, MR, etc. |
| study_population (BG) | Yes | Population description (must be general-population) |
| verification_status (BH) | Yes | One of: verified, unverified, placeholder |

#### Verification Process

**Before adding:**

1. **Study passes all inclusion criteria above**
2. **Semantic check** - the connection represents a real biological or causal mechanism:
   - Ask: "Is there a reason these two concepts are correlated beyond sharing the same disease?"
   - Correct: unhealthy behaviors → chronic conditions (lifestyle causes chronic illness)
   - Wrong: two biomarkers correlated only because the same disease causes both
3. **Pre-addition data check** - run `python3 scripts/data_checks.py spreadsheet --xlsx <subnet>`:
   - Naming matches (original gate value1/value2 = wrapper value1/value2)
   - No orphaned references
   - DAG is still valid

**After adding:**

4. **Full pipeline** - run all 4 data check stages:
   - Stage 1: spreadsheet check
   - Stage 2: config check (dependency_distal has correct INVARS, OUTVARS, EQUIVALENT_DISTAL)
   - Stage 3: pickle check (0 empty CPTs, no NaN)
   - Stage 4: post-run check (no NaN in predictions)

5. **Direction check** - run `objective_rr_comparison_test.py`, compare before/after:
   - No new WRONG (connecting study must not flip any direction)
   - No new FLAT (connecting study should improve or maintain signal)
   - Previously WRONG/FLAT cases in the connected chain should improve

6. **Results check** - run extract_results.py, compare before/after:
   - Calibration MAE should not increase
   - Validation windows should not widen significantly
   - RR magnitudes in the connected chain should move toward study values

**Red flags that indicate a bad connecting study:**
- New WRONG cases appear after adding
- Many validation windows widen
- Calibration MAE increases significantly
- The connecting study RR is extreme (>10 or <0.1) - check if the study measures the right association
- The study population is disease-conditioned (collider bias)

---

## 5. Process Rules

### Before Every Change

**Every build runs this checklist in order:**
```
DATA CHECKS (must all pass before proceeding to next stage):
  1. Spreadsheet check (before config creation)
  2. Config check (before build)
  3. Pickle check (before query)
  4. Post-run check (after query)

DD/CPT COUNT MATCH (must be exact across all three):
  Spreadsheet counts:
    pure_DD = rows with Type in {discrete_priors, discrete_nhanes_*}
    naive_0 = rows with Type in {naive_0_nhanes_*}
    naive_0_connected = naive_0 nodes referenced as input by other nodes
    naive_0_isolated = naive_0 nodes not referenced by anyone
    dd_only_naive_children = pure_DD nodes whose ONLY children are naive_0 nodes
    CPT = rows with Type in {dependency_*, any_of, all_of, avg, if_then_else}

  With naive_0 OFF:
    Expected DD = pure_DD - dd_only_naive_children + naive_0_connected
    Expected CPT = CPT
    (dd_only_naive_children are correctly excluded - their parent links are disabled)
    (naive_0_isolated are correctly excluded - nothing references them)

  With naive_0 ON:
    Expected DD = pure_DD
    Expected CPT = CPT + naive_0_connected (they become CPTs with parents)
    (naive_0_isolated is an ERROR if naive_0 is on - they should connect)

  All three must match:
    Spreadsheet expected DD = Config DD = Pickle DD
    Spreadsheet expected CPT = Config CPT = Pickle CPT

  Conservation of study inputs:
    Spreadsheet (study RR rows + distal link rows) = Config (INVARS with relative_risk)
    Spreadsheet (is_a + subsumes + equivalent_to + manual sens rows) = Config (INVARS with sensitivity)
    Every dependency INVARS entry must have either RR or sensitivity
    Every gate study row (Type=disease_name) must have RR, sensitivity, or be a gate trigger

RESULTS TESTS (run all, show immediately, compare before/after):
  5. Architecture direction
  6. Query RR
  7. Calibration
  8. Validation windows
  9. CI crosses null
  10. OIS
  11. Rho (network)
  12. Rho (NHANES)
  13. NHANES fidelity
  14. AUC
  15. Distal chain propagation

COMMIT after all pass.
```

**1. Check spreadsheet formulas after every edit.**
```
# Must match baseline after any spreadsheet edit:
- Column M (RR Stat Value) formula count: unchanged
- Column N (RR plus minus) formula count: unchanged
- Column S (index2) formula count: unchanged
- Cross-row references: 0
- Every formula in a column identical except row number
- User must open/save in Excel after openpyxl edits to recalculate cached values
```

**2. Check data quality before building.**
```
# Must pass before any build:
- Stat but no RR: 0
- dependency_priors missing index2: 0
- RR without plus_minus: 0
- Old/dangling node references: 0
- File size reasonable (not drastically smaller than previous)
```

**3. Verify intermediate products.** After config generation, check:
- Node count matches expected
- No NaN/None in config
- No RR without plus_minus
- Gate types are correct
- All expected nodes present with correct parents

After build, check pickle:
- DDs + CPTs = expected total
- 0 empty CPTs
- 0 NaN in CPT probability values
- All DDs sum to 1.0

### Making Changes

**3. One change at a time.** Make one architectural change → commit → build → test → next change. Never bundle untested changes.

**4. Test on subnets first** for complex changes. Build the subnet, measure before/after. Only apply to full network if subnet improves.

**5. Revert changes that are wrong, not changes that expose problems.** If a correct change (like adding a real connecting study) causes side effects in other nodes, fix those nodes too. Only revert if the change itself was incorrect.

**6. Save state before modifying.** Commit, save test results, document what and why.

### Editing the Spreadsheet

**7. Never use openpyxl to edit the spreadsheet.** It destroys formula cached values on save, even when only changing text cells. Use direct XML editing instead (xlsx is a zip of XML files):
- xlsx is a zip of XML files
- Extract, edit the sheet XML and shared strings, repackage
- This preserves all formulas and cached values perfectly
- For text cell changes: modify shared string indices in the sheet XML
- For new text values: add entries to sharedStrings.xml

**8. After spreadsheet edits, always verify:**
- Stat but no RR: 0 (formulas intact)
- dependency_priors missing index2: 0
- Old node name references: 0
- New node name references exist

### Build Artifacts

**9. Save each build to a timestamped directory:**
```
builds/<YYYYMMDD_HHMM>_<description>/
    bayesianNetworkProto.pickle
    bayesnet_config_linear.json
    validation/
        <node>_validation.csv  (from bayesnet_initialize_output/)
```
The validation CSVs in `bayesnet_initialize_output/` get overwritten by each build. Copy them to the run directory immediately after building.

---

## 6. Quality Measurements

Run after every build. **Six scripts** form the complete paper-grade test suite, with `data_checks.py` having both a pre-flight (CSV-integrity) and post-build (built-pickle integrity) mode.

### Complete test suite

| # | script + invocation | when | what it tests | paper section |
|---|---|---|---|---|
| 1 | `data_checks.py spreadsheet` | pre-flight | CSV integrity (no orphan inputs, no missing RR fields, K≤7, no broken chains, isolated naive_0 nodes flagged) | (build gate) |
| 2 | `data_checks.py post_run` | post-build | built-pickle integrity (no NaN CPTs, no degenerate cells, K respected by built net, naive_0 promotions actually fired) | (build gate) |
| 3 | `objective_rr_comparison_test.py` | post-build | per-row direction match + magnitude % err vs literature CI; DIRECT mode queries the row's `output`, DISEASE mode queries the row's `Type` if it's a known disease | §4 cycles table, abstract direction-match |
| 4 | `observed_evidence_auc.py` | post-build | per-disease AUC, observed-evidence-only (no NaN imputation), with per-target diagnostic-biomarker exclusion via `TRIVIAL_BIOMARKERS` | §4 per-condition AUC |
| 5 | `rho_gap_audit.py` | post-build | parent-co-parent ρ per target → LoTP-binding tier classification | §3 LoTP threshold + App. B |
| 6 | `ukbb_three_way.py` | post-build | net qRR vs UKBB midpoint vs NHANES marginal RR on n=81 held-out pairs; bucket A/B/C/D/E with severity D > E > C > B > A | abstract UKBB headline + §4 + App. F.5 |
| 7 | `validation_window_summary.py` | post-build | median/mean/max W from `bayesnet_initialize_output/*_validation.csv`; reports fraction with W > 0.05/0.10/0.25/0.50 and per-node violators | §4 median W=0.001 claim |

### What each test reports

**objective_rr_comparison_test.py.** For every spreadsheet row with an RR (Stat is set), tests two targets:
- **DIRECT** - query the row's `output` (the immediate edge child).
- **DISEASE** - query the row's `Type` if it's a known disease name (tests end-to-end propagation through the intermediate gate chain).
For each: clamp the parent to the row's input-values (OR-aggregated over multi-value rows), query the target, compute `q_rr = P(target=OUTVARS[0] | clamp) / baseline`, compare to `study_rr`. Reports per-row direction match, % error, within-CI flag, and aggregate stats (Pearson r, % within 25%/50%, etc.). Status codes: OK / FLAT / WRONG.

**observed_evidence_auc.py.** Per-condition AUC and whole-net mean/median. For each disease target T, builds evidence from each NHANES respondent's *actually-observed* parent values (NaN never imputed), excludes the per-target diagnostic-biomarker list `TRIVIAL_BIOMARKERS[T]` (e.g. `a1c`, `fasting_glucose`, `insulin` for diabetes; `hemoglobin` for anemia; specific cancer subtypes for `cancer`), queries `P(T=positive_val | evidence)`, computes AUC + 95% bootstrap CI + calibration intercept/slope. Run with `--targets <name>` for a single-condition spot check.

**rho_gap_audit.py.** For each target with K≥2 parents, computes the pairwise NHANES correlation between parent values, finds the max-pair |ρ|, classifies tier per the LoTP-binding rule (`ρ > 1/(1+σ)` for the cell-space ≥8 case). Tier counts (tight-committed / descriptive / abstain) drive the paper's per-tier UKBB direction-match.

**ukbb_three_way.py.** For each of the n=81 held-out UKBB exposure-outcome pairs, computes net qRR by clamping parent at UKBB's exposure-defined value, NHANES marginal RR independently from joint counts, and compares both to the UKBB-reported midpoint. Buckets each pair as A (all agree) / B (cohort artefact) / C (lit beats NHANES) / D (real model bug) / E (three-way disagreement). Severity: D > E > C > B > A.

### Retired tests (do NOT use for paper claims)

- `diagnose_architecture.py` - retired May 8 2026 to `scripts/retired/`. Walked DOWN through gates and tested "leaf clamp → walked-up target" against the row's `study_rr`, but the literature claim is for `parent → attached_to`, not the walked-up target. Caused recurring 0-WRONG-vs-N-WRONG disagreements between cache-warm and cold rebuilds. Replaced by `objective_rr_comparison_test.py`'s DIRECT + DISEASE modes.
- **Paper-claim AUC = `scripts/observed_evidence_auc.py`** - per-target diagnostic-biomarker exclusion (a1c→diabetes, blood pressure→hypertension, FEV1→COPD, BMI→obesity), target's own NHANES code excluded from evidence, observed-evidence-only methodology (never imputes NaN for absent answers). Writes per-target partial JSON (`paper/observed_evidence_auc_<label>.partial.json`) after every target. Pass `--targets` sorted by NHANES prevalence DESC so common diseases finish first.
- `extract_results.py` - many useful sections (calibration, evidence, ρ, study_rr_summary, nhanes_fidelity, chain_propagation, windows) but two sections should NOT be reported: Section 10 NHANES Individual AUC (target leakage → AUC≈1.000), and the query_rr direction count (uses immediate `attached_to` rather than the row's claimed disease; the May 7 OUTVARS-quartile patch papers over a CSV-design issue rather than fixing it). Replaced for paper-grade direction by `objective_rr_comparison_test.py`, for AUC by `observed_evidence_auc.py`.

### Interpreting results

- **DIRECT direction-match < 90% on `objective_rr_comparison_test.py`:** check the WRONG list for explaining-away or structural issues. WRONG concentrated on one disease's chain usually means a direct-bypass row is creating explaining-away on the chain's downstream gates.
- **DISEASE direction-match worse than DIRECT:** signal is not propagating through the chain (chain washout). Look at gate types between attached_to and the disease - multi-level any_of with weak inputs saturates toward 1.
- **AUC < 0.5 for any target on `observed_evidence_auc.py`:** real model error. Investigate. AUC ≈ 0.5 with tight CI bounds usually means the network has no signal for that target (chain washout) - paper should report "abstains" rather than claiming prediction.
- **Tier D non-zero on `ukbb_three_way.py`:** net disagrees with both literature and NHANES on those pairs. Real bug, not a cohort artefact.
- **Validation window median > 0.05 from build's saved windows JSON:** literature constraints are being widely relaxed by the QP solver. Check for over-constrained nodes.

---

## 6.bis. The Paper-Grade Tests (May 8 2026 update)

After May 8 2026 reorganization, the paper-grade test suite is the seven scripts in §6 above. This section maps each to what it claims and how external it actually is. Other tests (CI-crosses-null, OIS, chain propagation, evidence responses) are **tests of convenience** - useful for debugging but not headline evidence of correctness. The retired chain-walk levels of `diagnose_architecture.py` are no longer used because they conflate per-row literature claims with chain-mediation effects.

### The seven

| # | Test (script) | What It Claims | External? |
|---|------|----------------|-----------|
| 1 | **CSV integrity** (`data_checks.py spreadsheet`) | Spreadsheet has no orphan inputs / missing RRs / K>7 violations | Internal - pre-flight gate. |
| 2 | **Built-pickle integrity** (`data_checks.py post_run`) | No NaN CPTs, no degenerate cells, naive_0 actually fired where declared | Internal - post-build gate. |
| 3 | **Objective RR comparison** (`objective_rr_comparison_test.py`) | For every RR row in the CSV, clamping the parent to input-values shifts the target state (DIRECT = row's `output`; DISEASE = row's `Type`) by the study's RR | **Partially external** - study RRs come from literature. Tests that the QP's LS term converged on those targets. |
| 4 | **Per-disease AUC with biomarker exclusion** (`observed_evidence_auc.py`) | Per-respondent disease prediction beats random when diagnostic-equivalent labs are excluded from evidence | **Not external** - the network's CPTs were built partly from NHANES joint conditionals; AUC measures discrimination using non-tautological features only. |
| 5 | **LoTP rho** (`rho_gap_audit.py`) | Parent-co-parent ρ in NHANES per target → tier classification (tight-committed / descriptive / abstain) per the App. B threshold | **Not external** - ρ is computed from NHANES. Tests the structural condition for cross-CPT subset constraints to bind. |
| 6 | **UKBB external** (`ukbb_three_way.py`) | Net qRR vs UKBB midpoint vs NHANES marginal RR on n=81 held-out pairs the network never saw | **Genuinely external** - UKBB is an independent cohort; pairs were not in the literature corpus and the targets weren't fit during build. The bucket A/D distribution is the strongest external evidence in the paper. |
| 7 | **Validation window summary** (`validation_window_summary.py`) | Median / mean / max W per cell from the QP solver's binary-search widening | External-ish - measures the solver's compromise between literature and NHANES. Tight windows = literature was honored; wide windows = literature couldn't be jointly satisfied. |

Calibration and joint fidelity (extract_results.py's calibration + nhanes_fidelity sections) remain useful diagnostics, but they're not external by construction (the QP's DF term explicitly fits NHANES joint conditionals).

### Where NHANES enters at build time

**Three places**, not just priors (corrected Apr 20, 2026):

1. **Priors** (per-node marginal P(X=v)) - from NHANES population frequencies. Feeds the proto's prior vector.
2. **QP data-fit weights** (`sn_bayes/objectives.py:98 compute_nhanes_slot_weights`) - NHANES joint frequencies per parent-combination cell. Used as loss weights `w_i` in the QP data-fit term.
3. **QP data-fit targets** (`sn_bayes/objectives.py:211 _compute_nhanes_joint_targets`) - empirical P(outcome | parent1=v1 AND parent2=v2 …) computed directly from NHANES respondents. Used as targets `p'_i` in the data-fit term.

QP objective (`sn_bayes/dependency_v2.py:561`):

```
w_ls × (study-RR LS)  +  w_df × Σ w_i (p_i − p'_i)²  +  w_mono × (mono slack)
           ↑                              ↑
        literature                  NHANES joint
```

Current builds use `w_ls=1.0, w_df=1.0, w_mono=1.0` - all three active, with no reweighting across terms.

### Implications for claims

- **Joint Fidelity** and **NHANES AUC** are not independent tests of the network - they test how well the QP converged on one of its own targets. Keep them as diagnostics, not as headline external validity.
- **Objective RR** is closer to an external test because study RRs come from literature authors who had no access to our network. But the QP still aims at these targets, so direction-correctness measures solver convergence, not generalization.
- **Genuine external validation** (not currently implemented) would require: (a) holding out a subset of studies from the QP and testing them back, or (b) testing against a non-NHANES cohort, or (c) holding out NHANES respondents.
- At query time (`sn_bayes/utils.py:1744 get_frequencies`), the net is sealed - `pomegranate.predict_proba` runs on CPTs+priors only. No NHANES access. That's the only part of "not curve-fitting" that holds unambiguously.

### Study-Quality Checklist

*Needs verification through practice.* When a study passes all of these, the objective RR test should be within magnitude tolerance. When it fails, direction may still be right but magnitude may mismatch - that's a study issue, not a network bug.

A study row should have:

1. **Tight validation window** - the QP solver did not need to stretch CI to accommodate it (suggests the study fits the other evidence in that region).
2. **Narrow reported CI** - the study itself is precise. Wide CI = high uncertainty = noisy target.
3. **Reputable source** - meta-analysis > large cohort > single study. Pre-registered > not. Peer-reviewed.
4. **Adequate power** - n large enough for the reported effect size (OIS check).
5. **Correct target population** - matches NHANES demographics (age, sex, ethnicity, comorbidity). If study is on post-menopausal women and NHANES is general-population, population-correct the RR or accept magnitude mismatch.

   **Why this matters mechanically:** the OR→RR formula `RR = OR/(1−P0+P0·OR)` uses the target population's baseline P0 (NHANES). The OR coming in is the study's OR. If the study was run on a subpopulation, applying its OR with NHANES P0 implicitly assumes the OR **transports** from subpop to whole population. That is only true if (a) the effect is purely biological/mechanistic, or (b) there are no subpopulation-specific effect modifiers. Otherwise the computed RR is wrong - typically **too high** if the effect is stronger in the subpop than in general, **too low** if weaker. When transportability is doubtful, treat the study's OR as a *direction indicator and magnitude upper bound*; don't expect the net to match it exactly. Options if you have the data: use a pooled or stratum-weighted OR, or use age/sex-stratified node definitions in the spreadsheet.

   **Mandatory provenance rule:** if the OR (or any statistic) has been adjusted from the raw paper-reported value - for example to correct for subpopulation effect size, to convert between stat types, to weight multiple studies, or to apply any transformation beyond the Excel formula - the adjustment **must** be recorded in the comment column on the same row. The comment must include: (a) what was changed, (b) the original value, (c) the adjusted value, (d) the reason/method, and (e) a citation if the adjustment methodology comes from literature. Leaving an adjustment undocumented makes the row irreproducible and turns a study-quality issue into a data-integrity issue.
6. **OR/HR converted to RR** - using `RR = OR / (1 − P0 + P0·OR)` with the target-population baseline P0 from the appendix lookup (not the study's own P0). Excel formula in M/N columns does this; blank P0 = treat as 0 (matches Excel).

If a study fails criterion 5 or 6, direction should still be right but magnitude will be off. Document in the replacement log.

### Interpreting Aggregate Scores

- **Calibration**: MAE < 0.01 is fine. Over-5% count > 20 means drift - check QP regularization weights.
- **Objective RR**: direction % is what we publish. Magnitude % < 25% is excellent, < 50% is publishable. Direction wrong = architecture or study problem.
- **Window**: mean < 0.05 desirable. Median > 0.01 means many studies fighting.
- **Joint fidelity**: within-5% fraction > 0.9 desirable. < 0.7 means structural misfit.
- **NHANES AUC**: > 0.7 = useful. Mean > 0.95 on our network is suspiciously high - probably because diseases are defined from NHANES columns.

---

## 7. Diagnosing Problems

### FLAT (signal doesn't reach disease)
1. Check if there's an all_of gate in the chain. P(all K active) ≈ 0 for K≥3.
2. Fix: flip all_of → any_of if inputs are independent risk factors.
3. If the all_of is legitimate (true AND), reduce K by grouping.

### WRONG (signal goes wrong direction)
1. Check if it's explaining-away: correlated parents treated as independent.
2. Fix: add connecting studies between the correlated parents.
3. If it's structural (gateway pattern), restructure the architecture.

### Empty CPTs
1. Check if naive_0 is active - it changes proto state and can cause empty CPTs.
2. Check for NaN RR values in the config feeding into that node.
3. Check the node's parents - are they all built before this node?

### High Validation Windows
1. The studies attached to this node conflict - their CIs don't overlap enough.
2. Check for data entry errors, wrong stat types, or incompatible populations.
3. Consider replacing weak studies with stronger ones.

### WRONG from rare-event aggregator saturation (Apr 19 2026)
When a chain of `any_of` aggregators sits between rare-prevalence leaves and the disease, the aggregator's P(yes) stays near its tiny baseline regardless of any single leaf's evidence. Even anchoring a large RR at the disease-level distal edge (the "top" of the chain) produces only a minuscule expected shift, because the evidence path multiplies by the aggregator's P(activation) - which is ~0.03 for 3 stacked rare-exposure `any_of` gates.

Concrete math: if the top aggregator has P(yes) = 0.03 at baseline, leaf evidence raises it to ~0.04. Even with an anchor RR = 10 between aggregator and disease, the query RR at the disease = `[0.04·10·P_low + 0.96·P_low] / [0.03·10·P_low + 0.97·P_low] ≈ 1.01`, on the boundary of the diagnoser's direction threshold. The WRONG flag persists because the signal genuinely fails to discriminate at the disease level.

1. Diagnose: if 10+ WRONGs in one chain all have IDENTICAL query_rr (to 3 decimal places), you have saturation. Varying RR magnitudes with IDENTICAL query_rr = RR at anchor isn't being transmitted.
2. Fix options (in order of invasiveness):
   - Promote leaves to direct parents of the disease (bypass the saturating aggregators). Disease K grows but each path carries full RR.
   - Replace `any_of` with `avg` on the middle aggregator(s). NOTE: `avg` type has a code incompatibility with distal chains (INVARS shape mismatch) - would need code fix first.
   - Refactor the variable to have higher prevalence (e.g., "above median exposure" instead of "has rare exposure"). Changes what the node represents.
3. Known limitation in the Apr 2026 build: the pancreatic occupational-exposure chain (Ojajarvi 2000: chromium, nickel, silica, 4 hydrocarbons) produces 10 WRONGs from this saturation. All 7 study directions are correct vs paper; the WRONG flag is gate-structure, not sign error.

### WRONG from dominant equivalent_to parent
When a disease node has an equivalent_to parent (like sarcopenia for frailty) alongside distal parents, the equivalent_to dominates. Setting evidence on a distant leaf barely changes the conditions chain (already near 1.0), but the equivalent_to parent DROPS (explaining-away), and the net effect on the disease is negative.
1. Trace the signal: does the chain activate correctly up to the disease? If yes, the issue is at the disease CPT level.
2. Check if the equivalent_to parent drops when the distal parent activates - this confirms explaining-away.
3. Fix options: weaken equivalent_to, add connecting study between equivalent_to parent and the distal chain, or restructure so equivalent_to is not alongside many distal parents.

### Proven findings from theory testing (Apr 2026)
Tested on pretend nets with hand-set CPTs (like combining notebook scenarios):
- **any_of gates pass signal perfectly** - they're deterministic. Signal attenuation comes from the disease CPT (QP solver), not from the gates themselves.
- **dependency_distal propagates evidence sideways** - setting A=yes correctly increases P(B|A) through the connection. Confirmed with P(sg_B|A)=0.527 vs marginal 0.400.
- **Connections increase disease RR** - A→disease RR went from 1.537 to 1.666 (+8.4%) when B's correlation with A was modeled.
- **Saturated gates (P≈1) block connecting studies** - when a gate is almost always active, knowing a sibling is active can't push it higher. Target gates with P=0.2-0.8.
- **equivalent_to parents can dominate** - their sensitivity/specificity ≈ 1.0 makes them overwhelmingly informative, causing other parents to be explained away.

---

## 7.bis. Patterns learned in practice (Apr 20-21, 2026)

These are patterns and fixes learned during overnight iteration. Pattern names are action-oriented so an LLM can pattern-match its own failure modes.

### Pattern: duplicate-encoding parents

**Symptom:** a disease has TWO parents that encode the same underlying quantity with different granularity. Example: `bmi` (2-state: obesity/not) and `bmi_naive` (5-state: severe_obesity/obese/overweight/normal/underweight). Both appear as independent dependencies. Direction of either parent flips in the objective RR test.

**Why it fails:** the CPT has cells like "bmi=non-obese AND bmi_naive=severe_obesity" which are impossible in reality (severe_obesity IS obesity) but the QP treats them as independent. The QP compromises between two LS targets that contradict each other on half the cells, and the resulting CPT is bad.

**Fix:** blank one of them. Keep the higher-granularity one (more information). Document in comment: "redundant encoding blanked; kept bmi_naive as finer-grained."

**Detection check:** for each disease, look at its parents' definition rows. If two parents share the same NHANES `code` with different `index1` ranges → duplicate encoding. In our case: 9 cancer nodes had this issue. Fix restored +1.9pp direction on 14 rows.

### Pattern: monotonicity overreach

**Symptom:** a parent with ordinal states (`reverse=N` set) has the OUTER states correctly anchored (severe_obesity up, underweight up) but MIDDLE states get pulled below baseline. The direction of a middle-state study row flips.

**Why it fails:** the monotonicity constraint enforces adjacent ordering (`P(disease|severe) > P(disease|obese) > ... > P(disease|normal)`) and separately (`P(disease|underweight) > P(disease|normal)`). Middle states get squeezed between the two anchors; with no strong LS anchor at a middle state, monotonicity + LS solve to push the middle below baseline (since only the extremes have strong anchoring studies).

**Fix:** blank the middle-state study rows that have small effect sizes (e.g., bmi_naive=overweight with RR=1.11). Direction on severe_obesity and normal/underweight survives; middle states interpolate.

**Detection:** query the ordinal parent at each state. If outer states go UP/DOWN correctly but a middle state goes below baseline → monotonicity overreach on that middle state.

### Pattern: any_of aggregator saturation (rare-event)

**Symptom:** a disease has a distal edge through an intermediate `any_of` aggregator. Clamping any single leaf should raise the aggregator to 1.0, which should raise the disease. But the query returns P(disease) ≈ baseline (saturation, query RR ≈ 1.0).

**Why it fails:** when the disease has low NHANES prevalence (< 1%), even with aggregator=yes the CPT for P(disease|aggregator=yes) can't differ from baseline much because the solver finds CPT values satisfying all constraints where the aggregator contribution is flat. Essentially the signal has nowhere to go - the aggregator→disease distal edge is too weak to move a near-zero disease probability.

**Fix attempted:** flatten the aggregator - promote its leaves directly to the disease (bypass). This works sometimes (MPV fix: 5 of 6 rows corrected). Doesn't always work for VERY rare outcomes (pancreatic cancer at 0.1% - even direct-parent study RR can't move baseline enough).

**Detection:** in objective RR test output, multiple rows sharing the same target have query_rr values clustered around 1.0 (e.g., 0.97, 0.99, 1.00). They're all absorbed by the same chain's saturation.

### Pattern: distal-edge saturation at low prevalence

**Symptom:** direct path A → aggregator → disease. Aggregator responds correctly (P=1.0 when A is clamped), but P(disease | aggregator=yes) ≈ baseline.

**Why it fails:** the distal RR calculation through `calculate_rr_for_distal_var` produces a tiny multiplicative shift at low baseline probability. For a disease with 0.1% NHANES prevalence, even an RR=10 in the CPT gives final marginal P(disease|evidence) ≈ 1% - meaningful in absolute terms but within the "flat" threshold of the direction test.

**Fix:** no clean fix at this prevalence. Options:
- Reformulate the disease with higher base rate (e.g., "lifetime pancreatic cancer" instead of "current pancreatic").
- Use `dependency` instead of `distal` Type with an explicit literature RR for the aggregator→disease edge (overrides the automatic calculation).
- Accept as a structural limit and document.

**Detection:** disease has rare NHANES prevalence (<1%) AND all its distal-chain studies show query_rr clustering near 1.0.

### Pattern: subsumes domination

**Symptom:** disease B has a parent A via `subsumes` (meaning A is a broader category that includes B). Other parents of B have tiny effect in the query because A's subsumes relationship makes P(B|A=yes) ≈ 1.0, and P(A=yes) barely changes when we clamp other parents.

**Why it fails:** the subsumes relationship is defined via sensitivity/specificity near 1.0. Other parents' effects are drowned by A's deterministic contribution.

**Fix:** accept that other parents can only affect B through paths that also shift A. If a study's RR for another parent looks wrong, the paper's claim is likely not testable in the current network structure. Blank the row if the study is weak and the subsumes truly dominates biologically.

**Detection:** look for `Type=subsumes` on the disease's parent list. If found, small-effect direct-parent studies will be dominated.

### Pattern: co-parent correlation

**Symptom:** two or more parents of a disease are correlated in NHANES (metabolic syndrome cluster, etc.) but treated as independent in the net. Clamping one at a time gives tiny effect; the signal "should" propagate through correlated co-parents but doesn't.

**Why it fails:** Bayesian inference doesn't know bmi and fasting_glucose are correlated unless there's a network edge. So clamping fasting_glucose=high leaves bmi at its prior - you lose the implied bmi shift.

**Fix:** add a **connecting study edge** between the correlated parents. E.g., add `bmi → fasting_glucose` with a literature RR. Then when we clamp fasting_glucose=high, Bayesian inference backpropagates to raise bmi=obesity, and the disease CPT sees BOTH risk factors elevated.

**Detection:** domain knowledge (metabolic syndrome, lipid profile cluster, etc.), or ρ audit showing low parent correlation in the net vs high in NHANES.

### Pattern: orphan CPT on blank (pomegranate torch crash)

**Symptom:** blanking a study row crashes the build with `torch.zeros() invalid args`.

**Why it fails:** if you blank the ONLY study row feeding a `dependency_nhanes_explicit` node, the node ends up with 0 parents but still has its dependency Type → empty CPT → pomegranate crashes.

**Fix:** before blanking a row, check how many OTHER study rows feed the same output. If none, do NOT blank. Either:
- Change the node's Type from `dependency_nhanes_explicit` to `discrete_nhanes_explicit` (no dependency, just prior) AND blank the row.
- Keep the row intact and accept the weak signal.

### Debugging workflow

1. **Run `scripts/objective_rr_comparison_test.py`** on the current net. It tests every xlsx row with a study RR. Report includes a `direction_match` flag per row.
2. **Cluster the direction-wrong rows** by `row_output` and by `row_input`. Clusters point to the pattern: shared-output = saturation at that node; shared-input = co-parent correlation.
3. **Categorize each cluster**:
   - query_rr ≈ 1.0 → saturation (aggregator or distal-edge)
   - query_rr < baseline for UP study → direction flip (check duplicate-encoding, monotonicity, subsumes)
4. **Subnet test before full rebuild**: use `scripts/subnet_builder.py --target <node>` with a patch file. 2-3 min vs 15-25 min for full rebuild. Catches easy failures (structural errors, df_validate fails).
5. **Apply proven fix via JSON patch**: `scripts/apply_patch_to_xlsx.py --patch <patch>.json` mutates the xlsx reproducibly.
6. **Full rebuild + test panel**: one rebuild with batched fixes rather than one-fix-per-rebuild.
7. **Compare builds** via `scripts/five_core_chart.py`. Look for monotone improvement across the 5 core metrics.

### Which metrics to track during debugging

The 5 core tests (Section 6.bis) are the only ones trusted for paper claims, but during debugging also watch:

- **Diagnose_architecture WRONG count** - quick filter before running full tests.
- **Chain propagation** - for distal chains, does each hop carry signal?
- **ρ (rho_gap_audit)** - parent correlations in net vs NHANES. Low ρ means parents are independent in net but correlated in reality → co-parent correlation pattern.

---

## 7.ter. Patterns learned in practice (Apr 28, 2026 -- bucket-entry cycles)

This section adds patterns observed while pushing previously-abstaining major
clinical endpoints (stroke, all_cause_mortality, diabetes, CAD, lung_cancer,
frailty, cardiovascular_mortality) into the direction-committed bucket --
i.e., into the set of targets where every cited study row matches in
direction *and* lands within 50% of its literature point estimate. Each
pattern names the symptom, the underlying mechanism, the fix, and the
typical failure mode of the fix.

### The meta-strategy: start with the LoTP-committed core, grow outward

Before reading the individual patterns below, internalise the build
philosophy they serve. **A useful net is built by starting with the
committed core -- the set of targets where the math actually pins
everything down -- and growing the committed subset outward, one
principled addition at a time.**

The committed core has two structural sources (Section §3 of the SD4H
paper):

1. **Small-cell full determination.** A target with K = 1 or 2 parents
   has 2--8 cells; per-parent NHANES marginals + per-output NHANES
   marginal + simplex bounds + literature row(s) provide as many
   independent linear equations as cells. The QP solves the system; the
   feasible polytope collapses to (near) a point, the validation window
   `W` collapses, and the queried RR matches the literature centre to
   within solver tolerance. No further cleverness needed.

2. **LoTP-active multi-parent regime.** A target with K >= 3 parents has
   2^K cells, more than literature rows can constrain on their own. The
   law-of-total-probability subset constraints across CPTs become the
   load-bearing structure -- but they only *bind* (i.e., add information
   beyond what simplex + per-parent marginals already imply) when at
   least one parent pair has correlation `|rho| > 1/(1+sigma)`, where
   `sigma` is the QP's effective noise scale. In our network this
   threshold lies near `|rho| = 0.14`.

Targets that satisfy regime 1 *or* regime 2 enter the committed core
deterministically -- you do not have to optimise toward them, the
structural rule selects them. Targets that satisfy *neither* sit in the
descriptive tier (still useful: every direction correct, every row within
50%) or the abstain tier (no commitment).

**The growth operator** is therefore not "add another row to the model";
it is one of:

- **Add a 1--2-parent target with a literature row.** This is a
  direct entry into regime 1.
- **Add a parent-correlation-providing study between two existing
  parents of an existing K >= 3 target.** This raises that target's
  parent-pair `|rho|` past the activity threshold and pulls it into
  regime 2 *without requiring a multi-factor cohort study to exist*.
  This is the most leveraged single addition the system has: one rho
  entry can promote a whole target.
- **Substitute a wide-CI direct row with a tighter cohort-meta** on the
  same edge. Doesn't change which regime a target is in, but tightens
  its committed-tier window.

Patterns that are *not* growth operators in this sense:

- Adding a third or fourth row to the same parent of an
  already-committed target. Past one binding row, additional rows on
  the same cell only deliver least-squares compromise (mechanism (i)
  in App. A of the SD4H paper).
- Adding a row that increases K without raising rho on any parent
  pair. Larger K dilutes signal across more cells; without LoTP
  activity, the descriptive-tier behaviour worsens.

**Construction rhythm.** A typical iteration cycle looks like:

1. Run `objective_rr_comparison_test.py`. List the targets currently in
   the committed bucket and the descriptive tier.
2. Pick *one* target to promote (descriptive -> committed) or *one* new
   leaf to bring into regime 1.
3. Identify the principled growth move: parent-correlation study, gate
   flip, tighter-CI substitution, cycle-22 bypass.
4. **Pre-flight rule check before launching the build.** Verify the
   proposed change satisfies *every* one of:
   - K (direct CPT-parent count) of the target stays <= 5 (hard ceiling
     6 per design rule §8 step 2). Cycle-22 bypass costs K += 1 *per
     parent added*; if you want to add N bypasses, either route them
     through a NEW intermediate aggregator (K += 1 total instead of N)
     or split into N separate cycles.
   - Single-path discipline: a parent reaches the disease through ONE
     path. If the cycle-22 bypass adds a direct edge for a parent that
     also reaches the disease via an aggregator, either remove the
     aggregator path for that parent or accept the redundant-path
     constraint coupling cost (which can OOM the QP -- see "Failure
     mode: redundant pathways" below).
   - Subnet-first: the subnet build for the target catches gross
     direction propagation; do not commit a full rebuild before the
     subnet shows the change has the expected effect.
5. Apply the change. Subnet-test. Full rebuild.
6. Compare bucket size before/after. If it grew (or stayed the same on
   bucket count but tightened the committed tier's median W), commit
   under principled-change rules. If it shrank, revert. Either way,
   write down which regime the change targeted, so the rationale is on
   record.

The sections below name the specific moves available at step 3, and
the failure modes that block them.

### Pattern: cycle-22 bypass (direct row at the disease level)

**Symptom:** a disease has a strong-effect study (literature RR 2--8)
reaching it only through one or more intermediate logical-aggregator nodes
(`<disease>_behaviors`, `<disease>_lifestyle`, etc.). Objective RR test
shows model RR collapsed to ~1.0 on that row.

**Why it fails:** at each aggregator step, the marginal RR is averaged over
the unconstrained sibling branches (Section 6.bis, 5-mechanism account in
the SD4H paper appendix). With the literature only constraining the leaf
edge, the disease-level marginal sees only a small fraction of the leaf
effect.

**Fix:** add a *new* row in the spreadsheet whose `output` column is the
disease itself, `input` and `input values` mirror the original chain row,
and `Stat` / `Stat Value` / `Plus minus` carry the same study's effect.
Cite the same paper. The QP now has a literature constraint directly on
the disease's CPT cell `P(disease | parent=v)` and is forced to fit the
literature RR there.

**Where it works:**
- Stroke: cycle 2 substituted Ungvari 2025 cohort meta -> bucket entry.
- All-cause mortality: cycle 4 added direct `mic1_gdf15 -> all_cause_mortality` HR=2.52 -> bucket entry.
- Diabetes: cycle 6 added direct `daily_time_sitting -> diabetes` HR=0.5236 -> bucket entry.

**Where it fails (and why):**
- CAD K=15: cycle 7 added direct `intermittent_fasting -> CAD` OR=2.70.
  The original IF row's max-err only dropped 61.6% -> 56.4%; the new direct
  row landed at 59.4%. Both intermittent-fasting model values stayed at
  ~1.07 because CAD has 14 *other* study constraints competing for the
  same CPT (mechanism (v): large-K under-determined-cell drift; mechanism
  (i): least-squares compromise on colliding bands).
- Lung cancer K=17: cycle 8 added 2 direct smoking rows (RR=8.43, 5.50).
  Subnet test showed max-err 25%; full build delivered max-err 65% on the
  same row, because the global LoTP constraints from outside the subnet
  closure pulled the cell back toward unity.

**When to expect it to work:** the disease has a small parent count
(K <= 5 per design rule §8 step 2), the leaf study's RR is large
relative to other parents' contributions, and the existing direct-row
population is sparse on the target cell.

**Hard rule: cycle-22 bypass is a per-parent move with cost K += 1.**
If you want to bypass N chain-mediated parents:

- **DON'T** add N direct rows to the same disease in one cycle. If the
  disease's K is already >= 5, that violates the K-ceiling rule and
  blows up the QP's LoTP-coupled constraint matrix. Memory cost
  scales superlinearly because each newly-direct parent is *also*
  still a parent of the existing aggregator chains, creating coupled
  cross-CPT subset constraints. (This caused a WSL2 VM crash on Apr
  28, 2026 -- 7 frailty bypasses bundled into one cycle pushed K from
  5 to 12 and the QP's constraint matrix exceeded the 31 GB WSL
  memory ceiling.)

- **DO** route the N bypasses through one NEW intermediate aggregator
  (K += 1 total): create a new `any_of` (or `all_of` if appropriate)
  node whose parents are the N leaves you want to promote, and make
  that new node a single direct parent of the disease. Or split into
  N separate cycles, each adding ONE direct edge (K += 1 per cycle),
  and remove that parent's existing aggregator-chain edge so the
  signal flows through one path only.

- **REVERIFY** before launching the build. The pre-flight rule check
  in the construction rhythm (§7.ter meta-strategy, step 4) is
  mandatory. K-budget check is the rule that prevents OOM.

### Pattern: per-stratum direct edges (mutually-exclusive aggregator leaves)

**Symptom:** a target has a `distal` aggregator whose leaves are
mutually-exclusive partitions of an underlying continuous or
multi-categorical variable (e.g., age strata of a cholesterol effect:
`high_cholesterol_young_adult`, `high_cholesterol_adult`,
`high_cholesterol_elderly`). The literature reports DIFFERENT effect
sizes per stratum (RR=4.34 / 2.79 / 2.43). The model produces a SINGLE
uniform RR (e.g., 1.22) regardless of which stratum is clamped.

**Why it fails:** the `distal` Type's Monte-Carlo aggregation computes
ONE aggregator-to-disease RR by averaging over the joint NHANES
distribution of leaves. Mutually-exclusive leaves all funnel through
this single computed RR. The per-stratum literature signals are lost.

**Why connecting-study (option 2) does NOT fix this:** the strata are
*anti-correlated* by definition (a person is in exactly one age group
at a time). NHANES already encodes this. Adding a connecting study
restating the anti-correlation tells the QP nothing new. LoTP doesn't
help; the issue is averaging, not under-determination.

**Why dependency_distal as documented does NOT fix this either:** the
documented `dependency_distal` pattern (§Connecting Studies) addresses
*correlated-sibling explaining-away* via `equivalent_distal` wrap and
a connecting study from a positively-correlated sibling. Mutually-
exclusive strata don't have a positively-correlated sibling.

**Fix: add direct edges from each stratum to the disease.** For each
stratum, copy the existing leaf-study row (which currently has
`output=<aggregator>, input=<stratum>`) and add a parallel row with
`output=<disease>, input=<stratum>` and the same Stat values. Each
stratum now constrains its own CPT cell on the disease.

**Cost:** K(disease) increases by the number of strata. For a 3-stratum
fix on a target with K=4, K becomes 7 (over the K<=5 design rule's
budget) and inference time grows: 2^K cells per query, so K=4→7 is 128
cells per query (still very fast, ~ms scale). Acceptable when:
- The disease is a load-bearing endpoint (e.g., a paper-claim target)
- The strata literature is high-quality (cohort meta-analyses)
- The aggregator-collapse is the only thing keeping the disease out of
  the committed bucket

**Empirical confirmation (Apr 28, 2026):**
`cardiovascular_mortality` had 3 cholesterol-strata rows all collapsing
to `model_rr=1.22` against literature 4.34/2.79/2.43 (max-err 72%).
Adding 3 direct edges differentiated them: model_rr 3.20/[~2.6]/[~2.4]
respectively, max-err on the previously-72% row dropped to 26%. The
remaining elevated_cholesterol strata (a separate any_of aggregator
with its own leaves) still collapsed at model=1.14, requiring the same
fix to be applied there too. Per-stratum direct edges work; they need
to be applied to *each* aggregator that has the strata-collapse
pattern, not just one.

**When NOT to use this pattern:** if the target's K is already large
(7+) and the leaves are independent risk factors (not strata
partitions), use connecting studies between direct CPT parents
instead. The strata pattern is specifically for the partition case.

### Pattern: connecting-study placement matters

**Symptom:** following the manual's option-2 / dependency_distal
pattern, you add a connecting study between two correlated risk
factors expecting LoTP to activate at the target's CPT and tighten the
target's marginals. Result: no change. The target's rows behave
identically before and after.

**Why it fails:** the manual's pattern works when the connecting study
is between two **direct CPT parents of the target**. If the parents
are deep inside an aggregator chain - leaves of an aggregator that's
itself a leaf of another aggregator that's a parent of the target -
the connecting study activates LoTP at the *aggregator's* level, not
at the *target's* level. The target's CPT never sees the new
constraint.

**Diagnosis:** before adding a connecting study, check the target's
direct CPT parents (rows where `output=target` and `input` is
non-blank). The connecting study's `output` and `input` should both
appear in that parent set. If they don't, the connecting study is
deeper in the chain than the LoTP constraint can lift.

**Empirical confirmation (Apr 28, 2026):**
For frailty (direct parents: `sarcopenia, frailty_hallmarks,
frailty_behaviors, frailty_conditions, dependency_priors`), tried
adding a connecting study `depression → polypharmacy` (OR=2.18 from
Gu 2010). Both depression and polypharmacy are aggregator-internal
leaves, several hops below frailty's CPT. Subnet result was
*identical* to the no-connecting-study baseline: max-err 68.4%, mean
err ~26.7%, depression row model still 1.22. The connecting study
activated LoTP between depression and polypharmacy at the
nervous_dysfunction / chronic_illness aggregator levels, but that
information never propagated up to frailty's CPT.

**Fix:** to lift frailty's marginals via connecting studies, the
connecting study would need to be between two of frailty's *direct*
parents -- e.g., `sarcopenia ↔ frailty_behaviors`. But these are
typically aggregator nodes that don't have published literature
correlations. Where direct-parent connecting studies aren't available,
consider:
- Promoting an aggregator-internal leaf to a direct CPT parent (cost:
  K += 1) so it can participate in connecting studies at the disease
  level
- Using the per-stratum direct-edge pattern above if the leaves are
  partitions
- Accepting the descriptive tier (per the meta-strategy's "growth
  operators" -- not every target promotion is achievable with the
  available literature)

### Pattern: tighter-CI substitution

**Symptom:** a direct study row exists for the target, but its CI half-width
is wide (e.g., +-0.4 RR or +-0.7 SMD) because the underlying study is small,
narrative, or single-cohort. The QP's windowed band lets it land far from
the point estimate while still satisfying the constraint -- model RR drifts
20--50% from the literature RR.

**Why it fails:** the per-study band `s_i +- kappa*omega*CI_i` is wide
enough to admit a wide range of cell values; the QP picks the value that
minimizes least squares against *other* row constraints, sacrificing
fidelity to this one.

**Fix:** find a higher-quality meta-analysis or cohort meta on the same
exposure-outcome pair with similar effect direction but a tighter CI.
Replace the existing row's `Stat Value` and `Plus minus`, log the swap in
the `comment` column with the new citation.

**Where it works:**
- Stroke: replaced narrative-review OR=0.63+-0.195 with Ungvari 2025
  cohort meta HR=0.88+-0.035 (N=860,000) -> stroke bucket entry.
- All-cause mortality: replaced single-cohort Wiklund 2010 GDF15 HR=2.61
  with Xie 2019 cohort meta HR=2.52+-0.455 -> all_cause_mortality bucket
  entry.

**Where it fails:** if the existing CI is already tight, substituting a
similar effect-size with similar CI doesn't change anything. And if the
literature genuinely disagrees (one meta says RR=2.0, another says RR=3.5),
substituting the *more contradictory* study makes things worse.

### Pattern: principled-change cycles supersede strict-improvement gates

**Symptom:** during iteration, the strict "accept only if every metric
improves" rule blocks moves that are mathematically principled (e.g.,
adding a literature row that bumps a parent-pair |rho| above the activity
threshold; flipping a gate from `all_of` to `any_of` because the parents
are independent risk factors). The blocked move is the right move; some
secondary metric drift is the cost.

**Fix:** for principled cycles, accept the change if (a) it is
*causally honest* (the parent really does affect this output through the
proposed mechanism); (b) the move is *mathematically grounded* (counts
equations vs unknowns, brings rho above threshold, resolves a known
structural under-specification); (c) the rationale would survive a domain
expert's causal review. Log secondary regressions and revisit them later.

This rule SUPERSEDES the strict win/loss gate from
`feedback_systematic_iteration` for principled-change cycles. It does not
license random tweaks: "principled" means a real causal or structural
argument, not "this number went up."

### Pattern: subnet result does not project to the full build

**Symptom:** subnet builder run on a single target shows large
improvement (say, max-err 79.5% -> 25%); full rebuild with the same xlsx
delivers a much smaller improvement (max-err 79.5% -> 65%) on the same row.

**Why it fails:** the subnet builder restricts the QP to the dependency
closure of the target. The global cross-CPT subset constraints from outside
the closure (LoTP relations through nodes that are also parents of
*other* targets) are absent. Without those constraints binding cell
values, the QP has more freedom inside the subnet and can satisfy the
target's literature rows more closely. When you re-run the full build,
those external LoTP constraints pull cells back toward values consistent
with the rest of the network -- and the target's row error grows.

**Fix:** still use subnet for fast verification (catches structural
errors, dependency closures, gate-flip propagation), but DO NOT report a
subnet-only improvement as a paper claim. Always re-validate on the full
build before committing.

**Mnemonic:** subnet is the necessary condition; full build is the
sufficient condition.

### Pattern: K vs constraints budget

**Symptom:** a disease with very high parent count (K = 15 to 30) has all
direction-correct rows but several rows over the 50% bucket threshold,
including ones where the literature RR is large. Adding more direct rows
to the same target does not consistently move the offending rows below
50%.

**Why it fails:** the CPT has 2^K cells; with ~K literature rows, only K
linear functionals of the cell vector are constrained. The remaining
2^K - K cells are fixed by monotonicity priors plus LoTP subset
equalities. Those priors are slow to propagate strong signals to
specific marginals, so a strong-effect row's queried RR drifts toward
unity even though its constraint is in the QP.

**Fix options (no single one is reliable):**
- Prune weak parents (small-effect rows that don't move the bucket
  decision) to lower K.
- Tighten the CI on the strongest-effect rows so their bands dominate
  the QP solution.
- Add a connecting study between two strong parents (raises rho, which
  activates the LoTP subset constraint -- Section 6.bis on activity
  threshold).
- Accept the disease in the descriptive tier rather than the
  tight-committed tier; the 5-mechanism explanation in the SD4H paper
  appendix is the principled rationale.

### Pattern: wrong gate semantics (`all_of` vs `any_of`)

**Symptom:** an aggregator gate that conceptually fires on *any* risk
factor (any high blood lipid, any low-physical-activity behaviour, any
inflammatory dietary deficiency) is encoded as `all_of`. The aggregator
output stays at 0 unless every single parent fires; the parents'
literature signals never propagate.

**Fix:** flip the gate to `any_of`. Use `all_of` only for true
biological conjunctions where ALL inputs are required for the concept
to apply (e.g., central obesity = high BMI AND large waist circumference;
elevated cholesterol-by-age = at least two adjacent age-strata classifications).

**Heuristic:** if the gate's parents are independent risk factors -- each
contributes risk on its own without the others -- it's `any_of`. If the
gate's name is a syndrome or compound state requiring co-occurrence, it
might be `all_of`. When in doubt, ask whether a domain expert would say
"either alone is enough" (any_of) or "both at once define the concept"
(all_of).

**Cycle 9 example (in progress at time of writing):** flipped
`cad_behaviors` from `all_of` to `any_of`. Previously the gate required
*both* poor diet AND no intermittent fasting; clinical reality is that
either alone elevates CAD risk.

### Pattern: five mechanisms behind descriptive-tier residuals

A disease can be in the descriptive tier (every row direction-correct,
every row within 50%) and still show non-zero %-error on individual rows
even though LoTP holds exactly in BN inference. The residuals trace to:

1. **QP least-squares compromise on colliding bands** -- two studies on
   cell-overlapping rows can't both be hit at their literature centre
   under the QP's interval constraints; LS picks the projection.
2. **Transportability gap** -- each meta-analysis's marginal RR averages
   over its own cohort's joint of unclamped parents; the model averages
   over a NHANES-anchored joint. Different joints, different RRs, even
   with every cell satisfying its constraint exactly.
3. **Bands are intervals, not points** -- the QP can place any cell
   value within `+-kappa*omega*CI` of the literature centre.
4. **Inter-study contradiction under LoTP** -- two metas on different
   parent-clamps of the same disease may imply contradictory subset
   sums on shared cells; QP returns the LS projection.
5. **Large-K under-determined-cell drift** -- ~K literature rows can't
   pin 2^K cells; unconstrained cells inherit monotonicity priors whose
   drift propagates onto the constrained marginals.

The full statement is in App. A of the SD4H paper. Use this checklist
when triaging a descriptive-tier residual: identify which mechanism is
dominant, then either accept (descriptive tier is honest) or attempt the
relevant principled fix (substitute, prune K, add connecting study, flip
gate).

### Updated debugging order

Augmenting Section 7.bis's debugging workflow with the Apr-28 patterns:

1. Run `objective_rr_comparison_test.py`. Cluster direction-wrong AND
   over-50% rows by target.
2. For each over-50% target, classify the worst row's failure mode:
   - Goes through aggregator chain -> try cycle-22 bypass.
   - Already direct, wide CI -> try tighter-CI substitution.
   - Already direct, narrow CI, model still ~1.0 -> diagnose K vs
     constraints budget; consider pruning a weak parent.
   - Aggregator gate looks wrong -> consider all_of -> any_of flip.
3. Subnet-test the proposed fix.
4. Full rebuild. Re-validate on the whole-net obj_rr.
5. Accept iff principled rationale survives domain review (the
   principled-change rule above), even if a secondary metric drifts.
6. Commit with a message that names the cycle, the target, and the
   pattern applied.

---

## 7.quater. Patterns learned in practice (Apr 29-30, 2026 - subnet-growth + mechanism-mixing cycles)

### M2/M4/M7 mechanism-targeted single-row fixes are compounding

Building from a frozen cycle-9a baseline, applying mechanism-targeted single-row fixes
in two waves (cycle 11: 5 c13-script connecting studies; cycle 12: 6 implausible-row
blanks) lowered median %-err from 17.95% → 17.50% on 380 obj_rr rows, with direction
gain 94.74% → 95.78% and within-50% 88.68% → 90.50%. **Key insight**: M2 (add
connecting studies) and M7 (remove implausible studies) are independent moves that
compound additively. M4 (low-base-rate code-level inversion) is paper-limit only;
M3 (aggregator attenuation) needs tiny-net validation before scaling.

### Pattern: M7 implausible-SMD blanking

**When to apply**: a row's `Stat=SMD` with `|Stat Value| > 1.0` AND the source is a
single underpowered RCT (not a meta-analysis), AND the converted RR is extreme (>3
or <0.3) for the given control probability (P0).

**How to verify it's M7 and not legitimate**: read the citation. If the source paper
is a single-arm trial with n < 100 per group, the SMD is high-variance and won't
replicate at meta level. The converted RR pushes the QP into simplex corners,
degrading multiple downstream targets via cross-CPT explaining-away.

**Action**: blank the row's `Stat`, `Stat Value`, `Plus minus`, `RR Stat Value`,
`RR plus minus` fields. Keep `output`, `input`, `input values` so the structural
edge survives. Document in the `comment` column: original values, source PMID, and
"M7 implausible single-trial SMD".

**Empirical confirmation (Apr 29-30, 2026)**: blanking 5 rows from a single
Akkermansia muciniphila trial (Depommier 2019, n=40, rows 599/686/964/968/1290) and
2 rows from extreme caloric-restriction studies (rows 895/897) cleared 4 abstain-m
targets and dropped whole-net mean %-err by ~6 points without losing direction.

### Pattern: M4 row-removal at low base rate × rare parent

**When to apply**: target's NHANES base rate is very low (P(disease) < 0.005) AND
the parent's NHANES prevalence is also rare (P(parent_yes) < 0.05) AND the row's
literature direction is consistently wrong in the model output.

**Why**: simplex bound P(disease ∧ parent) ≤ min(P(disease), P(parent)) dominates;
the QP can't honor literature direction without violating probability law. Same
mechanism as the pancreatic-cancer chemical-exposure rows (per memory, code-level
CPT inversion bug).

**Action**: blank the row's Stat fields like M7. Document as "M4 paper limitation".
Direction-correct on the target then improves immediately.

**Empirical confirmation**: liver_cancer ← heavy_alcohol RR=2.5 inverted to 0.84
in c11. After blanking row 2444, liver_cancer direction recovered to 1/1.

### Pattern: rows with `Type=node_name` are STRUCTURAL distal-links - never blank

**When this matters**: any row where the `Type` column contains the name of another
node (e.g., `Type='c_reactive_protein_mg_L'`) is part of a distal chain. Its Stat
values feed into `calculate_rr_for_distal_var` for the chain.

**The pitfall (April 29, 2026)**: blanking such a row's Stat fields broke the
build with `KeyError: 'OUTVARS'` in `dependency.py:384` during recursive
`parse_dependency`. The chain expected the row to define a target's CPT but found
no usable values.

**Action**: before blanking, check the `Type` column. If it's a node name (rather
than `nan`, `equivalent_to`, `is_a`, `subsumes`, `dependency_priors`,
`dependency_nhanes_explicit`, `naive_0_nhanes_explicit`, `discrete_nhanes_explicit`,
`distal`, `dependency_distal`, `equivalent_distal`, `any_of`, `all_of`), the row
is a distal-chain link and must be REMOVED entirely or REPLACED, not blanked.

### Pattern: c13-script in-memory edges may be lost across cycles

**When this matters**: build scripts (e.g., `apply_cycle13_build.py`) sometimes
add literature edges in-memory at build time without persisting to xlsx. If a
later "clean rebuild" branch starts from a different xlsx state, the in-memory
edges are lost and the rebuild's metrics regress relative to the original cycle.

**Specific finding (April 29, 2026)**: cycle 13's 5 medical-knowledge edges
(htn-bmi, htn-sleep_apnea, liver-alcohol, ACM-frailty, CAD-depression) lived only
in the build script. The clean-rebuild branch's cycle-9a baseline was missing
all 5, contributing to a ~3.4pp direction regression vs cycle 13.

**Action**: persist all in-memory build-script edges to xlsx as soon as they
prove useful. The xlsx is the source of truth; build scripts should be
xlsx-loaders only, not edge-injectors.

### Pattern: PubMed verification can correct LLM-extraction subgroup mislabeling

**When to apply**: a study row's direction is wrong despite the paper being
well-cited. The LLM may have attached the effect size to the WRONG subgroup
(e.g., "high X" vs "low X" of a continuous variable, or "yes" vs "no" of a
binary).

**Action**: WebFetch the PMID's PubMed abstract. Verify what comparison the paper
actually reported. If the LLM mis-labeled the subgroup, change the `input values`
field to the correct subgroup; keep the `Stat Value` unchanged. Document as
relabel, not inversion.

**Empirical confirmation (April 29, 2026)**: cardiac_event ← endothelial_progenitor_cell
relabeled `high_above_200` → `high_above_200_not` after PMID 27073015 verification
(Rigato 2016 reports "reduced CPC/EPC levels were associated with a ~2-fold
increased risk", not the inverse). Cycle-9a's 0/3 direction recovered to 3/3 in c11.

### Pattern: U-shape papers are a systematic LLM-extraction failure mode

**When to suspect**: a paper reports a U-shaped or J-shaped dose-response curve
(e.g., IGF-1 mortality, BMI mortality, alcohol-CV) and a single row in xlsx is
attributed to that paper.

**The pattern (per 2nd-LLM independent re-extraction, Apr 29, 2026)**: the original
LLM extracts ONE tail's HR (e.g., low IGF-1 HR=1.33) and applies it to the row
asking about the OTHER tail (e.g., quartile_4 high IGF-1). Two rows in the
network's seed=7 30-row sample exhibited this (rows 505 and 1463, both U-shape
mortality papers).

**Action**: for any row whose paper title contains "U-shape", "J-shape",
"biphasic", "non-linear", verify both tails of the curve are encoded as separate
rows with the correct subgroup-specific values.

### Workflow: subnet test BEFORE full rebuild for any new edge

After accumulating multiple xlsx changes, the right order is:
1. **Document each fix's mechanism** in the commit message (M2/M4/M7/etc.)
2. **Subnet test each affected target** with `scripts/subnet_builder.py --target X`
   (~2 min). Verify no new direction wrongs and no build crashes in that subnet.
3. **Full rebuild** with `scripts/apply_cycle<N>_build.py` (~30-50 min).
4. **Run obj_rr_comparison_test** to measure cycle-level effect.
5. **Run extract_results** for the full 5-core panel.
6. **Compare to prior canonical** on PRIMARY metric: median %-err. Accept iff strict
   gain on primary or net-positive Pareto across direction + within-50%.
7. **Commit with clear delta** so subsequent cycles can revert one specific change
   without losing others.

### Lesson: `dependency_distal` wrappers are *structural-only* without numeric ρ

**The user's intent**: extending `dependency_distal` to model parent-pair
correlations among logical-aggregator inputs (e.g., heavy_alcohol × smoking
co-occur with NHANES ρ ≈ 0.35 inside `frailty_lifestyle`); the QP otherwise
treats P(p₁=hi, p₂=hi) ≈ P(p₁=hi)·P(p₂=hi), which under-estimates the joint
when parents are not independent and contributes to M3 aggregator-attenuation.

**What's actually in the xlsx (Apr 30, 2026 audit)**: 6 `dependency_distal`
wrappers, each a 3-row block:
1. `Type=dependency_distal` row declares wrapped_yes / wrapped_no values
2. `Type=equivalent_distal` row links wrapped node to its `original_*`
3. extra row with `Stat=rr` but **`Stat Value=NaN`** (no magnitude)

So the wrappers declare a relationship without specifying its strength. The
parser accepts the row, but the QP gains an extra polytope variable without
an informative constraint, so the band can grow rather than shrink. This is
why "wrappers don't deliver tight W" was found empirically.

**Why adding more wrappers without numbers doesn't help**: the polytope
already implicitly contains the weak-correlation case (independence assumption);
adding a structural-only wrapper just enlarges the cell-space.

**What would actually work**:
1. **Numeric-ρ dep_distal**: extend the parser in `sn_bayes/dependency_v2.py`
   (or wherever `dependency_distal` is consumed) to read a ρ column from the
   wrapper row and emit a covariance constraint into the QP. Validate on a
   subnet before propagating across the net.
2. **Direct connecting study (already works)**: when the chain
   `parent → aggregator → ... → disease` washes signal out (qRR ≈ 1.000),
   bypass with a meta-analysis row directly from `parent → disease`. Cycle 22's
   `intermittent_fasting → diabetes` is the working precedent. The 6 completely-
   flat qRR=1.000 diabetes-diet rows (yellow_vegetables, blueberries, cruciferous,
   vegetable_intake, dietary_fiber_gm, daily_servings_fruit) are candidates.

**General methodological lesson**: when a feature "doesn't work as asked", separate
*data missing* from *code missing* from *semantics wrong* before adding more
instances of the same pattern. For dep_distal the gap is data + code: the xlsx
column for numeric ρ doesn't exist, and the parser doesn't read one. Empty
wrappers reproduce the gap, they don't close it.

### LLM-extraction validation: external sample audit

**Approach (per Apr 29-30, 2026 audits)**: drawing two independent random samples
of n=30 rows from the 385-row pool (seeds 7 and 11) and auditing each row along
five dimensions (paper exists, title match, journal match, year match, semantic
correspondence) gives a Clopper-Pearson 95% CI on bibliographic accuracy.

**Empirical**: combined n=60 audit gives 59/60 = 98.3% bibliographic accuracy with
CI [91.1%, 99.96%]. Substantive (semantic) accuracy is weaker, ~90%, with the two
common failure modes being (i) U-shape papers (above) and (ii) mechanistic-adjacency
borrowings (a paper studied a related-but-not-identical exposure-outcome pair).

**For papers**: report bibliographic accuracy with the Clopper-Pearson CI;
separately report substantive accuracy as a lower bound (the audit may miss
errors the original LLM and the auditor both made).

---

## 7.quinquies. Patterns learned in practice (Apr 30, 2026 - CSV migration + auto-κ + systematic tier promotion via blanking)

### Data canonicality: CSV is canonical, xlsx is archive

- The build pipeline reads `data/relations.csv` via `sn_bayes/data_loader.load_relations()`. **Do not edit `data/Individual Relations.working.xlsx`** - it is no longer consumed by the build, only retained as an archival mirror.
- The Excel `=IF(...)` formulas in M (RR Stat Value) and N (RR plus minus) are NOT executed at build time. Python recomputes M/N from raw stats (C/D/A/B/E columns) via `sn_bayes/rr_formulas.py:compute_rr_and_pm`. The CSV always carries freshly-computed M/N (no stale Excel cache).
- To edit a relation row, use `scripts/apply_blanking_to_csv.py <xlsx_row> <reason>`. It backs up `data/relations.csv`, blanks the row's edge fields (F/G/H + C/D/E + M/N), and logs the change in the `comment` column.
- If the xlsx has been edited (legacy workflow), regenerate CSV with `scripts/regenerate_csv_from_xlsx.py`. Do NOT regenerate xlsx from CSV (xlsx auto-fill spreads stale formulas onto definition rows; the CSV is the safer source).

### CSV reads need numeric coercion

`pd.read_csv` loads mixed-type columns (e.g. `RR Stat Value` containing `SMD_UNRELIABLE` strings) as `object` dtype. The build code's `extract_row_stats` checks `isinstance(row['RR Stat Value'], (int, float))` and silently fails when the value is a string, propagating to a downstream pomegranate `torch.zeros()` `TypeError` at the first `dependency_priors`/`dependency_nhanes_explicit` node. `load_relations()` now coerces M/N + 8 other numeric columns via `pd.to_numeric(..., errors='coerce')`. `SMD_UNRELIABLE` strings become NaN (correctly: those rows shouldn't constrain the QP). If you write a new build script that reads the CSV directly, use `load_relations()` rather than `pd.read_csv()`.

### Bonferroni κ is auto-computed from N

`sn_bayes/bayesnet_creation.py:create_bayesnet_proto_linear` now defaults `input_ci_scale=None`, which triggers auto-compute via `sn_bayes/kappa.py:compute_kappa`:

    κ = Φ⁻¹(1 − α/(2N)) / Φ⁻¹(1 − α/2)    (α=0.05, N=number of literature constraint rows)

For the current net (N≈350 rows) this gives κ ≈ 1.94. The paper's quoted κ=1.85 corresponds to N=166 (citation-level count). The N used in the formula matches the QP-level count of independent test constraints (each parent-value row is a separate band test). To override, pass an explicit float to `input_ci_scale=` in your build call.

`scripts/data_checks.py spreadsheet --use-csv` now reports the computed κ. If your build's saved κ disagrees with `data_checks` re-computation, you have a stale cache (see next item).

### CPT cache hash includes κ - caches invalidate correctly across κ values

Prior to Apr 30 fix, the dirty-tracking cache hash in `cpt_cache.hash_solver_kwargs()` did NOT include `input_ci_scale`. So a cycle 14 build with κ=1.85 would silently propagate cached CPTs into a cycle 26 build that specified κ=1.0 (default). The result was a pickle with a MIX of κ=1.85 and κ=1.0 CPTs. The hash now includes κ; do not roll the fix back.

### K rule: warning at K > 7 (May 1) - do NOT auto-restore standard, this advice was wrong

**THIS SECTION'S ORIGINAL ADVICE WAS WRONG and caused the Apr 30 regression cascade.** Keeping it here marked as an anti-pattern so future Claudes don't repeat.

`scripts/data_checks.py` flags any node with K>7 (May 1 softening; was K>5 then K>6). The flag is a **warning**, not a constraint. The xlsx column structure caps the *definition row* at 5 value/index slots, but additional parent-value rows can be added without modifying the definition row. The Apr 30 K-restore was a mistake: it blanked 14 intentional bypass-fix rows to satisfy a too-tight K rule, regressing 5 targets. Don't repeat. See §0.2 for the corrected guidance.

(Original Apr 30 K-restore action - for historical reference only, do not re-execute):

    ACM K=10→5, cog_imp K=7→5, hypertension K=6→4, diabetes K=7→3, lung_cancer K=6→4

CPT cell count is 4^K, so dropping ACM from K=10 to K=5 is a 1024× build speedup on that node. Don't add direct lit rows that push K back above 5; route signals through aggregator children instead (per the standard structure).

### Tier promotion via blanking - what works and what doesn't

Empirical findings from systematic subnet-attack iteration on Apr 30:

- **Direction-wrong row blanking on abstain-d**: WORKS. Promotes target from abstain-d to descrK12 (or descrK3 if K≥3 remains). Examples: platelet_count_si ← fasting_glucose (estimated OR was direction-wrong); kidney_cancer ← hysterectomy (tight σ but structural inversion); cardiac_event ← dietary_fiber (small inversion).
- **Magnitude-failure (>50%err) row blanking on abstain-m**: WORKS when ≥1 row remains after blanking. Promotes abstain-m to descr. Example: fitness_concept ← aerobic_volume_concept (lit=2.5, qRR=1.0, %err=59).
- **σ-tightening on descrK3**: DOES NOT promote to tight-LoTP. The tier-LoTP threshold uses σ_min (the tightest row), not σ_max. Tightening the LOOSE row leaves σ_min unchanged so φ=1/(1+σ_min) doesn't drop, and ρ stays below threshold. Empirically tested on alzheimers, mpv_naive, hallmark_8, bmi, homa_ir, b_cell_lymphoma, stroke, cardiovascular_disease, IL6, heart_attack - zero promotions to tight-LoTP via this mechanism.
- **Adding a synthetic 2nd lit row to descrK12 K=2 n=1 → tight-small**: requires real literature; cannot be tested via fake null-effect rows because the QP needs a meaningful constraint.
- **Network ρ from degenerate parent pairs**: when `nhanes_rho=None` for a parent pair (no NHANES joint coverage), the `network_rho` may be forced near 1 by overlapping medical concepts (e.g., NASH ⊂ NAFLD; intermittent_fasting ⊂ dietary_restriction). These are NOT real LoTP-active. App. C classifier correctly excludes them. Filter for `nhanes_rho is not None OR rho ≤ 0.5` when computing real LoTP candidates.

### Tier ordering: descrK3 vs descrK12 are partition siblings, not ranked

Don't treat descrK3 → descrK12 as a "promotion." Both are descriptive tier; the suffix distinguishes K (≥3 vs ≤2). Blanking rows on a descrK3 target may shift it to descrK12 (because K drops below 3) without any quality improvement. The valid promotion ordering is:

    abstain-{d,m} ← (worst)
    descr{K3,K12} ←
    tight-{small,LoTP} ← (best)

### Subnet-test before applying any edit

`scripts/subnet_builder.py` builds a subnet for a target's closure (~2 min for K=5 targets, ~5-10 min for big closures like ACM). Use `--patch-file <json>` to test edits without committing them to xlsx/CSV. `scripts/subnet_tier_eval.py --label <subnet_label> --target <target>` reports the new tier estimate. Only after subnet test confirms the tier improvement, apply via `apply_blanking_to_csv.py`.

`scripts/parallel_subnet_attack.py --batch <json> --max-parallel N` fires N attacks in parallel; useful when generating dozens of candidates from an audit.

### Subnets use the full-net κ, not the subnet-closure κ (May 1, 2026 fix)

`scripts/subnet_builder.py` now computes Bonferroni κ from the **full post-patch relations CSV row count**, not from the subnet's smaller row count. Without this, the subnet's κ would be looser than what a full-net rebuild imposes (smaller N → smaller κ → wider literature CI bands → easier promotion), causing false-positive promotions that don't survive a full build. Override with `--input-ci-scale FLOAT` to pin κ (e.g. 1.85 to match cycle 14's hardcoded κ).

The κ formula `κ = Φ⁻¹(1 − α/(2N)) / Φ⁻¹(1 − α/2)` (paper App. A Theorem 2) treats N as the **number of independent literature constraint rows**, not nodes and not unique-citation studies. Each parent-value row in the CSV is one band test. Multi-value-parent unrolling produces multiple rows from one PMID; those rows test different cell-level contrasts (different P(Y|X=v) for different v) and so are different claims under Bonferroni - even though they share population data. The defensible choice is row-count (May 1, 2026 finding); paper's quoted κ=1.85 corresponds to the older study-count interpretation N=166 and should be updated to row-count semantics in a v2 revision.

### Subnet workflow rules (process discipline - May 1, 2026, v2 - RECOVERED FROM ERRORS)

**The clean process, fully articulated. Read end-to-end before any subnet work.**

The single source of truth is the **published-paper baseline build**, preserved as `builds/cpt_cache/cycleN/{config_linear.json, bayesianNetworkProto.pickle}`. Currently `cycle14`. **All subnet attacks must validate against THIS baseline, not against current CSV state.** This is the rule that produces translatable wins.

#### The eight steps

1. **Keep the baseline frozen.** Don't edit CSV/xlsx in ways that drift away from baseline before the next cycle is built. Branch state, don't mutate state. The cycle 25/26 mistake (multi-step CSV mutations layered between rebuilds, each rebuild bundling untested changes) created drift the cycle 27 build inherited.

2. **Enumerate attacks from baseline INVARS, not from current CSV.** Read the baseline `config_linear.json`. For each target, walk its `INVARS` dict and identify candidate attacks against THAT config:
    - `abstain-d` → blank each direction-wrong row
    - `abstain-m` → blank each row with `%err > 50%` (and combinations)
    - `descrK3/12` → blank highest-`%err` row to tighten
   **Critical anti-pattern**: do NOT enumerate from `paper/objective_rr_comparison_cycle12.json` (or any non-baseline obj_rr) and assume the (target, parent) tuples translate. Parent names drift across cycles (e.g., `bmi_naive` vs `bmi`); rows are added or removed. Many of those candidates fail when matched against baseline INVARS, becoming **ghost wins** - subnets show promotion but the baseline doesn't have the row to blank.

3. **Fire all attacks in parallel.** Single batch JSON, `--max-parallel ≥ 8`, on a quiet machine (no full-net build competing). Closure-only subnets run 1-10 min each; full batch in 20-30 min wall time. **Do not run a full-net build concurrently** - CPU contention stretches each subnet 3-5×.

4. **Validate every subnet with `subnet_tier_eval`.** The subnet builder's `OK=N FLAT=N WRONG=N` is closure-level *direction* count, NOT tier classification. Read the JSON `subnet_tier_eval_<label>.json` for each subnet's `tier_est` and compare to baseline tier from `paper/sd4h/appendix_b.tex` (App. B). Tier promotion = win; same or worse = not a win.

5. **Cross-check each win against baseline INVARS.** Confirm the patched (target, parent) tuple actually appears in baseline `config_linear.json[dependency_data][target][INVARS]`. If absent, the subnet was testing a row that didn't exist in baseline - discard the "win". Round 3 lost CAD and cv_mortality "wins" this way: their parents were added post-cycle-14, didn't translate.

6. **Apply all validated wins to baseline in ONE patched config.** Skip CSV edits entirely. Load baseline `config_linear.json`, remove the patched `(target, parent)` entries from `dependency_data[target][INVARS]` in-memory, pass the patched config to `create_bayesnet_proto_linear`. Reference: `scripts/apply_cycle28_clean_build.py` is the template.

7. **One rebuild per cycle.** ~80 minutes. Don't do multiple intermediate rebuilds (cycle 25 → 26 → 27 → 28); each multi-change rebuild mixes effects you can't isolate. The cycle-26 mess on May 1 was caused by exactly this anti-pattern.

8. **Run the full paper-grade test suite on the rebuild** (objective_rr, observed_evidence_auc, rho_gap_audit, ukbb_three_way, validation_window_summary, plus data_checks post_run).** Compare to baseline. If strictly better across direction / within-50% / tier counts, the new cycle becomes the next baseline. If not, accept the prior baseline; do NOT drift further.

#### What NOT to do (process anti-patterns from May 1)

- **DO NOT run subnets against current CSV assuming wins translate to baseline.** They often don't (parent name drift, post-baseline rows). Always enumerate from baseline INVARS.
- **DO NOT do multi-cycle sequential rebuilds.** Each is 80+ min, mixes changes. Cycle 26 was three half-tested changes glued together; the regressions it caused poisoned cycle 27 and would have poisoned cycle 28 if not caught.
- **DO NOT hold a full-net build running while a subnet batch is pending.** Kill the build, fire subnets, then start the build with all winners.
- **DO NOT apply CSV blanks between subnet rounds and rebuilds.** Each unvalidated CSV edit accumulates drift. Apply only after subnet validation, only at rebuild time.
- **DO NOT trust subnet `OK/FLAT/WRONG` as tier verdict.** Use `subnet_tier_eval`.
- **DO NOT use the K rule as a reason to blank load-bearing rows.** The K-restore mistake (Apr 30) blanked 14 intentional bypass rows to satisfy K≤5, then had to restore them. The K rule is a *warning*, not a build constraint. Per May 1 directive, K up to 7 is permitted (`data_checks.py:273`), and structural bypass-fixes have priority over the warning.
- **DO NOT trust memos that say "not fixable, code bug, skip."** Subnet-test anyway. The 2-minute test confirms or refutes. Pancreatic CPT inversion was treated this way for weeks; never validated.

#### Rank-by-P-success and saturate parallelism

**Don't pick one attack at a time and propose it.** Generate the full attack candidate list, rank by probability of success, and fire as many as cores allow in parallel.

How to rank:
- **High P (tier-promotion likely)**: dep_distal restructure on K-rich abstain-m / abstain-d targets where binding row is gate-attenuation; multi-row blanks on abstain-m where single-blank failed because second-worst row also exceeds threshold.
- **Medium P (tightening or possible promotion)**: dep_distal between correlated parents of descrK targets (NHANES ρ > 0.3, network ρ ≈ 0); single-row blanks on abstain-m with one outlier row.
- **Low P (descriptive metric improvement, no tier change)**: max-err row blanks on already-passing descrK3 targets.

Capacity: typical machine has 8-16 cores. Each subnet uses ~1 core for 1-15 min (closure-size dependent). Fire `--max-parallel min(N, cores)`. **Don't run a full-net build concurrently** - kill it first.

`subnet_builder.py` accepts both patch types in the same JSON:
- `{"xlsx_row": N, "fields": {...}}` - modify existing row (blank rows: set Stat Value etc. to null)
- `{"add_row": {col: val, ...}}` - append a new row with the given fields

So a dep_distal addition is one batch entry with three `add_row` patches: leaf study row, aggregator definition, distal link.

#### Quick checklist before firing any subnet batch

- [ ] Baseline build exists at `builds/cpt_cache/<baseline>/{config_linear.json, bayesianNetworkProto.pickle}`
- [ ] Per-target tier table for baseline (App. B equivalent) is loaded
- [ ] Candidate (target, parent) tuples were enumerated from baseline INVARS, not from a different cycle's obj_rr
- [ ] Candidates were ranked by P(success), full ranked list captured
- [ ] No full-net build is running concurrently
- [ ] `--max-parallel min(N_attacks, N_cores)` - saturate parallelism
- [ ] After completion: `subnet_tier_eval` outputs JSON for each, parse for tier promotion
- [ ] Cross-check each win's (target, parent) against baseline INVARS before applying

### Run the panel after every full rebuild

After a full cycle build (`apply_cycleNN_build.py`), run all 5:
1. `data_checks.py post_run <pickle> <config>` (basic sanity)
2. `extract_results.py <label> <pickle> <config>` (per-target obj-RR vs literature)
3. `objective_rr_comparison_test.py` (per-row direction + magnitude DIRECT and DISEASE)
4. `dsep_check.py` (structural integrity)
5. `rho_gap_audit.py` (ρ NHANES vs network per parent pair)

These take ~30-60 min total on the full net. Don't skip - diagnose-only is insufficient (memory `feedback_run_full_test_suite`).

### Don't kill running processes on user "stop"

Per memory `feedback_dont_kill_processes`: "stop" means "stop initiating new actions, just listen". It does NOT mean kill running processes. Killing is irreversible. Only kill when user explicitly says so OR when you've determined a process is redundant (e.g., a full-net rebuild superseded by a subnet test that already validated the change).

---

## 7.sexies. Subnet result audit - systematic organization (May 1, 2026)

**Why this exists:** May 1 session lost track of subnet wins. 66 tier-eval JSONs accumulated in `paper/`; only 4 of the wins were applied to cycle 28 because there was no systematic process to enumerate, classify, and cross-check them. User feedback: "use systematic organization to prevent losing and forgetting things." This section is the durable process.

### The problem subnet results pose

Every subnet attack (parallel or single) writes a `paper/subnet_tier_eval_<label>.json`. Over a session, 50-100 of these accumulate. Each represents a candidate net edit. Several issues compound:

1. **Multiple action types**, each requiring a different patch mechanism:
   - **Blank**: remove a (target, parent) row from CSV → results in tier improvement. Patch = remove from `dependency_data[target]['INVARS']` of the live config.
   - **Tighten**: reduce σ on a row → results in tier improvement. Patch = reduce `Plus minus` on that CSV row (NOT row removal).
   - **Status**: baseline tier check → not a patch, just a diagnostic.
   - **Compound** (e.g. `_cycle25` suffix): multiple changes baked in → not directly patchable.
   - **Restructure**: gate type or parent set rewired → custom CSV edits.

2. **Ghost wins**: subnet runs against current CSV state. If a (target, parent) tuple was added to CSV after cycle 14 (the paper baseline), a "win" from blanking that row doesn't correspond to any cycle-14 edit. Always cross-check against `builds/cpt_cache/cycle14/config_linear.json` INVARS before patching.

3. **Conflicting alternatives**: a single target may have two competing wins (e.g. `naive_sleep_apnea`: blank-bmi-keep-snore vs blank-snore-keep-bmi). Both can't be applied (would leave node empty). Pick by lower `max_err`.

4. **Tier-result interpretation**: `descrK3`/`descrK12`/`tight-LoTP`/`tight-small` are wins. `abstain-d`/`abstain-m` are not wins. `descrK3_blank` and `dirwrong` and `highErr`/`highSig` are *labels for experiments*, not tiers - read `tier_est` field.

### The systematic process

After every subnet batch, **before** firing the next cycle build:

1. **Run the audit script** - `python3 scripts/audit_subnet_wins.py`. It scans all `paper/subnet_tier_eval_*.json`, classifies action type, infers blanked parent (label fragment + cycle-14 INVARS substring match), and emits `paper/subnet_audit_log.tsv` sorted by recommendation.

2. **Review the TSV**. Recommendation column tells you what each result is:
   - `IN_PATCHES` - already applied to current build script.
   - `CANDIDATE` - real win, not yet applied. **Add to next build's PATCHES.**
   - `TIGHTEN_NOT_PATCH` - real win but needs σ adjustment, not row removal. Apply via CSV edit (`Plus minus` column for the matching row).
   - `GHOST_WIN` - win, but parent absent from cycle 14 INVARS. Skip (no edit possible).
   - `NON_PATCHABLE` / `OTHER` / `NO_WIN` - informational only.

3. **Resolve conflicts**: if two CANDIDATE rows target the same node with different blanks (e.g. `naive_sleep_apnea ← bmi` and `naive_sleep_apnea ← snore`), pick the one with lower `max_err`. Don't apply both. Record the rejected alternative as a comment in the build script.

4. **Update `CYCLE28_PATCHES` in `scripts/audit_subnet_wins.py`** to match the next-cycle build's patch list, then re-run the audit. Verify that all CANDIDATE rows have moved to IN_PATCHES.

5. **Update the build script's `PATCHES` list** (`scripts/apply_cycle*_*.py`) with the new tuples and a one-line comment per patch (source label + tier).

6. **Cross-check round-trip**: re-run audit; CANDIDATE count should be 0 (or only contain freshly-tested results from the latest batch).

### What goes in the build script vs. the audit script

- `scripts/audit_subnet_wins.py` is the **read-only enumeration**. It never modifies the build. Its `CYCLE28_PATCHES` constant must be hand-synced with the live build script's `PATCHES` list - that sync is the audit's truth check.
- `scripts/apply_cycle*_*.py` is the **build executor**. Each cycle gets its own script (don't overwrite - old scripts document what produced each cached pickle).

### Cycle 28b (May 1) worked example

- Cycle 28 (original) was built with only 4 round-1 wins. Subsequent rounds (3, 4, may1) produced 10 more validated wins + 11 tighten wins, but they weren't audited and weren't included.
- After audit was implemented, cycle 28b was launched as: cycle 14 + 4 round-1 wins + 10 may1/r3/r4 blanks (with the `naive_sleep_apnea` conflict resolved by `max_err`).
- The 11 tighten wins are deferred to a later cycle pending automated extraction of tightened-row σ values.

### Anti-patterns

- **"I already ran the panel on cycle 14, the new wins should be obvious"** - no, every win must be a row in the audit log. If the audit doesn't list it, you'll forget it.
- **"I'll just remember the wins from the conversation"** - context compaction will erase this. The TSV is the durable record.
- **"All tier-est wins are blank-applicable"** - false. Tighten wins require σ edits; check `_tighten_` substring in label.
- **Skipping cross-check against cycle 14 INVARS** - leads to ghost-win patches that no-op silently and don't show as failures in the audit.

### Local tier-eval ≠ confirmed global win - the validation rule

**Every promoted candidate must pass two screens, not just the local one.** A subnet tier-eval JSON proves a LOCAL improvement on one target's tier; it does not prove the FULL net improves overall. Some local wins regress global metrics (direction match, within-50%, AUC, joint fidelity, or ρ-gap on OTHER nodes). The local screen is fast cheap candidate selection; the global screen is what actually validates the change.

**The two-screen workflow (mandatory unless explicitly waived):**

1. **Local screen (fast):** subnet-build the candidate change, run `subnet_tier_eval`. If tier improves → CANDIDATE for global screen. If not → REJECT (still log to audit TSV with `NO_WIN`).
2. **Global screen (~30-60 min):** apply CANDIDATE(s) to a full-net build (`scripts/apply_cycle*_*.py`), then run the **full 5-core panel** on the resulting pickle (per §6.bis):
   - `scripts/data_checks.py spreadsheet --use-csv` (data sanity)
   - `scripts/objective_rr_comparison_test.py` (direction match per literature row, DIRECT + DISEASE)
   - `scripts/extract_results.py` (within-50% per row)
   - `scripts/dsep_check.py` (d-separation legitimacy)
   - `scripts/rho_gap_audit.py` (ρ feasibility coverage)
   - `scripts/observed_evidence_auc.py` (per-disease AUC, with per-target diagnostic-biomarker exclusion)
3. **Compare global metrics to the prior baseline**. A confirmed win requires non-regression on direction, within-50%, AUC, ρ-gap. If a candidate locally wins but globally regresses → REJECT and log the regression in `paper/subnet_audit_log.tsv`.

**Run-everything default:** unless a script takes more than ~1 hour, run it as part of every cycle's panel. The 5-core panel takes ~30-60 min total - that is NOT too expensive and must be run after each cycle build. `data_checks` runs in ~10s and must be run before each build. `subnet_tier_eval` runs in ~10-30s per subnet and must be run on every subnet build.

**Why this rule exists:** May 1 session promoted 4 round-1 wins to cycle 28's PATCHES list based on local tier-eval alone. The user pointed out: "blanking won by what criteria? you need to run not only the test of does it raise the tier, but also the test of the 5 core criteria. and you need to run that in addition to the datachecks." Recorded here so the rule survives compaction.

**When to waive the global screen:** never, for cycle promotion. The screen exists precisely to catch subnet wins that don't generalize. Waiving it is a known pre-condition for the regressions documented in §7.quater.

### First-principles + causal Bayesian reasoning supersedes strict-improvement gating

**Key principle (May 1 reaffirmation, also in §7.ter):** the rule "every metric must improve" is overly greedy. A causally-honest, first-principles change can be the right move even when one secondary metric drifts down. Examples:

- **Adding a `dependency_distal` between known-correlated siblings** (e.g., NHANES says `bmi × c_reactive_protein` ρ ≈ 0.36; the gate currently treats them as independent). The first-principles fix is to add the wrapper because Bayesian theory requires it. Some metric may worsen as the polytope contracts unevenly; the change is still right.
- **Adding a literature-backed connecting study** where one is missing (manual §3 connecting-study pattern). Even if the new edge initially raises max-err on one target, the structural correctness from now satisfying the actual probability identity is the win.
- **Removing a `Type=node_name` row that hacks around a gate** (e.g., row 781 cvd ← healthy_lifestyle had "reciprocal taken to fit any_of"; that hack is causally dishonest and worth removing on principle).

**The candidate-quality test, in priority order:**
1. **First-principles correctness:** does the change make the network *more causally honest*? (Adding a real correlation; removing a hack; replacing aggregator washout with a literature connecting study.)
2. **Bayesian principles:** does the change satisfy a probability-theory identity better than before? (Increasing parent-pair ρ when subset constraints depend on it; bringing K_DAG-induced explaining-away back into the model.)
3. **Local screen (subnet tier-eval):** secondary signal that the QP solver responds as predicted.
4. **Global screen (5-core panel):** measure of whether the principled change generalizes; secondary regressions are *expected and tolerated* if (1) and (2) hold.

**Per-user May 1:** "I would rather have you work from first principles and be causal, to improve the subunits, even if it temporarily gets slightly worse. so a good candidate would be to clean up the causally and Bayesian incorrect additions made." The strict-improvement audit gate from `feedback_systematic_iteration` is **superseded** by `feedback_principled_over_greedy` for any change rooted in §3 (feature types) or §0.bis (Bayesian framing). When the principled-change overrides the strict gate, log the rationale in `paper/subnet_audit_log.tsv` notes column.

**What "causally and Bayesian incorrect additions" look like:**
- Direct edges added by prior cycles to bypass an aggregator without modeling the underlying confounder (proper fix: `dependency_distal`).
- Reciprocal/inverted RRs taken to fit a gate's polarity (proper fix: change the gate type, not the RR).
- Independence assumptions on parents NHANES says are correlated (proper fix: add `dependency_distal` between them).
- Single-RCT high-magnitude rows used where a meta-analysis is available (proper fix: substitute the meta).
- `naive_0` paths where the variable is genuinely shared between several diseases (proper fix: pre-compute as discrete prior and remove the naive_0 path).

These are the candidate types worth promoting *even if* the 5-core panel shows a small overall regression - they make the model better-grounded.

---

## 7.septies. Applying `dependency_distal` to multipath / washout candidates (May 2, 2026)

**Why this exists.** May 1-2 session: blanking-style cycle 28b confirmed empirically what the user predicted from first principles - blanking removes literature evidence, can't fix UKBB descriptive-tier attenuation, and introduces local regressions (`cardiovascular_disease ← daily_time_sitting` went FLAT after the `cvd ← healthy_lifestyle` blank). The proper structural tool is `dependency_distal` 3-row blocks. This section is the operational guide for applying them.

### Two regimes, one tool

`dependency_distal` 3-row blocks treat **both** failure modes that cause UKBB descriptive-tier discordance:

- **Regime 3 - multipath / under-determined interaction cells.** Multi-parent target where parent-pair NHANES correlation is sub-threshold for LoTP activity, and the QP fills the joint cells NHANES-shape (≈ marginal-product). Wrap one of the correlated parents; the connecting-study row supplies the joint-cell info the QP would otherwise guess.
- **Regime 4 - aggregator-mediated chain attenuation.** Path from leaves to disease passes through 5-23 sequential aggregators; each `any_of` averages parent marginals; signal washes to ≈ 1. Wrap the final or an intermediate aggregator; the connecting-study row anchors the wrapper's CPT regardless of upstream attenuation.

Same mechanism - connecting-study RR pinning a marginal of the wrapper's CPT, propagated through LoTP. Different placement, different interpretation.

### The 3-row block (per Apr 16 spec; matches §3 line 844 and ci_movement v4)

```
Row 1 (identity):    output=W,  input=original_W,  Type=equivalent_distal
Row 2 (definition):  output=W,  input=NaN,         Type=dependency_distal,  value1=W_yes, value2=W_no
Row 3 (connecting):  output=W,  input=S,           Type=NaN (blank),        Stat=RR (or OR/HR), Stat Value=<lit>, Plus minus=<CI>
```

Rename existing `output=W` aggregator-definition rows to `output=original_W`. External references (other rows where W appears as `input`) stay unchanged - they now point at the wrapper, which inherits the original's behavior via row 1.

**Connecting study row's Type is BLANK (NaN), not the disease name.** A previous version of this section incorrectly prescribed `Type=<disease>` on row 3; that instruction was inconsistent with Apr 16 spec, with §3 line 844, and empirically crashes the build (ci_movement v3, May 5: KeyError). The connecting study with blank Type still bites - the QP applies the literature RR via the standard per-parent constraint path (`prob_a_and_not_a_given_b_and_not_b` in utils.py:1837), confirmed on ci_movement v4 (sibling evidence shifts wrapper marginal +7-11%).

If you want the sibling S to ALSO contribute its own distal aggregation up the chain (your "Role A"), add a SEPARATE row at the level-above gate (or at the disease, if W is at level 1):
- For W at level 1: `output=disease, input=S, Type=distal`
- For W at level 2+: `output=original_W_{k-1}, input=S, Type=<disease>`

### Per-candidate procedure (5 steps)

1. **Identify W (wrapper target).** For multipath, wrap the more-aggregating of the two correlated parents. For washout, wrap the aggregator closest to the disease.
2. **Sub-case A vs B.** Is W a clinically meaningful aggregating construct (a syndrome, behavior pattern, composite risk indicator)? Yes → A, proceed. No → can you recast by defining a new aggregator above the two correlated parents that *is* meaningful? Yes → A via recast. No → defer (parent-pair metas rarely exist; NHANES-derived ρ has circularity cost).
3. **Pick sibling S + verify literature.** S sits at W's conceptual level (not a parent of W, not a downstream consequence). Search PubMed for a meta of `(S-construct → disease)`. If no meta → defer.
4. **Write the 3-row block.** Format above. Replace existing edge from W to disease with edge from wrapper W (via the original_W rename).
5. **Subnet test, decide if recursive wrap needed.** If the local subnet tier-eval shows tier promotion (descriptive → tight-LoTP, or abstain → descriptive/tight) → done. If still attenuated and chain has more aggregator levels above → wrap one more level (max 2 levels per chain).

### Priority ordering (paper-impact-per-hour)

Apply candidates in this order:

1. **Frailty family (4 candidates).** `frailty_lifestyle ← (heavy_alcohol, smoking)` and 3 frailty_behaviors / frailty_weight family entries from `paper/multipath_candidates.tsv`. Frailty is currently abstain-d; rescuing it is the highest paper-impact result. Sub-case A - frailty constructs have abundant lifestyle metas.
2. **Washout candidates with sub-case A literature.** Top: `cognitive_impairment ← moderate_physical_activity (any_of)` 124× attenuation; `lung_cancer ← lc_industrial_exposure` 97×. Both wrap clinical aggregators (PA composite, occupational exposure composite); literature plentiful.
3. **17 lit-ready multipath candidates.** Most are tightening already-tight targets (metsyn, hypertension); lower paper-impact than abstain-rescue.
4. **187 multipath without existing CSV row.** Apply only after Tier 1-3 done. Hit rate depends on PubMed search effort.

### Validation criteria

**Primary - tier transition.** Did the target move from prior tier to a higher one? This is the headline.

**Secondary - UKBB shift on n=20 panel.** For Component 1 + washout pairs (4 of 6 descriptive-tier discordants going through chains, plus the lung_cancer/smoking abstain pair), expect qRR to move *away from 1* toward the literature value. UKBB-shift toward concordant if literature aligns with UKBB. If qRR shifts *toward* NHANES-shape, that's a yellow flag - wrapper may be over-fitting NHANES-aligned literature rather than literature-anchored values.

**Tertiary - local subnet tier-eval.** Available for all candidates, but rewards NHANES-shape; a successful candidate may show small or even slightly negative subnet tier-eval shift while producing the right kind of UKBB shift. Don't over-weight.

**Per-target W.** Should shrink. If W stays wide after a wrapper, the connecting-study constraint isn't biting - check if literature CI is too wide to be informative.

### Stop conditions (halt + ask)

1. **Tier demotion** on a previously-tight target → wrapper destabilized something working. Roll back.
2. **Connecting-study literature is from the same cohort as the target's primary literature** → double-counting evidence; artificial polytope tightness without independent info.
3. **More than 2 levels of recursive nesting** → third level is sub-case B, defer.
4. **Candidate would force NHANES-derived ρ as connecting RR** → halt; using NHANES-derived ρ requires App. E.4 partial-circularity edits before landing.
5. **5+ candidates in a row produce no tier transition** → likely hitting wrong literature conditional or Component 2 (corpus transportability) cases. Pause + audit.
6. **All 28 washouts collectively don't move any abstain target into tight-LoTP** → washout is structurally harder than 2-level wrapping fixes. Honest paper position: "washout remains future-work; partial recovery via wrappers at Component 1 cases only."

### Connecting-study RR sourcing convention

W is often a model-internal aggregator name (e.g., `frailty_lifestyle`, `diabetes_healthy_diet`) rather than a published clinical construct. The verified convention from `diabetes_healthy_diet`'s working wrapper (row 542): the connecting study uses a meta of `(sibling-construct → disease)` distally routed via `Type=<disease>`. Not a wrapper-literal RR(W | sibling), which doesn't exist in literature for model-internal names.

Concretely: for `frailty_lifestyle` wrapper with sibling `frailty_weight`, the row is `output=frailty_lifestyle, input=frailty_weight, Type=frailty, Stat=RR, Stat Value=1.85` - using a meta like Reber 2019 J Frailty Aging for obesity → frailty.

### Anti-patterns specific to this section

- **Don't blank correlated-parent edges** to "force LoTP activity"; that just removes evidence. The fix is dep_distal additions, not edge removal (per §7.sexies first-principles override).
- **Don't add a connecting-study row without a literature RR**; structural-only (`Stat=rr`, `Stat Value=NaN`) declares the relationship but adds no constraint, so the wrapper is inert.
- **Don't wrap a node deeper than 2 levels** in a chain. Third level enters synthetic-construct land where literature doesn't reach.
- **Don't reuse the same cohort for connecting-study + primary literature**. Independent evidence requirement.
- **Don't over-weight subnet tier-eval** as the validation signal. It rewards NHANES-shape; the right signal is UKBB shift + tier transition.

### Hand-back format per applied candidate (for traceability)

Per applied candidate, log:
- Candidate identifier (target + sibling pair)
- Sub-case (A direct, A via recast, deferred)
- Connecting-study literature (PMID, RR/OR/HR, CI)
- 3-row block written
- Local subnet tier-eval before/after
- Tier transition (yes/no)
- UKBB shift if pair in n=20 panel
- Per-target W before/after

Per deferred candidate, log: identifier + deferral reason (no lit, no recast, third-level nesting required, Component 2 corpus issue).

---

## 7.octies. Structural filter for `dependency_distal` candidates (May 2, 2026)

**Why this exists.** May 2 session: 28 dep_distal subnet attacks fired; 2 wins, 24 regressions, 2 timed out. Initial hypothesis ("depth from disease matters") was directionally right but mis-identified the dominant mechanism. The real determinant is **gate type along the path from wrapper to disease**, not depth per se.

### Why averaging gates destroy wrapper anchoring

The wrapper mechanism: row 3 carries `RR(wrapper | co_sibling)` with `Type=<disease>`. The literature pins the wrapper's CPT directly. For that constraint to produce a useful tightening at the disease's CPT, the wrapper's joint structure has to propagate to the disease via LoTP.

When the wrapper feeds the disease directly:
- The wrapper's constrained joint cells over (original_*, co_sibling) carry their literature anchor.
- The disease's CPT inherits that joint structure through standard LoTP propagation.
- The disease's joint cells over (wrapper, co_sibling) reflect the literature anchor.

When the wrapper feeds an `any_of` (or other averaging) aggregator:
- The aggregator deterministically combines its inputs: `aggregator_yes = OR(input_1_yes, input_2_yes, ...)`.
- The aggregator's marginal is approximately the average of its inputs' marginals.
- The wrapper's joint structure - the very thing the literature constraint was anchoring - gets averaged together with sibling leaves' marginals at the aggregator step.
- The aggregator's output to the disease is a single attenuated marginal, with the wrapper's joint anchoring effectively erased.

So the aggregator isn't just slowing propagation; it's deterministically destroying the joint structure that was the whole point of the wrapper.

The regression is *active harm* rather than benign no-op: the wrapper still adds a literature-band constraint at the wrapper level (RR(wrapper | co_sibling) bounded by the literature CI), reducing the wrapper's polytope freedom locally - but its downstream effect is killed by the aggregator. The QP is now more constrained in a way that doesn't help the disease, and may push the wrapper's CPT to a corner that's slightly worse for other study rows touching neighboring nodes. **Local tightening with no downstream payoff equals net regression.**

### Filter rule

Before applying any wrapper, walk the path from the proposed wrapper location to the disease and label each intermediate node's gate type. The wrapper is treatable only if **no averaging gate** sits between it and the disease.

| Gate type at intermediate node | Effect on wrapper propagation |
|---|---|
| `dependency` (literature-CPT) | Propagates fine - joint structure flows through QP-solved CPT |
| `dependency_distal` (another wrapper) | Propagates fine - wrapped joint structures compose |
| `dependency_nhanes_explicit/quartile` | Propagates fine - fixed marginal, doesn't average inputs |
| `is_a` / `subsumes` / `equivalent_to` / `equivalent_distal` | Propagates fine - sensitivity/specificity routing, not averaging |
| `any_of` | **DESTROYS wrapper anchoring** - gate averages inputs deterministically |
| `all_of` | **DESTROYS wrapper anchoring** - same logic |
| `avg` | **DESTROYS wrapper anchoring** - explicit averaging |
| `if_then_else` | Likely destroys (depends on conditions); conservatively treat as destroying |

A candidate is treatable only if every node on the path from wrapper-location to disease has a gate type from the top group. As soon as the path crosses an `any_of`, `all_of`, `avg`, or `if_then_else`, the candidate is structurally inaccessible to the current `dependency_distal` mechanism.

### Implications for the candidate space

Most multipath candidates that touch abstain-tier targets are structurally inaccessible. The 18 abstain-tier targets sit there because they're downstream of long aggregator chains. Almost every wrappable position on those chains has an aggregator between the wrapper and the disease.

**Empirical confirmation (May 2):**
- `ci_movement → cognitive_impairment` (direct, no aggregator between): WIN.
- `moderate_physical_activity → ci_physical_activity (any_of) → cognitive_impairment`: REGRESS.
- `frailty_lifestyle → frailty_behaviors (any_of) → frailty`: REGRESS.
- `frailty_weight → frailty_behaviors (any_of) → frailty`: REGRESS.
- `frailty_healthy_diet → frailty_behaviors (any_of) → frailty`: REGRESS.

All three regressions share the same structural feature: the path from wrapper to disease passes through an aggregator gate. The one win does not.

### Honest paper position

Abstain-tier targets whose every wrappable position lies behind genuinely-required averaging chains remain structurally inaccessible to the current mechanism. The framework reports *W* rather than committing - that's the correct behavior, not a bug. Recovering them would require either (a) replacing averaging aggregators with non-averaging gate types (alters clinical interpretation), (b) implementing a future mechanism (e.g., the unread numeric ρ slot on `dependency_distal` rows) that propagates joint structure through current averaging gates, or (c) the other tools in §7.nonies.

---

## 7.nonies. Tool-mix beyond `dependency_distal`: five tools, one decision tree (May 2, 2026)

**Why this exists.** §7.octies' filter shrinks the dep_distal-treatable space substantially. Many remaining candidates - including most abstain-tier targets - need different tools. This section walks through the tools already in the spreadsheet vocabulary (App. E Tab. 2 / Tab. 3) and gives a decision tree for picking the right one per candidate.

### The five tools

#### 1. `dependency_distal` 3-row block (§7.septies)

- **Mechanism:** wrap one of two correlated parents; route literature for `RR(wrapper | co_sibling)` via `Type=<disease>` tag.
- **Constraint added:** literature-band on the wrapper's CPT.
- **Reaches:** targets where the path from wrapper to disease has no averaging gate.
- **When to use:** multipath candidates where two correlated parents have a non-aggregator path to the disease.
- **Doesn't reach:** aggregator-chain washout cases.

#### 2. New direct study row

- **Mechanism:** add a regular literature-RR row at an existing edge that doesn't currently have one, or at a new edge that should exist per the literature.
- **Constraint added:** literature-band on a single edge.
- **Reaches:** descriptive-tier targets whose polytope is wide because the literature corpus is incomplete on that edge.
- **When to use:** when a meta exists for a parent-disease relationship that the network isn't currently using. The 187 multipath candidates that "lack existing CSV literature row" are partly this case.
- **Doesn't reach:** washout (signal still crosses the same gates).

#### 3. Replacing aggregator gate type

- **Mechanism:** change an aggregator's `TYPE` from `any_of` to `if_then_else`, or to a less-averaging form. Or change structure to deweight low-contribution inputs.
- **Constraint changed:** the aggregator's deterministic combination logic.
- **Reaches:** aggregator-mediated washout where the gate's averaging is destroying signal.
- **When to use:** when an aggregator's `any_of` semantic doesn't match the clinical reality. `any_of` says "any qualifying input puts you in the bucket" - if inputs are heterogeneous in contribution (e.g., a physical activity composite where vigorous-PA contributes much more than walking), the gate is averaging things that shouldn't be averaged equally.
- **Cost:** clinical-judgment call required; new gate must better reflect the underlying biology.
- **Doesn't reach:** cases where the aggregator's semantic is right but multiple aggregator levels chain together. Need tool 4 for that.

#### 4. Restructuring chains: collapsing aggregator levels

- **Mechanism:** replace a multi-level chain `L → G1 → G2 → G3 → D` with a flatter `L → G_combined → D` where `G_combined` aggregates collectively in one step.
- **Constraint changed:** number of averaging steps from leaf to disease.
- **Reaches:** deep-chain washout where the chain has more averaging levels than necessary.
- **When to use:** when intermediate aggregator levels are organizational conveniences rather than meaningful taxonomic distinctions.
- **Cost:** requires reviewing the chain's clinical taxonomy. Are `G1`, `G2`, `G3` representing different real levels of construct (behavior → behavior pattern → lifestyle factor → disease)? Or are some bookkeeping nodes? Collapsing real taxonomic levels changes interpretation; collapsing bookkeeping levels reduces attenuation cleanly.
- **Doesn't reach:** cases where every level is real and the chain is irreducibly deep.

#### 5. Promoting leaves to direct disease parents

- **Mechanism:** take a high-leverage leaf currently buried in an aggregator chain and make it a direct parent of the disease.
- **Constraint changed:** K_DAG at the disease goes up by 1; the leaf's literature RR now constrains the disease directly rather than being averaged.
- **Reaches:** cases where one specific leaf has a much stronger evidence base than its siblings, and where attenuating it through averaging is wasteful. Trades aggregator washout for under-determination at the disease.
- **When to use:** when a leaf has a strong literature RR currently being averaged with weakly-supported leaves at the same level. Examples: a well-studied biomarker buried in a behaviors aggregator; an asbestos-exposure node buried in lc_industrial_exposure where the asbestos meta is sharper than the other industrial exposures.
- **Cost:** K_DAG at the disease grows. If the disease is already at K=5-6, promoting another leaf may push it into the under-determined regime where dep_distal becomes necessary to recover. Selective is the operative word - promote a few high-leverage leaves before paying a K-cost that exceeds the leverage gain.
- **Doesn't reach:** cases where leaves are individually weak and the aggregator's averaging is what makes them collectively informative.

### Decision tree per candidate

```
1. Is the path from the proposed wrapper location to the disease aggregator-free?
   YES → Tool 1 (dep_distal).
   NO  → Continue to step 2.

2. Is the path crossing exactly ONE aggregator, and does that aggregator's gate type seem clinically over-averaging?
   YES → Tool 3 (gate-type change). Examine whether `any_of` is right; if not, propose switch (e.g., to `if_then_else` with explicit conditions, or weighted structure).
   NO  → Continue to step 3.

3. Is the path crossing multiple aggregators where some seem like organizational rather than taxonomic distinctions?
   YES → Tool 4 (chain collapsing). List aggregators; label each "real conceptual level" vs "bookkeeping convenience." Collapse bookkeeping.
   NO  → Continue to step 4.

4. Does the chain contain one or two leaves whose literature is much stronger than their siblings'?
   YES → Tool 5 (selective leaf promotion). Identify strongest-literature leaves; check if K_DAG can absorb their promotion (current K + N_promoted ≤ ~7).
   NO  → Continue to step 5.

5. Is there a meta that the network is currently not using on a parent-disease edge anywhere in the candidate's structure?
   YES → Tool 2 (add direct study row). Even if rest of structure can't be addressed, tightening one edge with a missing meta produces incremental improvement.
   NO  → Structurally inaccessible to current tools. Defer; sits correctly in abstain tier.
```

### Priority ordering by paper-impact-per-hour

1. **Tool 1 at frailty/CAD direct-parent positions** if any pass §7.octies filter. Abstain-d to tight-LoTP would be transformative.
2. **Tool 4 (chain collapse) on frailty/CAD** if any chain levels are bookkeeping. Highest-leverage non-dep_distal move - addresses the *cause* of abstain classification.
3. **Tool 1 at descriptive-tier targets** with clean paths.
4. **Tool 5 (leaf promotion) on lung_cancer chain** if asbestos / smoking-quantity / similar can move out of the chain.
5. **Tool 2 (new study rows) wherever literature is incomplete.**
6. **Tool 3 (gate-type changes)** on aggregators where `any_of` is clearly over-averaging - most clinical-judgment work.

### Clinical-judgment review required

Tools 3, 4, 5 involve interpretive calls that should be reviewed before applying:
- Tool 3: is the new gate type clinically right?
- Tool 4: are the collapsed levels really bookkeeping, not taxonomy?
- Tool 5: does promoting this leaf push K_DAG > 7?

Tool 1 (with §7.octies filter passed) and Tool 2 (with PMID-verified meta) can be applied directly.

### Stop conditions for the tool-mix cycle

1. Classification surfaces no abstain-tier targets that have any treatable position → confirm explicitly that abstain-tier residual is structural.
2. Tool 3 candidate involves an aggregator whose clinical interpretation isn't well-defined → need clinical-judgment input.
3. Tool 4 candidate involves an aggregator that may carry real taxonomic structure → don't collapse without confirming.
4. Tool 5 candidate would push disease's K_DAG above 7 → surface for K-budget discussion.
5. Total treatable count across all five tools < 20 → abstain-tier residual is more structural than expected; paper should be honest.

### Paper update

The current §5 ("Three tiers, two abstention modes") should be updated to:

> "Abstention is structural in two modes: low-base-rate symmetric polytope (pancreatic cancer, where CIs straddle RR=1) and aggregator-mediated attenuation (where the path from any wrappable position to the disease passes through averaging gates that destroy joint structure). The latter mode is partially addressable through gate-type review (§7.nonies tool 3), chain restructuring (§7.nonies tool 4), and selective promotion of high-evidence leaves to direct disease parents (§7.nonies tool 5); targets whose every wrappable position lies behind genuinely-required averaging chains remain structurally inaccessible to current tools and report W rather than committing."

---

## 7.decies. Saturation, polarity, and grouping rules (May 6, 2026)

**Why this exists.** Tiny-net experiments and a full-net audit on cycle 25 surfaced four wiring rules that, when violated, compress AUC by saturating intermediate gates and washing out individual input signals. The rules are not judgment calls - they follow from the structure of how `any_of` / `all_of` gates aggregate evidence. This section states the four rules, gives the math of why each matters, and describes the operational fix when a rule is violated.

### The four rules (apply all simultaneously)

1. **Use the least-prevalent state of every leaf** as the gate trigger, with the RR direction set accordingly. The rare side carries more information (entropy maximized away from 50/50). Same epidemiology, different reference frame: "non-vegetarians have RR=1.36 vs vegetarians" and "vegetarians have RR=0.73 vs non-vegetarians" describe the same population, but only the second framing is usable in a BN because the trigger varies meaningfully.

2. **Limit K per gate.** An `any_of` gate collapses K inputs into a single yes/no. When `gate=yes`, the disease CPT cell for `gate=yes` is one value - it cannot tell whether 1 input fired or all of them. Higher K means more information lost in the binary collapse. Keep K small (target 2-3, hard ceiling 7 per §0.2).

3. **Don't group correlated leaves into one gate.** If `bmi`, `waist_circumference`, `body_fat_pct` all reflect central adiposity and one fires for a patient, the others tend to fire too. The gate counts the underlying construct once (no compound lift) AND we lose the ability to tell which sub-aspect contributed. With K=3 correlated inputs, the gate carries roughly K=1 worth of information.

4. **Don't put strong opposite-direction RRs in the same intermediate gate.** A gate input with RR=2.0 (risk) combined with another at RR=0.5 (protective) gives the QP solver contradictory constraints on a single gate→disease CPT cell. The solver compromises somewhere in the middle and both signals wash out. Symptom: when the protective input fires, the gate still predicts "risk-side" disease, inverting the protective effect.

When all four rules hold, the gate is non-saturated, individual inputs preserve their literature RRs, and disease predictions discriminate across patient profiles. When any rule is violated, expect compressed AUC for diseases downstream of that gate.

### Diagnosis: saturated gates

A gate is **saturated** when its baseline marginal P(gate=yes) sits near 1.0 with no evidence applied. Once saturated, evidence cannot move the gate - the disease prediction is locked at the saturated CPT entry regardless of the patient's risk-factor profile.

To diagnose:

```python
# Query each any_of/all_of gate's baseline marginal (no evidence)
from sn_bayes.utils import bayesInitialize, query
net = bayesInitialize(proto)
for gate in any_of_gates:
    m = query(net, proto, {}, [gate])[gate]
    p_yes = next(v for k,v in m.items() if 'yes' in k.lower())
    if p_yes > 0.95: print(f"SATURATED  {gate}  P={p_yes:.3f}")
    elif p_yes > 0.85: print(f"HEAVY      {gate}  P={p_yes:.3f}")
```

A gate at P>0.95 is functionally a constant; the disease's CPT entry for `gate=yes` becomes the only one consulted. A gate at 0.85-0.95 is heavily compressed: only patients in the rare 5-15% complementary state ever get a different prediction.

On cycle 25 this returned **15 SATURATED + 10 HEAVY out of 138 gates** - 18% of all aggregators were either dead or near-dead.

### Anti-pattern 1: rare-protective-as-NO-state trigger

A leaf representing a rare protective behavior (vegetarianism, intermittent fasting, probiotic supplementation, dietary restriction) is wired into a gate using its **NO-state** as the firing trigger, with a positive RR (`>1`) on the gate→disease edge. Examples from the cycle 25 net:

| Leaf state used as gate trigger | NHANES P | Gates referencing it |
|---|---|---|
| `probiotic_supplementation_no` | 0.98 | 6 |
| `vegetarian_no` | 0.98 | 4 |
| `intermittent_fasting_no` | 0.88 | 3 |
| `dietary_restriction_no` | 0.75 | 3 |
| `frailty_healthy_diet_no` | 0.66 | 3 |

The trigger is true for 75-98% of NHANES, so the gate input behaves like a constant. Each of these triggers feeds 3-6 different gates, each of which then feeds many diseases. One bad leaf-wiring saturates 5-15 disease predictions.

The math of the two framings is identical - "non-vegetarians have RR=1.36 vs vegetarians" and "vegetarians have RR=1/1.36=0.73 vs non-vegetarians" describe the same epidemiology. But the BN cannot use the first framing because the trigger state is true for almost everyone, so the gate input never varies and conveys no information.

**Fix: polarity flip.** Both ends must move together:

1. **Input value:** `vegetarian_no` → `vegetarian_yes`
2. **Stat / RR Stat / Plus minus:** invert the RR ( `1.363` → `1/1.363 = 0.734`, with PM rescaled on log-RR)

The flipped form encodes the same epidemiological claim about reality but with the rare side as reference. The gate's marginal contribution from this input drops from "almost always firing" (0.98) to "almost never firing" (0.02), giving the gate room to respond to the patient's actual state.

**Necessary, not sufficient.** The flip alone doesn't fully unlock the gate when other inputs are also high-prevalence. Tested directly with the cycle-25 wiring of `cardiovascular_diet`:

```
A: NO-state trigger (current real net)
  No evidence: P(gate=yes) = 0.9945  (saturated)
  Patient IS vegetarian: P(disease) = 0.0906
  Patient is NOT vegetarian: P(disease) = 0.1001
  Spread = 0.0095

B: YES-state trigger (flip applied)
  No evidence: P(gate=yes) = 0.7221  (NOT saturated)
  Patient IS vegetarian: P(disease) = 0.1102
  Patient is NOT vegetarian: P(disease) = 0.0998
  Spread = 0.0104
```

The flip desaturates the gate (0.9945 → 0.7221) but the disease-level signal stays small because the gate is also fed by `unhealthy_diet` at 0.70 prevalence. The any_of fires for most patients regardless of the vegetarian status. To recover the protective signal, additionally route the protective leaf direct to disease (Tool 5 from §7.nonies) or split the gate (Tool 3 / Tool 4).

### Anti-pattern 2: high-prevalence "yes"-state leaf

A leaf whose "yes" state qualifies 50-80% of the population. Examples from cycle 25:

| Leaf | Yes-state P | Gates referencing it |
|---|---|---|
| `unhealthy_waist_yes` | 0.69 | 4 |
| `glyphosate_based_herbicides_yes` | 0.79 | 3 |
| `household_pollution_yes` | 0.54 | 7 |
| `dietary_fiber_gm_yes` | 0.80 | 2 |

Unlike Anti-pattern 1 these are not framing inversions. Some are just true reflections of US population reality - `unhealthy_waist_yes` at 0.69 matches CDC obesity prevalence; `dietary_fiber_gm_yes` flags people not meeting dietary recommendations and most Americans don't. The threshold may be set correctly relative to clinical guidelines, but the resulting prevalence is too high for the leaf to discriminate inside an `any_of` gate. Other examples (e.g., `glyphosate_based_herbicides_yes` at 0.79) suggest the threshold is genuinely too broad and could be tightened to capture only high-exposure subgroups.

**Three possible fixes**, depending on whether the threshold is correct:

1. **Tighten the threshold** if the cutoff is genuinely too broad (e.g., glyphosate "any exposure" → "high exposure quartile"). Change the leaf's `discrete_nhanes_explicit` cutoff and document the new cutoff in the `comment` column with the citation that supports it.
2. **Promote to direct disease parent** (§7.nonies Tool 5) if the leaf has a strong literature RR and tightening the threshold would lose the standard clinical definition. The literature RR applies cleanly to the disease without being averaged through a saturating gate. Costs K_DAG=1 at the disease.
3. **Drop the leaf from the saturating gate** if the leaf doesn't add discriminative information at the population prevalence (the gate marginal is the same with or without this input). The leaf may still be useful elsewhere in the net.

### Why these compound with the independence assumption (AUC bias)

A second, related compression comes from the BN's treatment of correlated leaves as independent. When two leaves are correlated in reality (e.g., `bmi`, `waist_circumference`, `body_fat_pct` all reflect central adiposity), naive aggregation through a gate underestimates the compound lift. Tested in tiny nets:

| Leaf correlation α | Naive Δ at disease | True Δ at disease | BN underestimates by |
|---|---|---|---|
| 0.0 (independent) | 0.0087 | 0.0122 | 29% |
| 0.3 | 0.0072 | 0.0127 | 43% |
| 0.6 | 0.0056 | 0.0174 | 68% |
| 0.9 | 0.0047 | 0.0262 | 82% |

For diseases with clusters of correlated risk factors (metabolic syndrome cluster, cardiovascular cluster), the under-estimation compounds with gate saturation. Both push P(disease) toward baseline regardless of the patient's true risk, compressing the predictive range and lowering AUC.

The independence-assumption portion is not directly addressable in current BayesExpert structure (would require a true common-cause node with bidirectional information flow). What `dependency_distal` provides is parallel-input edges, not common-cause modeling - it adds an extra path but does not propagate evidence between sibling leaves through a shared latent.

### Decision tree (extends §7.nonies)

When auditing a saturated gate (P(gate=yes) > 0.85):

1. **Inspect each input row.** Get the input's NHANES marginal (`P(state) = nhanes[code].mean()` after thresholding).

2. **If the trigger state has P > 0.6**, classify:
   - **Phrased as "X_no"** (rare protective behavior in NO-state) → Anti-pattern 1. Polarity-flip the input + invert the RR. Expect partial relief if other inputs are also high-prevalence.
   - **Phrased as "X_yes"** (high-prevalence positive state) → Anti-pattern 2. Pick one of the three fixes above based on whether the threshold is genuinely too broad or just reflects population reality.

3. **If polarity flip is applied but gate is still saturated**, the gate has additional high-prevalence inputs. Either:
   - Apply Anti-pattern 2 fix to those other inputs (if they're threshold-loose), or
   - Split the gate into separate risk and protective sub-gates (§7.nonies Tool 3), or
   - Promote the now-rare protective leaf to a direct disease parent (§7.nonies Tool 5).

4. **Never mix risk and protective inputs in the same `any_of`**. any_of fires when any input fires, but mixing direction means the gate has no consistent semantic. Symptoms: when the protective input fires, the gate still says "yes" and the disease's gate→disease RR is treated as risk-raising, inverting the protective effect.

### Stop conditions

- Polarity flip without RR inversion → catastrophic regression (vegetarians get higher disease prediction). Always flip both ends.
- Tightening a threshold past the cutoff supported by clinical guidelines or literature → the leaf no longer represents a defensible construct. Either find a tighter cutoff that has a literature citation, or use Tool 5 / drop instead.
- Promotion to direct disease parent (Tool 5) on a polarity-flipped protective leaf is fine, but adds K_DAG=1 to the disease. If the disease is already at K=5-6, prefer Tool 3 (gate split) over Tool 5.

---

## 8. Building a New Network - Systematic Procedure (Any Domain)

This section is a **domain-agnostic step-by-step procedure** for constructing a BayesExpert network on any topic, executable by an LLM (Claude or similar) or a human collaborator. The procedure produces a net that respects the four wiring rules (§7.decies) by construction, avoiding the saturation and independence-assumption failure modes that compress AUC.

### Inputs needed

To start, you need three things:

1. **A literature corpus** for the topic - meta-analyses, systematic reviews, large primary studies. Examples:
   - Health: PubMed meta-analyses (e.g., diabetes risk factors)
   - Economics: NBER working papers, Federal Reserve studies (e.g., loan default predictors)
   - Politics: ANES literature, Pew Research reports (e.g., voting behavior)
   - Sports: Player-stat regression studies (e.g., injury risk factors)
   - Any domain with quantified effect sizes (RR / OR / HR / standardized differences)

2. **A reference population dataset** with the variables the literature talks about. Each domain has a canonical dataset:
   - Health: NHANES (US adults), UK Biobank, MIMIC-IV
   - Economics: Survey of Consumer Finances, American Community Survey
   - Politics: ANES, CES, Pew tracking polls
   - Sports: League play-by-play data (Statcast for MLB, etc.)
   - The dataset must contain enough variables to compute population marginals and pairwise correlations.

3. **Domain knowledge** - either a human collaborator who knows the field, or an LLM with appropriate training. This is needed for naming latent constructs and judging whether a gate is "definitional" or "risk-aggregator."

### The nine steps

#### Step 1: Identify outcome (disease) nodes

Decide what you're predicting. List the outcome nodes - the terminal nodes of the DAG. Examples:
- Health: `lung_cancer`, `diabetes`, `heart_attack`
- Economics: `loan_default`, `bankruptcy`, `unemployment`
- Politics: `votes_party_X`, `turnout_yes`, `policy_support_high`
- Sports: `injury_season_lost`, `top_quartile_performance`

Each outcome gets a `dependency_priors` definition row with a baseline prevalence prior. The prevalence comes from the reference dataset (e.g., 10% diabetes in NHANES → `index1=0.1`).

#### Step 2: Extract risk factors from literature

For each outcome, search the literature for meta-analyses linking risk factors. Record per study:
- Risk factor name (matching reference dataset variables when possible)
- Effect size (RR / OR / HR / SMD)
- 95% confidence interval
- Sample size
- Population studied
- Citation

Use the existing intake guide (§1) to organize findings into the spreadsheet's column structure.

#### Step 3: Get marginals and correlations from reference data

For each risk factor, compute:
- **Marginal**: P(factor = yes-state) in the reference population
- **Pairwise correlations**: corr(factor_i, factor_j) for all pairs - either Pearson (binary indicators) or Spearman (ordinal/continuous)

Tools: pandas `df.corr()` on indicator columns; clustering libraries for grouping correlated variables. See `scripts/discover_clusters.py` for a reference implementation.

#### Step 4: Apply the four wiring rules (per §7.decies)

For each prospective leaf and gate input:

1. **Rule 1 - Least-prevalent state.** Pick the rare state of every variable as the gate trigger; invert the literature RR if needed. Information per row is maximized away from 50/50; gate marginals don't saturate.
2. **Rule 2 - Limit K per gate.** Target K = 2-3 inputs per `any_of`/`all_of`; hard ceiling K = 7. Beyond that, use latents or split.
3. **Rule 3 - Don't group correlated leaves into one gate.** If two inputs have empirical correlation ≥ 0.3, they don't belong in the same gate (the gate will saturate AND lose information through the binary collapse). Cluster them under a common-cause latent instead (Step 5).
4. **Rule 4 - Don't mix opposite-direction RRs in the same gate.** Risk inputs (RR > 1) and protective inputs (RR < 1) in the same `any_of` cancel out in the QP solver. Split them or keep only one direction per gate.

#### Step 5: Cluster correlated factors and design latents

From Step 3's correlation matrix, identify clusters: groups of factors with pairwise correlation ≥ 0.3. Algorithm:
- Build a graph: nodes = factors, edges = correlations ≥ threshold
- Connected components = candidate clusters
- For each cluster of size ≥ 2, design a common-cause latent

Per cluster:

```
latent_<construct>           ← discrete_priors, prior 0.3 (default - adjustable)
each_member ← latent         ← dependency_nhanes_explicit (with NHANES code)
                                with literature-derived RR (sqrt of pairwise RR
                                if redistributing existing pairwise studies, or
                                from latent-specific literature if available)
```

Naming the latent requires domain knowledge. Look for the construct that explains why these factors co-occur - the same study population, the same biological pathway, the same socioeconomic mechanism. Examples:
- Health cluster {bmi, waist, fat_pct} → `central_adiposity_latent`
- Economic cluster {fico_score, credit_utilization, payment_history} → `creditworthiness_latent`
- Political cluster {education, urban_residence, age_under_45} → `cosmopolitan_latent`

If no clear construct emerges, give the latent a generic name (`latent_cluster_X`) and rely on the data to anchor it.

#### Step 6: Design the architecture

Wire up the net using the cluster structure:

- For each cluster, add the latent and its child edges (Step 5 output)
- For each remaining gate (factors not in any cluster), use `any_of` / `all_of` ONLY if the gate is **definitional**:
  - Definitional: encodes a clinical criterion, formal definition, or logical rule (e.g., "metabolic_syndrome: 3 of 5 criteria")
  - Not definitional: aggregates correlated risk factors → don't gate, route through latent or direct edges to disease
- For each outcome (disease) node, parent edges come from:
  - Cluster latents (one edge per relevant cluster, providing the cluster's compound effect)
  - Direct factor edges (Tool 5 promotion - for high-leverage individual factors not adequately captured by a cluster)
  - Definitional gates (when a formal definition is the parent)
- Stack latents hierarchically when clusters themselves correlate - top-level latent → sub-cluster latents → factors. Evidence then flows across clusters via the upper latent.

#### Step 7: Build and validate

1. Run `prepare_config` → `linearize_config` → `create_bayesnet_proto_linear` (the standard pipeline).
2. Run the validation suite:
   - **Saturation audit**: query each gate's marginal; flag any > 0.85.
   - **Direction tests**: for each disease, query P(disease | factor = yes) for each direct factor; compare sign to literature RR. Flag mismatches.
   - **Correlation tests**: for each cluster, query P(member_2 | member_1 = yes); should lift above prior. If not, the latent's RRs are mis-calibrated.
   - **AUC test** (if the reference dataset has outcome labels): per-target AUC. Should beat naive baseline (no risk factors).

#### Step 8: Iterate on findings

- **Saturated gates** (Step 7 audit) → split the gate (Rule 2) or convert to latent (Rule 3) or drop weakest inputs.
- **Direction mismatches** → check Rule 1 (polarity flip) and Rule 4 (mixed-direction inputs). The mismatch is usually one of these.
- **Cluster non-propagation** → latent's child RRs are too weak; recompute from literature or NHANES correlations.
- **Low AUC on a target** → likely a saturation cascade in that target's closure. Audit ancestors, apply Tool 5 (promote high-leverage factors directly to disease) for the worst-saturated gates.

#### Step 9: Document and version

For each net, commit:
- `relations.csv` (the canonical source)
- A README in the relevant subdirectory documenting:
  - Topic (what's being predicted)
  - Reference dataset used
  - Literature corpus
  - Latents added (their constructs + literature support)
  - Validation metrics
- Tag the build (e.g., `topic_v1_<date>`)

Future Claudes / collaborators can resume by reading the README + this manual section.

### Worked example: building a non-health net

Sketched walkthrough for a `loan_default` predictor on the Survey of Consumer Finances (SCF):

1. **Outcomes**: `loan_default_30day`, `loan_default_90day`, `bankruptcy_filing`
2. **Literature**: NBER working papers on consumer credit risk; Federal Reserve research on default predictors
3. **Reference data**: SCF triennial dataset (variables: income, credit utilization, debt-to-income, employment status, etc.)
4. **Marginals + correlations** from SCF: e.g., {credit_utilization, payment_history_late, fico_score} cluster with pairwise corr 0.5-0.7
5. **Latents**: `creditworthiness_latent` (clusters {fico, utilization, payment_history}); `income_stability_latent` (clusters {tenure, sector_volatility, income_growth})
6. **Architecture**:
   - `creditworthiness_latent → fico, utilization, payment_history` (each with literature-derived RR)
   - `income_stability_latent → tenure, sector, income_growth`
   - `loan_default ← creditworthiness_latent (RR = 4.0)`, `loan_default ← income_stability_latent (RR = 2.5)`, `loan_default ← debt_to_income (direct, RR = 2.0)` (Tool 5 promotion for high-leverage individual factor)
7. **Build, validate** against held-out SCF cohort
8. **Iterate**: saturation, direction, correlation tests
9. **Document**: README in `loan_default_v1/` with literature citations

The same structure works for any domain. The four wiring rules and the latent-clustering procedure are domain-agnostic; only the literature, reference data, and latent construct names change.

### Tools provided in this codebase

**Scaffolding (Step 0 - start a new domain from zero):**
- `scripts/new_domain_starter.py` - creates `domains/<topic>/` with directory structure, blank `relations.csv` (with proper headers), reference-dataset placeholder, literature intake prompt template, README workflow checklist. Run once per new topic.

**Discovery & audit (Steps 3-5):**
- `scripts/discover_clusters.py` - finds correlation clusters in a reference dataset. Domain-agnostic via `--relations` and `--dataset` flags; defaults to BayesExpert paths.
- `scripts/audit_gates_for_migration.py` - classifies each existing gate as KEEP / PROMOTE / LATENT / SPLIT / RULE4. Requires a built proto.
- `scripts/generate_migration_plan.py` - emits a unified migration patch JSON from the audit.

**Validation (Step 7):**
- `scripts/data_checks.py` - structural checks before build
- `scripts/objective_rr_comparison_test.py` - post-build per-row direction + magnitude (DIRECT + DISEASE modes)
- `scripts/observed_evidence_auc.py` - per-disease AUC, observed-evidence-only, per-target diagnostic-biomarker exclusion
- `scripts/test_common_cause_existing_types.py` - verifies common-cause architecture works (smoke test)

**Iteration (Step 8):**
- `scripts/subnet_builder.py` - fast disease-by-disease testing (no full rebuild)
- `scripts/audit_subnet_wins.py` - aggregate per-disease wins into a global migration

### Starting from zero on a new topic

```bash
# 1. Scaffold
python3 scripts/new_domain_starter.py \
    --topic <your_topic> \
    --description "..." \
    --reference-dataset /path/to/your/dataset.csv \
    --dataset-source "Citation/URL of the dataset" \
    --output-dir domains/<your_topic>

# 2. Document variables (open domains/<your_topic>/data/variables.md and fill in)

# 3. Extract literature using the prompt template
#    Pass domains/<your_topic>/literature/INTAKE_PROMPT.md to an LLM with your corpus

# 4. Run discovery (fills in cluster candidates)
python3 scripts/discover_clusters.py \
    --relations domains/<your_topic>/data/relations.csv \
    --dataset domains/<your_topic>/data/reference_dataset.csv \
    --output domains/<your_topic>/paper/cluster_candidates.tsv

# 5. Apply the four wiring rules and design the architecture (manual; see §7.decies + §8)

# 6. Build using the standard pipeline (prepare_config → linearize_config → create_bayesnet_proto_linear)

# 7. Validate, iterate, document - per Steps 7-9 above
```

A capable Claude reading this manual + working through these tools should be able to construct a BayesExpert net for any topic with a literature corpus and reference dataset, without code modifications.

### Anti-patterns documented elsewhere - read before each cycle

- §0.2 (K ≤ 6 rule + bypass-fix awareness) - direct edges often exist for reasons, don't blindly cut K
- §7.bis through §7.decies - accumulated lessons from cycle 1-25 of this codebase's net
- §7.quinquies - subnet workflow rules (don't run subnets and full builds concurrently; one change per cycle)
- `feedback_*.md` in the memory dir - collaboration patterns the user has formalized over time

---

## 8.0. Stopping criterion -- when is the net done?

The construction loop has no natural endpoint -- you can always look for
another study, tighten another CI, prune another K. To prevent indefinite
iteration, an LLM (or human) needs an explicit stop signal. Use this
checklist; the net is "done enough" to ship a paper or deploy when **all
five** items are met:

1. **Headline whole-net metrics clear the publication thresholds:**
   - Direction-match >= 95% on objective_rr_comparison
   - Within-50% rate >= 85%
   - Median %-error <= 20%
   - Median validation window W <= 0.005 across cells with binding
     literature constraints

2. **Direction-committed bucket covers >= 70% of named target endpoints.**
   "Named" means listed in the paper's claims or in the user's intake
   list of primary outcomes. The other <= 30% sit in the descriptive or
   abstain tier with a documented reason for each.

3. **Every clinically-load-bearing endpoint (the ones the paper or
   product makes specific predictions about) is in the committed
   bucket.** It is acceptable to abstain on rare or chain-mediated
   endpoints; it is not acceptable to claim risk for one without it
   passing direction + within-50% on every cited row.

4. **No row over 100% error.** A single row at >= 100% means the model
   is qualitatively wrong on that edge -- the literature says factor-of-2
   effect, the model says the opposite. Either fix the row, drop the
   parent, or flag the row as out-of-scope (and document).

5. **Build is reproducible.** Saved pickle + JSON + xlsx in
   `paper_results/` reproduce the headline numbers exactly when re-loaded
   by `scripts/reproduce_paper.py`. Anyone with the repo can verify.

If any item fails, identify the worst-performing target and apply the
appropriate growth operator from §7.ter. Re-evaluate after each cycle.
If the metric keeps drifting in one direction over 3+ consecutive cycles
without improvement, you've hit a structural ceiling -- consider
restructuring the DAG (deeper aggregator chains, gate-type changes, or
parent pruning) rather than another row addition.

**What "done" does *not* mean:**

- It does not mean every target is in the committed bucket. The
  abstain tier is a feature, not a failure: it reports that the
  literature plus the law of total probability cannot uniquely
  determine the marginal at the available evidence level. A clinical
  user reading "this prediction abstains; window W = 0.34" learns
  more than they would from a confident-looking but unreliable
  point estimate.
- It does not mean median W = 0. Cells with no literature constraint
  legitimately sit at W close to the simplex bound; what matters is
  W on the *constrained* cells.
- It does not mean the joint-fidelity test passes 100%. Joint
  fidelity is partially tautological (per-node NHANES marginals are
  QP constraints) and is reported as a sanity check, not as the
  primary validation.

---

## 8.bis. Iterative 4 + window optimization (paper construction process)

**Observation during Apr 20-21 iteration:** each fix traded metrics - a
structural fix (flatten, blank redundant parent, etc.) improved direction
and calibration, but WIDENED the window. And vice versa: a study
substitution that tightened the window tended to briefly regress
direction on related rows.

**Approach (the repo owner, 2026-04-21):** coordinate descent on (4 metrics, window):

### Phase 1 - optimize 4 metrics, allow window to widen

Metrics: direction, calibration, joint fidelity, NHANES AUC.

Methods:
- Blank duplicate-encoding parents (see §7.bis).
- Flatten saturating any_of aggregators (see §7.bis).
- Add connecting studies to resolve co-parent correlations (see §7.bis).
- Promote or bypass structural chains that are absorbing signal.

Accept window regression in this phase.

### Phase 2 - optimize window, hold 4 metrics as acceptance floor

Window measures study-vs-study and study-vs-net disagreement. To reduce:

1. Run `scripts/extract_results.py` and look at `widest_20` - the 20 studies the QP had to stretch most. These are the contested ones.
2. For each high-window row, decide:
   - **Substitute the study**: find a better-fitting meta-analysis (newer, larger, more population-matched). Replace the row.
   - **Restructure**: add an intermediate node that explains why two studies disagree.
   - **Rescore the study**: maybe its RR was wrong because its P0 was wrong (rerun autofill_p0_sd).
   - **Blank if irresolvable**: mark as low-quality and remove.
3. **Subnet-test each candidate change in parallel (per §7.quinquies v2)**, then rebuild **once** with all validated changes - NOT once per change. The "rebuild after 5-10 changes" wording above pre-dates the v2 process; the disciplined version is "fire all candidates as parallel subnets, validate, batch winners, ONE rebuild".

Accept small direction/calibration regression as long as staying above the phase-1 acceptance floor.

### Iterate

Each round = one full cycle (subnet batch → batch winners → ONE rebuild → 5-core panel). Each rebuild should improve max-over-phases of each metric. Convergence: both phases simultaneously satisfy the acceptance criteria. Per §7.quinquies v2: multi-cycle rebuilds in a single round (cycle N → N+1 → N+2 each adding one untested change) is the anti-pattern that caused the May-1 cycle 25/26/27 mess. Don't do it.

### Why this works for the paper

- The window metric is the "empirical audit" of study consistency (memory
  `project_window_metric_paper.md`). It's THE signal that lets a
  construction process decide when studies need to be replaced vs when
  the architecture needs to change.
- Demonstrating that the alternating process converges is the paper's
  LLM-assisted-construction story: an LLM can follow this loop without
  human guidance because each step's next action is computable from the
  previous step's metrics.

### Current state (Apr 21)

Net is in phase 1 of this process. cycle4_no_df at 96.5% direction, 2.8%
calibration over-5%, window mean 0.215 (widened from no_df baseline 0.115).
Next session should enter phase 2 using `widest_20` from
`paper/results_cycle4_no_df.json` (or whatever the latest cycle is) to
identify study-substitution targets.

### Scenario design - prevalence targeting (Apr 21)

The scenario-based "evidence" test is a **test of convenience** (not a
paper-core metric; see §6.bis). It evaluates whether the net responds
correctly when clamped to common lifestyle states. Its value depends on
scenario selection.

**Rule: match scenarios to the states real users will actually have or
try.** For lifestyle applications, that means:

- **Prevalent risk states** - states a large fraction of the target
  population has. In NHANES: overweight (bmi 25-29) is ~32%, obesity
  (bmi≥30) is ~36%, severely obese (bmi≥40) is only ~7%. Test all tiers,
  not just the extreme. Same for sleep (6-7 hours is common; <5 is
  extreme), sedentary behavior (4-6 hrs sitting is moderate; >10 hrs is
  extreme), etc.
- **Common intervention states** - what people actually try: mediterranean
  diet, intermittent fasting, daily walks, vegetarian, alcohol reduction,
  smoking cessation, sleep 7-8 hours, fiber increase, weight loss 5-10%.
- **Combinations** - real users stack interventions (diet + exercise +
  sleep). Test multi-evidence scenarios.

Current scenario roster in `scripts/extract_results.py` (as of Apr 21):
11 risk scenarios (both extreme and moderate tiers), 8 protective
scenarios, 2 combination scenarios. Covers the typical clinical lifestyle
scenarios a user would evaluate.

**What NOT to test in scenarios** (paper-claim perspective): edge-case
effects that no one would try (e.g., "what if I ate only blueberries for
a year"). Those aren't prevalent and their test result doesn't generalize.

---

## 9. LLM-native build workflow (for construction via Claude / similar)

This section describes the operational workflow an LLM can follow to build a net
from studies, without human-in-the-loop Excel editing. Every step is scriptable.

### 9.1 Fixed QP settings (paper-principled)

```python
create_bayesnet_proto_linear(
    linear, use_v2=True, use_subset=True,
    w_ls=1.0, w_df=0.0, w_mono=1.0,
    nhanes_data=nhanes, cache_dir=cache,
)
```

- `w_ls=1.0` - LS to study RRs (always on; whole method)
- `w_df=0.0` - data-fit to NHANES joint: **OFF**. It's curve-fitting against the NHANES AUC test. Interconnectedness via subset constraints substitutes.
- `w_mono=1.0` - monotonicity slack. Optional; can ablate for paper.
- `use_subset=True` - subset/pairwise constraints. Mathematical truth (law of total probability). Always on.

### 9.2 xlsx editing via JSON patches, not openpyxl loops

**Rule:** every xlsx edit goes through a named JSON patch file in `scripts/patches/`.
This makes edits reproducible, auditable, and revert-able.

**Pattern:**
```python
# Build patch
patches = [{'xlsx_row': 1534, 'fields': {'output': 'chemical_pc_risk',
            'comment': '[<date> <reason>] promoted from pc_hydrocarbon...'}}, ...]
with open('scripts/patches/<name>.json','w') as f: json.dump(patches, f)

# Apply
python3 scripts/apply_patch_to_xlsx.py --patch scripts/patches/<name>.json
```

**Guardrail rules:**
- Never modify the original `data/Individual Relations.xlsx`. Only the `working.xlsx`.
- Backup before any substantial patch: `cp working.xlsx xlsx_pre_<name>.xlsx`.
- Comment every edited cell with `[<date> <pattern-name>] <reason>`.

### 9.3 P0 autofill flow

Column A (P0) should come from NHANES. `scripts/autofill_p0_sd.py` does this
automatically. It:
1. For each study row (Stat ∈ {OR, HR, SMD, ES, WMD}), looks up the output node's definition row.
2. Based on the definition row's `Type`:
   - `*_nhanes_explicit` → compute P(value1) via `calculate_priors_explicit` from NHANES code + index1
   - `*_nhanes_quartile` → 0.25 by construction
   - `dependency_priors` / `discrete_priors` → read index1 directly as probability
   - `all_of` / `any_of` / `avg` / `if_then_else` / `dependency_distal` → skip (P0 comes from child aggregation)
3. Writes to col A in place; also evaluates simple Excel formulas elsewhere
   (`=1-Qn`, `=<num>/<num>`, etc.) so `pd.read_excel` sees plain values.
4. Recomputes col M/N using `sn_bayes/rr_formulas.py` (Python port of Excel formulas).
5. Appends a comment note to col AC on every changed row.

**Do not hand-edit P0 values.** Rerun autofill after adding new rows.

### 9.4 Subnet testing (always before full rebuild)

```bash
python3 scripts/subnet_builder.py \
    --target <disease_node> \
    --label <label> \
    --patch-file scripts/patches/<patch>.json
```

Produces a 2-3 min build of just the dependency closure of `<disease_node>`.
Run `scripts/objective_rr_comparison_test.py --pickle <subnet_pickle>` against the subnet to see direction + magnitude per row.
Query the subnet directly to see CPT behavior on the specific node.

**Only commit to full rebuild if subnet shows expected improvement.**

### 9.5 Test panel (after every full rebuild)

1. **`scripts/objective_rr_comparison_test.py`** - direction + magnitude test against every literature RR row in xlsx. Writes `paper/objective_rr_comparison_<label>.json`. This is the headline test.
2. **`scripts/extract_results.py`** - calibration, windows, joint fidelity, ρ, study_rr_summary, nhanes_fidelity, chain_propagation. Writes `paper/results_<label>.json`. Slow (30-60 min). NOTE: Section 4 (query_rr direction) and Section 10 (NHANES Individual AUC) should NOT be reported - direction has the OUTVARS/walked-up issue, AUC has target leakage. Use `objective_rr_comparison_test.py` and `observed_evidence_auc.py` instead.
3. **`scripts/five_core_chart.py`** - produces comparison chart across all builds. Run after each extract finishes.

### 9.6 Commit + document

- Every build gets its own pickle + config JSON saved, for reproducibility.
- Every architectural change gets a commit with clear message describing what changed.
- Patterns learned (like those in §7.bis) go back into this manual.

### 9.7 Known traps (avoid these)

- **Don't blank a study row if it's the only parent** of a `dependency_nhanes_explicit` node → torch.zeros crash (empty CPT). Change the node's Type to `discrete_nhanes_explicit` first.
- **K up to 7 is permitted (May 1 directive)**; warning at K>7. Cycle 14 ACM had K=10 in paper baseline. Don't blank rows just to satisfy the warning - use `dependency_distal` if K really needs to come down.
- **Don't mix encoding granularity** (`bmi` + `bmi_naive` on the same disease). Blank the coarser one.
- **Don't use `avg` Type in distal chains** - INVARS shape incompatibility. Known code limitation.
- **Don't rely on Excel formula cache** after openpyxl writes - cache gets wiped. Either resolve formulas to plain values (preferred, per `autofill_p0_sd.py`) or open+save in Excel.
- **Don't forget the comment** when adjusting a study RR. Mandatory provenance rule - see §6.bis criterion 5.

### 9.8 Example: fix a saturation flip via subnet

Pattern: objective RR shows `X → Y` with `query_rr ≈ 1.0`. Saturation.

1. Run `subnet_builder --target Y` - confirm saturation is in a subnet.
2. Hypothesize fix: flatten aggregator, bypass, or change Type.
3. Write patch JSON with the structural change.
4. Subnet-test the patch: build + diagnose + query.
5. If subnet shows the fix works, commit to full rebuild with the patch.
6. Run full test panel after rebuild.
7. Document the pattern + fix in §7.bis if it's a new pattern.

### 9.9 Citation verification (MANDATORY - read this every cycle)

**Rule.** Every study row added to `data/relations.csv` must cite a real, PubMed-resolvable meta-analysis or large systematic review. No estimates. No derivations from threshold data. No PMIDs from memory.

**Why this rule exists.** On 2026-05-10 an audit of 148 study rows the LLM had added since the Apr-4 standard baseline found 65 of them cited fake PMIDs - IDs that resolved to real-but-unrelated papers (Rett syndrome MECP2 mutations, ZnO/Ag2O nanocomposites, Abbe-flap surgical reconstruction, athletic jumping performance, etc.). The relationships themselves were textbook epidemiology (smoking→stroke, age→osteoporosis, etc.) but the citations were fabricated. The paper's thesis is that an LLM following this manual extends the network *from real literature*. A single fake PMID undermines that thesis. The affected rows were corrected against the cited papers, and the headline numbers were recomputed against the corrected network.

**Procedure: before adding any row with a `citation` field.**

1. **Fetch the abstract.** WebFetch `https://pubmed.ncbi.nlm.nih.gov/<PMID>/` (or `https://www.ncbi.nlm.nih.gov/pmc/articles/PMC<id>/` for PMC IDs).
2. **Check the title.** Does it match the relationship being encoded? "Smoking and stroke" must be about smoking and stroke - not about insecticide-exposed fumigators in Colombia.
3. **Check the magnitude.** The abstract should report an RR / OR / HR / SMD / MD within ~50% of the encoded value (sign-inverted is acceptable: paper reports RR=0.6 for protective state X, row encodes RR=1/0.6≈1.67 for opposite state - that is mathematically sound).
4. **Check the population.** Meta-analysis of cohort studies > narrative review > single trial. Avoid single-trial SMDs as the basis for a population-level dependency (see the Akkermansia muciniphila proof-of-concept trial which got blanked for this reason in cycle 18).

**Two-agent quorum (required for EVERY new citation, regardless of batch size).** A single LLM agent reading an abstract can mis-extract the reported effect size - or, worse, can hallucinate a PMID that looks plausible. The risk per row is independent of batch size; quorum protection must therefore apply per row, not per batch.

1. Spawn agent A: "for each (output, input) relationship in this list, find the real meta-analysis PMID, report title + author/year + reported RR/OR/HR + 95% CI."
2. Spawn agent B with the **same list** but a different prompt phrasing (e.g., "find the canonical meta-analysis used to support this clinical relationship; return the PMID, the paper's reported pooled effect size, and the population studied").
3. After both finish, compare:
   - Same PMID and same reported RR (within 10%) → **APPROVE** and add.
   - Same PMID, different reported RR (>10% gap) → **HOLD** - one agent mis-read the abstract; have the user resolve.
   - Different PMID → **HOLD** - disagreement on the canonical paper; user decides which to use, or both are real metas (in which case keep both).
   - Either marks TRULY-NOT-FOUND → **DROP** the row.
4. Record both agents' reports as an artifact alongside the commit (`paper/citation_quorum_<batch>_<date>.json`) so reviewers can trace provenance.

For a 1-row addition, this is still 2 agent invocations - not optional. (Empirically: of the 75 problematic citations caught on 2026-05-10, several were added one at a time across earlier sessions; no batch-threshold rule would have caught them. Per-row quorum is what catches them.)

**Orchestrator spot-check (third verification layer).** Two-agent quorum protects against single-agent hallucination but not against rare double-hallucination (both agents independently inventing the same plausible-sounding PMID). After each batch of quorum-approved rows lands, the orchestrating Claude (not the worker agents) WebFetches **at minimum 30% of the approved PMIDs** - randomly sampled - and confirms title + author + year + reported effect size match what the agents claimed. If any spot-check fails, freeze the batch and audit all approved PMIDs before pushing. Memorialise the spot-check sample in the quorum-artifact JSON alongside the agent reports.

(Empirically observed 2026-05-11: a 5-row spot-check across cycles 2-5 verified PMIDs 22372522 / 27543718 / 21672193 / 29402646 / 40222723 - all five matched. Two-agent quorum was working; the orchestrator-spot-check confirms it didn't fail silently. Record each spot-check alongside the corresponding commit.)

**If verification fails:** do not add the row. Find a different real meta, or skip the relationship until one is found. A network with K=4 verified parents is better than one with K=7 where 3 are fabrications.

**Procedure: before finalizing any CSV state for release.**

Run `python3 scripts/verify_citations.py data/relations.csv` (added 2026-05-10). The script:

- Extracts each citation's PMID (from `pubmed.ncbi.nlm.nih.gov/<id>/`, `PMID: <id>`, or `PMC<id>` patterns).
- For each unique PMID, WebFetches the PubMed abstract.
- Compares the abstract's title against the citation field's title text (if present in a `<science><title>...</title>` wrapper or "Author Year Journal" prefix).
- Returns a JSON report: `verified_ok`, `topic_mismatch`, `pmid_unresolvable`, `no_pmid`.

Exit code is non-zero if any row is `topic_mismatch` or `pmid_unresolvable`. CI / pre-push hooks should block until the report is clean.

**Format conventions for the citation field.** Pick exactly one of these forms so the verifier can parse the PMID without ambiguity:

```
https://pubmed.ncbi.nlm.nih.gov/12345678/ - <author> <year> <journal>, "<title>". <effect-size>.
https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/ - <author> <year> ...
```

Do NOT use the `<science><title>...</title></><authors>...</></><source>...</>` XML wrapper - that format gave the LLM cover to invent PMIDs because the structure looked authoritative without a resolvable ID. Plain prose is harder to fake.

**Audit cadence.**

- After every full rebuild: run `verify_citations.py` over all rows that changed in the last 7 days.
- Before any public release (code repository, PDF appendix, table of inputs): run over the entire CSV.
- Quarterly: spot-check 5% of the corpus at random; recheck for PubMed retractions of cited papers.

**The 4 row tells of a fabricated citation.** Any one of these means the row needs verification before it stays:

- Citation has no PMID URL and no PMC ID - only an author-year and a free-text title.
- Citation has a PMID but no author-year (LLMs sometimes invent the number first, then forget to attach the author).
- Citation's reported RR/OR is exactly the value the LLM wanted to encode (a real meta-analysis usually reports a 95% CI whose midpoint is *not* the round number you'd otherwise guess; e.g., `RR=1.5` is more likely fabricated than `RR=1.43 (95%CI 1.20-1.70)`).
- Comment column says "estimated from threshold data", "derived from", "approximation", "consensus", or any other word that means "this didn't come from a single paper". Either find a single paper that reports the value, or drop the row.

**LLM behavioral rule.** When the user requests adding a new relationship and the LLM cannot find a real meta in 2-3 WebFetch attempts: say so plainly. Do not paper over the gap with a PMID from memory. The cost of one fake citation is days of cleanup and a credibility hit; the cost of saying "no real meta found, suggest dropping this row" is zero.
