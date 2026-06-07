"""Standalone (stdlib-only) compatibility loader for pomegranate BayesianNetwork pickles.

Lives in its own module so it can be imported by reviewer-facing demo
scripts that have no venv set up (no pandas / torch / numpy etc.) -
`sn_bayes.utils` pulls heavy dependencies at module-load time which
breaks the demo's stdlib-only promise.

Use:
    from sn_bayes.pickle_compat import smart_load_pickle
    proto = smart_load_pickle('bayesianNetworkProto_v2cleaned_final.pickle.gz')

The loader sniffs the first 2 bytes; if 0x1f 0x8b (gzip magic) the file
is transparently gunzipped before unpickling. Handles both legacy
`.pickle` files (uncompressed) and the post-2026-05-12 `.pickle.gz`
convention (introduced to reduce mirror push size from 8.2 MB → 0.28 MB,
a 30x reduction; pomegranate's pickle has heavy string-key redundancy
that gzip eliminates).
"""
import gzip
import pickle


def smart_load_pickle(path):
    """Load a pickle file, auto-detecting gzip via magic bytes."""
    with open(path, 'rb') as f:
        magic = f.read(2)
    if magic == b'\x1f\x8b':
        with gzip.open(path, 'rb') as f:
            return pickle.load(f)
    with open(path, 'rb') as f:
        return pickle.load(f)
