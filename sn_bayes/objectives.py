"""
Objective functions for CPT construction.

Constraints define what MUST be true (the feasible region).
Objectives define how to CHOOSE among feasible solutions (a preference).

Each builder returns (P, q) for the QP: minimize (1/2) x^T P x + q^T x.

The main objective is `data_fit_and_smoothness`. It minimizes:

    w * sum(wi * (pi - pi')^2) + (1-w) * sum(1/dist(i,j) * (pi - pj)^2)

Weights wi come from NHANES joint frequencies, computed using
mask-building pattern from config_creation.distal. For parents with
NHANES codes, masks are ANDed per CPT slot to get actual joint counts.
create_independent_groups handles sparse data by splitting parents into
groups with sufficient joint data (>1000 respondents) vs groups treated
as independent. For parents without codes (composite nodes), falls back
to net-derived frequencies from get_frequencies (utils.py).
"""

import numpy as np
import pandas as pd

from sn_bayes.config_creation.distal import create_independent_groups, calculate_final_prior

NHANES_THRESHOLD = 1000


def unweighted_least_squares(ls_R, ls_s, size):
    """LS objective: minimize ||Rx - s||^2.

    R encodes marginal study constraints, s encodes targets.
    All feasible solutions satisfy Rx ≈ s, so this is near-degenerate
    when DOF > 0. The w parameter in dependency_v2 scales this relative
    to data-fit."""
    P = np.dot(ls_R.T, ls_R)
    q = -np.dot(ls_R.T, ls_s)
    return P, q


def compute_feature_distances(cartesian, keylist, vdict):
    """Compute normalized Euclidean distance between all pairs of CPT slots.

    Distance is based on ordinal position of each variable's value,
    normalized by the number of values for that variable.
    """
    vlist = [vdict[k] for k in keylist]
    num_values_per_feature = [len(v) for v in vlist]
    feature_distances = {}

    for i, c1 in enumerate(cartesian):
        for j, c2 in enumerate(cartesian):
            if i != j:
                distance = 0.0
                for k_idx in range(len(keylist)):
                    idx1 = vlist[k_idx].index(c1[k_idx])
                    idx2 = vlist[k_idx].index(c2[k_idx])
                    if num_values_per_feature[k_idx] > 1:
                        norm_dist = abs(idx1 - idx2) / (num_values_per_feature[k_idx] - 1)
                        distance += norm_dist ** 2
                distance = np.sqrt(distance / len(keylist)) if len(keylist) > 0 else 1.0
                feature_distances[(i, j)] = max(distance, 0.01)

    return feature_distances


def _build_value_mask(nhanes_data, code, value_ranges, target_values):
    """Build boolean mask on NHANES for specific values of a variable.

    Uses the pattern from create_masks_priors (distal.py lines 65-89).
    """
    # nonna mask: non-null AND within any defined value range
    nonna_mask = ~nhanes_data[code].isna()
    valid_mask = pd.Series(False, index=nhanes_data.index)
    for value_name, ranges in value_ranges.items():
        for r in ranges:
            if isinstance(r, (tuple, list)):
                valid_mask |= (nhanes_data[code] >= r[0]) & (nhanes_data[code] <= r[1])
            elif isinstance(r, (int, float)):
                valid_mask |= (nhanes_data[code] == r)
    nonna_mask = nonna_mask & valid_mask

    # value mask: matches the target values
    value_mask = pd.Series(False, index=nhanes_data.index)
    for val_name in target_values:
        if val_name not in value_ranges:
            continue
        for r in value_ranges[val_name]:
            if isinstance(r, (tuple, list)):
                value_mask |= (nhanes_data[code] >= r[0]) & (nhanes_data[code] <= r[1])
            elif isinstance(r, (int, float)):
                value_mask |= (nhanes_data[code] == r)

    return value_mask, nonna_mask


def compute_nhanes_slot_weights(
    cartesian, keylist, vdict, frequencies,
    nhanes_data, linear_config,
):
    """Compute per-CPT-slot weights from NHANES joint frequencies.

    For parents with NHANES codes: builds masks per value (same pattern as distal.py),
    ANDs them per slot, counts respondents. Uses create_independent_groups
    to handle sparse joints.

    For parents without codes (composite nodes): falls back to
    get_frequencies-derived joint probabilities.

    Returns:
        weights: dict {slot_index: float}, normalized to sum to 1
    """
    dep_data = linear_config.get('dependency_data', linear_config)

    # Classify parents: which have NHANES codes, which don't
    coded_parents = {}   # {parent_name: (code, value_ranges)}
    uncoded_parents = [] # parent names without direct NHANES mapping
    for k in keylist:
        node = dep_data.get(k, {})
        code = node.get('CODE')
        vr = node.get('value_ranges')
        if code and vr and code in nhanes_data.columns:
            coded_parents[k] = (code, vr)
        else:
            uncoded_parents.append(k)

    # Build per-(parent, value) masks for coded parents
    parent_value_masks = {}  # {(parent, value_name): value_mask}
    parent_nonna_masks = {}  # {(parent, value_name): nonna_mask}
    for k, (code, vr) in coded_parents.items():
        for val_name in vdict[k]:
            vm, nm = _build_value_mask(nhanes_data, code, vr, [val_name])
            parent_value_masks[(k, val_name)] = vm
            parent_nonna_masks[(k, val_name)] = nm

    weights = {}

    if coded_parents:
        # Use create_independent_groups to find which coded parents
        # have enough joint data
        # Build condition-keyed nonna masks for the group finder
        condition_masks = {}
        for k in coded_parents:
            # Use the first value's nonna mask (same for all values of same variable)
            first_val = vdict[k][0]
            condition_masks[k] = parent_nonna_masks[(k, first_val)]

        conditions = set(coded_parents.keys())
        groups = create_independent_groups(condition_masks, nhanes_data, conditions, NHANES_THRESHOLD)

        for i, combo in enumerate(cartesian):
            # For coded parents: compute joint counts per group
            coded_weight = 1.0
            for group in groups:
                # AND masks within group
                joint_value = pd.Series(True, index=nhanes_data.index)
                joint_nonna = pd.Series(True, index=nhanes_data.index)
                for k in group:
                    k_idx = keylist.index(k)
                    val_name = combo[k_idx]
                    joint_value &= parent_value_masks[(k, val_name)]
                    joint_nonna &= parent_nonna_masks[(k, val_name)]
                n_nonna = joint_nonna.sum()
                if n_nonna > 0:
                    coded_weight *= joint_value.sum() / n_nonna
                else:
                    coded_weight *= 1e-6

            # For uncoded parents: multiply by their net-derived marginal
            uncoded_weight = 1.0
            if uncoded_parents:
                # Extract just the uncoded dimensions from the combo
                for k in uncoded_parents:
                    k_idx = keylist.index(k)
                    val_name = combo[k_idx]
                    # Sum frequency over all combos matching this value for this parent
                    marginal = sum(
                        frequencies[c] for c in cartesian
                        if c[k_idx] == val_name
                    )
                    uncoded_weight *= marginal

            # NOTE on bias: between-group combination here is a product of
            # group-level NHANES-data joints, implicitly assuming independence
            # between the groups `create_independent_groups` chose. When the
            # between-group parents are actually correlated (the case
            # dependency_distal exists to model), this underestimates the
            # weight of correlated-parent combos. Compare with subset
            # constraints, which use the net-so-far's `frequencies` joint
            # (after Apr 18 2026 fix in subset_constraints.py). A/B test of
            # switching DF weights to `frequencies[combo]` is a TODO; left
            # as-is to preserve the existing DF behavior until measured.
            weights[i] = max(coded_weight * uncoded_weight, 1e-6)
    else:
        # No coded parents at all - use get_frequencies directly
        for i, combo in enumerate(cartesian):
            weights[i] = max(frequencies.get(combo, 1e-6), 1e-6)

    # Normalize
    total = sum(weights.values())
    if total > 0:
        weights = {k: v / total for k, v in weights.items()}
    else:
        n = len(cartesian)
        weights = {i: 1.0 / n for i in range(n)}

    return weights


def _compute_nhanes_joint_targets(
    cartesian, keylist, vdict, val_prev,
    nhanes_data, linear_config, output_node,
    min_respondents=30,
):
    """Compute P(outcome | parent1=v1 AND parent2=v2 ...) from NHANES.

    Uses create_independent_groups to split parents into groups with
    sufficient joint data. For each group, computes the joint conditional
    P(outcome | group_parents). Combines across groups by multiplication
    (independence between groups), then normalizes.

    Falls back to averaged marginals from val_prev when:
    - Output variable has no NHANES code
    - No coded parents available

    Args:
        output_node: name of the output variable (e.g. 'cardiovascular_disease')
    """
    dep_data = linear_config.get('dependency_data', linear_config)

    # Check if output variable has a NHANES code
    out_config = dep_data.get(output_node, {})
    out_code = out_config.get('CODE')
    out_vr = out_config.get('value_ranges')
    out_outvars = out_config.get('OUTVARS', out_config.get('PRIORS', {}))

    if not out_code or not out_vr or out_code not in nhanes_data.columns:
        return None  # Can't compute from NHANES, caller should fall back

    # Build output mask
    positive_val = list(out_outvars.keys())[0]
    out_value_mask, out_nonna_mask = _build_value_mask(
        nhanes_data, out_code, out_vr, [positive_val]
    )
    # Also build full output nonna (both positive and negative values)
    _, out_full_nonna = _build_value_mask(
        nhanes_data, out_code, out_vr, list(out_vr.keys())
    )

    # Build per-parent, per-value masks for coded parents
    # Skip parents that share the same NHANES code as the output (tautological)
    coded_parents = {}  # {parent_name: (code, value_ranges)}
    coded_parent_masks = {}  # {(parent, value): (value_mask, nonna_mask)}
    for k in keylist:
        node = dep_data.get(k, {})
        code = node.get('CODE')
        vr = node.get('value_ranges')
        if code and vr and code in nhanes_data.columns:
            if code == out_code:
                continue  # Same NHANES code as output - skip to avoid tautology
            coded_parents[k] = (code, vr)
            for val_name in vdict[k]:
                vm, nm = _build_value_mask(nhanes_data, code, vr, [val_name])
                coded_parent_masks[(k, val_name)] = (vm, nm)

    if not coded_parents:
        return None  # No coded parents, can't compute from NHANES

    # Use create_independent_groups to find which coded parents have joint data
    # (including overlap with the output variable)
    condition_nonna = {}
    for k in coded_parents:
        first_val = vdict[k][0]
        _, nm = coded_parent_masks[(k, first_val)]
        condition_nonna[k] = nm & out_full_nonna  # require output also non-null

    conditions = set(coded_parents.keys())
    groups = create_independent_groups(
        condition_nonna, nhanes_data, conditions, NHANES_THRESHOLD
    )

    # Base rate: P(outcome) among all respondents with valid output
    base_rate = (out_full_nonna & out_value_mask).sum() / out_full_nonna.sum()

    # For each CPT slot, compute target
    observed_probs = {}
    for i, combo in enumerate(cartesian):
        # Compute per-group conditional, multiply across groups
        slot_prob = 1.0
        any_nhanes = False

        for group in groups:
            # AND masks for all parents in this group
            joint_mask = out_full_nonna.copy()
            for k in group:
                k_idx = keylist.index(k)
                val_name = combo[k_idx]
                vm, nm = coded_parent_masks[(k, val_name)]
                joint_mask = joint_mask & nm & vm

            n_joint = joint_mask.sum()
            if n_joint >= min_respondents:
                group_cond = (joint_mask & out_value_mask).sum() / n_joint
                # Express as ratio to base rate, so multiplication works
                if base_rate > 0:
                    slot_prob *= group_cond / base_rate
                any_nhanes = True
            # else: this group contributes factor 1.0 (no data, no pull)

        if any_nhanes:
            # Convert back: target = base_rate * product_of_ratios
            observed_probs[i] = np.clip(base_rate * slot_prob, 0.001, 0.999)
        else:
            # All groups sparse - fall back to averaged marginals
            slot_probs = []
            for j, val_name in enumerate(combo):
                k = keylist[j]
                if k in val_prev and val_name in val_prev[k]:
                    slot_probs.append(val_prev[k][val_name])
            if slot_probs:
                observed_probs[i] = np.mean(slot_probs)
            else:
                observed_probs[i] = list(out_outvars.values())[0]

    return observed_probs


def get_monotonicity_pairs(
    cartesian, keylist, vdict, invars_config,
    reverse_output=False, node_reverse=None,
    linear_config=None, bayesianNetwork=None, cpt=None,
):
    """Identify CPT cell pairs that should satisfy monotonicity ordering.

    Uses dictVarsAndValues ordering (worst-to-best from spreadsheet) and
    the reverse column to determine which adjacent value pairs should have
    P(disease|risk) >= P(disease|safe).

    Returns:
        list of (risk_idx, safe_idx, n_vals) tuples where:
            risk_idx: CPT cell index for the worse (higher disease) value
            safe_idx: CPT cell index for the better (lower disease) value
            n_vals: number of values for this parent (for normalization)
    """
    from sn_bayes.utils import dictVarsAndValues

    if node_reverse is None:
        node_reverse = {}

    # Get the canonical value ordering from dictVarsAndValues
    if bayesianNetwork is not None and cpt is not None:
        ordered_vdict = dictVarsAndValues(bayesianNetwork, cpt)
    else:
        ordered_vdict = None

    pairs = []

    for k_idx, k in enumerate(keylist):
        # Get ordered values for this parent
        if ordered_vdict and k in ordered_vdict:
            all_ordered = ordered_vdict[k]
        elif linear_config:
            dep_data = linear_config.get('dependency_data', {})
            parent_outvars = dep_data.get(k, {}).get('OUTVARS', dep_data.get(k, {}).get('PRIORS', {}))
            all_ordered = list(parent_outvars.keys()) if isinstance(parent_outvars, dict) else list(vdict[k])
        else:
            all_ordered = list(vdict[k])

        cpt_values = set(vdict[k])
        values_list = [v for v in all_ordered if v in cpt_values]

        if len(values_list) < 2:
            continue

        parent_reverse = node_reverse.get(k)
        n_vals = len(values_list)

        # Build adjacent ordered pairs: (higher_disease_val, lower_disease_val)
        ordered_value_pairs = []

        if parent_reverse is not None and int(parent_reverse) > 0:
            rev_idx = int(parent_reverse)
            for vi in range(min(rev_idx, n_vals) - 1):
                ordered_value_pairs.append((values_list[vi], values_list[vi + 1]))
            for vi in range(max(rev_idx - 1, 0), n_vals - 1):
                ordered_value_pairs.append((values_list[vi + 1], values_list[vi]))
        else:
            for vi in range(n_vals - 1):
                ordered_value_pairs.append((values_list[vi], values_list[vi + 1]))

        # For each adjacent pair, find CPT cell pairs
        for higher_val, lower_val in ordered_value_pairs:
            for i, combo_i in enumerate(cartesian):
                if combo_i[k_idx] != higher_val:
                    continue
                for j, combo_j in enumerate(cartesian):
                    if combo_j[k_idx] != lower_val:
                        continue
                    same = True
                    for dim in range(len(keylist)):
                        if dim != k_idx and combo_i[dim] != combo_j[dim]:
                            same = False
                            break
                    if not same:
                        continue

                    if reverse_output:
                        ri, si = j, i
                    else:
                        ri, si = i, j
                    pairs.append((ri, si, n_vals))

    return pairs


def data_fit_and_monotonicity(
    cartesian, keylist, vdict, frequencies, val_prev, size,
    w=0.7, nhanes_data=None, linear_config=None, output_node=None,
    invars_config=None, reverse_output=False, node_reverse=None,
    bayesianNetwork=None, cpt=None,
):
    """Build P and q for: w * data_fit + (1-w) * monotonicity.

    Data fit: sum(wi * (pi - pi')^2)
        wi = NHANES joint frequency for this CPT slot (via the NHANES masks
             + create_independent_groups for sparse data handling)
        pi' = NHANES joint conditional P(outcome | parents) when available,
              otherwise multiplicative independence from study RRs

    Monotonicity: penalizes inversions where a risk parent value gives
        LOWER disease probability than a safe value. Only acts on
        inverted cell pairs, preserving correct sharp gradients.

    Args:
        cartesian: list of CPT slot tuples
        keylist: parent variable names
        vdict: {var: [values]} per parent
        frequencies: {combo: prob} from get_frequencies (net-derived)
        val_prev: {var: {val: conditional_prob}} from study RRs
        size: 2 * len(cartesian)
        w: 1.0 = pure data fit, 0.0 = pure monotonicity
        nhanes_data: wide-format NHANES DataFrame (optional, for proper weights)
        linear_config: linearized config with CODE/value_ranges (optional)
        output_node: name of the output variable (optional, for NHANES targets)
        invars_config: INVARS dict from config node (for monotonicity risk values)
        reverse_output: if True, first outvar is "good" state

    Returns:
        (P, q) for the QP objective
    """
    n = len(cartesian)

    # Weights
    if nhanes_data is not None and linear_config is not None:
        weights = compute_nhanes_slot_weights(
            cartesian, keylist, vdict, frequencies,
            nhanes_data, linear_config,
        )
    else:
        # Fallback: use net-derived frequencies
        raw = {i: max(frequencies.get(c, 1e-6), 1e-6) for i, c in enumerate(cartesian)}
        total = sum(raw.values())
        weights = {k: v / total for k, v in raw.items()}

    # Targets: NHANES joint conditionals when available, else averaged marginals
    observed_probs = None
    if nhanes_data is not None and linear_config is not None and output_node is not None:
        observed_probs = _compute_nhanes_joint_targets(
            cartesian, keylist, vdict, val_prev,
            nhanes_data, linear_config, output_node,
        )

    if observed_probs is None:
        # Fallback: multiplicative independence assumption
        # P(outcome | p1=v1, p2=v2) = prior * prod(P(outcome|pi=vi) / prior)
        # = prod(val_prev[pi][vi]) / prior^(k-1)
        prior = list(val_prev[keylist[0]].values())[0]  # baseline prevalence
        observed_probs = {}
        for i, combo in enumerate(cartesian):
            product = 1.0
            n_parents = 0
            for j, val_name in enumerate(combo):
                k = keylist[j]
                if k in val_prev and val_name in val_prev[k]:
                    product *= val_prev[k][val_name]
                    n_parents += 1
            if n_parents > 0 and prior > 0:
                observed_probs[i] = np.clip(product / (prior ** (n_parents - 1)), 0.001, 0.999)
            else:
                observed_probs[i] = prior

    P = np.zeros((size, size), np.double)
    q = np.zeros(size, np.double)

    # Part 1: Data fitting - w * sum(wi * (pi - pi')^2)
    for i in range(n):
        wi = weights.get(i, 1e-6)
        oi = observed_probs.get(i, 0.5)
        P[i, i] += w * wi
        q[i] -= 2 * w * wi * oi
        P[i + n, i + n] += w * wi
        q[i + n] -= 2 * w * wi * (1 - oi)

    # Monotonicity is now handled via slack variables in dependency_v2.py,
    # not as a quadratic penalty here. See get_monotonicity_pairs().

    # Regularization
    for i in range(size):
        if P[i, i] == 0:
            P[i, i] = 1e-6

    eigenvalues = np.linalg.eigvals(P)
    min_eig = np.min(eigenvalues.real)
    if min_eig < 0:
        P += (-min_eig + 1e-6) * np.eye(size)

    return P, q
