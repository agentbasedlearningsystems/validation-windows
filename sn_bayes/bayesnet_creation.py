import json
import logging
import os
import time

from sn_bayes.config_creation import (
    prior_types,
    dependency_types,
)
from .utils import (
    dependency,
    all_of,
    any_of,
    avg,
    if_then_else,
    addCpt,
    non_cpt_descriptions,
)
from . import cpt_cache
from sn_service.service_spec.bayesian_pb2 import BayesianNetwork

logger = logging.getLogger(__name__)


class InputValuesError(Exception):
    pass


def add_anomaly(bayes_net: BayesianNetwork, anomaly_name: str, anomaly_data: dict):
    anomaly = bayes_net.anomalies.add()
    anomaly.varName = anomaly_name

    for param, value in anomaly_data['parameters'].items():
        if value in {'True', 'False'}:
            value = eval(value)
        setattr(anomaly, param, value)

    for detector_name in anomaly_data['detectors']:
        detector = anomaly.detectors.add()
        detector.name = detector_name


def add_discrete_distribution(bayes_net: BayesianNetwork, var_name: str, priors: dict):
    discrete_distribution = bayes_net.discreteDistributions.add()
    discrete_distribution.name = var_name
    for value, prob in priors.items():
        variable = discrete_distribution.variables.add()
        variable.name = value
        variable.probability = prob


def add_all_of(bayes_net: BayesianNetwork, var_name: str, data: dict, outstring: str) -> str:
    invars = {}
    for invar_name, values in data['INVARS'].items():
        if len(values) > 1:
            msg = f"Multiple input values for the same variable {invar_name} are found: {values}"
            raise InputValuesError(msg)
        else:
            invars[invar_name] = set(values[0]['VALUE'])

    cpt = {var_name: all_of(bayes_net, {}, invars, data['OUTVARS'])}
    
    outstring = outstring + addCpt(bayes_net, cpt)
    return outstring


def add_any_of(bayes_net: BayesianNetwork, var_name: str, data: dict, outstring: str) -> str:
    invars = {}
    for invar_name, inputs in data['INVARS'].items():
        input_values = set()
        for entry in inputs:
            for value in entry['VALUE']:
                input_values.add(value)     # Show warning that input value was already here? 
        invars[invar_name] = input_values
    cpt = {var_name: any_of(bayes_net, {}, invars, data['OUTVARS'])}

    outstring = outstring + addCpt(bayes_net, cpt)
    return outstring


def add_dependency(bayes_net: BayesianNetwork, var_name: str, data: dict, outstring: str,
                   use_v2=False, use_subset=False, w=None, w_ls=None, w_df=None,
                   w_mono=0.0, nhanes_data=None, linear_config=None, deconfound=False) -> str:
    invars = []
    for invar, input_values in data['INVARS'].items():
        for value in input_values:
            # Skip entries without STATS (e.g., SMD_UNRELIABLE rows whose
            # RR could not be computed; extract_row_stats returns {} for
            # these). The CPT is built from the surviving entries only.
            stats = value.get('STATS')
            if not stats:
                continue
            invars.append(({invar: value['VALUE']}, stats))
    dep_kwargs = dict(deconfound=deconfound,
                      use_v2=use_v2, use_subset=use_subset,
                      w_mono=w_mono,
                      nhanes_data=nhanes_data, linear_config=linear_config,
                      output_node=var_name)
    if w_ls is not None:
        dep_kwargs['w_ls'] = w_ls
    if w_df is not None:
        dep_kwargs['w_df'] = w_df
    if w is not None and w_ls is None and w_df is None:
        dep_kwargs['w'] = w
    cpt = {var_name: dependency(bayes_net, {}, invars, data['OUTVARS'], **dep_kwargs)}

    outstring = outstring + addCpt(bayes_net, cpt)
    return outstring


def add_avg(bayes_net: BayesianNetwork, var_name: str, data: dict, outstring: str) -> str:
    cpt = {var_name : avg(bayes_net, {}, data['INVARS'], data['OUTVARS'])}
    outstring = outstring + addCpt(bayes_net, cpt)
    return outstring


def add_if_then_else(bayes_net: BayesianNetwork, var_name: str, data: dict, outstring: str) -> str:
    invars = {}
    for invar_name, inputs in data['INVARS'].items():
        input_values = set()
        for entry in inputs:
            for value in entry['VALUE']:
                input_values.add(value)     # Show warning that input value was already here? 
        invars[invar_name] = input_values
    cpt = {var_name: if_then_else(bayes_net, {}, invars, data['OUTVARS'])}

    outstring = outstring + addCpt(bayes_net, cpt)
    return outstring


def _try_cache_hit(bayes_net, var_name, data, solver_kwargs):
    """If the cache holds a reusable CPT/DD for var_name whose parents match
    the current node, copy it in and return True. Else return False."""
    prev_proto = solver_kwargs.get('_cache_prev_proto')
    reusable = solver_kwargs.get('_cache_reusable_set')
    if prev_proto is None or reusable is None or var_name not in reusable:
        return False
    expected_parents = sorted((data.get('INVARS') or {}).keys()) or None
    if cpt_cache.copy_cpt_from_prev(bayes_net, prev_proto, var_name,
                                      expected_parents=expected_parents):
        return True
    return False


def parse_dependency(bayes_net: BayesianNetwork, var_name: str, data: dict, outstring: str, parsed: set,
                     **solver_kwargs) -> str:
    if var_name in parsed:
        return outstring

    if data['TYPE'] in prior_types:
        if _try_cache_hit(bayes_net, var_name, data, solver_kwargs):
            parsed.add(var_name)
            return outstring
        add_discrete_distribution(bayes_net, var_name, data['PRIORS'])
        parsed.add(var_name)
        return outstring

    if 'INPUTS' in data:    # Linear case parsing
        for inp_var_name, inp_var_data in data['INPUTS'].items():
            outstring = parse_dependency(bayes_net, inp_var_name, inp_var_data, outstring, parsed, **solver_kwargs)

    # Also ensure INVARS parents are built (for naive-converted nodes)
    if 'INVARS' in data:
        config = solver_kwargs.get('linear_config', {})
        dep_data = config.get('dependency_data', config)
        for inv_name in data['INVARS']:
            if inv_name not in parsed and inv_name in dep_data:
                outstring = parse_dependency(bayes_net, inv_name, dep_data[inv_name], outstring, parsed, **solver_kwargs)

    if _try_cache_hit(bayes_net, var_name, data, solver_kwargs):
        parsed.add(var_name)
        return outstring

    if data['TYPE'] == 'all_of':
        outstring = add_all_of(bayes_net, var_name, data, outstring)

    elif data['TYPE'] == 'any_of':
        outstring = add_any_of(bayes_net, var_name, data, outstring)

    elif data['TYPE'] == 'avg':
        outstring = add_avg(bayes_net, var_name, data, outstring)

    elif data['TYPE'] == 'if_then_else':
        outstring = add_if_then_else(bayes_net, var_name, data, outstring)

    elif data['TYPE'] in dependency_types:
        # Strip internal cache keys before forwarding - add_dependency doesn't accept them.
        solver_only_kwargs = {k: v for k, v in solver_kwargs.items()
                              if not k.startswith('_cache_')}
        outstring = add_dependency(bayes_net, var_name, data, outstring, **solver_only_kwargs)

    else:
        msg = f"Dependency node of unexpected type '{data['TYPE']}' is found. Aborting."
        raise TypeError(msg)

    parsed.add(var_name)
    return outstring


def create_bayesnet_proto_linear(config: dict, use_v2=False, use_subset=False,
                                 w=None, w_ls=None, w_df=None, w_mono=0.0,
                                 nhanes_data=None, deconfound=False,
                                 cache_dir=cpt_cache.LATEST_DIR) -> tuple:
    """Build a BayesianNetwork protobuf from a linearized config.

    Args:
        config: dict with 'anomaly_data' and 'dependency_data' keys,
                as produced by linearize_config().
        use_v2: if True, use dependency_direct_v2 for QP solving
        w: legacy single-knob LS/data-fit balance. If given, expands to
           (w_ls, w_df) = (w, 1-w). Ignored when w_ls or w_df is set.
        w_ls: independent LS objective weight (new).
        w_df: independent data-fit objective weight (new).
        w_mono: monotonicity strength. 0.0=off, 1.0=full
        nhanes_data: wide-format NHANES DataFrame (for data-fit targets)
        cache_dir: directory for dirty-tracking CPT cache. Pass None to
                   disable. Env var BAYESNET_DISABLE_CACHE=1 also disables.

    Returns:
        (BayesianNetwork, outstring) tuple.
    """
    bayes_net = BayesianNetwork()

    for anomaly_name, anomaly_data in config.get('anomaly_data', {}).items():
        add_anomaly(bayes_net, anomaly_name, anomaly_data)

    solver_kwargs = {}
    if deconfound:
        solver_kwargs['deconfound'] = True
    if use_v2:
        v2_kwargs = {
            'use_v2': True,
            'use_subset': use_subset,
            'w_mono': w_mono,
            'nhanes_data': nhanes_data,
            'linear_config': config,
        }
        # Prefer the independent knobs. Fall back to legacy `w` if supplied.
        if w_ls is not None:
            v2_kwargs['w_ls'] = w_ls
        if w_df is not None:
            v2_kwargs['w_df'] = w_df
        if w_ls is None and w_df is None and w is not None:
            v2_kwargs['w'] = w
        solver_kwargs.update(v2_kwargs)

    # --- Dirty-tracking CPT cache: load prev build, compute reusable set ---
    cache_enabled = (cache_dir is not None and
                     os.environ.get('BAYESNET_DISABLE_CACHE') != '1')
    prev_proto, prev_hashes, prev_solver_hash = (None, None, None)
    reusable = set()
    if cache_enabled:
        prev_proto, prev_hashes, prev_solver_hash = cpt_cache.load_cache(cache_dir)
        if prev_proto is not None:
            current_solver_hash = cpt_cache.hash_solver_kwargs({
                'use_v2': use_v2, 'use_subset': use_subset,
                'w': w, 'w_ls': w_ls, 'w_df': w_df, 'w_mono': w_mono,
                'deconfound': deconfound,
            })
            reusable = cpt_cache.compute_reusable_set(
                config['dependency_data'], prev_hashes,
                current_solver_hash, prev_solver_hash)
            print(f"  [cpt_cache] {len(reusable)}/{len(config['dependency_data'])} "
                  f"nodes reusable from previous build")
        else:
            print(f"  [cpt_cache] no previous build found at {cache_dir}")
    solver_kwargs['_cache_prev_proto'] = prev_proto
    solver_kwargs['_cache_reusable_set'] = reusable

    outstring = ""
    parsed = set()  # shared across all nodes so parents built once
    for var, data in config['dependency_data'].items():
        try:
            outstring = parse_dependency(bayes_net, var, data, outstring, parsed, **solver_kwargs)
        except TypeError as e:
            msg = f"Error occurred during '{var}' node parsing: {str(e)}"
            raise TypeError(msg)

    # --- Save cache for next build ---
    if cache_enabled:
        try:
            cpt_cache.save_cache(bayes_net, config, {
                'use_v2': use_v2, 'use_subset': use_subset,
                'w': w, 'w_ls': w_ls, 'w_df': w_df, 'w_mono': w_mono,
                'deconfound': deconfound,
            }, cache_dir)
            print(f"  [cpt_cache] saved to {cache_dir}")
        except Exception as e:
            print(f"  [cpt_cache] save failed: {e}")

    return bayes_net, outstring