"""Python port of the Excel M/N formulas from working.xlsx.

Converts OR/HR/SMD/ES/WMD → RR using target-population P0 (col A) and
target-population sd (col B, for ES/WMD only).

M formula: nominal RR.
N formula: plus-minus (approximate uncertainty), computed as
  RR(stat=nominal) − RR(stat = nominal − plus_minus).

Returns string sentinel 'SMD_UNRELIABLE' in both when SMD < 0.1 and the
capped-SMD-derived RR exceeds 5 (Chinn guard).

Mirrors behavior of the Excel formula in
`data/Individual Relations.working.xlsx` as of Apr 20, 2026.
"""
import numpy as np

SMD_CAP = 1.5


def _or_to_rr(OR, p0):
    return OR / (1 - p0 + p0 * OR)


def _hr_to_rr(HR, p0):
    if p0 is None or p0 == 0:
        # Rare-outcome limit: HR ≈ RR. Excel would produce div-by-zero or
        # an error here; returning HR value matches the rare-outcome
        # asymptote lim_{p0→0} (1-e^(HR·ln(1-p0)))/p0 = HR.
        return float(HR)
    return (1 - np.exp(HR * np.log(1 - p0))) / p0


def _rr_from_stat(val, stat_u, p0, sd, apply_smd_cap=True):
    """Compute RR from a single effect-size value under given stat type.

    Matches Excel formula semantics: blank P0 is treated as 0 (rare-outcome
    limit → RR ≈ OR). This preserves compatibility with rows whose col A is
    unset; data_checks separately enforces that OR/HR/etc. rows should have
    A populated.
    """
    if stat_u == 'RR':
        return val  # pass through
    # Excel treats blank A as 0 (rare-outcome limit). Also handle NaN.
    if p0 is None or (isinstance(p0, float) and np.isnan(p0)):
        p0 = 0.0
    if sd is not None and isinstance(sd, float) and np.isnan(sd):
        sd = None
    if stat_u in ('ES', 'WMD'):
        if sd is None or sd == 0: return None
        smd = val / sd
        OR = np.exp(1.81 * smd)
        return _or_to_rr(OR, p0)
    elif stat_u == 'SMD':
        capped = min(val, SMD_CAP) if apply_smd_cap else val
        OR = np.exp(1.81 * capped)
        return _or_to_rr(OR, p0)
    elif stat_u == 'OR':
        return _or_to_rr(val, p0)
    elif stat_u == 'HR':
        return _hr_to_rr(val, p0)
    else:
        return val  # unknown stat: pass through


def compute_rr_and_pm(stat, C, D, A, B):
    """Compute (RR, plus_minus) mirroring the Excel M and N formulas.

    Args:
        stat: 'OR', 'HR', 'SMD', 'ES', 'WMD', 'RR', or None/''
        C: stat value (effect size)
        D: plus_minus (uncertainty in effect size)
        A: P0 (target baseline probability)
        B: sd (target population sd, for ES/WMD)

    Returns:
        (RR, PM) as floats, or ('SMD_UNRELIABLE', 'SMD_UNRELIABLE'), or
        (None, None) when inputs are incomplete.
    """
    if stat is None or stat == '' or C is None:
        return (None, None)
    if isinstance(C, float) and np.isnan(C):
        return (None, None)
    stat_u = stat.strip().upper()

    # SMD unreliable guard: checked against the capped-SMD-derived RR.
    if stat_u == 'SMD' and A is not None and A < 0.1:
        capped_rr = _rr_from_stat(C, stat_u, A, B, apply_smd_cap=True)
        if capped_rr is not None and capped_rr > 5:
            return ('SMD_UNRELIABLE', 'SMD_UNRELIABLE')

    RR = _rr_from_stat(C, stat_u, A, B)
    if RR is None:
        return (None, None)

    # Plus-minus via (C - D) perturbation
    if D is None or (isinstance(D, float) and np.isnan(D)):
        return (RR, None)

    if (C - D) > SMD_CAP and stat_u == 'SMD':
        # Excel rule: when (C-D) > 1.5 the SMD cap kicks in both sides,
        # so the perturbation doesn't reach zero. Approximate by ratio.
        PM = RR * D / C if C else None
    else:
        lower_RR = _rr_from_stat(C - D, stat_u, A, B)
        if lower_RR is None or lower_RR == 0:
            PM = None
        else:
            PM = RR - lower_RR

    return (RR, PM)


def is_float_rr(v):
    """Return True if v is a usable float RR (not sentinel, not NaN)."""
    if v is None: return False
    if isinstance(v, str):
        return False  # SMD_UNRELIABLE etc.
    try:
        f = float(v)
        return not np.isnan(f) and not np.isinf(f)
    except (TypeError, ValueError):
        return False
