naive_types = {
    'naive_0_nhanes_explicit',
    'naive_0_nhanes_explicit_average',
    'naive_0_nhanes_quartile',
    'naive_0_nhanes_quartile_average',
}

prior_types = {
    'discrete_priors',
    'discrete_nhanes_explicit',
    'discrete_nhanes_quartile',
    'discrete_nhanes_quartile_average',
} | naive_types  # naive_0 still acts as prior when no parent specified


dependency_types = {
    'dependency_nhanes_explicit',
    'dependency_nhanes_quartile',
    'dependency_nhanes_explicit_average',
    'dependency_nhanes_quartile_average',
    'dependency_priors',
    'dependency_distal',
    'all_of',
    'any_of',
    'avg',
    'if_then_else',
}


stats_recalculation_types = {
    'is_a',
    'equivalent_to',
    'equivalent_distal',
    'subsumes',
}


quartile_priors_types = {
    'discrete_nhanes_quartile',
    'discrete_nhanes_quartile_average',
    'naive_0_nhanes_quartile',
    'naive_0_nhanes_quartile_average',
}


explicit_types = {
    'discrete_nhanes_explicit',
    'naive_0_nhanes_explicit',
    'naive_0_nhanes_explicit_average',
    'dependency_nhanes_explicit',
    'dependency_nhanes_explicit_average',
}


types_rename = {
    'dependency_nhanes_explicit': 'dependency',
    'dependency_nhanes_explicit_average': 'dependency',
    'dependency_nhanes_quartile': 'dependency',
    'dependency_priors': 'dependency',
    'dependency_distal': 'dependency',

    'discrete_priors': 'discrete',
    'discrete_nhanes_explicit': 'discrete',
    'naive_0_nhanes_explicit': 'discrete',
    'naive_0_nhanes_explicit_average': ' discrete',
    'discrete_nhanes_quartile': 'discrete',
    'naive_0_nhanes_quartile': 'discrete',
    'naive_0_nhanes_quartile_average': 'discrete',
    'discrete_nhanes_quartile_average': 'discrete',
}