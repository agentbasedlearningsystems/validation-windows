"""
dependency_direct_v2: CPT construction with subset constraints.

This is an extension of the original dependency_direct from utils.py.
The original function is unchanged - this version adds subset constraints
that tighten the feasible region without adding modeling assumptions.

Usage:
    from sn_bayes.dependency_v2 import dependency_direct_v2

    # Same signature as the original, with optional extras:
    result = dependency_direct_v2(
        bayesianNetwork, cpt, invars, outvars,
        use_subset=True,         # add subset constraints
        compute_believability=True, # compute per-cell feasible ranges
    )

    # Returns same tuple as original: (cpt_rows, keylist, outvars, description)
    # Plus optional believability dict in result[4] if compute_believability=True
"""

import itertools
import warnings
import time

import numpy as np
import pandas as pd
from qpsolvers import solve_qp

from sn_bayes.utils import (
    OrderedSet,
    get_priors,
    get_frequencies,
    get_window,
    get_var_val_positions,
    dictVarsAndValues,
    prob_a_and_not_a_given_b_and_not_b,
    validation,
)
from sn_bayes.subset_constraints import (
    compute_marginal_and_conditional_freqs,
    build_subset_lhs_rows,
    build_subset_inequalities_for_window,
    compute_feasible_ranges,
)
from sn_bayes.objectives import (
    unweighted_least_squares,
    data_fit_and_monotonicity,
    get_monotonicity_pairs,
)


def dependency_direct_v2(bayesianNetwork, cpt, invars, outvars,
                         priors=None, adjust=False,
                         use_subset=True,
                         compute_believability=False,
                         w=None,
                         w_ls=None,
                         w_df=None,
                         w_mono=0.0,
                         mono_margin=0.0001,  # Apr 23: was 0.01 (too lenient at low base rate; full inversion at P(disease)~1e-4 went unpunished, see issue #13)
                         input_ci_scale=1.0,  # Apr 24: uniform multiplier on input CI half-widths before binary search. 1.0 = bands as reported (per-edge marginal-95% coverage); 1.85 = Bonferroni-widened for joint-95% over n=166 studies.
                         nhanes_data=None,
                         linear_config=None,
                         output_node=None):
    """
    Build a CPT using QP optimization with optional subset constraints.

    Args:
        bayesianNetwork: protobuf network spec
        cpt: conditional probability table spec
        invars: list of (vardict, numval_dict) from studies
        outvars: output variable dict
        priors: prior probabilities (optional, computed if None)
        adjust: whether to adjust probabilities
        use_subset: if True, add subset constraints from probability theory
        compute_believability: if True, compute per-cell feasible ranges
        w_ls: Least-squares objective weight. 0 = off, 1 = full. Independent.
        w_df: Data-fit (NHANES joint targets) weight. 0 = off, 1 = full.
            Independent. Requires nhanes_data when > 0.
        w: DEPRECATED legacy alias. If provided, maps to w_ls=w, w_df=(1-w).
            Cannot be combined with w_ls / w_df.
        w_mono: Monotonicity penalty weight. 0 = off, 1 = full.
            Penalizes cells where risk value gives LOWER disease than safe value.

        At least one of w_ls, w_df, w_mono must be > 0 (else no objective).
        nhanes_data: wide-format NHANES DataFrame (needed when w < 1.0)
        linear_config: linearized config dict (needed for mono and data-fit)
        output_node: name of the disease node being built

    Returns:
        If compute_believability is False:
            (cpt_rows, keylist, outvars, description)  - same as original
        If compute_believability is True:
            (cpt_rows, keylist, outvars, description, believability)
            where believability is a list of (combo, min, max, width) tuples
    """

    # =========================================================================
    # RESOLVE OBJECTIVE WEIGHTS (w_ls / w_df independent; w is legacy alias)
    # =========================================================================
    if w is not None and (w_ls is not None or w_df is not None):
        raise ValueError("dependency_direct_v2: use either `w` (legacy) OR "
                         "`w_ls`+`w_df`, not both.")
    if w is not None:
        # Legacy path: w controls LS vs DF balance
        w_ls = float(w)
        w_df = 1.0 - float(w)
    else:
        # New path: independent weights. Default to pure LS if neither given.
        if w_ls is None and w_df is None:
            w_ls, w_df = 1.0, 0.0
        elif w_ls is None:
            w_ls = 0.0
        elif w_df is None:
            w_df = 0.0
    if w_ls == 0.0 and w_df == 0.0 and (w_mono is None or w_mono == 0.0):
        raise ValueError("dependency_direct_v2: all objective weights (w_ls, "
                         "w_df, w_mono) are zero - no objective function to "
                         "minimize. At least one must be > 0.")

    # =========================================================================
    # SETUP - identical to original dependency_direct
    # =========================================================================

    validation_row = {}
    tic = time.perf_counter()

    keyset = OrderedSet([])
    frequency_cache = {}
    prevalence_condition_regardless = list(outvars.items())[0][1]
    condition_val = list(outvars.items())[0][0]
    if priors is None:
        priors = get_priors(bayesianNetwork, invars, prevalence_condition_regardless, cpt, adjust)

    prob_a_given_b, prob_a_given_not_b, prob_not_a_given_b, prob_not_a_given_not_b = \
        prob_a_and_not_a_given_b_and_not_b(invars, priors, outvars)

    # Build description string
    description = "Against the baseline risks, "
    firsttime = True
    for k, v in outvars.items():
        phrase = "" if firsttime else " and "
        description + phrase + k + " of " + str(v) + " , "
        firsttime = False
    firsttime = True
    for vardict, numval_dict in invars:
        for val, varlist in vardict.items():
            for test, num in numval_dict.items():
                keyset.add(val)
                phrase = "" if firsttime else (" , and " if varlist[-1] is val else " , ")
                description = description + phrase + "the " + test.replace("_", " ") + \
                    " that {0} will be " + condition_val + " for those in the " + \
                    val + " category of "
                firsttime1 = True
                sum_priors = 0
                for var in varlist:
                    phrase = "" if firsttime1 else " or "
                    description = description + phrase + var
                    firsttime1 = False
                    sum_priors += priors[val][var]
                description = description + " is " + str(num)
                firsttime = False
    description = description + "."

    keylist = list(keyset)
    pos = {k: n for n, k in enumerate(keylist)}
    vdict = dictVarsAndValues(bayesianNetwork, cpt)

    # Build val_prev and not_val_prev
    val_prev = {}
    not_val_prev = {}
    for k in keylist:
        val_prev[k] = {}
        not_val_prev[k] = {}
        previous = None
        previous_not = None
        for v in vdict[k]:
            if k in prob_a_given_b and v in prob_a_given_b[k]:
                val_prev[k][v] = prob_a_given_b[k][v]
                previous = prob_a_given_not_b[k][v]
            elif previous is not None:
                val_prev[k][v] = previous
            else:
                val_prev[k][v] = prevalence_condition_regardless
            if k in prob_not_a_given_b and v in prob_not_a_given_b[k]:
                not_val_prev[k][v] = prob_not_a_given_b[k][v]
                previous_not = prob_not_a_given_not_b[k][v]
            elif previous_not is not None:
                not_val_prev[k][v] = previous_not
            else:
                not_val_prev[k][v] = prevalence_condition_regardless

    vlist = [vdict[v] for v in keylist]
    cartesian = list(itertools.product(*vlist))

    # =========================================================================
    # BUILD INDIVIDUAL CONSTRAINTS - identical to original
    # =========================================================================

    cpt_rows = []
    lhs_inequality_equation1 = {}
    lhs_inequality_equation2 = {}
    lhs_inequality_equation3 = {}
    lhs_inequality_equation4 = {}
    rhs_inequality_equation1 = {}
    rhs_inequality_equation2 = {}
    rhs_inequality_equation3 = {}
    rhs_inequality_equation4 = {}
    obj = np.ones(2 * len(cartesian))

    frequencies = get_frequencies(bayesianNetwork, keylist, cpt, adjust)
    bnd = []

    lhs_eq = []
    rhs_eq = []

    included_vars = {}
    for excluded in keylist:
        included_vars[excluded] = keylist[:pos[excluded]] + keylist[pos[excluded] + 1:]

    for i, c in enumerate(cartesian):
        rhs_eq.append(1.)
        lhs_eq_list = np.zeros(2 * len(cartesian))
        lhs_eq_list[i] = 1.
        lhs_eq_list[i + len(cartesian)] = 1.
        lhs_eq.append(lhs_eq_list)

        for vardict, numval_dict in invars:
            for k, varlist in vardict.items():
                if k not in lhs_inequality_equation1:
                    lhs_inequality_equation1[k] = {}
                if k not in lhs_inequality_equation2:
                    lhs_inequality_equation2[k] = {}
                if k not in lhs_inequality_equation3:
                    lhs_inequality_equation3[k] = {}
                if k not in lhs_inequality_equation4:
                    lhs_inequality_equation4[k] = {}
                if k not in rhs_inequality_equation1:
                    rhs_inequality_equation1[k] = {}
                if k not in rhs_inequality_equation2:
                    rhs_inequality_equation2[k] = {}
                if k not in rhs_inequality_equation3:
                    rhs_inequality_equation3[k] = {}
                if k not in rhs_inequality_equation4:
                    rhs_inequality_equation4[k] = {}

                for v in varlist:
                    if v not in lhs_inequality_equation1[k]:
                        lhs_inequality_equation1[k][v] = np.zeros(2 * len(cartesian), np.double)
                    if v not in lhs_inequality_equation2[k]:
                        lhs_inequality_equation2[k][v] = np.zeros(2 * len(cartesian), np.double)
                    if v not in lhs_inequality_equation3[k]:
                        lhs_inequality_equation3[k][v] = np.zeros(2 * len(cartesian), np.double)
                    if v not in lhs_inequality_equation4[k]:
                        lhs_inequality_equation4[k][v] = np.zeros(2 * len(cartesian), np.double)
                    if v not in rhs_inequality_equation1[k]:
                        rhs_inequality_equation1[k][v] = 0
                    if v not in rhs_inequality_equation2[k]:
                        rhs_inequality_equation2[k][v] = 0
                    if v not in rhs_inequality_equation3[k]:
                        rhs_inequality_equation3[k][v] = 0
                    if v not in rhs_inequality_equation4[k]:
                        rhs_inequality_equation4[k][v] = 0

                    if c[pos[k]] == v:
                        rhs_inequality_equation2[k][v] = prob_a_given_b[k][v]
                        lhs_inequality_equation2[k][v][i] = np.double(frequencies[c])
                        rhs_inequality_equation4[k][v] = prob_not_a_given_b[k][v]
                        lhs_inequality_equation4[k][v][i + len(cartesian)] = np.double(frequencies[c])
                    else:
                        rhs_inequality_equation1[k][v] = prob_a_given_not_b[k][v]
                        lhs_inequality_equation1[k][v][i] = np.double(frequencies[c])
                        rhs_inequality_equation3[k][v] = prob_not_a_given_not_b[k][v]
                        lhs_inequality_equation3[k][v][i + len(cartesian)] = np.double(frequencies[c])

        minimum = 1.0
        for j, k in enumerate(keylist):
            if val_prev[k][c[j]] < minimum:
                minimum = val_prev[k][c[j]]
        bnd.append((0, 1.0))

    for i, c in enumerate(cartesian):
        minimum_not = 1.0
        for j, k in enumerate(keylist):
            if not_val_prev[k][c[j]] < minimum_not:
                minimum_not = not_val_prev[k][c[j]]
        bnd.append((0, 1.0))

    size = 2 * len(cartesian)

    q = np.zeros(size, np.double)
    lb = np.zeros(size, np.double)
    ub = np.ones(size, np.double)

    # Normalize individual constraints
    for vardict, numval_dict in invars:
        for k, varlist in vardict.items():
            for v in varlist:
                norm = np.sum(lhs_inequality_equation1[k][v])
                if norm != 0:
                    lhs_inequality_equation1[k][v] /= norm
                norm = np.sum(lhs_inequality_equation2[k][v])
                if norm != 0:
                    lhs_inequality_equation2[k][v] /= norm
                norm = np.sum(lhs_inequality_equation3[k][v])
                if norm != 0:
                    lhs_inequality_equation3[k][v] /= norm
                norm = np.sum(lhs_inequality_equation4[k][v])
                if norm != 0:
                    lhs_inequality_equation4[k][v] /= norm

    # Prevalence equality constraints
    rhs_eq.append(prevalence_condition_regardless)
    rhs_eq.append(1. - prevalence_condition_regardless)
    prev_a = np.zeros(2 * len(cartesian), np.double)
    prev_not_a = np.zeros(2 * len(cartesian), np.double)
    for i, c in enumerate(cartesian):
        prev_a[i] = np.double(frequencies[c])
        prev_not_a[i + len(cartesian)] = np.double(frequencies[c])
    lhs_eq.append(prev_a)
    lhs_eq.append(prev_not_a)

    # =========================================================================
    # === PAIRWISE EXTENSION: precompute LHS rows and conditional freqs ===
    # === (bounds are computed inside the binary search with ci_windows) ===
    # =========================================================================

    subset_lhs_a = None
    subset_lhs_not_a = None
    subset_cond_freq = None
    subset_marginal_freq = None
    subset_joint = None

    if use_subset and len(keylist) >= 2:
        subset_marginal_freq, _pair_freq, subset_cond_freq, subset_joint = \
            compute_marginal_and_conditional_freqs(frequencies, keylist, pos, vdict)

        subset_lhs_a, subset_lhs_not_a = \
            build_subset_lhs_rows(cartesian, frequencies, keylist, pos, vdict)

    # =========================================================================
    # === PRECOMPUTATION ===
    # =========================================================================

    _obj_P = None
    _obj_q = None
    if output_node is None:
        output_node = list(cpt.keys())[0] if cpt else None

    # Data-fit component (when w_df > 0 and NHANES data available).
    # Legacy w < 1.0 check is equivalent since resolve-block earlier set
    # w_df = 1 - w when only `w` was provided. Using w_df is safer because
    # w stays None when the caller passes w_ls/w_df directly.
    if w_df > 0.0 and nhanes_data is not None and linear_config and output_node:
        dep_data = linear_config.get('dependency_data', linear_config)
        node_data_cfg = dep_data.get(output_node, {})
        invars_config_df = node_data_cfg.get('INVARS', {})
        reverse_output_df = bool(node_data_cfg.get('REVERSE'))
        node_reverse_df = {}
        for parent_name in keylist:
            parent_data = dep_data.get(parent_name, {})
            parent_rev = parent_data.get('REVERSE')
            if parent_rev is not None:
                node_reverse_df[parent_name] = parent_rev

        _obj_P, _obj_q = data_fit_and_monotonicity(
            cartesian, keylist, vdict, frequencies, val_prev, size, w=1.0,
            nhanes_data=nhanes_data, linear_config=linear_config,
            output_node=output_node,
            invars_config=invars_config_df, reverse_output=reverse_output_df,
            node_reverse=node_reverse_df,
            bayesianNetwork=bayesianNetwork, cpt=cpt,
        )

    # Monotonicity pairs (slack variable approach)
    # w_mono controls strength: 0 = off, 1 = full
    # mono_margin is the tolerance below which an inversion is considered
    # within numerical noise and not penalized. Default 0.01 (1% of unit
    # probability). Configurable via the function signature - callers that
    # want stricter monotonicity can pass 0.005, looser can pass 0.05.
    # See paper §... for sensitivity analysis.
    mono_pairs = []
    mono_lam = w_mono  # controlled by w_mono parameter
    reverse_output = False
    node_reverse = {}

    if w_mono > 0 and linear_config and output_node:
        dep_data = linear_config.get('dependency_data', linear_config)
        node_data = dep_data.get(output_node, {})
        invars_config = node_data.get('INVARS', {})
        reverse_output = bool(node_data.get('REVERSE'))
        for parent_name in keylist:
            parent_data = dep_data.get(parent_name, {})
            parent_rev = parent_data.get('REVERSE')
            if parent_rev is not None:
                node_reverse[parent_name] = parent_rev

        mono_pairs = get_monotonicity_pairs(
            cartesian, keylist, vdict, invars_config,
            reverse_output=reverse_output, node_reverse=node_reverse,
            linear_config=linear_config,
            bayesianNetwork=bayesianNetwork, cpt=cpt,
        )

    # =========================================================================
    # BINARY SEARCH - same structure as original, with subset added
    # =========================================================================

    window = 1.0
    cut = 1.0
    lastTrue = None

    while cut > 0.0005:
        lhs_ineq = []
        rhs_ineq = []
        ls_R = []
        ls_s = []

        # --- Individual constraints (identical to original) ---
        for vardict, numval_dict in invars:
            for k, varlist in vardict.items():
                if k not in validation_row:
                    validation_row[k] = {}
                for v in varlist:
                    window_factor = get_window(bayesianNetwork, invars, outvars,
                                               prob_a_given_not_b, k, v)
                    # input_ci_scale (default 1.0) widens every input CI uniformly before
                    # the binary search. Set to 1.85 for Bonferroni-corrected joint-95%
                    # coverage on the n=166-study network.
                    if input_ci_scale != 1.0 and k in window_factor and v in window_factor[k]:
                        window_factor[k][v] = window_factor[k][v] * input_ci_scale
                    ci_window = window * window_factor[k][v] \
                        if k in window_factor and v in window_factor[k] else window

                    # UB
                    rhs_ineq1 = 0. if rhs_inequality_equation1[k][v] + ci_window < 0 else (
                        1. if rhs_inequality_equation1[k][v] + ci_window > 1
                        else rhs_inequality_equation1[k][v] + ci_window)
                    rhs_ineq2 = 0. if rhs_inequality_equation2[k][v] + ci_window < 0 else (
                        1. if rhs_inequality_equation2[k][v] + ci_window > 1
                        else rhs_inequality_equation2[k][v] + ci_window)
                    rhs_ineq3 = 0. if rhs_inequality_equation3[k][v] + ci_window < 0 else (
                        1. if rhs_inequality_equation3[k][v] + ci_window > 1
                        else rhs_inequality_equation3[k][v] + ci_window)
                    rhs_ineq4 = 0. if rhs_inequality_equation4[k][v] + ci_window < 0 else (
                        1. if rhs_inequality_equation4[k][v] + ci_window > 1
                        else rhs_inequality_equation4[k][v] + ci_window)

                    lhs_ineq.append(np.multiply(lhs_inequality_equation1[k][v], 1.))
                    rhs_ineq.append(rhs_ineq1)
                    lhs_ineq.append(np.multiply(lhs_inequality_equation2[k][v], 1.))
                    rhs_ineq.append(rhs_ineq2)
                    lhs_ineq.append(np.multiply(lhs_inequality_equation3[k][v], 1.))
                    rhs_ineq.append(rhs_ineq3)
                    lhs_ineq.append(np.multiply(lhs_inequality_equation4[k][v], 1.))
                    rhs_ineq.append(rhs_ineq4)

                    # LB
                    rhs_ineq1 = 0. if rhs_inequality_equation1[k][v] - ci_window < 0 else (
                        1. if rhs_inequality_equation1[k][v] - ci_window > 1
                        else rhs_inequality_equation1[k][v] - ci_window)
                    rhs_ineq2 = 0. if rhs_inequality_equation2[k][v] - ci_window < 0 else (
                        1. if rhs_inequality_equation2[k][v] - ci_window > 1
                        else rhs_inequality_equation2[k][v] - ci_window)
                    rhs_ineq3 = 0. if rhs_inequality_equation3[k][v] - ci_window < 0 else (
                        1. if rhs_inequality_equation3[k][v] - ci_window > 1
                        else rhs_inequality_equation3[k][v] - ci_window)
                    rhs_ineq4 = 0. if rhs_inequality_equation4[k][v] - ci_window < 0 else (
                        1. if rhs_inequality_equation4[k][v] - ci_window > 1
                        else rhs_inequality_equation4[k][v] - ci_window)

                    lhs_ineq.append(np.multiply(lhs_inequality_equation1[k][v], -1.))
                    rhs_ineq.append(-rhs_ineq1)
                    lhs_ineq.append(np.multiply(lhs_inequality_equation2[k][v], -1.))
                    rhs_ineq.append(-rhs_ineq2)
                    lhs_ineq.append(np.multiply(lhs_inequality_equation3[k][v], -1.))
                    rhs_ineq.append(-rhs_ineq3)
                    lhs_ineq.append(np.multiply(lhs_inequality_equation4[k][v], -1.))
                    rhs_ineq.append(-rhs_ineq4)

                    # Objective function terms
                    ls_R.append(np.multiply(lhs_inequality_equation1[k][v], 1.))
                    ls_s.append(rhs_inequality_equation1[k][v])
                    ls_R.append(np.multiply(lhs_inequality_equation2[k][v], 1.))
                    ls_s.append(rhs_inequality_equation2[k][v])
                    ls_R.append(np.multiply(lhs_inequality_equation3[k][v], 1.))
                    ls_s.append(rhs_inequality_equation3[k][v])
                    ls_R.append(np.multiply(lhs_inequality_equation4[k][v], 1.))
                    ls_s.append(rhs_inequality_equation4[k][v])

                    row = validation(k, v, rhs_inequality_equation2[k][v],
                                     condition_val, invars, outvars,
                                     window, window_factor, prob_a_given_not_b)
                    if len(row) > 0:
                        validation_row[k][v] = row

        # =================================================================
        # === SUBSET EXTENSION: add subset constraints to this iteration ===
        # === Bounds are derived from windowed individual targets, so they  ===
        # === are recomputed each iteration with current ci_window values.  ===
        # =================================================================

        if use_subset and subset_lhs_a is not None:
            # Collect per-variable ci_windows for this binary search step.
            # These match the individual constraints above: ci_window = window * window_factor[k][v]
            # For the "a" side, the target is val_prev (≈ rhs_inequality_equation2).
            # For the "~a" side, the target is not_val_prev (≈ rhs_inequality_equation4).
            ci_windows_a = {}
            ci_windows_not_a = {}
            for vardict_pw, numval_dict_pw in invars:
                for k_pw, varlist_pw in vardict_pw.items():
                    if k_pw not in ci_windows_a:
                        ci_windows_a[k_pw] = {}
                        ci_windows_not_a[k_pw] = {}
                    for v_pw in varlist_pw:
                        wf = get_window(bayesianNetwork, invars, outvars,
                                        prob_a_given_not_b, k_pw, v_pw)
                        ci_w = window * wf[k_pw][v_pw] if k_pw in wf and v_pw in wf[k_pw] else window
                        ci_windows_a[k_pw][v_pw] = ci_w
                        ci_windows_not_a[k_pw][v_pw] = ci_w
                    # Fill in values not in varlist (same as max factor, matching original)
                    var_val_pos = get_var_val_positions(bayesianNetwork)
                    wf = get_window(bayesianNetwork, invars, outvars,
                                    prob_a_given_not_b, k_pw, varlist_pw[0])
                    for val_pw in vdict[k_pw]:
                        if val_pw not in ci_windows_a[k_pw]:
                            ci_windows_a[k_pw][val_pw] = window * wf[k_pw].get(val_pw, 1.0) \
                                if k_pw in wf else window
                            ci_windows_not_a[k_pw][val_pw] = ci_windows_a[k_pw][val_pw]

            pw_lhs, pw_rhs = build_subset_inequalities_for_window(
                subset_lhs_a, subset_lhs_not_a,
                val_prev, not_val_prev,
                subset_cond_freq, keylist, vdict,
                ci_windows_a, ci_windows_not_a,
                marginal_freq=subset_marginal_freq,
                subset_joint=subset_joint,
            )
            lhs_ineq.extend(pw_lhs)
            rhs_ineq.extend(pw_rhs)

        # =================================================================
        # END PAIRWISE EXTENSION
        # =================================================================

        # Convert to arrays and solve
        lhs_ineq = np.array(lhs_ineq, np.double)
        rhs_ineq = np.array(rhs_ineq, np.double)
        lhs_eq = np.array(lhs_eq, np.double)
        rhs_eq = np.array(rhs_eq, np.double)
        ls_s = np.array(ls_s, np.double)
        ls_R = np.array(ls_R, np.double)

        # === OBJECTIVE FUNCTION ===
        # Independent term weights:
        #   w_ls  × LS  +  w_df × DF  +  w_mono × Mono
        # LS = match marginal study RR targets.
        # DF = match NHANES joint conditional targets (requires _obj_P).
        # Mono is applied below via slack variables (w_mono).
        # Small eye-regularization keeps P positive-definite.

        P_ls, q_ls = unweighted_least_squares(ls_R, ls_s, size)
        P = w_ls * P_ls + np.eye(size, dtype=np.double) * 1e-6
        q_obj = w_ls * q_ls

        if _obj_P is not None and w_df > 0.0:
            P = P + w_df * _obj_P
            q_obj = q_obj + w_df * _obj_q

        # =================================================================
        # MONOTONICITY via slack variables
        # For each pair (risk_idx, safe_idx, n_vals):
        #   Add slack s_k >= 0
        #   Constraint: x[risk] - x[safe] + margin + s_k >= 0
        #   Objective:  (lam/(n_vals-1)) * s_k^2
        # Only penalizes when x[risk] < x[safe] - margin (violation).
        # =================================================================
        n_slacks = len(mono_pairs)
        if n_slacks > 0:
            n_cells = len(cartesian)
            total_size = size + n_slacks

            # Expand P: add slack penalty on diagonal
            P_exp = np.zeros((total_size, total_size), np.double)
            P_exp[:size, :size] = P
            for sk, (ri, si, nv) in enumerate(mono_pairs):
                w_slack = mono_lam / max(nv - 1, 1)
                P_exp[size + sk, size + sk] = w_slack

            # Expand q
            q_exp = np.zeros(total_size, np.double)
            q_exp[:size] = q_obj

            # Expand lb/ub: slacks >= 0, no upper bound
            lb_exp = np.zeros(total_size, np.double)
            lb_exp[:size] = lb
            ub_exp = np.ones(total_size, np.double)
            ub_exp[:size] = ub
            for sk in range(n_slacks):
                ub_exp[size + sk] = 1e6  # slack unbounded above

            # Expand equality constraints: slacks don't participate
            if lhs_eq.ndim == 2:
                eq_pad = np.zeros((lhs_eq.shape[0], n_slacks), np.double)
                lhs_eq_exp = np.hstack([lhs_eq, eq_pad])
            else:
                lhs_eq_exp = lhs_eq

            # Expand inequality constraints: slacks don't participate in existing ones
            if lhs_ineq.ndim == 2 and lhs_ineq.shape[0] > 0:
                ineq_pad = np.zeros((lhs_ineq.shape[0], n_slacks), np.double)
                lhs_ineq_exp = np.hstack([lhs_ineq, ineq_pad])
                rhs_ineq_exp = rhs_ineq.copy()
            else:
                lhs_ineq_exp = np.zeros((0, total_size), np.double)
                rhs_ineq_exp = np.array([], np.double)

            # Add monotonicity inequality constraints:
            # x[risk] - x[safe] + margin + s_k >= 0
            # Rewritten as: -x[risk] + x[safe] - s_k <= margin
            mono_ineq_rows = []
            mono_ineq_rhs = []
            for sk, (ri, si, nv) in enumerate(mono_pairs):
                # Disease side: x[ri] - x[si] + margin + s_k >= 0
                # => -x[ri] + x[si] - s_k <= margin
                row = np.zeros(total_size, np.double)
                row[ri] = -1.0
                row[si] = 1.0
                row[size + sk] = -1.0
                mono_ineq_rows.append(row)
                mono_ineq_rhs.append(mono_margin)

            if mono_ineq_rows:
                mono_ineq = np.array(mono_ineq_rows, np.double)
                mono_rhs = np.array(mono_ineq_rhs, np.double)
                lhs_ineq_exp = np.vstack([lhs_ineq_exp, mono_ineq]) if lhs_ineq_exp.shape[0] > 0 else mono_ineq
                rhs_ineq_exp = np.concatenate([rhs_ineq_exp, mono_rhs])

            # Solve expanded QP
            err = False
            opt_full = None
            try:
                opt_full = solve_qp(P_exp, q_exp, lhs_ineq_exp, rhs_ineq_exp,
                                    lhs_eq_exp, rhs_eq, lb_exp, ub_exp,
                                    solver="osqp")
            except ValueError as ve:
                print("ValueError")
                print(ve)
                err = True

            # Extract original variables (discard slacks)
            opt = opt_full[:size] if opt_full is not None else None
        else:
            err = False
            opt = None
            try:
                opt = solve_qp(P, q_obj, lhs_ineq, rhs_ineq, lhs_eq, rhs_eq,
                               lb, ub, solver="osqp")
            except ValueError as ve:
                print("ValueError")
                print(ve)
                err = True

        if window == 1.0 and (err or opt is None):
            warning_msg = f"Infeasible at maximum window size for variable val'{condition_val}'"
            warnings.warn(warning_msg)

        if err or opt is None:
            break
        cut /= 2
        if not err and opt is not None:
            lastTrue = opt
        window = window - cut if not err else window + cut

    # =========================================================================
    # EXTRACT RESULTS - identical to original
    # =========================================================================

    if lastTrue is not None:
        opt = lastTrue
    if opt is not None:
        for i, c in enumerate(cartesian):
            cpt_row = []
            cpt_row.extend(c)
            cpt_row.append(list(outvars.items())[0][0])
            val = opt[i]
            cpt_row.append(val)
            cpt_rows.append(cpt_row)

            cpt_row = []
            cpt_row.extend(c)
            cpt_row.append(list(outvars.items())[1][0])
            val = 1.0 - opt[i]
            cpt_row.append(val)
            cpt_rows.append(cpt_row)

    toc = time.perf_counter()
    diff = toc - tic

    dflist = [valid_dict for k, v_dict in validation_row.items()
              for v, valid_dict in v_dict.items()]
    if len(dflist) > 0:
        df = pd.DataFrame(dflist)
        df.to_csv(f"./bayesnet_initialize_output/{condition_val}_validation.csv",
                  index=False)

    # =========================================================================
    # === PAIRWISE EXTENSION: compute believability if requested ===
    # =========================================================================

    believability = None
    if compute_believability and opt is not None:
        believability = compute_feasible_ranges(
            lhs_ineq, rhs_ineq, lhs_eq, rhs_eq,
            lb, ub, len(cartesian)
        )
        # Attach combo labels
        believability = [
            {
                'combo': cartesian[i],
                'solved_value': opt[i],
                'feasible_min': b[0],
                'feasible_max': b[1],
                'range_width': b[2],
                'believability': 1.0 - b[2],
            }
            for i, b in enumerate(believability)
        ]

    # =========================================================================

    if compute_believability:
        return (cpt_rows, keylist, outvars, description, believability)
    else:
        return (cpt_rows, keylist, outvars, description)
