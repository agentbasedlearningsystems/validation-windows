import logging
import time

import pandas as pd


ZERO_CUTOFF_THRESHOLD = 1e-16    # Threshold to replace very small probabilities with zeros


def replace_missing(data: pd.DataFrame, nhanes: pd.DataFrame, replace_value: int = -1) -> pd.DataFrame:
    # Replace missing with -1 so after table pivot we treat added NaN(NHANES category) and missing(data is not available for seqn) differently
    for i in range(1, 6):
        codes_with_missing = data[data[f'index{i}'].str.contains('missing', na=False)]['code'].unique()
        for code in codes_with_missing:
            nhanes.loc[(nhanes['var_code'] == code) & (nhanes['value'].isna()), 'value'] = replace_value
    return nhanes


def zero_cutoff_values(nhanes: pd.DataFrame, threshold: float = ZERO_CUTOFF_THRESHOLD):
    # Replace very small values with zero inplace
    nhanes['turn_to_zero'] = nhanes['value'].apply(lambda e: (e > 0) & (e < threshold))
    nhanes.loc[nhanes['turn_to_zero'], 'value'] = 0
    nhanes.drop('turn_to_zero', axis=1, inplace=True)
    

def preprocess_nhanes(nhanes: pd.DataFrame, spreadsheet_data: pd.DataFrame) -> pd.DataFrame:
    logging.info('Started NHANES data preprocessing..')
    start = time.time()
    
    var_codes = spreadsheet_data['code'].dropna().unique()
    nhanes = nhanes.loc[nhanes['var_code'].isin(var_codes), :].reset_index(drop=True)
    nhanes.drop(['variable_name', 'file_name'], axis='columns', inplace=True)
    
    nonna_types = spreadsheet_data[~spreadsheet_data['Type'].isna()]
    codes_average = nonna_types[nonna_types['Type'].str.endswith('average')]['code'].dropna().unique()
    nhanes = replace_missing(spreadsheet_data, nhanes, replace_value=-1)
    zero_cutoff_values(nhanes)
    nhanes.drop(labels='file_name', axis='columns', inplace=True, errors='ignore')
    nhanes.dropna(axis='rows', subset='value', inplace=True)
    
    average_rows = nhanes[nhanes['var_code'].isin(codes_average)]
    averaged = average_rows.groupby(['seqn', 'var_code']).agg('mean').reset_index()
    
    non_average_rows = nhanes[~nhanes['var_code'].isin(codes_average)]
    non_average_unique = non_average_rows.groupby(['seqn', 'var_code'])["value"].agg(lambda e: e.mode()[0]).reset_index()
    
    nhanes = pd.concat([averaged, non_average_unique], ignore_index=True)
    logging.info(f'Finished preprocessing NHANES data. It took {(time.time()-start):.2f} seconds.')
    
    return nhanes