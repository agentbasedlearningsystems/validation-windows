"""Dirty-tracking CPT cache for faster rebuilds.

Skips the QP solver for nodes whose (config-hash ∧ all-ancestors-config-hash)
are unchanged from the previous build. Copies the cached CPT (or discrete
distribution) directly from the previous build's protobuf.

Works by:
  1. Loading builds/cpt_cache/latest/{bayesianNetworkProto.pickle, config_linear.json}
     before the build.
  2. Computing a config-hash for every node in the new config.
  3. For each node: if its hash matches the previous build's hash AND every
     ancestor's hash also matches, mark it 'reusable'.
  4. During build: reusable nodes copy their CPT/DD from the previous proto
     into the new proto; non-reusable nodes run the solver as usual.
  5. After build: save the new proto + config into builds/cpt_cache/latest/
     for the next rebuild.

The cascade rule (all ancestors must also match) is required because a CPT's
QP solution depends on the partial net's predict_proba, which depends on the
ancestor CPTs' shapes.

To disable: pass cache_dir=None to create_bayesnet_proto_linear, or use env
var BAYESNET_DISABLE_CACHE=1.
"""
import hashlib
import json
import os
import pickle
from typing import Optional


CACHE_DIR = 'builds/cpt_cache'
LATEST_DIR = os.path.join(CACHE_DIR, 'latest')


def _stable_key(obj):
    """Stable representation of a config-data dict for hashing.
    Sort keys, recurse into dicts/lists, round floats to 10 decimal places."""
    if isinstance(obj, dict):
        return {k: _stable_key(obj[k]) for k in sorted(obj.keys())}
    elif isinstance(obj, list):
        return [_stable_key(v) for v in obj]
    elif isinstance(obj, tuple):
        return tuple(_stable_key(v) for v in obj)
    elif isinstance(obj, float):
        return round(obj, 10)
    return obj


def hash_node_data(node_data: dict) -> str:
    """Stable hash of a node's config data. Exclude INPUTS (recursive config)
    because we track ancestor identity separately via per-node hashes."""
    data_for_hash = {k: v for k, v in node_data.items() if k != 'INPUTS'}
    canonical = json.dumps(_stable_key(data_for_hash), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def hash_solver_kwargs(kwargs: dict) -> str:
    """Hash of solver config: use_v2, use_subset, w_ls, w_df, w_mono, plus
    `nhanes_hash` (content fingerprint of input data) and `code_hash` (source
    files that define solver behavior). If any changes, ALL CPTs must be
    re-solved. Callers are responsible for computing nhanes_hash/code_hash
    via hash_nhanes_data() and hash_solver_code() below."""
    rel = {k: v for k, v in kwargs.items()
           if k in ('use_v2', 'use_subset', 'w', 'w_ls', 'w_df', 'w_mono',
                    'mono_margin', 'deconfound', 'nhanes_hash', 'code_hash')}
    return hashlib.sha256(json.dumps(rel, sort_keys=True, default=str).encode()).hexdigest()


def hash_nhanes_data(df) -> str:
    """Content fingerprint of the NHANES DataFrame. Returns a short hex.
    Any cell change → cache miss, because NHANES feeds subset/DF/distal."""
    if df is None:
        return 'none'
    try:
        import pandas as pd
        h = pd.util.hash_pandas_object(df, index=True).values.tobytes()
        return hashlib.sha256(h).hexdigest()[:16]
    except Exception:
        # Fallback: shape + column fingerprint
        return hashlib.sha256(
            f"{getattr(df, 'shape', None)}:{sorted(list(getattr(df, 'columns', [])))}"
            .encode()).hexdigest()[:16]


_SOLVER_CODE_FILES = (
    'sn_bayes/utils.py',
    'sn_bayes/dependency_v2.py',
    'sn_bayes/subset_constraints.py',
    'sn_bayes/objectives.py',
    'sn_bayes/bayesnet_creation.py',
    'sn_bayes/config_creation/distal.py',
)


def hash_solver_code() -> str:
    """Fingerprint of solver source files. Edits to QP, subset, DF, distal,
    or the build loop itself invalidate every cached CPT."""
    h = hashlib.sha256()
    for rel in _SOLVER_CODE_FILES:
        if os.path.isfile(rel):
            with open(rel, 'rb') as f:
                h.update(rel.encode() + b':')
                h.update(f.read())
                h.update(b'\n')
    return h.hexdigest()[:16]


def compute_ancestor_hashes(dep_data: dict) -> dict:
    """For each node, compute a combined hash that includes all ancestors.
    This is the key used for cache lookup: two nodes with the same own-hash
    but different ancestor-hashes yield different CPT solutions."""
    own = {name: hash_node_data(data) for name, data in dep_data.items()}
    combined = {}

    def rec(name, visited):
        if name in combined:
            return combined[name]
        if name in visited:
            # Cycle (shouldn't happen in a DAG); return own-hash as fallback
            return own.get(name, '')
        visited.add(name)
        data = dep_data.get(name, {})
        parent_hashes = []
        for parent in sorted((data.get('INVARS') or {}).keys()):
            if parent in dep_data:
                parent_hashes.append(rec(parent, visited))
        h = hashlib.sha256((own.get(name, '') + ''.join(parent_hashes)).encode()).hexdigest()
        combined[name] = h
        return h

    for name in dep_data:
        rec(name, set())
    return combined


def load_cache(cache_dir: str = LATEST_DIR):
    """Load previous build's proto + config-hash index. Returns (proto, hashes_by_name, solver_hash)
    or (None, None, None) if no valid cache."""
    proto_path = os.path.join(cache_dir, 'bayesianNetworkProto.pickle')
    config_path = os.path.join(cache_dir, 'config_linear.json')
    solver_path = os.path.join(cache_dir, 'solver_hash.txt')
    if not (os.path.isfile(proto_path) and os.path.isfile(config_path)):
        return None, None, None
    try:
        with open(proto_path, 'rb') as f:
            proto = pickle.load(f)
        with open(config_path) as f:
            config = json.load(f)
        hashes = compute_ancestor_hashes(config.get('dependency_data', config))
        solver_hash = None
        if os.path.isfile(solver_path):
            with open(solver_path) as f:
                solver_hash = f.read().strip()
        return proto, hashes, solver_hash
    except Exception as e:
        print(f"  [cpt_cache] failed to load: {e}")
        return None, None, None


def save_cache(bayes_net, config: dict, solver_kwargs: dict, cache_dir: str = LATEST_DIR):
    """Save current build's proto + config for next rebuild's cache lookup."""
    os.makedirs(cache_dir, exist_ok=True)
    proto_path = os.path.join(cache_dir, 'bayesianNetworkProto.pickle')
    config_path = os.path.join(cache_dir, 'config_linear.json')
    solver_path = os.path.join(cache_dir, 'solver_hash.txt')
    with open(proto_path, 'wb') as f:
        pickle.dump(bayes_net, f)
    with open(config_path, 'w') as f:
        json.dump(config, f)
    with open(solver_path, 'w') as f:
        f.write(hash_solver_kwargs(solver_kwargs))


def compute_reusable_set(current_dep_data: dict, prev_hashes: dict,
                          current_solver_hash: str, prev_solver_hash: str) -> set:
    """Return the set of node names whose CPTs can be reused from the previous build."""
    if prev_hashes is None or prev_solver_hash != current_solver_hash:
        return set()
    current_hashes = compute_ancestor_hashes(current_dep_data)
    return {name for name, h in current_hashes.items()
            if prev_hashes.get(name) == h}


def _find_cpt(proto, name):
    for cpt in getattr(proto, 'conditionalProbabilityTables', []):
        if cpt.name == name:
            return cpt
    return None


def _find_dd(proto, name):
    for dd in getattr(proto, 'discreteDistributions', []):
        if dd.name == name:
            return dd
    return None


def _cached_item_shape_ok(cached_item, expected_parents=None):
    """Sanity check on cached CPT/DD before copying. If the cached item's
    parent names don't match what the current config expects, the hash-match
    was a false positive (different node with same effective data). Refuse
    the copy so we fall back to the solver."""
    if not cached_item.name:
        return False, "cached item has empty name"
    if expected_parents is None:
        return True, ""
    cached_parents = []
    for p in getattr(cached_item, 'parents', []):
        cached_parents.append(p.name if hasattr(p, 'name') else str(p))
    if sorted(cached_parents) != sorted(expected_parents):
        return False, (f"parent mismatch: cached={sorted(cached_parents)}, "
                       f"expected={sorted(expected_parents)}")
    return True, ""


def copy_cpt_from_prev(current_bayes_net, prev_proto, name: str,
                        expected_parents=None) -> bool:
    """Copy node `name`'s CPT or discrete distribution from prev_proto into
    current_bayes_net. If expected_parents is given, verify shape matches
    before copying; refuse on mismatch so the solver runs instead.
    Returns True if found, validated, and copied."""
    prev_cpt = _find_cpt(prev_proto, name)
    if prev_cpt is not None:
        ok, why = _cached_item_shape_ok(prev_cpt, expected_parents)
        if not ok:
            print(f"  [cpt_cache] refusing to copy '{name}': {why}")
            return False
        new_cpt = current_bayes_net.conditionalProbabilityTables.add()
        new_cpt.CopyFrom(prev_cpt)
        return True
    prev_dd = _find_dd(prev_proto, name)
    if prev_dd is not None:
        ok, why = _cached_item_shape_ok(prev_dd, expected_parents)
        if not ok:
            print(f"  [cpt_cache] refusing to copy '{name}': {why}")
            return False
        new_dd = current_bayes_net.discreteDistributions.add()
        new_dd.CopyFrom(prev_dd)
        return True
    return False


# ---------------------------------------------------------------------------
# Correctness verification
# ---------------------------------------------------------------------------
# The cache's soundness rests on one claim: if (own config-hash ∧ ancestor
# config-hashes ∧ solver config-hash) are all unchanged, the QP solver would
# produce the identical CPT. To validate this claim, run the build TWICE
# (cache on, cache off) and byte-compare every CPT/DD. Any diff means the
# hash is missing an input that actually affects the solve. Use
# `scripts/verify_cache.py` as the driver.

def cpts_identical(cpt_a, cpt_b) -> bool:
    """Byte-for-byte proto equality via SerializeToString. Works for CPTs
    and DDs (any proto message). Exact - not tolerance-based - because QP
    with identical inputs must produce identical outputs."""
    if cpt_a is None or cpt_b is None:
        return cpt_a is cpt_b
    return cpt_a.SerializeToString() == cpt_b.SerializeToString()


def diff_protos(proto_a, proto_b) -> dict:
    """Compare every CPT/DD across two bayesianNetwork protos.

    Returns:
        {
          'identical':   [names that match byte-for-byte],
          'differ':      [(name, 'cpt'|'dd') that share a name but differ],
          'only_in_a':   [names present in A only],
          'only_in_b':   [names present in B only],
          'summary':     'X identical, Y differ, Z only in A, W only in B',
        }
    """
    cpts_a = {c.name: c for c in getattr(proto_a, 'conditionalProbabilityTables', [])}
    cpts_b = {c.name: c for c in getattr(proto_b, 'conditionalProbabilityTables', [])}
    dds_a = {d.name: d for d in getattr(proto_a, 'discreteDistributions', [])}
    dds_b = {d.name: d for d in getattr(proto_b, 'discreteDistributions', [])}

    identical, differ = [], []

    for name in sorted(set(cpts_a) & set(cpts_b)):
        if cpts_identical(cpts_a[name], cpts_b[name]):
            identical.append(name)
        else:
            differ.append((name, 'cpt'))

    for name in sorted(set(dds_a) & set(dds_b)):
        if cpts_identical(dds_a[name], dds_b[name]):
            identical.append(name)
        else:
            differ.append((name, 'dd'))

    all_a = set(cpts_a) | set(dds_a)
    all_b = set(cpts_b) | set(dds_b)
    only_a = sorted(all_a - all_b)
    only_b = sorted(all_b - all_a)

    return {
        'identical': identical,
        'differ': differ,
        'only_in_a': only_a,
        'only_in_b': only_b,
        'summary': (f"{len(identical)} identical, {len(differ)} differ, "
                    f"{len(only_a)} only in A, {len(only_b)} only in B"),
    }


def assert_cache_correct(proto_cached, proto_fresh,
                          allow_diff: Optional[int] = 0) -> None:
    """Raise AssertionError if cached build diverges from fresh build.

    allow_diff=0 (default): any byte-level diff fails. Use for release-gate.
    allow_diff=N: up to N node diffs allowed (for debugging drift sources).
    """
    report = diff_protos(proto_cached, proto_fresh)
    n_diff = len(report['differ']) + len(report['only_in_a']) + len(report['only_in_b'])
    if n_diff > (allow_diff or 0):
        sample = report['differ'][:10]
        raise AssertionError(
            f"Cache correctness check failed: {report['summary']}. "
            f"First differing nodes: {sample}. "
            f"Only in cached build: {report['only_in_a'][:5]}. "
            f"Only in fresh build: {report['only_in_b'][:5]}."
        )
