"""
Subset constraints for Bayesian network CPT construction.

These constraints are mathematically necessary (not modeling assumptions):
for each subset of parent variables and value assignment,
the sub-marginal P(disease | Vi=vi, Vj=vj) is bounded by the individual
marginals and the conditional frequencies from the baked network.

The bounds follow from:
    P(disease|Vi=vi) = P(disease|Vi=vi,Vj=vj)*P(Vj=vj|Vi=vi)
                     + P(disease|Vi=vi,Vj!=vj)*P(Vj!=vj|Vi=vi)
    0 <= P(disease|Vi=vi,Vj!=vj) <= 1

Which gives:
    lb = max(0, (target - (1 - p_cond)) / p_cond)
    ub = min(1, target / p_cond)

where target = P(disease|Vi=vi) and p_cond = P(Vj=vj|Vi=vi).
"""

import numpy as np
from qpsolvers import solve_qp

# Minimum conditional frequency to avoid division instability
_MIN_COND_FREQ = 1e-6


def compute_marginal_and_conditional_freqs(frequencies, keylist, pos, vdict):
    """
    Compute marginal and pairwise conditional frequencies from the joint.

    Returns:
        marginal_freq: dict {(ki, vi): float}
        pair_freq: dict {(ki, vi, kj, vj): float}
        cond_freq: dict {(ki, vi, kj, vj): float}
            P(kj=vj | ki=vi)
        subset_joint: dict {subset_key: float}
            P(all values in subset_key), for subset sizes >= 3.
            subset_key is tuple of (parent_name, value) pairs.
    """
    from itertools import combinations, product as iprod

    marginal_freq = {}
    pair_freq = {}
    cond_freq = {}
    subset_joint = {}

    for ki_idx, ki in enumerate(keylist):
        for vi in vdict[ki]:
            total = 0.0
            for combo, freq in frequencies.items():
                if combo[ki_idx] == vi:
                    total += freq
            marginal_freq[(ki, vi)] = total

    for ki_idx, ki in enumerate(keylist):
        for kj_idx, kj in enumerate(keylist):
            if ki_idx >= kj_idx:
                continue
            for vi in vdict[ki]:
                for vj in vdict[kj]:
                    total = 0.0
                    for combo, freq in frequencies.items():
                        if combo[ki_idx] == vi and combo[kj_idx] == vj:
                            total += freq
                    pair_freq[(ki, vi, kj, vj)] = total
                    pair_freq[(kj, vj, ki, vi)] = total

    for (ki, vi, kj, vj), pf in pair_freq.items():
        mf = marginal_freq.get((ki, vi), 0.0)
        if mf > _MIN_COND_FREQ:
            cond_freq[(ki, vi, kj, vj)] = pf / mf
        else:
            cond_freq[(ki, vi, kj, vj)] = 0.0

    # Joint probability of arbitrary subset value assignments (size >= 3).
    # Size-2 is already in pair_freq; size-3+ is needed to replace the
    # independence-approximation P(Vj,Vk|Vi) ≈ P(Vj|Vi)·P(Vk|Vi) that the
    # earlier implementation used inside build_subset_inequalities_for_window.
    K = len(keylist)
    max_subset = min(K + 1, 7)
    for subset_size in range(3, max_subset):
        for subset_indices in combinations(range(K), subset_size):
            subset_names = [keylist[idx] for idx in subset_indices]
            for value_combo in iprod(*[vdict[n] for n in subset_names]):
                subset_key = tuple((subset_names[j], value_combo[j])
                                   for j in range(subset_size))
                total = 0.0
                for combo, freq in frequencies.items():
                    match = True
                    for pname, pval in subset_key:
                        if combo[pos[pname]] != pval:
                            match = False
                            break
                    if match:
                        total += freq
                subset_joint[subset_key] = total

    return marginal_freq, pair_freq, cond_freq, subset_joint


def build_subset_lhs_rows(cartesian, frequencies, keylist, pos, vdict):
    """
    Build normalized LHS coefficient vectors for ALL subset sub-marginals
    (pairs, triplets, quadruplets, etc.).

    For each subset of parents and each value assignment to that subset,
    builds a coefficient vector where entries are freq(combo)/subset_total
    for combos matching all subset values, zero otherwise.

    Returns:
        lhs_a: dict {subset_key: np.array of length 2*len(cartesian)}
        lhs_not_a: dict {same}

    subset_key is a tuple of (parent_name, value) pairs, e.g.:
        ((ki, vi), (kj, vj)) for pairs
        ((ki, vi), (kj, vj), (kk, vk)) for triplets
    """
    from itertools import combinations, product as iprod

    n = len(cartesian)
    size = 2 * n
    lhs_a = {}
    lhs_not_a = {}

    K = len(keylist)
    max_subset = min(K + 1, 7)  # subsets of size 2 through K, capped at 6

    # Generate all subsets of size 2 to K
    for subset_size in range(2, max_subset):
        for subset_indices in combinations(range(K), subset_size):
            subset_keys = [keylist[idx] for idx in subset_indices]
            subset_values = [vdict[k] for k in subset_keys]

            # For each value assignment to this subset
            for value_combo in iprod(*subset_values):
                row_a = np.zeros(size, np.double)
                row_not_a = np.zeros(size, np.double)

                for i, c in enumerate(cartesian):
                    match = all(c[pos[subset_keys[j]]] == value_combo[j]
                               for j in range(subset_size))
                    if match:
                        row_a[i] = np.double(frequencies[c])
                        row_not_a[i + n] = np.double(frequencies[c])

                norm_a = np.sum(row_a)
                if norm_a > 0:
                    row_a /= norm_a
                norm_not_a = np.sum(row_not_a)
                if norm_not_a > 0:
                    row_not_a /= norm_not_a

                # Key: tuple of (parent, value) pairs
                subset_key = tuple((subset_keys[j], value_combo[j])
                                   for j in range(subset_size))
                lhs_a[subset_key] = row_a
                lhs_not_a[subset_key] = row_not_a

    return lhs_a, lhs_not_a


def _derive_bound(target, p_cond):
    """Derive [lb, ub] for a sub-marginal from one parent's marginal."""
    if p_cond < _MIN_COND_FREQ:
        return 0.0, 1.0
    ub = min(1.0, target / p_cond)
    lb = max(0.0, (target - 1.0 + p_cond) / p_cond)
    return lb, ub


def _subset_p_cond(parent_name, parent_value, subset_key,
                   cond_freq, marginal_freq, subset_joint):
    """Return P(subset-minus-this | this_parent=this_value).

    Size-2 subset: use pairwise cond_freq (exact by definition).
    Size-3+ subset: use joint probability / marginal directly, NOT the
        independence product ∏ P(V_j|V_i) which assumed conditional
        independence of the other subset parents given this parent.
    """
    if len(subset_key) == 2:
        # P(other | this): single other parent, direct cond_freq lookup.
        for other_name, other_value in subset_key:
            if other_name == parent_name:
                continue
            cf_key = (parent_name, parent_value, other_name, other_value)
            return cond_freq.get(cf_key, 0.0)
        return 0.0

    # Size-3+: P(all subset values) / P(this_parent=this_value).
    joint = subset_joint.get(subset_key, 0.0)
    marg = marginal_freq.get((parent_name, parent_value), 0.0)
    if marg < _MIN_COND_FREQ:
        return 0.0
    return joint / marg


def build_subset_inequalities_for_window(
    lhs_a, lhs_not_a,
    val_prev, not_val_prev,
    cond_freq, keylist, vdict,
    ci_windows_a, ci_windows_not_a,
    marginal_freq=None, subset_joint=None,
):
    """
    Build subset inequality constraint rows for a specific window level.

    Works with all subsets (pairs, triplets, etc.) from build_subset_lhs_rows.
    For each subset, derives bounds from ALL individual parent marginals,
    then intersects them.

    Size-3+ subsets use the exact joint conditional P(others | this)
    = P(all subset values) / P(this), requiring marginal_freq and
    subset_joint (both returned by compute_marginal_and_conditional_freqs).
    If either is None, falls back to the independence product (legacy path,
    only approximate; kept for backward compatibility if callers weren't
    updated).

    Returns:
        lhs_ineq_rows: list of np.arrays
        rhs_ineq_vals: list of floats
    """
    lhs_rows = []
    rhs_vals = []

    exact_p_cond_available = (marginal_freq is not None and subset_joint is not None)

    for subset_key, row_a in lhs_a.items():
        # Skip zero-frequency subsets
        if np.sum(np.abs(row_a)) == 0:
            continue

        # subset_key is tuple of (parent_name, value) pairs
        # Derive bounds from each parent's marginal in the subset
        lb_all = 0.0
        ub_all = 1.0

        for parent_name, parent_value in subset_key:
            if parent_name not in val_prev or parent_value not in val_prev[parent_name]:
                continue

            ci = ci_windows_a.get(parent_name, {}).get(parent_value, 0.0)
            target_max = min(1.0, val_prev[parent_name][parent_value] + ci)
            target_min = max(0.0, val_prev[parent_name][parent_value] - ci)

            if exact_p_cond_available:
                p_cond = _subset_p_cond(parent_name, parent_value, subset_key,
                                        cond_freq, marginal_freq, subset_joint)
            else:
                # Legacy fallback (independence approximation for size-3+)
                p_cond = 1.0
                for other_name, other_value in subset_key:
                    if other_name == parent_name:
                        continue
                    cf_key = (parent_name, parent_value, other_name, other_value)
                    p_cond *= cond_freq.get(cf_key, 0.5)

            if p_cond < _MIN_COND_FREQ:
                continue

            _, ub_from_this_max = _derive_bound(target_max, p_cond)
            lb_from_this_min, _ = _derive_bound(target_min, p_cond)

            lb_all = max(lb_all, lb_from_this_min)
            ub_all = min(ub_all, ub_from_this_max)

        if lb_all > ub_all:
            ub_all = lb_all

        if lb_all > 0.001 or ub_all < 0.999:
            lhs_rows.append(row_a.copy())
            rhs_vals.append(ub_all)
            lhs_rows.append(-row_a.copy())
            rhs_vals.append(-lb_all)

        # --- P(~disease) bounds ---
        if subset_key not in lhs_not_a:
            continue
        row_not_a = lhs_not_a[subset_key]
        if np.sum(np.abs(row_not_a)) == 0:
            continue

        lb_not_all = 0.0
        ub_not_all = 1.0

        for parent_name, parent_value in subset_key:
            if parent_name not in not_val_prev or parent_value not in not_val_prev[parent_name]:
                continue

            ci = ci_windows_not_a.get(parent_name, {}).get(parent_value, 0.0)
            target_max = min(1.0, not_val_prev[parent_name][parent_value] + ci)
            target_min = max(0.0, not_val_prev[parent_name][parent_value] - ci)

            if exact_p_cond_available:
                p_cond = _subset_p_cond(parent_name, parent_value, subset_key,
                                        cond_freq, marginal_freq, subset_joint)
            else:
                p_cond = 1.0
                for other_name, other_value in subset_key:
                    if other_name == parent_name:
                        continue
                    cf_key = (parent_name, parent_value, other_name, other_value)
                    p_cond *= cond_freq.get(cf_key, 0.5)

            if p_cond < _MIN_COND_FREQ:
                continue

            lb_from_this, _ = _derive_bound(target_min, p_cond)
            _, ub_from_this = _derive_bound(target_max, p_cond)

            lb_not_all = max(lb_not_all, lb_from_this)
            ub_not_all = min(ub_not_all, ub_from_this)

        if lb_not_all > ub_not_all:
            ub_not_all = lb_not_all

        if lb_not_all > 0.001 or ub_not_all < 0.999:
            lhs_rows.append(row_not_a.copy())
            rhs_vals.append(ub_not_all)
            lhs_rows.append(-row_not_a.copy())
            rhs_vals.append(-lb_not_all)

    return lhs_rows, rhs_vals


def compute_feasible_ranges(lhs_ineq, rhs_ineq, lhs_eq, rhs_eq, lb, ub, n_cells):
    """
    For each cell, compute the feasible range [min, max] by solving
    two LPs (minimize and maximize that cell).

    Uses scipy.optimize.linprog for the LP solves (OSQP doesn't handle
    zero-P QPs well). Falls back to QP with small regularization if
    linprog is unavailable.

    Args:
        lhs_ineq, rhs_ineq: inequality constraints (Gx <= h)
        lhs_eq, rhs_eq: equality constraints (Ax = b)
        lb, ub: variable bounds
        n_cells: number of cells in the "a" half (total vars = 2*n_cells)

    Returns:
        ranges: list of (min_val, max_val, range_width) for each cell
    """
    from scipy.optimize import linprog

    size = 2 * n_cells
    bounds = list(zip(lb, ub))
    ranges = []

    for i in range(n_cells):
        # Minimize x_i
        c_min = np.zeros(size, np.double)
        c_min[i] = 1.0
        try:
            res_min = linprog(c_min, A_ub=lhs_ineq, b_ub=rhs_ineq,
                              A_eq=lhs_eq, b_eq=rhs_eq, bounds=bounds,
                              method='highs')
            min_val = res_min.x[i] if res_min.success else 0.0
        except Exception:
            min_val = 0.0

        # Maximize x_i (minimize -x_i)
        c_max = np.zeros(size, np.double)
        c_max[i] = -1.0
        try:
            res_max = linprog(c_max, A_ub=lhs_ineq, b_ub=rhs_ineq,
                              A_eq=lhs_eq, b_eq=rhs_eq, bounds=bounds,
                              method='highs')
            max_val = res_max.x[i] if res_max.success else 1.0
        except Exception:
            max_val = 1.0

        width = max(0.0, max_val - min_val)
        ranges.append((min_val, max_val, width))

    return ranges
