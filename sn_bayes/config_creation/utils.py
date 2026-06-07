def is_int(inp_str: str) -> bool:
    try:
        int(inp_str)
    except ValueError:
        return False
    return True


def is_float(inp_str: str) -> bool:
    try:
        float(inp_str)
    except ValueError:
        return False
    return True


def _add_subnodes(dependency_data: dict, added: dict):
    for name, data in dependency_data.items():
        if name in added:
            continue
        if 'INPUTS' not in data:
            added[name] = data
        else:
            added = _add_subnodes(data['INPUTS'], added)
            data.pop('INPUTS')
            added[name] = data
    return added


def linearize_config(config: dict):
    """Flatten a nested config into a linear dict of nodes.

    Accepts the full config dict (with 'anomaly_data' and 'dependency_data' keys).
    Returns a new config dict with flattened dependency_data.
    """
    if 'dependency_data' in config:
        dep_data = config['dependency_data']
    else:
        dep_data = config

    added = {}
    for variable_name, dependency_data in dep_data.items():
        if 'INPUTS' in dependency_data:
            added = _add_subnodes(dependency_data['INPUTS'], added)
            dependency_data.pop('INPUTS')
        added[variable_name] = dependency_data

    if 'dependency_data' in config:
        return {'anomaly_data': config.get('anomaly_data', {}), 'dependency_data': added}
    return added


def show_tree(node_name: str, node_data: dict, indent: int = 0):
    """Print a config node and its children as an indented tree."""
    t = node_data.get('TYPE', '?')
    prefix = '  ' * indent
    priors = node_data.get('PRIORS', node_data.get('OUTVARS', ''))
    if isinstance(priors, dict):
        priors_str = ', '.join(f'{k}={v:.4f}' for k, v in list(priors.items())[:3])
    elif isinstance(priors, list):
        priors_str = ', '.join(priors[:3])
    else:
        priors_str = str(priors)[:60]
    print(f'{prefix}{node_name} [{t}] ({priors_str})')

    for inv_name, inv_entries in node_data.get('INVARS', {}).items():
        for entry in inv_entries:
            vals = entry.get('VALUE', [])
            stats = entry.get('STATS', {})
            rr = stats.get('relative_risk', '')
            sens = stats.get('sensitivity', '')
            if rr:
                print(f'{prefix}  <- {inv_name} {vals} RR={rr:.3f} +/-{stats.get("plus_minus", 0):.3f}')
            elif sens:
                print(f'{prefix}  <- {inv_name} {vals} sens={sens:.3f} spec={stats.get("specificity", 0):.3f}')
            else:
                print(f'{prefix}  <- {inv_name} {vals}')

    for sub_name, sub_data in node_data.get('INPUTS', {}).items():
        show_tree(sub_name, sub_data, indent + 1)