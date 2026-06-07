#!/usr/bin/env python3
"""End-to-end build of the demonstration Bayesian network.

Reads data/Individual Relations.working.xlsx + data/preprocessed_nhanes.csv,
produces bayesianNetworkProto.pickle + bayesnet_config_linear.json, and
runs a quick post-build diagnostic.

Usage:
    python scripts/build_demo.py

The paper-principled weights (w_ls=1, w_df=0, w_mono=1, use_subset=True) are
used by default. Override via environment variables W_LS, W_DF, W_MONO if
you want to experiment.
"""
import os, sys, time, json, pickle, copy, shutil, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')

LABEL = os.environ.get('LABEL', 'demo')
W_LS   = float(os.environ.get('W_LS',   '1.0'))
W_DF   = float(os.environ.get('W_DF',   '0.0'))
W_MONO = float(os.environ.get('W_MONO', '1.0'))

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def load_df():
    df = pd.read_excel('./data/Individual Relations.working.xlsx',
                       sheet_name='all worksheet anom', header=0)
    for i in range(1, 6):
        df[f'index{i}'] = df[f'index{i}'].astype(str)
    df.dropna(axis='index', how='all', inplace=True)
    inp = df[~df['input'].isna()]
    df.loc[inp[inp['input'].str.startswith('fixme')].index, 'input'] = np.nan
    df.replace('nan', np.nan, inplace=True)
    df = df.map(lambda e: e.strip() if isinstance(e, str) else e)
    df['code'] = df['code'].apply(lambda e: e.upper() if isinstance(e, str) and not pd.isna(e) else e)
    return df

def main():
    log(f"=== build: {LABEL} (w_ls={W_LS}, w_df={W_DF}, w_mono={W_MONO}) ===")

    if not os.path.exists('./data/preprocessed_nhanes.csv'):
        print("ERROR: data/preprocessed_nhanes.csv not found.")
        print("       See data/README.md for instructions to produce it.")
        sys.exit(1)

    df = load_df()
    nhanes = pd.read_csv('./data/preprocessed_nhanes.csv')

    log(f"spreadsheet: {len(df)} rows; NHANES: {len(nhanes)} respondents")

    # Structural checks
    try:
        from sn_bayes.df_checks import df_validate
        issues = df_validate(df)
        fails = [i for i in issues if i['severity'] == 'FAIL']
        if fails:
            log(f"WARN: {len(fails)} validation FAILs (build will continue): "
                f"{[(i['rule'], i['message'][:60]) for i in fails[:5]]}")
    except Exception as e:
        log(f"(df_validate skipped: {e})")

    # Compile
    from sn_bayes.config_creation import prepare_config
    from sn_bayes.config_creation.utils import linearize_config
    from sn_bayes.bayesnet_creation import create_bayesnet_proto_linear

    t0 = time.time()
    try:
        cfg = prepare_config(df, nhanes, skip_validation=True)
        linear = linearize_config(cfg)
    except TypeError:
        cfg = prepare_config(df, nhanes)
        linear = linearize_config(cfg)
    log(f"prep {time.time()-t0:.1f}s, {len(linear['dependency_data'])} nodes")

    # Solve CPTs
    t0 = time.time()
    cache = f'builds/cpt_cache/{LABEL}'
    os.makedirs(cache, exist_ok=True)
    proto, _ = create_bayesnet_proto_linear(
        copy.deepcopy(linear),
        use_v2=True, use_subset=True,
        w_ls=W_LS, w_df=W_DF, w_mono=W_MONO,
        nhanes_data=nhanes, cache_dir=cache,
    )
    log(f"build {time.time()-t0:.1f}s")

    # Save
    pkl = f'bayesianNetworkProto_{LABEL}.pickle'
    cf  = f'bayesnet_config_linear_{LABEL}.json'
    with open(pkl, 'wb') as f:
        pickle.dump(proto, f)
    def _safe(o):
        try:
            json.dumps(o); return o
        except Exception:
            return str(o)
    with open(cf, 'w') as f:
        json.dump(json.loads(json.dumps(linear, default=_safe)), f)
    shutil.copy(pkl, 'bayesianNetworkProto.pickle')
    shutil.copy(cf,  'bayesnet_config_linear.json')
    log(f"saved {pkl} and {cf} (also copied to default names)")

    # Quick diagnostic
    try:
        import subprocess
        t0 = time.time()
        subprocess.run(['python3', 'scripts/diagnose_architecture.py'],
                       capture_output=True, text=True, timeout=900)
        log(f"diagnose {time.time()-t0:.1f}s")
    except Exception as e:
        log(f"diagnose skipped: {e}")

    log("Done. Next steps:")
    log("  python scripts/objective_rr_comparison_test.py --label {0} --pickle {1} --config {2}".format(LABEL, pkl, cf))
    log("  python scripts/extract_results.py {0} {1} {2}".format(LABEL, pkl, cf))

if __name__ == '__main__':
    main()
