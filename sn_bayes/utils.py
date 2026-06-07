import json
import copy
import typing
import itertools
import time
import logging

import torch
import pandas as pd
import numpy as np
from dateutil.parser import parse

# Re-exported for convenience - actual implementation in sn_bayes.pickle_compat
# (a stdlib-only module so the reviewer-facing demo scripts can use it without
# triggering heavy imports like pandas / torch).
from sn_bayes.pickle_compat import smart_load_pickle

from pomegranate.distributions import (
    Categorical,
    ConditionalCategorical,
)

from sn_bayes.bayesnet_wrapper import BayesianNetworkWrapper
import sn_service.service_spec.bayesian_pb2
from sn_service.service_spec.bayesian_pb2 import Query  # type: ignore

T = typing.TypeVar("T")

class OrderedSet(typing.MutableSet[T]):
    """A set that preserves insertion order by internally using a dict."""

    def __init__(self, iterable: typing.Iterator[T]):
        self._d = dict.fromkeys(iterable)

    def add(self, x: T) -> None:
        self._d[x] = None

    def discard(self, x: T) -> None:
        self._d.pop(x, None)

    def __contains__(self, x: object) -> bool:
        return self._d.__contains__(x)

    def __len__(self) -> int:
        return self._d.__len__()

    def __iter__(self) -> typing.Iterator[T]:
        return self._d.__iter__()

    def __str__(self):
        return f"{{{', '.join(str(i) for i in self)}}}"

    def __repr__(self):
        return f"<OrderedSet {self}>"



def create_spreadsheet(path):

    def _upper(e):
        if not pd.isna(e):
            return e.upper()
        return e


    data = pd.read_excel(path, sheet_name='all worksheet', header=0)

    for i in range(1, 6):
        data[f"index{i}"] = data[f"index{i}"].astype(str)

    #data.drop(labels=['index', 'counter'], axis='columns', inplace=True)
    data.dropna(axis='index', how='all', inplace=True)
    #data = data[~data['output'].isna()]

    data_inputnotna = data[~data['input'].isna()]
    data.loc[data_inputnotna[data_inputnotna['input'].str.startswith('fixme')].index, 'input'] = np.nan

    data.replace('nan', np.nan, inplace=True)    # Pandas parses missing values as str 'nan' in object dtype columns instead of NaN

    data = data.applymap(lambda e: e.strip() if isinstance(e, str) else e)
    data['code'] = data['code'].apply(_upper)

    return data



def create_pivoted(spreadsheet_data, nhanes_path):

    import os
    
    # NHANES PREPROCESS
    ZERO_CUTOFF_THRESHOLD = 1e-16


    def replace_missing(data: pd.DataFrame, nhanes: pd.DataFrame, replace_value: int = -1) -> pd.DataFrame:
        # Replace missing with -1 so after pivot we treat added NaN(NHANES category) and missing(data is not available for seqn) differently
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
        print('Started NHANES data preprocessing..')
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
        print(f'Finished preprocessing NHANES data. It took {time.time()-start} seconds.')
        return nhanes


    dfs = []
    for fname in os.listdir(nhanes_path):
        df = None
        if fname.endswith('csv'):
            df = pd.read_csv(os.path.join(nhanes_path, fname))
        elif fname.endswith('parquet'):
            df = pd.read_parquet(os.path.join(nhanes_path, fname))
        if df is not None:
            df.rename(lambda e: e.lower(), axis=1, inplace=True)
            dfs.append(df)

    nhanes = pd.concat(dfs, axis=0)

    nhanes = preprocess_nhanes(nhanes, spreadsheet_data)
    pivoted = nhanes.pivot_table(columns='var_code', index=['seqn'])
    pivoted.columns = pivoted.columns.droplevel(level=0)

    return pivoted
    

      

def _lookup_range_value(x, ranges_map):
   # First check NaN case
   if pd.isna(x) and -1 in ranges_map:
       return ranges_map[-1]
   
   # Then check ranges
   for r, value in ranges_map.items():
       if isinstance(r, tuple) and r[0] <= x <= r[1]:
           return value
       elif r == x:
           return value
   return np.nan

def nhanes_to_longevity(pivoted, spreadsheet):
   valid_codes = set(spreadsheet['code'].dropna())
   result = pivoted.reset_index()
   keep_cols = ['seqn'] + [col for col in pivoted.columns if col in valid_codes]
   result = result[keep_cols]
   
   var_map = dict(zip(spreadsheet['code'], spreadsheet['output']))
   
   for code in keep_cols[1:]:
       type_row = spreadsheet[spreadsheet['code'] == code]['Type'].iloc[0]
       output_name = var_map[code]
       
       if pd.isna(type_row):
           continue
           
       if 'quartile' in type_row.lower():
           mask = result[code].notna()
           if mask.any():
               values = result.loc[mask, code]
               quartiles = np.percentile(values, [25, 50, 75])
               
               def assign_quartile(x):
                   if pd.isna(x):
                       return np.nan
                   if x <= quartiles[0]:
                       return f'{output_name}_quartile_1'
                   elif x <= quartiles[1]:
                       return f'{output_name}_quartile_2'
                   elif x <= quartiles[2]:
                       return f'{output_name}_quartile_3'
                   else:
                       return f'{output_name}_quartile_4'
               
               result.loc[mask, code] = values.apply(assign_quartile)
       else:
           ranges_map = {}
           group = spreadsheet[spreadsheet['code'] == code]
           for _, row in group.iterrows():
               for i in range(1, 6):
                   if pd.isna(row[f'index{i}']):
                       continue
                   ranges = extract_values(str(row[f'index{i}'])) if isinstance(row[f'index{i}'], str) else [row[f'index{i}']]
                   value = str(row[f'value{i}']).strip()
                   for r in ranges:
                       if isinstance(r, tuple):
                           ranges_map[(r[0], r[1])] = value
                       else:
                           ranges_map[r] = value

           result[code] = result[code].apply(lambda x: _lookup_range_value(x, ranges_map))
   
   result = result.rename(columns=lambda x: var_map.get(x, x))
   return result


   
        

def extract_values(values_str: str) -> list:
    values_str = values_str.replace(' ', '')
    values = []
    for elem in values_str.split(','):
        if '-' in elem:
            values.append(tuple(float(e) for e in elem.split('-')))
        elif elem == 'missing':
            values.append(-1)
        else:
            values.append(float(elem))
    return values



def cpt_cell_from_data(output_var_dict, input_var_dict, longevity):
   # Filter rows matching input conditions
   mask = pd.Series(True, index=longevity.index)
   for var, val in input_var_dict.items():
       mask &= longevity[var].str.startswith(val, na=False)
   
   matching_rows = longevity[mask]
   total = len(matching_rows)
   
   if total == 0:
       return {list(output_var_dict.values())[0]: 0}
   
   # Calculate probability for specified output value
   output_var, output_val = list(output_var_dict.items())[0]
   prob = (matching_rows[output_var].str.startswith(output_val, na=False)).sum() / total
       
   return {output_val: prob}

def percentage_cpt_cell(output_var_dict, input_var_dict, longevity):
   # Filter rows matching all conditions
   mask = pd.Series(True, index=longevity.index)
   
   for var, val in {**output_var_dict, **input_var_dict}.items():
       mask &= longevity[var].str.startswith(val, na=False)
       
   matching_rows = longevity[mask]
   total_population = len(longevity)
   
   if total_population == 0:
       return 0
       
   return len(matching_rows) / total_population * 100    



def var_deps(bayesianNetwork) -> dict:
    var_deps = {}
    for dist in bayesianNetwork.discreteDistributions:
        var_deps[dist.name] = []
    for table in bayesianNetwork.conditionalProbabilityTables:
        #print ("table: {}".format(table.name))
        var_deps[table.name]= []
        for var in table.randomVariables:
            #print(var.name)
            var_deps[table.name].append(var.name)
    return var_deps


def fillcols(var_dict):
    var_deps = copy.deepcopy(var_dict)
    #go through the dict and create a lists of lists , 
    #where a var goes in the list only if all of the variable upon which 
    #it depends are in the previous lists.
    tree_list = []
    initial_len = len(var_deps)
    final_len=0
    while len(var_deps)>  0 and final_len < initial_len:
        next_level = []
        deletes =[]
        for var,deplist in var_deps.items():
            all_found = True
            for d in deplist:
                found = False
                for l in tree_list:
                    if d in l:
                        found = True
                if not found:
                    all_found = False
            if all_found:
                deletes.append(var)
                next_level.append(var)
        for delete in deletes:
            var_deps.pop(delete)
        final_len = len(var_deps)
        tree_list.append(next_level)
    return(tree_list)    

  
def make_tree(bayesianNetwork, connections = True):
    variable_dependencies = var_deps(bayesianNetwork)
    #print(variable_dependencies)
    tree = fillcols(variable_dependencies)
    #print(tree)
    newtree = []
    for ply in tree:
        newl=[]
        for v in ply: 
            newstr = v + "("
            #print(v)
            deps = variable_dependencies[v]
            for d in deps:
                newstr += d
                newstr += ","
            newstr = newstr[:-1]+ ")"
            newl.append(newstr)
        newtree.append(newl)
    df_dict ={}
    for i,l in enumerate(newtree):
        key = "level"+ str(i)
        df_dict[key] = l  
    df = pd.DataFrame.from_dict(df_dict, orient='index').T
    if not connections:
        df=df.replace(to_replace=r"([0-9a-zA-Z\-_\.]+)(.*)", value=r"\1", regex=True) 
    #df.str.replace(r'[^0-9a-zA-Z\-_]+', '', regex=True)  
    return df


def complexity_check(bayesianNetwork,
#todo: check obscenity                
        max_size_in_bytes = 256000000,             
        allowed_number_nodes = 5000,
        allowed_number_variables=9, allowed_number_variable_values= 15):

        passes = True
        messages = []
        size = bayesianNetwork.ByteSize()
        if size > max_size_in_bytes:
                passes = False
                messages.append("This net's size is {0} bytes while max size is {1} bytes".format(size,max_size_in_bytes))
        else:
                var_val_positions = get_var_val_positions(bayesianNetwork)
                num_nodes = len(var_val_positions)
                if num_nodes > allowed_number_nodes:
                        passes = False
                        messages.append("This net's number of nodes is {0} while allowed number is {1}".format(num_nodes,allowed_number_nodes))

                lenlist = [len(l) for l in list(var_val_positions.values())]
                maxvarval=  max(lenlist) if len(lenlist) > 0  else 0
                if maxvarval > allowed_number_variable_values:
                        passes = False
                        messages.append("This net's max number of variable values is {0} while allowed number is {1}".format(maxvarval,allowed_number_variable_values))
                row_test = True        
                for table in bayesianNetwork.conditionalProbabilityTables:
                        numvars = len(table.conditionalProbabilityRows[0].randomVariableValues)-1
                        if numvars > allowed_number_variables:
                                passes = False
                                messages.append("Variable {0} has {1} dependancies while the allowed number is {2}".format(table.name, numvars,allowed_number_variables))
        errors = '\n'.join(messages)
        return (passes,errors)

        

def get_var_positions(bayesianNetwork):
    var_positions = {}
    check_for_repeats = set()

    for i, dist in enumerate(bayesianNetwork.discreteDistributions):
        var_positions[dist.name] = i
        #print ("dist.name") 
        #print (dist.name)
        #print ("i")
        #print (i)
        if dist.name in check_for_repeats:
            print(f"double instance of {dist.name}") 
        else:
            check_for_repeats.add(dist.name)
    start = len(var_positions)
    #print ("start")
    #print(start)
    for j, table in enumerate(bayesianNetwork.conditionalProbabilityTables):
        var_positions[table.name] = j + start
        #print("table.name")
        #print(table.name)
        #print("j")
        #print(j)
        if table.name in check_for_repeats:
            print(f"double instance of {table.name}") 
        else:
            check_for_repeats.add(table.name)

    return var_positions



def get_var_val_positions(bayesianNetwork):
    var_val_positions = {}
    for dist in bayesianNetwork.discreteDistributions:
        var_val_positions[dist.name] = {}
        for pos, var in enumerate(dist.variables):
            var_val_positions[dist.name][var.name] = pos
            
    for table in bayesianNetwork.conditionalProbabilityTables:
        var_val_positions[table.name] = {}
        for pos, var in enumerate(table.outvars):
            var_val_positions[table.name][var.name] = pos
    return var_val_positions



def get_internal_var_val_positions(bayesianNetwork):
        var_val_positions = {}
        for j,table in enumerate(bayesianNetwork.conditionalProbabilityTables):
                var_val_positions[table.name] ={}
                for pos,var in enumerate(table.outvars):
                        var_val_positions[table.name][var.name] = pos
        return var_val_positions


def get_var_names(bayesianNetwork):
    var_names = {}
    for i, dist in enumerate(bayesianNetwork.discreteDistributions):
        var_names[i] = dist.name
        
    start = len(var_names)
    for j, table in enumerate(bayesianNetwork.conditionalProbabilityTables):
        var_names[j+start] = table.name
        
    return var_names


def get_var_val_names(bayesianNetwork):
    var_val_names = {}
    for dist in bayesianNetwork.discreteDistributions:
        var_val_names[dist.name] = {}
        for pos, var in enumerate(dist.variables):
                var_val_names[dist.name][pos] = var.name
    
    for table in bayesianNetwork.conditionalProbabilityTables:
        var_val_names[table.name] = {}
        for pos, var in enumerate(table.outvars):
                var_val_names[table.name][pos] = var.name
    return var_val_names

def parse_net(query, bayesianNetwork):
    var_val_names = get_var_val_names(bayesianNetwork)
    var_names = get_var_names(bayesianNetwork)
    var_val_positions = get_var_val_positions(bayesianNetwork)
    reverse_val_dict = {}
    evidence_dict = {}
    anomaly_tuples = {}
    
    for e in query.evidence:
        if e.var_num in var_names and var_names[e.var_num] in var_val_names and e.response in var_val_names[var_names[e.var_num]]:
            var_name = var_names[e.var_num]
            var_val_name = var_val_names[var_name][e.response]
            evidence_dict[var_name] = var_val_name 
    
    outvar_list = [var_names[o.var_num] for o in query.outvars if o.var_num in var_names]
    explainvars = [var_names[o.var_num] for o in query.explainvars if o.var_num in var_names]
    
    # Fixed reversevals parsing - vals is already a repeated field (list-like)
    for e in query.reversevals:
        if e.var_num in var_names:
            var_name = var_names[e.var_num]
            # Convert the repeated field to a regular Python list
            # If vals is empty, it will be an empty list []
            reverse_val_dict[var_name] = list(e.vals)

    for s in query.timeseries:
        anomaly_tuples[var_names[s.var_num]] = [(t.val, t.interval) for t in s.timevals]
    
    anomaly_params_dict = {}
    for o in bayesianNetwork.anomalies:
        anomaly_params_dict[o.varName] = {}
        anomaly_params_dict[o.varName]['low'] = o.low
        anomaly_params_dict[o.varName]['high'] = o.high
        anomaly_params_dict[o.varName]['low_percent'] = o.low_percent
        anomaly_params_dict[o.varName]['high_percent'] = o.high_percent
        anomaly_params_dict[o.varName]['is_all'] = o.is_all
        anomaly_params_dict[o.varName]['n_steps'] = o.n_steps
        anomaly_params_dict[o.varName]['step_size'] = o.step_size
        anomaly_params_dict[o.varName]['c'] = o.c
        anomaly_params_dict[o.varName]['n'] = o.n
        anomaly_params_dict[o.varName]['side'] = o.side
        anomaly_params_dict[o.varName]['window'] = o.window
        anomaly_params_dict[o.varName]['detectors'] = []
        for d in o.detectors:
            anomaly_params_dict[o.varName]['detectors'].append(d.name)
    
    switch = query.switch
    baseline = readable(bayesianNetwork, query.baseline) if query.baseline else None
    include_list = [var_names[o.var_num] for o in query.include_list if o.var_num in var_names]
    
    return (evidence_dict, outvar_list, explainvars, reverse_val_dict, anomaly_tuples, anomaly_params_dict, include_list, baseline, switch)
""" 

def parse_net(query, bayesianNetwork):
        #print("parse_net query")
        #print(query)
        var_val_names = get_var_val_names(bayesianNetwork)
        var_names = get_var_names(bayesianNetwork)
        var_val_positions = get_var_val_positions(bayesianNetwork)
        reverse_val_dict = {}
        evidence_dict = {}
        anomaly_tuples = {}
        for e in query.evidence:
            if e.var_num in var_names and var_names[e.var_num] in var_val_names and e.response in var_val_names[ var_names[e.var_num]]:
                var_name = var_names[e.var_num]
                var_val_name = var_val_names[var_name][e.response]
                evidence_dict[var_name] = var_val_name 
        outvar_list =[var_names[o.var_num] for o in query.outvars if o.var_num in var_names]
        explainvars =[var_names[o.var_num] for o in query.explainvars if o.var_num in var_names]
        for e in query.reversevals:
            if e.var_num in var_names and var_names[e.var_num] in var_val_positions  and all(item in var_val_positions[var_names[e.var_num]] for item in e.vals): 
                reverse_val_dict[var_names[e.var_num]] = [item for item in e.vals]


        for s in query.timeseries:
            anomaly_tuples[var_names[s.var_num]] = [(t.val,t.interval)for t in s.timevals]
        anomaly_params_dict = {}
        for o in bayesianNetwork.anomalies:
            anomaly_params_dict[o.varName]= {}
            anomaly_params_dict[o.varName]['low'] = o.low
            anomaly_params_dict[o.varName]['high'] = o.high
            anomaly_params_dict[o.varName]['low_percent'] = o.low_percent
            anomaly_params_dict[o.varName]['high_percent'] = o.high_percent
            anomaly_params_dict[o.varName]['is_all']= o.is_all
            anomaly_params_dict[o.varName]['n_steps']= o.n_steps
            anomaly_params_dict[o.varName]['step_size'] = o.step_size
            anomaly_params_dict[o.varName]['c'] = o.c
            anomaly_params_dict[o.varName]['n'] = o.n
            anomaly_params_dict[o.varName]['side'] = o.side
            anomaly_params_dict[o.varName]['window'] = o.window
            anomaly_params_dict[o.varName]['detectors'] = []
            for d in o.detectors:
                anomaly_params_dict[o.varName]['detectors'].append(d.name)
        switch=query.switch
        baseline= readable(bayesianNetwork,query.baseline) if query.baseline else None
        include_list = [var_names[o.var_num] for o in query.include_list if o.var_num in var_names]
        return(evidence_dict, outvar_list, explainvars, reverse_val_dict,anomaly_tuples,anomaly_params_dict,include_list,baseline,switch)
 """
from adtk.data import validate_series

def quan(s,percentile):
    sr = s.quantile(percentile)
    return sr[0]

def std(s):
    sr = s.std()
    return sr

def iqr(s,c):
        sr = s.quantile(0.25)
        q1 = sr[0]
        sr = s.quantile(0.75)
        q3=sr[0]
        iqr = q3 - q1

        abs_low = (
            (
                q1
                - iqr
                * (c if (not isinstance(c, tuple)) else c[0])
            )
            if (
                (c if (not isinstance(c, tuple)) else c[0])
                is not None
            )
            else -float("inf")
        )
        abs_high = (
            q3
            + iqr * (c if (not isinstance(c, tuple)) else c[1])
            if (
                (c if (not isinstance(c, tuple)) else c[1])
                is not None
            )
            else float("inf")
        )
        #print("s.max()")
        #print (s.max())
        #print("s.min()")
        #print(s.min())
        if abs_high > s.max()[0] and abs_low < s.min()[0]:
            print ("no iqr anomalies")
        return(abs_low,abs_high)


def detect_anomalies(anomaly_tuples,bayesianNetwork,anomaly_params):
        #print ("anomaly_tuples")
        #print(anomaly_tuples)
        #print ("anomaly_params")
        #print (anomaly_params)
        evidence = {}
        anomaly_dict = {}
        signal_dict ={}
        combined_signals={}
        fitted={}
        s_dict = {}
        anomaly_out = {}
        anomaly_out['signal'] = {}
        anomaly_out['anomalies']= {}
        anomaly_out['fitted'] = {}
        anomaly_out['evidence'] = {}
        var_val_names = get_var_val_names(bayesianNetwork)
        var_names = get_var_names(bayesianNetwork)
        df = pd.DataFrame(columns=['time','value'])
        combined_df = pd.DataFrame(columns = ['time','value'])
        for var, time_tuples in anomaly_tuples.items():
            anomaly_dict[var]={}
            combined_signals[var] ={}
            fitted[var] = {}
            last_interval = 1
            dti = pd.to_datetime('1/1/2018')
            for tup in time_tuples:
                val = float(tup[0])
                interval = float(tup[1])
                if interval == 0.0:
                    interval = last_interval
                last_interval = interval
                if interval is not None and val is not None:
                    dti += pd.Timedelta(f'{interval} seconds')
                    if dti is not pd.NaT:
                        df = pd.concat([df, pd.DataFrame([{'time': dti, 'value': val}])], ignore_index=True)
            if not df.empty:
                df=df.set_index('time')
                s = validate_series(df)
                if pd.NaT in s.index:
                    s = s.drop(pd.NaT)
                signal_dict[var]=s
                #print("s")
                #print(s)
                s_dict[var]=[]
                last_dti = None
                interval = 0
                for index, row in signal_dict[var].iterrows():
                    if last_dti is not None:
                        this_dti = index
                        diff = this_dti - last_dti
                        interval = float(diff.total_seconds())
                        #print ("interval")
                        #print (interval)
                    last_dti = index
                    s_dict[var].append ((interval,row['value']))      
                detector_set = set(anomaly_params[var]["detectors"])
                for detector in detector_set:
                    if detector == "AutoregressionAD":
                        try:
                            from adtk.detector import AutoregressionAD
                            autoregression_ad = AutoregressionAD(n_steps=anomaly_params[var]["n_steps"], 
                                    step_size=anomaly_params[var]["step_size"], c=anomaly_params[var]["c"])
                            anomaly_dict[var][detector]  = autoregression_ad.fit_detect(s)
                            if  "low" not in fitted[var]:
                                fitted[var]["low"],fitted[var]["high"] = iqr(s,anomaly_params[var]["c"])                            
                        except RuntimeError as e:
                            print(f'AutoregressionAD-{var} RuntimeError')
                            print(e)
                        except ValueError as e:
                            print(f'AutoregressionAD-{var} ValueError')
                            print(e)
                    elif detector == "LevelShiftAD":
                        try:
                            from adtk.detector import LevelShiftAD
                            ls_ad = LevelShiftAD(c=anomaly_params[var]["c"],
                                    side=anomaly_params[var]["side"],window=anomaly_params[var]["window"])
                            anomaly_dict[var][detector] = ls_ad.fit_detect(s)
                            if "low" not in fitted[var]:
                                fitted[var]["low"],fitted[var]["high"] = iqr(s,anomaly_params[var]["c"])                            
                        except RuntimeError as e:
                            print(f'LevelShiftAD-{var} RuntimeError')
                            print(e)
                        except ValueError as e:
                            print(f'LevelShiftAD-{var} ValueError')
                            print(e)

                    elif detector == "InterQuartileRangeAD":
                        try:
                            from adtk.detector import InterQuartileRangeAD
                            iqr_ad = InterQuartileRangeAD(c=anomaly_params[var]["c"])
                            anomaly_dict[var][detector] = iqr_ad.fit_detect(s)
                            if "low" not in fitted[var]:
                                fitted[var]["low"],fitted[var]["high"] = iqr(s,anomaly_params[var]["c"])                            
                        except RuntimeError as e:
                            print(f'InterQuartileRangeAD-{var} RuntimeError')
                            print(e)
                        except ValueError as e:
                            print(f'InterQuartileRangeAD-{var} ValueError')
                            print(e)


                    elif detector == "QuantileAD":
                        try:
                            from adtk.detector import QuantileAD
                            low= None if anomaly_params[var]['side'] == "positive" else anomaly_params[var]['low_percent']
                            high=None if anomaly_params[var]['side'] == 'negative' else anomaly_params[var]['high_percent']
                            quantile_ad = QuantileAD(high=high, low=low)
                            anomaly_dict[var][detector] = quantile_ad.fit_detect(s)
                            fitted[var]['low_percent'] = quan(s,anomaly_params[var]['low_percent'])
                            fitted[var]['high_percent'] = quan(s,anomaly_params[var]['high_percent'])

                        except RuntimeError as e:
                            print(f'QuantileAD-{var}')
                            print(e)
                        except ValueError as e:
                            print(f'QuantileAD-{var}')
                            print(e)

                    elif detector == "ThresholdAD":          
                        try:
                            from adtk.detector import ThresholdAD
                            low= None if anomaly_params[var]['side'] == "positive" else anomaly_params[var]['low']
                            high=None if anomaly_params[var]['side'] == 'negative' else anomaly_params[var]['high']
                            threshold_ad = ThresholdAD(high=high, low=low)
                            anomaly_dict[var][detector] = threshold_ad.detect(s)

                        except RuntimeError as e:
                            print(f'ThresholdAD-{var}')
                            print(e)
                        except ValueError as e:
                            print(f'ThresholdAD-{var}')
                            print(e)

                    elif detector == "VolatilityShiftAD":
                        try:
                            from adtk.detector import VolatilityShiftAD
                            volatility_shift_ad = VolatilityShiftAD(c=anomaly_params[var]['c'], side=anomaly_params[var]['side'],window=anomaly_params[var]['window'])
                            anomaly_dict[var][detector] = volatility_shift_ad.fit_detect(s)
                            fitted[var]['std'] = std(s)
                            #print (f"fitted[{var}]['std']")
                            #print (fitted[var]['std'])
               
                        except RuntimeError as e:
                            print(f'VolatilityShiftAD-{var}')
                            print(e)
                        except ValueError as e:
                            print(f'VolatilityShiftAD-{var}')
                            print(e)



                firsttime = True
                for detector,df in anomaly_dict[var].items():
                    if firsttime:
                        combined_df = df
                        combined_df = combined_df.rename(columns={'value': detector})
                        firsttime = False
                    else:
                        combined_df[detector] = df['value']
                combined_df['value']=combined_df.all(axis=1) if anomaly_params[var]['is_all'] else combined_df.any(axis=1)

                is_anomalous = combined_df[['value']].tail(anomaly_params[var]["n"])['value'].any()
                evidence[var] = var_val_names[var][0] if is_anomalous else var_val_names[var][1]
                #print("anomaly_dict")
                #print(anomaly_dict)
                #print("combined_df")
                #print(combined_df)
                temp = combined_df[['value']].to_records()
                #print("temp")
                #print(temp)
                anomaly_out['anomalies'][var]= [tup[1] for tup  in temp]
                #anomaly_out['signal'][var] = list(signal_dict[var].to_records())
                anomaly_out['signal'][var]=s_dict[var]
                #print (f"anomaly_out['anomalies'][{var}][0]:")
                #print (anomaly_out['anomalies'][var][0])
                anomaly_out['fitted'][var] = fitted[var]
                anomaly_out['evidence'][var] = evidence[var]
        return (anomaly_out)


def readable(bayesianNetwork,response):

        var_val_names = get_var_val_names(bayesianNetwork)
        var_names = get_var_names(bayesianNetwork)
        readable = {}
        for answer in response.varAnswers:
            var = var_names[answer.var_num]
            readable[var]={}
            #print("answer")
            #print(answer)
            for state in answer.varStates:
                readable[var][var_val_names[var][state.state_num]]= state.probability
        
        #print ("readable")
        #print(readable)
        return readable
                 
def create_query (bayesianNetwork,evidence_dict,outvar_list,reverse_val_dict,explainvars=[],
        timeseries = [], include_list = [], baseline = None, switch= ""):
        #create a query for the test service

        #print("evidence_dict")
        #print(evidence_dict)
        #print("outvar_list")
        #print(outvar_list)
        query = Query()
        query.switch = switch
        if baseline:
                query.baseline.CopyFrom( baseline)


        var_val_positions = get_var_val_positions(bayesianNetwork)
        #print ("var_val_positions")
        #print (var_val_positions)
        var_positions = get_var_positions(bayesianNetwork)
        #print ("var_positions")
        #print (var_positions)
        for v in include_list:
                if v in var_positions:
                        outvar = query.include_list.add()
                        outvar.var_num = var_positions[v]

                        #print ("outvar")
        for k,v in evidence_dict.items():
                if k in var_positions and k in var_val_positions and v in var_val_positions[k]:
                        evidence= query.evidence.add()
                        evidence.var_num = var_positions[k]
                        evidence.response = var_val_positions[k][v]
                        #print("evidence")
                        #print(evidence)
        for v in outvar_list:
                if v in var_positions:
                        outvar = query.outvars.add()
                        outvar.var_num = var_positions[v]
                        #print ("outvar")
                        #print(outvar)
        for v in explainvars:
                if v in var_positions:
                        explainvar = query.explainvars.add()
                        explainvar.var_num = var_positions[v]
        for k,v in reverse_val_dict.items():
                if k in var_positions and k in var_val_positions and all(item in var_val_positions[k] for item in v):
                        reversevals= query.reversevals.add()
                        reversevals.var_num = var_positions[k]
                        for val in v:
                            reversevallist = reversevals.vals.add()
                            reversevallist.val = val
        for t in timeseries:
            if t["var"] in var_positions:
                    time = query.timeseries.add()
                    time.var_num = var_positions[t["var"]]
                    for q in t["timevals"]:
                        timeseries = time.timevals.add()
                        timeseries.val = q["val"]
                        timeseries.interval = q["interval"]

                    
        return query

def get_template_priors(bayesianNetwork):
        template_priors = {}
        for dist in bayesianNetwork.discreteDistributions:
                template_priors[dist.name]={}
                for var in dist.variables:
                        template_priors[dist.name][var.name] = var.probability
        return template_priors


def predict_proba_adjusted(baked_net, netspec, evidence_list={}, adjust=False):
    var_val_names = get_var_val_names(netspec)
    var_positions = get_var_positions(netspec)
    template_priors = get_template_priors(netspec)
    prior_var = list(template_priors.keys())[0]
    #print ("template_priors")F
    #print (template_priors)
    dist = {}
    answer = {}

    if len(evidence_list) < 1 and adjust:
        for prior_val, prior_prob in template_priors[prior_var].items():
            evidence = {prior_var: prior_val}
            description = baked_net.predict_proba(evidence)
            for dist_name, val_dict in var_val_names.items():
                if dist_name not in dist:
                    dist[dist_name] = {}
               
                try:
                    answer[dist_name] = description[var_positions[dist_name]]
                except AttributeError as e:
                    #print("AttributeError")
                    #print(dist_name)
                    #print(e)
                    pass
                    
                for k, v in evidence.items(): 
                    var_vals = {}
                    for num, val in var_val_names[k].items():
                        #prob = 0.99999 if v == val else 0.00001
                        prob = 1. if v == val else 0.
                        var_vals[val] = prob
                    answer[k] = var_vals

                for dummy, val in val_dict.items():
                    if val not in dist[dist_name]:
                        dist[dist_name][val] = answer[dist_name][val]*prior_prob
                    else:
                        dist[dist_name][val] += answer[dist_name][val]*prior_prob

    else:
        description = baked_net.predict_proba(evidence_list)
        for dist_name, val_dict in var_val_names.items():
            if dist_name not in dist:
                dist[dist_name] = {}
               
            try:
                answer[dist_name] = description[var_positions[dist_name]]
            except AttributeError as e:
                #print("AttributeError")
                #print(dist_name)
                #print(e)
                pass
                
            for k, v in evidence_list.items(): 
                var_vals = {}
                for num, val in var_val_names[k].items():
                    #prob = 0.99999 if v == val else 0.00001
                    prob = 1. if v == val else 0.
                    var_vals[val] = prob
                answer[k] = var_vals
                
            for dummy, val in val_dict.items():
                if val not in dist[dist_name]:
                    try:
                        dist[dist_name][val] = answer[dist_name][val]
                    except KeyError as e:
                        print("KeyError e") 
                        print(e)
                        print("answer")
                        print(answer)
                        print("description")
                        print(description)

    return dist

 
def batch_query(baked_net, netspec, evidence_list, out_var_list):
    answer_list = []
    var_positions = get_var_positions(netspec)
    #print ('evidence_list')
    #print (evidence_list)
    evidence_list = list(evidence_list)
    if "always_true" in var_positions:
        evidence_list["always_true"] = "always_true"

    description = baked_net.predict_proba(evidence_list, max_iterations=20, check_input = False, n_jobs=1)
    #print ("description")
    #print (description)
    for i, evidence in enumerate(evidence_list):
        answer = {}
        for dist_name in out_var_list:
            try:
                answer[dist_name] = description[i][var_positions[dist_name]]
            except AttributeError as e:
                #print(dist_name)
                #print(e)
                pass
        answer_list.append(answer)
    return answer_list



def query(baked_net, netspec, evidence, out_var_list, adjust=False):
    answer = {}
    probs = predict_proba_adjusted(baked_net, netspec, evidence, adjust)

    for dist_name in out_var_list:
        answer[dist_name] = probs[dist_name]
    return answer

       

def explain_why_bad(baked_net, netspec, evidence, explain_list, reverse_vals, internal_query_result=None, include_list = []):
    #print (internal_query_result)
    return explain(baked_net, netspec, evidence, explain_list, reverse_vals, 
                   internal_query_result=internal_query_result, 
                   include_list = include_list, explain_why_bad = True)

def explain_why_good(baked_net, netspec, evidence, explain_list,reverse_vals,  internal_query_result = None, include_list = []):
    adict = dictVarsAndValues(netspec, {})
    return explain(baked_net, netspec, evidence, explain_list,reverse_vals, 
                   internal_query_result=internal_query_result,
                   include_list = include_list, explain_why_bad = False)


def internal_query(baked_net, netspec, evidence, adjust=False):
    var_val_positions = get_var_val_positions(netspec)
    output_list = [var for var, val in var_val_positions.items()]
    result = query(baked_net, netspec, evidence, output_list, adjust)
    return result


def get_hack():
    reverse_dict = {'bmi':[],'bmi_naive':[4], 'dietary_restriction':[1]}
    var_val_dict = {'bmi':{0:'bmi_over_30_obesity',1:'bmi_over_30_obesity_not'},'bmi_naive':{0:'bmi_40_and_over_severe_obesity',1:'bmi_30_to_39_obesity',
                                 2:'bmi_25_to_29_overweight',3:'bmi_19_to_24_normal',4:'bmi_under_19_underweight'}, 
                    'dietary_restriction':{0:'dietary_restriction_yes',1:'dietary_restriction_no'}}
    return reverse_dict, var_val_dict
    

    
def find_positive_and_negative_increments(var_value_list,reverse_vals_list):
     
    """
    This function returns a dictionary from each variable value to a tupple, (positive list, negative list) for the nodes around it that are positive, and the nodes around it that are negative. 
    You can compute effects with both and take the maximum score on hallmarks in the calling function.  The input is the ordered list of variable value names and a list of what is in the reverse evidence column.

    Reverse_vars_list (4 var case) and represnetation in peaks and troughs.  if a peak return only negative list, if a trough return only positive list, otherwise return both.

    null: 1,1,1 ; peaks:3  troughs: 0

    1: 0,0,0; peaks: 0 troughs: 3

    2: 1,0,0; peaks: 2 troughs: 0,3

    3: 1,1,0; peaks: 2 troughs: 0,3

    1,2: 0,1,1; peaks:0,3  troughs: 1

    1,3: 0,0,1; peaks: 0,3 troughs: 2

    2,3: 0,1,0; peaks: 1,3 troughs: 0,2

    1,2,3: 0,1,0; peaks: 0,2 troughs: 1,3


    1.  determine which way all positions are heading

    loop through all positions and make a list with 1 as default, and everytime the position is in re flip to zero


    2.  figure out if its a peak or trough or neither. 

    if its the beginning and its heading up, its a trough
    it is  the beginning and its heading down, its a peak
    if it is the end and the position before it was heading down, its a trough
    if it is the end and the position before it was heading up, its a peak
    if it is internal, and the one before it is going up, and it is going down, it is a peak
    if it is internal, and the one before it is going down, and it is going up, its a trough

    3.  apply rules to find positive list and negative list from peaks and troughs and neither

    if its a peak, return a list of the 2 surrounding positions that exist as negative list, and empty list in positive list
    if its a trough, return a list of the 2 surrounding positions that exist as a positive list , and an empty in the negatie list
    else if its heading up, return a list with the previous node in negative list and the next node in the positive list, if they exist
    if it is is heading down, return a list with the previous node in the positive list and the next node in the negative list, if they exist"""

    is_increasing = True
    is_increasing_at_position = []
    for index, var in enumerate(var_value_list):
        #determine the direction the position in the list is going,  If the position is in the reverse evidence list, flip it.
        if index + 1 in reverse_vals_list:
            is_increasing = not is_increasing
        is_increasing_at_position.append(is_increasing) 

    from enum import Enum, auto
    class SignalShape(Enum):
        PEAK = auto()
        TROUGH = auto()
        NEITHER = auto()

    
    is_peak_or_trough_at_position = []
    for index,is_increasing in enumerate(is_increasing_at_position):
        if index == 0:
            if is_increasing:
                is_peak_or_trough_at_position.append(SignalShape.TROUGH)
            else:
                is_peak_or_trough_at_position.append(SignalShape.PEAK)
        elif index == len(is_increasing_at_position)-1:
            if is_increasing_at_position[len(is_increasing_at_position)-2]:
                is_peak_or_trough_at_position.append(SignalShape.PEAK)
            else:
                is_peak_or_trough_at_position.append(SignalShape.TROUGH)
        else:  #its internal
            if  is_increasing_at_position[index-1] and not is_increasing:
                is_peak_or_trough_at_position.append(SignalShape.PEAK) 
            elif  not is_increasing_at_position[index-1] and is_increasing:
                is_peak_or_trough_at_position.append(SignalShape.TROUGH) 
            else:
                is_peak_or_trough_at_position.append(SignalShape.NEITHER)    

    positive_and_negative_increments = {}

    for index,is_peak_or_trough in enumerate(is_peak_or_trough_at_position):    
        positive_list = []
        negative_list = []
        """if is_peak_or_trough is SignalShape.PEAK:
            if index != 0:
                negative_list.append(var_value_list[index-1]) 
            if index != len(is_increasing_at_position)-1:
                negative_list.append(var_value_list[index+1]) 
        elif is_peak_or_trough is SignalShape.TROUGH:
            if index != 0:
                positive_list.append(var_value_list[index-1]) 
            if index != len(is_increasing_at_position)-1:
                positive_list.append(var_value_list[index+1]) 
        elif is_peak_or_trough is SignalShape.NEITHER and is_increasing_at_position[index]:
            if index != 0:
                negative_list.append(var_value_list[index-1]) 
            if index != len(is_increasing_at_position)-1:
                positive_list.append(var_value_list[index+1]) 
        elif is_peak_or_trough is SignalShape.NEITHER and not is_increasing_at_position[index]:
            if index != 0:
                positive_list.append(var_value_list[index-1]) 
            if index != len(is_increasing_at_position)-1:
                negative_list.append(var_value_list[index+1]) """

        
        if is_peak_or_trough is SignalShape.PEAK:
            if index != 0:
                negative_list.append(var_value_list[index-1]) 
            if index != len(is_increasing_at_position)-1:
                negative_list.append(var_value_list[index+1]) 
        elif is_peak_or_trough is SignalShape.TROUGH:
            if index != 0:
                positive_list.append(var_value_list[index-1]) 
            if index != len(is_increasing_at_position)-1:
                positive_list.append(var_value_list[index+1]) 
        elif is_peak_or_trough is SignalShape.NEITHER and is_increasing_at_position[index]:
            if index != 0:
                negative_list.append(var_value_list[index-1]) 
            if index != len(is_increasing_at_position)-1:
                positive_list.append(var_value_list[index+1]) 
        elif is_peak_or_trough is SignalShape.NEITHER and not is_increasing_at_position[index]:
            if index != 0:
                positive_list.append(var_value_list[index-1]) 
            if index != len(is_increasing_at_position)-1:
                negative_list.append(var_value_list[index+1]) 
        positive_and_negative_increments[var_value_list[index]]=  (positive_list, negative_list) 

    return   positive_and_negative_increments      


                
                       



def explain(baked_net, netspec, evidence, explain_list,
            reverse_vals,
            internal_query_result=None, include_list: list = [], explain_why_bad = True):
    #explain_list lists output variables to tell what input variable would make them less likely (for example covid severity)
    #reverse_vars tells which of the values for a explaining var should perturb one val to the left rather than the right (the default)
     #  
    #first make a list of all the pertubations to make
    #print  ("in explain, evidence,explain_list, reverse_explain_list, reverse_vars")
    #print  (evidence)
    #print  (explain_list)
    #print  (reverse_explain_list)
    #print  (reverse_vars
    #print ("In explain, internal_query_result")
    #print(internal_query_result)
    #print ("include_list")
    #print(include_list)
    evidence_perturbations = {}
    var_val_names = get_var_val_names(netspec)
 
            
             
    # First get the result if we change nothing and store in before_change
    
    result = internal_query(baked_net, netspec, evidence) if internal_query_result is None or not internal_query_result else internal_query_result
    #print ("result")
    #print (result)
    #next run each, obtaining the values of vars to be explained.  
    #find the difference between these outputvalues and the output values from the original evidence input
    #result = query(baked_net,netspec,evidence,explain_list)
    #print ("result (without changes)")
    #print(result)
    #before_change = {}
    #explanation = {}
    before_change = {k: v for k, v in result.items() if k in explain_list}
    explanation = {k:{} for k, v in result.items() if k in explain_list}
   
    # Next go through every node of the net and purturb each in the direction needed according to if we are
    # explaining why good or bad.  have a list of the evidence as well as the single purturbation for each node
    # and compute them all in batch.
    # Since there may be more than one value surrounding a varible that can be positive or negative, first send 
    # each through to find the one step perturbation that is most effective, and then use the individual nodes  
    # perturbation with the most effect 


    # first change all the ambiguous results to crisp results so as to from there choose a place from which to perturb.
    internal_winners = {}
    for key, val_dict in result.items():
        winner = max(val_dict, key=val_dict.get)
        winner_val = val_dict[winner]
        internal_winners[key] = (winner, winner_val)
    #print('internal_winners')
    #print(internal_winners)
    internal_evidence = {k: tup[0] for k, tup in internal_winners.items()}
    #print ('internal_evidence')
    #print(internal_evidence)
        
    for var, val in internal_evidence.items():
        if var in var_val_names and var in reverse_vals:
            positive_and_negative_increments = find_positive_and_negative_increments(var_val_names[var],reverse_vals[var]) 
            if val in positive_and_negative_increments:
                incr_list = positive_and_negative_increments[val][0] if explain_why_bad else positive_and_negative_increments[val][1] 
                for incr in incr_list:
                    if (len(include_list) == 0 or var in include_list):
                        new_evidence = copy.deepcopy(evidence)
                        new_evidence[var] = incr   #new_val
                        evidence_perturbations[(var,incr)] = new_evidence

        # you now have a list of the perturbations for the single node, so send each to inference and use the one with the most effect.
     
    evidence_list = list(evidence_perturbations.values())
    after_change_list = batch_query(baked_net, netspec, evidence_list, explain_list)


        #find the max once diff is taken
        
        
    for (var,val), after_change in zip(evidence_perturbations.keys(), after_change_list):
        #print("result")
        #print(result)
        
        for outvar in explain_list:
            explanation[outvar].setdefault(var, {})
            bad_val = var_val_names[outvar][0]
            if (outvar in after_change and 
            outvar in before_change and 
            outvar in explanation and 
            bad_val in after_change[outvar] and
            bad_val in before_change[outvar]):
				
                before_num = before_change[outvar][bad_val]                
                after_num = after_change[outvar][bad_val]

				
				# Check for None and numeric types
                if (before_num is not None and 
                    after_num is not None and 
                    isinstance(before_num, (int, float, np.number)) and 
                    isinstance(after_num, (int, float, np.number))):
                    
                    diff = before_num - after_num if explain_why_bad else after_num - before_num
                    explanation[outvar][var][val] = diff

    for var, val in internal_evidence.items():
        for outvar in explain_list:
            if outvar in explanation and var in explanation[outvar] and explanation[outvar][var]:
                explanation[outvar][var] = max(explanation[outvar][var].values()) if explanation[outvar][var] else None
    return explanation
              
 
                          

def make_nmap(): 
        nmap = {}
        cutoff = {}
        for a in range(2,10):
                if not a in cutoff:
                        cutoff[a] ={}
                val = 1/a
                for j in range (0,a):
                        cutoff[a][j] = j*val
        #print("cutoff")
        #print(cutoff)
        for a in range(2,10):
                if not a in nmap:
                        nmap[a]={}
                for b in range(2,10):
                        if not b in nmap[a]:
                                nmap[a][b] = {}
                        for i in range (0,a):
                                lowercutoffi = cutoff[a][i]
                                uppercutoffi = cutoff[a][i+1] if i+1 < a else 1
                                k=0
                                #print("len(cutoff[b])")
                                #print(len(cutoff[b]))
                                
                                while k< len(cutoff[b]) and lowercutoffi >= cutoff [b][k]:
                                        #print("cutoff [b][k]")
                                        #print(cutoff [b][k])
                                        k += 1
                                bucketnumLower = k-1
                                k=0
                                #print("len(cutoff[b])")
                                #print(len(cutoff[b]))
                                while k< len(cutoff[b]) and uppercutoffi > cutoff [b][k]:
                                        #print("cutoff [b][k]")
                                        #print(cutoff [b][k])
                                        k += 1
                                bucketnumUpper = k
                                #print("lowercutoffi")
                                #print(lowercutoffi)
                                #print("uppercutoffi")
                                #print(uppercutoffi)
                                #print("bucketnumLower")
                                #print(bucketnumLower)
                                #print("bucketnumUpper")
                                #print(bucketnumUpper)
                                coveredBuckets = [s for s in range(bucketnumLower, bucketnumUpper)]
                                nmap[a][b][i] = set(coveredBuckets)
                                
        return(nmap)
         
        

def dictVarsAndValues(bayesianNetwork,cpt):
    varsAndValues = {}
    for dist in bayesianNetwork.discreteDistributions:
        varsAndValues[dist.name] = []
        for var in dist.variables:
            varsAndValues[dist.name].append(var.name)
            
    for dist in bayesianNetwork.conditionalProbabilityTables:
        varsAndValues[dist.name] = []
        for var in dist.outvars:
            varsAndValues[dist.name].append(var.name)
            
    for name,cpt_tuple in cpt.items():
        #print('name')
        #print(name)
        #print('cpt_tuple')
        #print(cpt_tuple)
        varsAndValues[name] = cpt_tuple[2]
    return varsAndValues

def any_of(bayesianNetwork, cpt, invars, outvars):
        #print (outvars)
        import itertools
        
        description = "{0} has the value of " + outvars[0] + " if "
        firsttime = True
        for var,vals in invars.items():
                phrase = "" if firsttime else ", OR "
                firsttime = False
                description = description + phrase + var + " has the value of "
                firsttime2 = True
                for val in vals:
                        phrase = "" if firsttime2 else " or " 
                        firsttime2 = False
                        description = description + phrase + val
        description = description + "; otherwise {0} has the value of " + outvars[1]+"."

        vdict = dictVarsAndValues(bayesianNetwork, cpt)
        vlist = [vdict[v] for v in invars.keys()]
        cartesian = list(itertools.product(*vlist))
        klist = [a for a in invars.values()]
        keylist = list(invars.keys())
        cpt_rows = []
        for c in cartesian:
                qany=False
                i=0
                while (not qany) and i < len(klist):
                        vset = klist[i]
                        if c[i] in vset:
                                qany = True
                        i += 1
                for i,o in enumerate(outvars):
                        cpt_row = []
                        cpt_row.extend(c)
                        cpt_row.append(o)
                        val = 1.0 if (i == 0 and qany) or (i == 1 and not qany) else 0.0
                        cpt_row.append(val)
                        cpt_rows.append(cpt_row)
        return (cpt_rows,keylist, outvars,description)



def all_of(bayesianNetwork, cpt, invars, outvars):
        #print (outvars)
        description = "{0} has the value of " + outvars[0] + " if "
        firsttime = True
        for var, vals in invars.items():
            phrase = "" if firsttime else ", AND "
            firsttime = False
            description = description + phrase + var + " has the value of "
            firsttime2 = True
        
            for val in vals:
                phrase = "" if firsttime2 else " or " 
                firsttime2 = False
                description = description + phrase + val
        description = description + "; otherwise {0} has the value of " + outvars[1]+ "."

        vdict = dictVarsAndValues(bayesianNetwork, cpt)
        vlist = [vdict[v] for v in invars.keys()]
        cartesian = list(itertools.product(*vlist))
        klist = [a for a in invars.values()]
        keylist = list(invars.keys())
        cpt_rows = []
    
        for c in cartesian:
            qall=True
            for i, vset in enumerate(klist):
                if c[i] not in vset:
                    qall = False
            for i, o in enumerate(outvars):
                cpt_row = []
                cpt_row.extend(c)
                cpt_row.append(o)
                val = 1.0 if (i == 0 and qall) or (i == 1 and not qall) else 0.0
                cpt_row.append(val)
                cpt_rows.append(cpt_row)

        return (cpt_rows, keylist, outvars, description)


def avg(bayesianNetwork, cpt, invars, outvars):
        clause = ""        
        description = ""
        veryfirsttime = True
        for outvar in outvars:
                description = description + "{0} has the value of " + outvar + " if the values of "
                if veryfirsttime:
                        firsttime = True
                        for var in invars:
                                phrase = "" if firsttime else " and "
                                firsttime = False
                                clause = clause + phrase + var 
                level = "" if veryfirsttime else "next "
                veryfirsttime = False
                description = description + clause + " average to the " + level + "highest level of risk. "
    
    #print (outvars)
        import itertools
        nmap = make_nmap()
        #print(nmap)
        vdict = dictVarsAndValues(bayesianNetwork, cpt)
        vlist = [vdict[v] for v in invars]
        cartesian = list(itertools.product(*vlist))
        #klist = [a for a in invars.values()]
        keylist = invars
        cpt_rows = []
        num_outvars = len(outvars)
        for c in cartesian:
                bins = {}
                #for i,vset in enumerate(klist):
                for i,varlist in enumerate(vlist):
                        for j , slot in enumerate(varlist):
                                if slot == c[i]:
                                        var_number = j
                        num_invars = len(varlist)
                        addset = nmap[num_invars][num_outvars][var_number]
                        incr = 1./len(addset)
                        for p in addset:
                                if p not in bins:
                                        bins[p] = 0
                                bins[p]+= incr
                #print("c")
                #print(c)
                #print("bins")
                #print(bins)

                area = sum(bins.values())
                mean = area/2.
                cummu = 0. 
                for k,v in bins.items():
                        cummu +=v
                        if cummu > mean:
                                winner = k
                                break

                for i,o in enumerate(outvars):
                        cpt_row = []
                        cpt_row.extend(c)
                        cpt_row.append(o)
                        #val = 1.0 if (winner == i) else 0.0)
                        val = bins[i]/area if i in bins else 0.0 #not in winner take all version
                        cpt_row.append(val)
                        cpt_rows.append(cpt_row)

        return (cpt_rows,keylist,outvars,description)




def if_then_else(bayesianNetwork, cpt, invars, outvars):
        #print (outvars)
        import itertools
 
        description = "" 
        firsttime = True
        for i, (var,vals) in enumerate(invars.items()):
                phrase = "" if firsttime else "; otherwise "
                description = description + phrase +"{0} has the value of " + outvars[i] + " if " + var + " has the value "
                firsttime = False
                firsttime2 = True
                for val in vals:
                        phrase = "" if firsttime2 else " or " 
                        firsttime2 = False
                        description = description + phrase + val
        description = description + "; otherwise {0} has the value of " + outvars[-1]+"."


        vdict = dictVarsAndValues(bayesianNetwork, cpt)
        vlist = [vdict[v] for v in invars.keys()]
        cartesian = list(itertools.product(*vlist))
        klist = [a for a in invars.values()]
        #print('cartesian')
        #print(cartesian)
        #print('klist')
        #print(klist)
        keylist = list(invars.keys())
        cpt_rows = [] 
        for c in cartesian:
                result = ""
                i=0
                while (result == "") and i < len(klist):
                        vset = klist[i]
                        if c[i] in vset:
                                result = outvars[i]
                        i += 1
                if result == "":
                        result = outvars[-1]

                for i,o in enumerate(outvars):
                        cpt_row = []
                        cpt_row.extend(c)
                        cpt_row.append(o)
                        val = 1.0 if (o == result) else 0.0
                        cpt_row.append(val)
                        cpt_rows.append(cpt_row)
        return (cpt_rows,keylist,outvars,description)





def addCpt(bayesianNetwork, cpt):
    outstring= ""

    for name, cpt_tuple in cpt.items():
        #print(name)
                    
        conditionalProbabilityTable = bayesianNetwork.conditionalProbabilityTables.add()
        conditionalProbabilityTable.name = name
            
        for rv in cpt_tuple[1]:
            randomVariable = conditionalProbabilityTable.randomVariables.add()
            randomVariable.name = rv
                
        for row in cpt_tuple[0]:
            conditionalProbabilityRow = conditionalProbabilityTable.conditionalProbabilityRows.add()
            for i, var in enumerate(row):
                nvars = len(row)-1
                if i < nvars:
                    randomVariableValue = conditionalProbabilityRow.randomVariableValues.add()
                    randomVariableValue.name = var
                else:
                    conditionalProbabilityRow.probability = var
                        
        for outvar in cpt_tuple[2]:
            out = conditionalProbabilityTable.outvars.add()
            out.name = outvar

        #print('ADDED CPT')
        #print(conditionalProbabilityTable.name)
        #print(conditionalProbabilityTable.conditionalProbabilityRows)
        #print('-'*25)
        outstring = outstring +  cpt_tuple[3].format(name) + "\n\n"

    return outstring


_LAPLACE_EPS = 1e-6


def _fix_probs(probs: torch.Tensor) -> torch.Tensor:
    # Laplace floor: clamp every cell to [eps, 1-eps] and renormalize so each row
    # sums to 1. This serves two purposes:
    #   (1) clamps QP-precision-noise negatives (~-1e-3) and over-1s back into
    #       valid range
    #   (2) replaces the exact 0s and 1s in deterministic any_of/all_of gates
    #       with eps and 1-eps, which prevents pomegranate's joint-inference
    #       from producing NaN when evidence forces a deterministic-0 cell
    #       (the 0/0 → NaN underflow path was the dominant cause of inference
    #       failure on this 429-node net).
    probs = torch.clamp(probs, min=_LAPLACE_EPS, max=1 - _LAPLACE_EPS)
    total_prob = torch.sum(probs, dim=-1, keepdim=True)
    return probs / total_prob

def _fix_total_prob(probs: torch.Tensor) -> torch.Tensor:
    total_prob = torch.sum(probs, dim=-1, keepdim=True)
    max_probs, max_idx = torch.max(probs, dim=-1, keepdim=True)
    probs.scatter_(-1, max_idx, max_probs-(total_prob-1))
    
    return probs


logging.basicConfig(level=logging.ERROR)

def bayesInitialize(bayes_net_proto, max_iter: int = 20) -> BayesianNetworkWrapper:

    var_val_positions = get_var_val_positions(bayes_net_proto)
    var_positions = get_var_positions(bayes_net_proto)
    
    distributions = {}
    for dist in bayes_net_proto.discreteDistributions:
        distribution = torch.Tensor([[var.probability for var in dist.variables]])
        
        total_prob = torch.sum(distribution, dim=-1, keepdim=True)
        if (total_prob != 1).any():
            #logging.warning(f"Discrete distribution {table.name} total prorbability over 1: {distribution}")
            #logging.warning(f"Discrete distribution {dist.name} total prorbability over 1.")
            distribution = _fix_total_prob(distribution)
            
        distributions[dist.name] = Categorical(distribution)

    edges = []
    for table in bayes_net_proto.conditionalProbabilityTables:
        shape = []
        for var in table.randomVariables:
            n_vals = len(var_val_positions.get(var.name, {}))
            if n_vals == 0:
                # Defensive: a parent var with no registered values would
                # produce a zero-dimension tensor that crashes pomegranate's
                # ConditionalCategorical. Log and skip - see docs/FIXES_LEDGER.md.
                print(f'[bayesInitialize] WARN: CPT "{table.name}" has parent "{var.name}" '
                      f'with 0 registered values; skipping CPT', flush=True)
                shape = None
                break
            shape.append(n_vals)

        if shape is None:
            continue

        shape.append(len(table.outvars))
        if shape[-1] == 0 or any(s == 0 for s in shape):
            # Defensive: empty outvars or any zero dim → degenerate tensor.
            print(f'[bayesInitialize] WARN: CPT "{table.name}" has degenerate '
                  f'shape={shape} (outvars={len(table.outvars)}, '
                  f'parents={len(table.randomVariables)}); skipping CPT', flush=True)
            continue

        probs = torch.zeros(shape)

        if table.name == 'cognitive_impairment':
            print(table.name, len(table.conditionalProbabilityRows))
        for row in table.conditionalProbabilityRows:
            indices = []
            for var, value in zip(table.randomVariables, row.randomVariableValues):    # zip ends with shortest iterable
                indices.append(var_val_positions[var.name][value.name])

            table_value = row.randomVariableValues[-1].name
            indices.append(var_val_positions[table.name][table_value])
            probs[tuple(indices)] = row.probability

            #if row.probability < 0 or row.probability > 1:
            #    print('='*100)
            #    print(table.name)
            #    print(row)
            #    print('='*100)

            #if np.isnan(row.probability):
            #    print('='*100)
            #    print(table.name)
            #    print(row)
            #    print('='*100)

        # Always Laplace-floor: even CPTs without QP precision noise (e.g.
        # any_of / all_of gates that have exact 0s by design) must avoid
        # exact 0s so pomegranate's joint inference doesn't underflow to NaN.
        probs = _fix_probs(probs)

        total_prob = torch.sum(probs, dim=-1, keepdim=True)
        if (total_prob != 1).any():
            #logging.warning(f"Table {table.name} has rows with total prorbability over 1: {probs}")
            #logging.warning(f"Table {table.name} has rows with total prorbability over 1.")
            probs = _fix_total_prob(probs)
        
        cond_distribution = ConditionalCategorical(torch.unsqueeze(probs, 0))
        distributions[table.name] = cond_distribution

        # add edges
        for var in table.randomVariables:
            edges.append((distributions[var.name], cond_distribution))


    #try:    # DEBUG
    bayes_net = BayesianNetworkWrapper(distributions, edges, var_positions, var_val_positions, bayes_net_proto, max_iter=max_iter)
    #except:
    #    import ipdb; ipdb.set_trace()

    return bayes_net


def non_cpt_descriptions(bayesianNetwork):
        description = ""
        for dist in bayesianNetwork.discreteDistributions:
                description += "\nThe prevalence of  " + dist.name 
                firsttime = True
                for var in dist.variables:
                        phrase = "" if firsttime else ","
                        if var is dist.variables[-1]:
                            phrase += " and"
                        description = description + phrase +" of value " + var.name + " is " + str(var.probability)
                        firsttime = False
                description += ".\n"
        return description



def get_priors(bayesianNetwork, invars, prevalence, cpt, adjust=False):
    var_positions = get_var_positions(bayesianNetwork)
    var_val_names = get_var_val_names(bayesianNetwork)
    pomegranate = bayesInitialize(bayesianNetwork)
    #print(bayesianNetwork)
    pomegranate.bake()
    evidence = {}
    if "always_true" in var_positions:
        evidence["always_true"]="always_true"
    priors = {}
    for k, v in evidence.items(): 
        var_vals = {}
        for num, val in var_val_names[k].items():
                #prob = 0.99999 if v == val else 0.00001
                prob = 1.0 if v == val else 0.
                var_vals[val] = prob
        priors[k] = var_vals
    probs = predict_proba_adjusted(pomegranate,bayesianNetwork,evidence,adjust)#pomegranate.predict_proba(evidence)
    #print("probs")
    #print (probs)
    vdict = dictVarsAndValues(bayesianNetwork, cpt)
    for vardict, numval_dict in invars:
        numval = numval_dict["relative_risk"] if "relative_risk" in numval_dict else (
                            (numval_dict["sensitivity"]/prevalence) if "sensitivity" in numval_dict else 1) 
        asum = 0
        for k, varlist in vardict.items():
            if k not in priors:
                priors[k] = {}
            for v in vdict[k]:
                try:
                    #problem="hypertension"
                    #if k is problem or v is problem:
                    #print("k")
                    #print(k)
                    #print("v")
                    #print(v)
                        #print("var_positions")
                        #print(var_positions)
                        #print ("probs")
                        #print (probs)
                        #print("priors")
                        #print(priors)
                        #print("json.loads(probs[var_positions[k]].to_json())['parameters']")
                        #print(json.loads(probs[var_positions[k]].to_json())['parameters'])
                    priors[k][v] = probs[k][v]#(json.loads(probs[var_positions[k]].to_json()))['parameters'][0][v]
                    asum += priors[k][v]
                except AttributeError as e:
                    pass
            #print("asum")
            #print(asum)
    return priors
                
def get_frequencies(bayesianNetwork,keylist,cpt,adjust=False,preconditionals = {}):
        #print ("keylist")
        #print (keylist)
        #print ("preconditionals")
        #print(preconditionals)

        pomegranate= bayesInitialize(bayesianNetwork)
        #print(bayesianNetwork)
        pomegranate.bake()
       
        frequencies = {}
        var_positions = get_var_positions(bayesianNetwork)
        vdict = dictVarsAndValues(bayesianNetwork, cpt)
        #print ("vdict")
        #print (vdict)
        conditionals={}
        for i in range (len(keylist)):
                #print ("i")
                #print(i)
                short_keylist = keylist[:i] 
                vlist = [vdict[v] for v in short_keylist]
                #print("vlist")
                #print(vlist)
                cartesian = [()] if i==0 else ([tuple([a]) for a in vlist[0] ] if i==1 else list(itertools.product(*vlist)))
                #print ("cartesian")
                #print(cartesian)
                for c in cartesian:
                        evidence = {} if i == 0 else {k:v for k,v in zip(short_keylist,c)} 
                        if "always_true" in keylist:
                            evidence["always_true"]="always_true"
                        evidence.update(preconditionals)
                        #print ("evidence")
                        #print (evidence)
                        #probs = pomegranate.predict_proba(evidence)
                        probs=predict_proba_adjusted(pomegranate,bayesianNetwork,evidence,adjust)
                        #print ("probs")
                        #print(probs)
                        conditionals[c]={}
                        for val in vdict[keylist[i]]:
                                if keylist[i] in evidence:
                                    #conditionals[c][val] = 0.99999 if val in evidence[keylist[i]] else 0.00001
                                    conditionals[c][val] = 1.0 if val in evidence[keylist[i]] else 0.
                                try:
                                        conditionals[c][val] = probs[keylist[i]][val] if not np.isnan(probs[keylist[i]][val]) else 0.#(json.loads(probs[var_positions[keylist[i]]].to_json()))['parameters'][0][val]
                                except AttributeError as e:
                                        print (e)
                                        pass
        #print("conditionals")
        #print(conditionals)
        vlist = [vdict[v] for v in keylist]
        cartesian = list(itertools.product(*vlist))


        #prob (a,b,c) = prob a * prob b|a * prob c|ab  
        asum = 0
        for c in cartesian:
                #print ("cartesian")
                #print(cartesian)
                product = 1
                for i in range(len(c)):
                        key = tuple(c [:i])
                        #print("key")
                        #print(key)
                        product *= conditionals[key][c[i]]
                frequencies[c]=product
                asum += product
        #print("asum")
        #print(asum)
        return frequencies
        
 

def rr_prob_a_and_not_a_given_b_and_not_b (rr_dict,prior_a,prior_b_dict):

#Solution to these equations:
#rri=probagivenbi / probagivengood
#probagivengood= sum(probagivengoodbi*priorgoodbi)/sum(priorgoodbi)
#probagivenbi*priorbi +probagivennotbi * (1-prior bi) = priora
#sumi(probagivenbi*priorbi)=priora
#probagivenbi + probnotagivenbi = 1.0
#probagivennotbi + probnotagivennotbi = 1.0

#rr_dict: {v:rr}  (if no rr it is good) prior_b_dict: {v:prior}
#returns {b:{prob_a_given_b, prob_a_given_not_b, prob_not_a_given_b, prob_not_a_given_not_b}}

        b = {}
        sum_prior_b_rr = 0
        for v,prior in prior_b_dict.items():
                rr = rr_dict[v] if v in rr_dict else 1.
                sum_prior_b_rr += prior * rr
        prob_a_given_good = prior_a/sum_prior_b_rr
        
        for v,prior in prior_b_dict.items():
                b[v] = {}
                rr = rr_dict[v] if v in rr_dict else 1.
                b[v]["prob_a_given_b"] = prob_a_given_good * rr
                b[v]["prob_not_a_given_b"] = 1. - b[v]["prob_a_given_b"]
                b[v]["prob_a_given_not_b"] = -1*prior_a * (sum_prior_b_rr-prior*rr)/((prior-1.)*sum_prior_b_rr) if ((prior-1.)*sum_prior_b_rr) != 0 else 1.
                b[v]["prob_not_a_given_not_b"]= 1. - b[v]["prob_a_given_not_b"] 
                
        return b

def ss_prob_a_and_not_a_given_b_and_not_b (sensitivity,specificity,prior_a,prior_b):
    #Strategy:  break down into TP, TN, FP, FN
    # 4 equations with 4 unknowns:
    #1.  sensitivity = TP/(TP+FN)
    #2.  specificity = TN/(TN+FP)
    #3.  prior_b = TP+FP
    #4.  prior_a = FN+TP
    #
    #Therefore:
    TP = sensitivity * prior_a
    TN = specificity * (1.-prior_a)
    FN = prior_a - TP
    FP = (1.-prior_a) - TN

    #print ("tp tn fn fp")
    #print (f"{TP} {TN} {FN} {FP}")
    
    #Solution:
    prob_a_given_b = prior_a if (TP+FP)==0 else TP/(TP+FP)
    prob_a_given_not_b = prior_a if (TN+FN)==0 else FN/(TN+FN)
    prob_not_a_given_b = 1. - prior_a if (TP+FP)==0 else FP/(TP+FP)
    prob_not_a_given_not_b = 1. - prior_a if (TN+FN)==0 else TN/(TN+FN)

    return prob_a_given_b, prob_a_given_not_b, prob_not_a_given_b, prob_not_a_given_not_b
 

def prob_a_and_not_a_given_b_and_not_b (invars, priors, outvars):
         
        prob_a_given_b = {}
        prob_a_given_not_b = {}
        prob_not_a_given_b = {}
        prob_not_a_given_not_b = {}
         
        dependency_dict = {}
        
        prior_a = list(outvars.items())[0][1]
        
        for vardict,numval_dict in invars:
            for k,varlist in vardict.items():
                #print("k")
                #print(k)
                if k not in dependency_dict:
                    dependency_dict[k]={}
                for v in priors[k]:
                    #print("v")
                    #print(v)
                
                    #print("priors[k][v]")
                    #print(priors[k][v])
                    prior_b = priors[k][v] 
                    if "relative_risk" in numval_dict:
                        if "relative_risk" not in dependency_dict[k]:
                            dependency_dict[k]["relative_risk"]={}
                        if v in varlist:    
                            dependency_dict[k]["relative_risk"][v]= numval_dict["relative_risk"]
                    elif "sensitivity" in numval_dict:
                        if "sensitivity" not in dependency_dict[k]:
                            dependency_dict[k]["sensitivity"] = {}
                            dependency_dict[k]["specificity"] = {}
                        dependency_dict[k]["sensitivity"][v]= numval_dict["sensitivity"]
                        dependency_dict[k]["specificity"][v]= numval_dict["specificity"]
        
        for k,var_dependency_dict in dependency_dict.items():
          
            if k not in prob_a_given_b:
                prob_a_given_b[k] = {}
            if k not in prob_a_given_not_b:
                prob_a_given_not_b[k] = {}
            if k not in prob_not_a_given_b:
                prob_not_a_given_b[k] = {}
            if k not in prob_not_a_given_not_b:
                prob_not_a_given_not_b[k] = {}
            for dependency_type, var_dict in var_dependency_dict.items():
                if dependency_type == "relative_risk":
                    #print ("var_dict")
                    #print(var_dict)
                    #print("prior_a")
                    #print (prior_a)
                    #print(f"priors[{k}]")
                    #print (priors[k])
                    prob_dict = rr_prob_a_and_not_a_given_b_and_not_b(var_dict,prior_a,priors[k])
                    #print("prob_dict")
                    #print(prob_dict)
                    for v,prob_type_dict in prob_dict.items():
                        for prob_type,val in prob_type_dict.items():
                            if prob_type == 'prob_a_given_b':
                                prob_a_given_b[k][v]= val
                            elif prob_type == 'prob_a_given_not_b':
                                prob_a_given_not_b[k][v] =val
                            elif prob_type == 'prob_not_a_given_b':
                                prob_not_a_given_b[k][v] =val
                            elif prob_type == 'prob_not_a_given_not_b':
                                prob_not_a_given_not_b[k][v] =val
                elif dependency_type == "sensitivity":
                    for v,val in var_dict.items():
                        try:
                            prob_a_given_b[k][v], prob_a_given_not_b[k][v], prob_not_a_given_b[k][v], prob_not_a_given_not_b[k][v] = ( 
                                ss_prob_a_and_not_a_given_b_and_not_b(val,var_dependency_dict["specificity"][v],prior_a,priors[k][v])
                                )
                        except Exception as e:
                            print('invars')
                            print(invars)
                            print("priors")
                            print(priors)
                            print("outvars")
                            print(outvars)
                            print('val')
                            print(val)
                            print('var_dependency_dict')
                            print(var_dependency_dict["specificity"][v])
                            print('prior_a')
                            print(prior_a)
                            print("prior_b")
                            print(priors[k][v])
                            raise e
                    


                category = f"relative_risk:{numval_dict['relative_risk']}" if "relative_risk" in numval_dict else (
                        f"sensitivity/specificity:{numval_dict['sensitivity']}/{numval_dict['specificity']}")
                #print(category)
                #print("prob_a_given_not_b[k][v] ")
                #print (prob_a_given_not_b[k][v] )
                #print("prob_a_given_b[k][v] ")
                #print(prob_a_given_b[k][v] )


                #print("prob_not_a_given_not_b[k][v] ")
                #print (prob_not_a_given_not_b[k][v] )
                #print("prob_not_a_given_b[k][v] ")
                #print(prob_not_a_given_b[k][v] )
        return prob_a_given_b, prob_a_given_not_b, prob_not_a_given_b, prob_not_a_given_not_b



def get_good_vars(v,invars,bayesianNetwork,var_val_positions = None, var_val_names = None):
        #print ("v")
        #print(v)
        #print("invars")
        #print(invars)
    
    
        if var_val_positions is None:    
            var_val_positions = get_var_val_positions(bayesianNetwork)
        if var_val_names is None:
            var_val_names= get_var_val_names(bayesianNetwork)
        #print("var_val_positions")
        #print(var_val_positions)
        #print("var_val_names")
        #print(var_val_names)

        numvals = len(var_val_positions[v])
        # Find out if there are more non stated items to the right or the left, and then send the 
        # majority .  if there are the same numbe of non stated on the right and left send 
        # those on the right, because on the right are positive values, and most cases compare to 
        # a better situation

        lowest_val = numvals
        highest_val = 0
        for tup in invars:
            if v in tup[0]:
                for val in tup[0][v]:
                    if var_val_positions[v][val]>highest_val:
                        highest_val = var_val_positions[v][val]
                    if var_val_positions[v][val]<lowest_val:
                        lowest_val = var_val_positions[v][val]
        #print("highest_val")
        #print(highest_val)
        #print("lowest_val")
        #print(lowest_val)
        right_side_not_included = numvals-1-highest_val
        left_side_not_included = lowest_val
        good_vars = []
        if left_side_not_included > right_side_not_included:
            good_vars = [var_val_names[v][i] for i in range (0,lowest_val+1)]
        else:
            good_vars = [var_val_names[v][i] for i in range (highest_val+1,len(var_val_positions[v]))]
        return good_vars


def get_rr_vals(v,invars):
        rr_vals = {}
        for tup in invars:
            if v in tup[0]:
                for val in tup[0][v]:
                    if "relative_risk" in tup[1]:
                        rr_vals[val]=tup[1]["relative_risk"]
        return rr_vals


def replace_rr(invars,new_rr,v,val):
        #print("v")
        #print(v)
        #print("val")
        #print(val)
        for tup in invars:
            if v in tup[0] and val in tup[0][v] and "relative_risk" in tup[1]:
                tup[1]["relative_risk"]=new_rr


def dependency(bayesianNetwork, cpt, invars, outvars, deconfound = False, adjust=False,
               use_v2=False, use_subset=False, w=None, w_ls=None, w_df=None,
               w_mono=0.0, nhanes_data=None, linear_config=None, output_node=None,
               input_ci_scale=1.0):
        # Deconfounding: convert observational (total) RRs to direct RRs by
        # subtracting the network-mediated indirect effects.
        #
        # WHEN TO USE:
        #   deconfound=False (default): no deconfounding. Use when study RRs are
        #     already adjusted for confounders, or when parents are independent.
        #   deconfound=True: automatically detects per-node whether parents share
        #     ancestors in the DAG. If they do, deconfounds. If not, skips.
        #     Use when study RRs are observational (unadjusted).
        #
        # Deconfounding is needed when parent variables are correlated through
        # shared ancestors in the network, AND the study RRs are observational
        # (not adjusted for the other parents). The observational RR includes both
        # the direct effect (V→disease) and the indirect effect (V is correlated
        # with other parents through shared causes, which also affect disease).
        # Deconfounding removes the indirect portion so the CPT reflects only
        # the direct effect.
        #
        # NOT NEEDED when: parents are independent (no shared ancestors), or when
        # study RRs are already adjusted for confounders (e.g., from RCTs or
        # properly adjusted meta-analyses).
        #
        # WHAT IT DOES for each parent variable V:
        # 1. Build a copy of the network where disease does NOT depend on V
        # 2. Query P(disease | V=risk) and P(disease | V=good) in that network
        #    - this measures the INDIRECT effect of V through correlated parents
        # 3. Convert total and indirect effects to odds ratios
        # 4. Subtract on log-odds scale: log(OR_direct) = log(OR_total) - log(OR_indirect)
        #    - this decomposition is exact under no effect modification
        # 5. Convert back to RR for the QP solver
        # 6. If the deconfounded RR is near 1.0 (between 0.9-1.1), the variable
        #    is dropped - its effect is fully explained by other parents
        #
        # Parents are processed from highest DAG level first to ensure upstream
        # effects are deconfounded before downstream ones.
        priors = None
        tic = time.perf_counter()
        varlist=[list(tup[0].keys())[0] for tup in invars if "relative_risk" in tup[1]]
        varlist = list(set(varlist))

        # When deconfound=True, auto-detect per-node whether parents share
        # ancestors in the DAG. Only deconfound nodes where confounding exists.
        should_deconfound = False
        if deconfound and len(varlist) > 1:
            try:
                df_tree = make_tree(bayesianNetwork, False)
                ancestors = {}
                for v in varlist:
                    ancestors[v] = set()
                    for col in df_tree.columns:
                        col_vals = df_tree[col].tolist()
                        if v in col_vals:
                            v_level = int(col.replace('level', ''))
                            for higher_col in df_tree.columns:
                                higher_level = int(higher_col.replace('level', ''))
                                if higher_level < v_level:
                                    for anc in df_tree[higher_col].dropna().tolist():
                                        if anc and anc != v:
                                            ancestors[v].add(anc)
                for i, v1 in enumerate(varlist):
                    for v2 in varlist[i+1:]:
                        if ancestors.get(v1, set()) & ancestors.get(v2, set()):
                            should_deconfound = True
                            break
                    if should_deconfound:
                        break
            except Exception:
                should_deconfound = False

        if len(varlist) > 0 and should_deconfound:
            prevalence_condition_regardless = list(outvars.items())[0][1]
            priors = get_priors(bayesianNetwork,invars,prevalence_condition_regardless,cpt,adjust)
            var_val_positions = get_var_val_positions(bayesianNetwork)
            var_val_names= get_var_val_names(bayesianNetwork)
            copyNetwork = copy.deepcopy(bayesianNetwork)
            #print("cpt")
            #print(cpt)
            copyCPT = copy.deepcopy(cpt)
            discreteDistribution = copyNetwork.discreteDistributions.add()
            discreteDistribution.name = "always_true"
            variable = discreteDistribution.variables.add()
            variable.name = "always_true"
            #variable.probability = 0.99999
            variable.probability = 1.0
            variable = discreteDistribution.variables.add()
            variable.name = "no_always_true"
            #variable.probability = 0.00001
            variable.probability = 0.
            #print("copyNetwork")
            #print(copyNetwork)
            newInvars = copy.deepcopy(invars)
            newInvars.append(({'always_true': ['always_true']}, {'relative_risk':1.0}))
            #print ("newInvars")
            #print(newInvars)
            outvar = list(outvars.items())[0][0]
            df = make_tree(bayesianNetwork,False)
            #print("df")
            #print(df)
            order_dict = {}

            for i in range(len(df.columns)):
                order_dict[i]=[]
                colname = "level"+str(i)
                for v in varlist:
                    #print("df[colname].tolist()")
                    #print(df[colname].tolist())
                    if v in df[colname].tolist():
                        order_dict[i].append(v)
            #print("order_dict")
            #print(order_dict)
            low_rrs =[]
            for i in range(len(order_dict)):
                for v in order_dict[i]:
                    #print("v")
                    #print(v)
                    thisNetwork = copy.deepcopy(copyNetwork)
                    for  j in range(len(newInvars)):
                       # print("newInvars[j][0]")
                       # print(newInvars[j][0])
                        if v in newInvars[j][0]:
                            copyInvars = copy.deepcopy(newInvars)
                            popped = copyInvars.pop(j)
                            count = 0
                            for  l in range(len(copyInvars)):
                                if v in copyInvars[l][0]:
                                    count += 1
                            for k in range (count):
                                for l in range (len(copyInvars)):
                                    if v in copyInvars[l][0]:
                                        copyInvars.pop(l)
                                        break
                            #print("copyInvars")
                            #print(copyInvars)
                            #print("thisNetwork")
                            #print(thisNetwork)
                            copyCPT[outvar]= dependency_direct(thisNetwork,{},copyInvars,outvars)

                            addCpt(thisNetwork,copyCPT)
                            copied = bayesInitialize(thisNetwork)
                            copied.bake()
                            var_val_names_this = get_var_val_names(thisNetwork)
                            good_vars = get_good_vars(v,invars,bayesianNetwork,var_val_positions,var_val_names)
                            chance_of_outvar_given_good = 0.
                            outvar_chance_dict = {}
                            for g in good_vars:
                                #print ("g")
                                #print (g)
                                chance_of_outvar = query(copied,thisNetwork,{v:g},[outvar],adjust)
                                #print(chance_of_outvar)
                                #print ("var_val_names")
                                #print(var_val_names)
                                #print ("chance_of_outvar[outvar][var_val_names_this[outvar][0]]")
                                #print (chance_of_outvar[outvar][var_val_names_this[outvar][0]])
                                outvar_chance_dict [g]= chance_of_outvar[outvar][var_val_names_this[outvar][0]]#list(chance_of_outvar[outvar].values())[0]
                            prior_sum = 0
                            for val,outvar_chance in outvar_chance_dict.items():
                                chance_of_outvar_given_good += outvar_chance * priors[v][val]
                                prior_sum += priors[v][val]
                            chance_of_outvar_given_good /= prior_sum
                            #print ("chance_of_outvar_given_good")
                            #print(chance_of_outvar_given_good)
                            rr = popped [1]["relative_risk"]
                            chance_of_outvar_given_var = 0.
                            outvar_chance_dict = {}
                            for val in newInvars[j][0][v]:
                                #print("val")
                                #print(val)
                                chance_of_outvar = query(copied,thisNetwork,{v:val}, [outvar],adjust)
                                #print ("chance_of_outvar[outvar][var_val_names_this[outvar][0]]")
                                #print (chance_of_outvar[outvar][var_val_names_this[outvar][0]])
                                outvar_chance_dict[val] = chance_of_outvar[outvar][var_val_names_this[outvar][0]]#list(chance_of_outvar[outvar].values())[0]
                            prior_sum = 0
                            for val,outvar_chance in outvar_chance_dict.items():
                                chance_of_outvar_given_var += outvar_chance * priors[v][val]
                                prior_sum += priors[v][val]
                            #print ("chance_of_outvar_given_var")
                            #print(chance_of_outvar_given_var)
                            # Deconfound on log-odds scale (exact decomposition):
                            # log(OR_direct) = log(OR_total) - log(OR_indirect)
                            chance_of_outvar_given_var /= prior_sum
                            p_indirect_risk = max(chance_of_outvar_given_var, 1e-10)
                            p_indirect_good = max(chance_of_outvar_given_good, 1e-10)
                            # Clamp probabilities away from 0 and 1 for log-odds
                            def _to_log_odds(p):
                                p = max(min(p, 1-1e-10), 1e-10)
                                return np.log(p / (1 - p))
                            def _from_log_odds(lo):
                                lo = max(min(lo, 20), -20)  # prevent overflow
                                return np.exp(lo) / (1 + np.exp(lo))
                            # Total effect: convert study RR to OR then log-odds
                            # OR_total = RR * (1-P0) / (1 - RR*P0) where P0 = prevalence
                            prevalence_for_conversion = max(chance_of_outvar_given_good, 1e-10)
                            or_total = rr * (1 - prevalence_for_conversion) / max(1 - rr * prevalence_for_conversion, 1e-10)
                            log_or_total = np.log(max(or_total, 1e-10))
                            # Indirect effect on log-odds scale
                            log_or_indirect = _to_log_odds(p_indirect_risk) - _to_log_odds(p_indirect_good)
                            # Direct effect
                            log_or_direct = log_or_total - log_or_indirect
                            or_direct = np.exp(log_or_direct)
                            # Convert OR back to RR
                            new_rr = or_direct / ((1 - prevalence_for_conversion) + prevalence_for_conversion * or_direct)
                            #print(f"Deconfound {v}: RR_total={rr:.4f}, OR_total={or_total:.4f}, log_OR_indirect={log_or_indirect:.4f}, OR_direct={or_direct:.4f}, RR_direct={new_rr:.4f}")
                            if new_rr > .9 and new_rr < 1.1:
                                low_rrs.append(popped)
                            elif abs(rr-new_rr)>.1:
                                #print ("newInvars before replace")
                                #print (newInvars)
                                replace_rr(newInvars,new_rr,v,val)
                                #print ("newInvars after replace")
                                #print (newInvars)
            for  j in range(len(newInvars)):
                if "always_true"  in newInvars[j][0]:
                    newInvars.pop(j)
                    break
           # print ("newInvars before removing new ones")
           # print (newInvars)
           # print ("low_rrs")
           # print (low_rrs)
            for low in low_rrs:
                try:
                    newInvars.remove(low)
                except ValueError as ve:
                    #print(ve)
                    pass
            #print ("invars")
            #print(invars)
            #print("newInvars")
            #print(newInvars)
        else:
            newInvars = invars

        if use_v2:
            from sn_bayes.dependency_v2 import dependency_direct_v2
            v2_kwargs = dict(
                use_subset=use_subset,
                w_mono=w_mono,
                nhanes_data=nhanes_data, linear_config=linear_config,
                output_node=output_node,
                input_ci_scale=input_ci_scale,
            )
            if w_ls is not None:
                v2_kwargs['w_ls'] = w_ls
            if w_df is not None:
                v2_kwargs['w_df'] = w_df
            if w_ls is None and w_df is None and w is not None:
                v2_kwargs['w'] = w
            cpt_rows, keylist, outvars, description = dependency_direct_v2(
                bayesianNetwork, cpt, newInvars, outvars, priors,
                **v2_kwargs,
            )
        else:
            cpt_rows, keylist, outvars, description = dependency_direct(bayesianNetwork, cpt, newInvars, outvars, priors)

        toc = time.perf_counter()
        diff = toc - tic

        #print (f"{outvars}  wrapper took {diff} seconds")
        return (cpt_rows,keylist,outvars,description)


def align_ci(ci,amt):
    ci_z= {80:1.282,85:1.440,90:1.645,95:1.960,99:2.576,99.5:2.807,99.9:3.291}
    new_amt = amt if ci == 95 else ci_z[95]*amt/ci_z[ci]
    return new_amt

def normalize_ci(maxi,window_dict):
    factor=1.0/maxi if maxi != 0 else 1.
    new_dict = {k: v*factor for k, v in window_dict.items() }
    return new_dict


def ss_plus_minus(prior_a,sensitivity,specificity,sensitivity_plus_minus, specificity_plus_minus,prob_a_given_not_b):
        # finds rr ci using the equation prob_a_given_b = prob_a_given_not_b * rr = 
        # (sensitivity * prior_a) / (sensitivity* prior_a  + (1.-prior_a) *(1.-specificity)) 
        # subtracting the version without the ci from the one with the ci

        with_ci =  (((sensitivity + sensitivity_plus_minus) * prior_a)/ 
                ((sensitivity + sensitivity_plus_minus) * prior_a + 
                (1.-prior_a)* (1.-(specificity + specificity_plus_minus))))
        without_ci = ((sensitivity * prior_a)/ 
                (sensitivity * prior_a  + (1.-prior_a) *(1.-specificity))) 
        plus_minus = 0.05 if prob_a_given_not_b == 0 else abs(with_ci - without_ci)/prob_a_given_not_b
        return plus_minus


def get_window(bayesianNetwork,invars,outvars,prob_a_given_not_b,k,v):
        # This only makes a difference between windows for values of variable 
        # if the CI are different for the different 
        # variable values, for example one rr for one value of obesity and another for another
        var_val_positions = get_var_val_positions(bayesianNetwork)
        window = {}
        for vardict,numval_dict in invars:
            for var, vallist in vardict.items():
                if "ci" in numval_dict:
                    if var not in window:
                        window[var] = {}
                    if ("sensitivity" in numval_dict) :
                        sensitivity = numval_dict["sensitivity"]
                        specificity = numval_dict["specificity"] if ("specificity" in numval_dict) else 0.0

                        if ("sensitivity_plus_minus" in numval_dict):
                              sensitivity_plus_minus = numval_dict["sensitivity_plus_minus"]
                        elif ("plus_minus" in numval_dict):
                              sensitivity_plus_minus = numval_dict["plus_minus"] 
                        else:
                              sensitivity_plus_minus = 0.0

                        if ("specificity_plus_minus" in numval_dict):
                              specificity_plus_minus = numval_dict["specificity_plus_minus"]
                        elif ("plus_minus" in numval_dict):
                              specificity_plus_minus = numval_dict["plus_minus"] 
                        else:
                              specificity_plus_minus = 0.0

                        prior_a = list(outvars.items())[0][1]
                        plus_minus = ss_plus_minus(prior_a,sensitivity, specificity, sensitivity_plus_minus, specificity_plus_minus,prob_a_given_not_b[k][v])
                    else:
                        plus_minus = numval_dict["plus_minus"]
                    for val in vallist:
                        window[var][val]= align_ci(numval_dict["ci"],plus_minus)
                    #print("window")
                    #print(window)
                    maxi= max(window[var].values())
                    #print("maxi")
                    #print(maxi)
                    for val,dummy in var_val_positions[var].items():
                        if val not in window[var]:
                              window[var][val] = maxi
                    window[var] = normalize_ci(maxi,window[var])
        #print("outvars")  
        #print(outvars)         
        #print("window (return)")
        #print(window)
        return(window)


def get_stat_info (k,v,invars,outvars,prob_a_given_not_b):
        stattype = stat = ci95 = None
        for vardict,numval_dict in invars:
            if "ci" in numval_dict and k in vardict and v in vardict[k]:
 
                if ("sensitivity" in numval_dict) :
                        sensitivity = numval_dict["sensitivity"]
                        specificity = numval_dict["specificity"] if ("specificity" in numval_dict) else 0.0
                        
                        if ("sensitivity_plus_minus" in numval_dict):
                              sensitivity_plus_minus = numval_dict["sensitivity_plus_minus"]
                        elif ("plus_minus" in numval_dict):
                              sensitivity_plus_minus = numval_dict["plus_minus"] 
                        else:
                              sensitivity_plus_minus = 0.0

                        if ("specificity_plus_minus" in numval_dict):
                              specificity_plus_minus = numval_dict["specificity_plus_minus"]
                        elif ("plus_minus" in numval_dict):
                              specificity_plus_minus = numval_dict["plus_minus"] 
                        else:
                              specificity_plus_minus = 0.0

                        prior_a = list(outvars.items())[0][1]
                        plus_minus = ss_plus_minus(prior_a,sensitivity, specificity, sensitivity_plus_minus, specificity_plus_minus,prob_a_given_not_b[k][v])
                else:
                        plus_minus = numval_dict["plus_minus"]
                
                stattype = "relative_risk" if "relative_risk" in numval_dict else "specificity"
                stat = numval_dict[stattype]
                ci95 = align_ci(numval_dict["ci"],plus_minus)
                break
        return stattype,stat,ci95

import warnings
def validation(k,v,probagivenb,condition_val,invars,outvars,final_window,window_factor,prob_a_given_not_b):
        #print("k")
        #print(k)
        #print("v")
        #print(v)
        #print("probagivenb")
        #print(probagivenb)
        #print("final_window")
        #print(final_window)
        if not probagivenb > 0:
                    warning_msg = f"Probability of '{condition_val}' given '{k}' of '{v}' is <= 0"  
                    warnings.warn(warning_msg)
                    print(warning_msg)
        row = {}
        stattype,stat,ci95 = get_stat_info (k,v,invars,outvars,prob_a_given_not_b)
        if ci95 is not None:
            row["condition"]= condition_val
            row["variable"]=k
            row["value"] = v
            row["statistic"] = stattype
            row["window"] = final_window
            row["mean"]=stat
            row["ci95"] = ci95
            # ci_slack_used: P-space slack actually consumed on this row
            # (final_window * normalized CI factor). Honest, scale-free
            # across rows. Prefer this over "score" for diagnostics.
            wf = window_factor[k][v] if k in window_factor and v in window_factor[k] else 1.0
            row["ci_slack_used"] = final_window * wf
            # Solver-derived CI for this edge's CPT cell, in probability units
            # (same units as probagivenb). [point - W, point + W], clipped to [0,1].
            _lo = max(0.0, probagivenb - final_window * wf)
            _hi = min(1.0, probagivenb + final_window * wf)
            row["solver_ci"] = [_lo, _hi]
            # coverage: marginal per-edge CI coverage of solver_ci under the normal
            # approximation. Input 95% CI = band at z=1.96; scaling by c=final_window
            # gives effective coverage 2*Phi(c*1.96)-1 per study. Runs at c=1 recover
            # coverage=0.95; Bonferroni c=1.85 gives joint coverage=0.95 for n=166.
            # Named "coverage" not "p" to avoid the NHST-p collision.
            from math import erf, sqrt
            _z = final_window * 1.959963984540054
            row["coverage"] = max(0.0, min(1.0, erf(_z / sqrt(2.0))))
            # score (legacy): ci_slack_used * stat / query_P. The /query_P
            # divisor inflates for rare outcomes and is misleading.
            row["score"] = ((probagivenb + (final_window*wf)) * stat/probagivenb)-stat if probagivenb>0 else 1.
        return row


def dependency_direct(bayesianNetwork, cpt, invars, outvars, priors = None,adjust=False):
        #print('DEPENDENCY DIRECT')
        #print('invars')
        #print(invars)
        #print('outvar')
        #print(outvars)
        import itertools
        from scipy.optimize import linprog
        from qpsolvers import solve_qp
        import warnings
        import time

        validation_row = {}
        #print("start timing...")
        tic = time.perf_counter()

        keyset = OrderedSet([])
        frequency_cache = {}
        prevalence_condition_regardless = list(outvars.items())[0][1]
        condition_val = list(outvars.items())[0][0]
        if priors is None:
            priors = get_priors(bayesianNetwork,invars,prevalence_condition_regardless,cpt,adjust)
        #print("priors")
        #print(priors)
        prob_a_given_b, prob_a_given_not_b, prob_not_a_given_b, prob_not_a_given_not_b =  prob_a_and_not_a_given_b_and_not_b(invars, priors, outvars)

        description = "Against the baseline risks, "
        firsttime = True
        for k,v in outvars.items():                        
                phrase = "" if firsttime else " and "
                description + phrase + k + " of " + str(v) + " , "
                firsttime = False
        firsttime = True
        for vardict,numval_dict in invars:
                for val, varlist in vardict.items():
                        for test,num in numval_dict.items():
                                keyset.add(val)
                                phrase = "" if firsttime else (" , and " if varlist [-1] is val else " , " )

                                description= description + phrase + "the "+ test.replace("_"," ")  +" that {0} will be " + condition_val +" for those in the " + val + " category of " 
                                firsttime1 = True
                                sum_priors = 0
                                for var in varlist:                                
                                        phrase = "" if firsttime1 else " or "
                                        description = description + phrase + var
                                        firsttime1 = False
                                        #print("val var to priors")
                                        #print (f"{val}  : {var}")
                                        sum_priors += priors[val][var]
                                description = description + " is " + str(num)

                                firsttime = False
                                        
        description = description + "."
        
        #print("prob_a_given_not_b")
        #print (prob_a_given_not_b)
        #print("prob_a_given_b")
        #print(prob_a_given_b)


        #print("prob_not_a_given_not_b")
        #print (prob_not_a_given_not_b)
        #print("prob_not_a_given_b")
        #print(prob_not_a_given_b)

        keylist = list(keyset)
        pos = {k:n for n,k in enumerate(keylist)}
        vdict = dictVarsAndValues(bayesianNetwork, cpt)

        #print("vdict")
        #print(vdict)
        val_prev = {}
        not_val_prev = {}
        for k in keylist:
                val_prev[k]={}
                not_val_prev[k] = {}
                previous = None
                previous_not = None
                for v in vdict[k]:
                        #natural order is worse to better, and we want to fill in worse first because better is more accurate
                        if k in prob_a_given_b and v in prob_a_given_b[k]:
                                val_prev[k][v] = prob_a_given_b[k][v] 
                                previous = prob_a_given_not_b[k][v]
                        elif previous is not None:
                                val_prev [k][v] = previous
                        else:
                                val_prev [k][v] = prevalence_condition_regardless
                        if k in prob_not_a_given_b and v in prob_not_a_given_b[k]:
                                not_val_prev[k][v] = prob_not_a_given_b[k][v] 
                                previous_not = prob_not_a_given_not_b[k][v]
                        elif previous_not is not None:
                                not_val_prev [k][v] = previous_not
                        else:
                                not_val_prev [k][v] = prevalence_condition_regardless
        #print("val_prev")                        
        #print(val_prev)                            

        vlist = [vdict[v] for v in keylist]
        #print("vlist")
        #print (vlist)
        cartesian = list(itertools.product(*vlist))
       #in cartesian, worst is first and best comes later
        cpt_rows = []
        lhs_inequality_equation1 = {}
        lhs_inequality_equation2 = {}
        lhs_inequality_equation3 = {}
        lhs_inequality_equation4 = {}
        rhs_inequality_equation1 = {}
        rhs_inequality_equation2 = {}
        rhs_inequality_equation3 = {}
        rhs_inequality_equation4 = {}
        obj = np.ones(2*len(cartesian))

        frequencies = get_frequencies(bayesianNetwork,keylist,cpt,adjust)
        #print ("frequencies")
        #print(frequencies)
        #bnd = [(1.0,1.0)] * len(cartesian)
        bnd = []
 
        lhs_eq = []
        rhs_eq = []
                                
        included_vars = {}                        
        for excluded in keylist:
            included_vars[excluded]= keylist[:pos[excluded]] + keylist[pos[excluded]+1:] 
        for i,c in enumerate(cartesian):
                #print ("i:c")
                #print (i)
                #print (c)
                #print ("frequencies[c]")
                #print(frequencies[c])
                #equation1  (doesnt have every one in it )
                #non elderly hbp prevalence  = 
                #  (prevalence of hbp among adult obese psych) * prevalence of adult obese psych 
                #+ (prevalence of hbp among youngadult obese psych) * prevalence of youngadult obese psych
                #+ (prevalence of hbp among teen obese psych) * prevalence of teen obese psych
                #+ (prevalence of hbp among child obese psych) * prevalence of child obese psych
                #+ (prevalence of hbp among adult overweight psych) * prevalence of adult overweight psych
                #+ (prevalence of hbp among youngadult overweight psych) * prevalence of youngadult overweight psych
                #+ (prevalence of hbp among teen overweight psych) * prevalence of teen overweight psych
                #+ (prevalence of hbp among child overweight psych) * prevalence of child overweight psych
                #+ (prevalence of hbp among adult healthy psych) * prevalence of adult healthy psych
                #+ (prevalence of hbp among youngadult healthy psych) * prevalence of youngadult healthy psych
                #+ (prevalence of hbp among teen healthy psych) * prevalence of teen healthy psych
                #+ (prevalence of hbp among child healthy psych) * prevalence of child healthy psych
                #+ (prevalence of hbp among adult obese nonpsych) * prevalence of adult obese nonpsych 
                #+ (prevalence of hbp among youngadult obese nonpsych) * prevalence of youngadult obese nonpsych
                #+ (prevalence of hbp among teen obese nonpsych) * prevalence of teen obese nonpsych
                #+ (prevalence of hbp among child obese nonpsych) * prevalence of child obese nonpsych
                #+ (prevalence of hbp among adult overweight nonpsych) * prevalence of adult overweight nonpsych
                #+ (prevalence of hbp among youngadult overweight nonpsych) * prevalence of youngadult overweight nonpsych
                #+ (prevalence of hbp among teen overweight nonpsych) * prevalence of teen overweight nonpsych
                #+ (prevalence of hbp among child overweight nonpsych) * prevalence of child overweight nonpsych
                #+ (prevalence of hbp among adult healthy nonpsych) * prevalence of adult healthy nonpsych
                #+ (prevalence of hbp among youngadult healthy nonpsych) * prevalence of youngadult healthy nonpsych
                #+ (prevalence of hbp among teen healthy nonpsych) * prevalence of teen healthy nonpsych
                #+ (prevalence of hbp among child healthy nonpsych) * prevalence of child healthy nonpsych
                         
                         
                #equation2 (has the balance)         
                #elderly hbp prevalence  = 
                #  (prevalence of hbp among elderly obese psych) * prevalence of elderly obese psych 
                #+ (prevalence of hbp among elderly overweight psych) * prevalence of elderly overweight psych
                #+ (prevalence of hbp among elderly healthy psych) * prevalence of elderly healthy psych
                #+ (prevalence of hbp among elderly obese nonpsych) * prevalence of elderly obese nonpsych 
                #+ (prevalence of hbp among elderly overweight nonpsych) * prevalence of elderly overweight nonpsych
                #+ (prevalence of hbp among elderly healthy nonpsych) * prevalence of elderly healthy nonpsych
                
                 
                #equation3  (doesnt have every one in it )
                #non elderly non hbp prevalence  = 
                #  (prevalence of non hbp among adult obese psych) * prevalence of adult obese psych 
                #+ (prevalence of non hhbp among youngadult obese psych) * prevalence of youngadult obese psych
                #+ (prevalence of non hhbp among teen obese psych) * prevalence of teen obese psych
                #+ (prevalence of non hhbp among child obese psych) * prevalence of child obese psych
                #+ (prevalence of non hbp among adult overweight psych) * prevalence of adult overweight psych
                #+ (prevalence of non hbp among youngadult overweight psych) * prevalence of youngadult overweight psych
                #+ (prevalence of non hbp among teen overweight psych) * prevalence of teen overweight psych
                #+ (prevalence of non hbp among child overweight psych) * prevalence of child overweight psych
                #+ (prevalence of non hbp among adult healthy psych) * prevalence of adult healthy psych
                #+ (prevalence of non hbp among youngadult healthy psych) * prevalence of youngadult healthy psych
                #+ (prevalence of non hbp among teen healthy psych) * prevalence of teen healthy psych
                #+ (prevalence of non hbp among child healthy psych) * prevalence of child healthy psych
                #+ (prevalence of non hbp among adult obese nonpsych) * prevalence of adult obese nonpsych 
                #+ (prevalence of non hbp among youngadult obese nonpsych) * prevalence of youngadult obese nonpsych
                #+ (prevalence of non hbp among teen obese nonpsych) * prevalence of teen obese nonpsych
                #+ (prevalence of non hbp among child obese nonpsych) * prevalence of child obese nonpsych
                #+ (prevalence of non hbp among adult overweight nonpsych) * prevalence of adult overweight nonpsych
                #+ (prevalence of non hbp among youngadult overweight nonpsych) * prevalence of youngadult overweight nonpsych
                #+ (prevalence of non hbp among teen overweight nonpsych) * prevalence of teen overweight nonpsych
                #+ (prevalence of non hbp among child overweight nonpsych) * prevalence of child overweight nonpsych
                #+ (prevalence of non hbp among adult healthy nonpsych) * prevalence of adult healthy nonpsych
                #+ (prevalence of non hbp among youngadult healthy nonpsych) * prevalence of youngadult healthy nonpsych
                #+ (prevalence of non hbp among teen healthy nonpsych) * prevalence of teen healthy nonpsych
                #+ (prevalence of non hbp among child healthy nonpsych) * prevalence of child healthy nonpsych
                         
                         
                #equation4 (has the balance)         
                #elderly with non hbp prevalence = 
                #  (prevalence of non hbp among elderly obese psych) * prevalence of elderly obese psych 
                #+ (prevalence of non hbp among elderly overweight psych) * prevalence of elderly overweight psych
                #+ (prevalence of non hbp among elderly healthy psych) * prevalence of elderly healthy psych
                #+ (prevalence of non hbp among elderly obese nonpsych) * prevalence of elderly obese nonpsych 
                #+ (prevalence of non hbp among elderly overweight nonpsych) * prevalence of elderly overweight nonpsych
                #+ (prevalence of non hbp among elderly healthy nonpsych) * prevalence of elderly healthy nonpsych
                
                #equations 1-4 are inequalities, but then there are c equalities , equations that say prob a + ~prob a == 1

                rhs_eq.append(1.)
                lhs_eq_list = np.zeros(2*len(cartesian))
                lhs_eq_list[i]=1.
                lhs_eq_list[i+len(cartesian)]=1.
                lhs_eq.append(lhs_eq_list)

               
                for vardict,numval_dict in invars:
                        #relative_risk = numval_dict["relative_risk"] if "relative_risk" in numval_dict else (
                        #    (prevalence_condition_regardless-numval_dict["sensitivity"])/numval_dict["sensitivity"] if "sensitivity" in numval_dict else 1) 
                        for k, varlist in vardict.items():
                        
                                if k not in lhs_inequality_equation1:
                                        lhs_inequality_equation1[k] = {}
                                if k not in lhs_inequality_equation2:
                                        lhs_inequality_equation2[k] = {}
                                if k not in lhs_inequality_equation3:
                                        lhs_inequality_equation3[k] = {}
                                if k not in lhs_inequality_equation4:
                                        lhs_inequality_equation4[k] = {}
                                if k not in rhs_inequality_equation1:
                                        rhs_inequality_equation1[k] = {}
                                if k not in rhs_inequality_equation2:
                                        rhs_inequality_equation2[k] = {}
                                if k not in rhs_inequality_equation3:
                                        rhs_inequality_equation3[k] = {}
                                if k not in rhs_inequality_equation4:
                                        rhs_inequality_equation4[k] = {}
                

                                for v in varlist:
                                        if v not in lhs_inequality_equation1[k]:
                                                lhs_inequality_equation1[k][v] = np.zeros(2*len(cartesian),np.double) #lhs will be list of lists
                                        if v not in lhs_inequality_equation2[k]:
                                                lhs_inequality_equation2[k][v] = np.zeros(2*len(cartesian),np.double)
                                        if v not in lhs_inequality_equation3[k]:
                                                lhs_inequality_equation3[k][v] = np.zeros(2*len(cartesian),np.double) #lhs will be list of lists
                                        if v not in lhs_inequality_equation4[k]:
                                                lhs_inequality_equation4[k][v] = np.zeros(2*len(cartesian),np.double)
                                        if v not in rhs_inequality_equation1[k]:
                                                rhs_inequality_equation1[k][v] = 0 #rhs will be list of floats
                                        if v not in rhs_inequality_equation2[k]:
                                                rhs_inequality_equation2[k][v] = 0
                                        if v not in rhs_inequality_equation3[k]:
                                                rhs_inequality_equation3[k][v] = 0 #rhs will be list of floats
                                        if v not in rhs_inequality_equation4[k]:
                                                rhs_inequality_equation4[k][v] = 0
                                        #print ("c[pos[k]]")
                                        #print (c[pos[k]])
                                        #tup1 = included_vars[k].copy().sort() if len(included_vars[k]) > 0 else ()
                                        #input_tuple = (tup1,v)
                                        #if input_tuple not in frequency_cache:
                                            #prob_others_given_b = get_frequencies(bayesianNetwork,included_vars[k],cpt,preconditionals = {k:v})
                                            #frequency_cache[input_tuple] = prob_others_given_b
                                            #print (f"{input_tuple} stored in frequency cache")
                                        #else:
                                            #prob_others_given_b = frequency_cache[input_tuple]
                                            #print(f"{input_tuple} retrieved from frequency cache of size {len(frequency_cache)}")
                                        #print ("prob_others_given_b")
                                        #print (prob_others_given_b)
                                        #c_no_k = c[:pos[k]]+c[pos[k]+1:]
                                        if c[pos[k]]  == v:
                                                # rhs_equality_equation2[k][v] = priors[k][v]*relative_risk* val_prev[k][v]
                                                rhs_inequality_equation2[k][v] = prob_a_given_b [k][v]
                                                lhs_inequality_equation2[k][v][i] = np.double(frequencies[c]) #prob_others_given_b[c_no_k]
                                                rhs_inequality_equation4[k][v] = prob_not_a_given_b [k][v]  
                                                lhs_inequality_equation4[k][v][i+len(cartesian)] = np.double(frequencies[c])#prob_others_given_b[c_no_k]
                                        else:
                                                # rhs_equality_equation1[k][v] = val_prev[k][v]
                                                rhs_inequality_equation1[k][v] = prob_a_given_not_b[k][v]
                                                lhs_inequality_equation1[k][v][i] = np.double(frequencies[c])  #prob_others_given_b[c_no_k]
                                                rhs_inequality_equation3[k][v] =  prob_not_a_given_not_b [k][v] 
                                                lhs_inequality_equation3[k][v][i+len(cartesian)] = np.double(frequencies[c] )#prob_others_given_b[c_no_k]
                                                                                       

                #make independence the lower bound
                #product = 1
                #for j,k in enumerate(keylist):
                        #product *= 1.-val_prev [k][c[j]] 
                #bnd.append(( 1-product, 1.0))

                minimum = 1.0
                for j,k in enumerate(keylist):
                        if val_prev [k][c[j]] < minimum:
                            minimum = val_prev [k][c[j]]
                #bnd.append((minimum, 1.0))
                bnd.append((0,1.0))

        for i,c in enumerate(cartesian):
                minimum_not = 1.0
                for j,k in enumerate(keylist):
                        if not_val_prev [k][c[j]] < minimum_not:
                            minimum_not = not_val_prev [k][c[j]]
                #bnd.append((minimum_not, 1.0))
                bnd.append((0, 1.0))

        size = 2*len(cartesian)

        #P = np.ones((size,size),np.double)
        #for i in range(size):
                #P[i][i] = 0.
                #for j in range(size):
                    #if i >= size/2 and j< size/2 or j >= size/2 and i < size/2:
                       # P[i][j] = 0.
        P = np.identity(size,np.double) 
        for i in range(len(cartesian)):
                P[i][i] = np.double(i+1)
        for i in range(len(cartesian),size):
                P[i][i] = np.double(size-i)
   
        q = np.zeros (size,np.double)	
        lb = np.zeros(size,np.double)
        ub = np.ones(size,np.double)

        #print("before norm:")       
        #print("rhs_inequality_equation2") 
        #print(rhs_inequality_equation2) 
        #print("lhs_inequality_equation2")
        #print(lhs_inequality_equation2)
        #print("rhs_inequality_equation4") 
        #print(rhs_inequality_equation4) 
        #print("lhs_inequality_equation4")
        #print(lhs_inequality_equation4)
        #print("rhs_inequality_equation1")
        #print(rhs_inequality_equation1)
        #print("lhs_inequality_equation1")
        #print(lhs_inequality_equation1)
        #print("rhs_inequality_equation3")
        #print(rhs_inequality_equation3)
        #print("lhs_inequality_equation3")
        #print(lhs_inequality_equation3)
                                               


        for vardict,numval_dict in invars:
                for k, varlist in vardict.items():
                    for v in varlist:
                        norm = np.sum(lhs_inequality_equation1[k][v])
                        if norm != 0:
                            lhs_inequality_equation1[k][v]=lhs_inequality_equation1[k][v]/norm
                        norm = np.sum(lhs_inequality_equation2[k][v])
                        if norm != 0:
                            lhs_inequality_equation2[k][v]=lhs_inequality_equation2[k][v]/norm
                        norm = np.sum(lhs_inequality_equation3[k][v])
                        if norm != 0:
                            lhs_inequality_equation3[k][v]=lhs_inequality_equation3[k][v]/norm
                        norm = np.sum(lhs_inequality_equation4[k][v])
                        if norm != 0:
                            lhs_inequality_equation4[k][v]=lhs_inequality_equation4[k][v]/norm

        #We also want to include that the first half adds to the prevaence of a and the sencond to the prevalence of ~a


        rhs_eq.append(prevalence_condition_regardless)
        rhs_eq.append(1.-prevalence_condition_regardless)
        prev_a = np.zeros(2*len(cartesian),np.double)
        prev_not_a = np.zeros(2*len(cartesian),np.double)
        for i,c in enumerate(cartesian):
                prev_a[i]=np.double(frequencies[c])
                prev_not_a [i+len(cartesian)] = np.double(frequencies[c])
        lhs_eq.append(prev_a)
        lhs_eq.append(prev_not_a)

        #print("after norm:")       
        #print("rhs_inequality_equation2") 
        #print(rhs_inequality_equation2) 
        #print("lhs_inequality_equation2")
        #print(lhs_inequality_equation2)
        #print("rhs_inequality_equation4") 
        #print(rhs_inequality_equation4) 
        #print("lhs_inequality_equation4")
        #print(lhs_inequality_equation4)
        #print("rhs_inequality_equation1")
        #print(rhs_inequality_equation1)
        #print("lhs_inequality_equation1")
        #print(lhs_inequality_equation1)
        #print("rhs_inequality_equation3")
        #print(rhs_inequality_equation3)
        #print("lhs_inequality_equation3")
        #print(lhs_inequality_equation3)
                                               


        #window = 1.0
        window = 1.0
        #cut = 1.0
        cut = 1.0
        lastTrue = None
        #At first test window at 1.0 to ensure that there is a solution at all , then narrow down on it with binary search to get the smallest feasable window
        #while cut > 0.05: 
        while cut > 0.0005:                                
                lhs_ineq = []
                rhs_ineq = []
               # ls_R=np.zeros((size,size),np.double) 
               # ls_s=np.zeros (size,np.double)	
                ls_R=[]
                ls_s=[]
                    
                for vardict,numval_dict in invars:
                        #relative_risk= numval_dict["relative_risk"] if "relative_risk" in numval_dict else (
                         #           (prevalence_condition_regardless-numval_dict["sensitivity"])/numval_dict["sensitivity"] if "sensitivity" in numval_dict else 1) 
                        for k, varlist in vardict.items():
                                if k not in validation_row:
                                    validation_row[k] = {}
                                for v in varlist:
                                        window_factor = get_window(bayesianNetwork,invars,outvars,prob_a_given_not_b,k,v)
                                        ci_window = window *window_factor[k][v] if k in window_factor and v in window_factor[k] else window
                                        #equality doesnt work
                                        #lhs_eq.append(lhs_equality_equation1[k][v])
                                        #rhs_eq.append(rhs_equality_equation1[k][v])
                                        #lhs_eq.append(lhs_equality_equation2[k][v])
                                        #rhs_eq.append(rhs_equality_equation2[k][v])
                                        
                                        #UB

                                        rhs_ineq1 = 0. if rhs_inequality_equation1[k][v]+ ci_window < 0 else (
                                                1. if rhs_inequality_equation1[k][v] + ci_window > 1 else rhs_inequality_equation1[k][v]+ci_window )
                                        rhs_ineq2 = 0. if rhs_inequality_equation2[k][v] + ci_window  < 0 else (
                                                1. if rhs_inequality_equation2[k][v] + ci_window >  1 else rhs_inequality_equation2[k][v]+ ci_window) 
                                        rhs_ineq3 = 0. if rhs_inequality_equation3[k][v]+ ci_window < 0 else (
                                                1. if rhs_inequality_equation3[k][v] + ci_window > 1 else rhs_inequality_equation3[k][v]+ci_window )
                                        rhs_ineq4 = 0. if rhs_inequality_equation4[k][v] + ci_window < 0 else (
                                                1. if rhs_inequality_equation4[k][v] + ci_window > 1 else rhs_inequality_equation4[k][v]+ci_window )

                                        lhs_ineq.append(np.multiply(lhs_inequality_equation1[k][v],1.))
                                        rhs_ineq.append(rhs_ineq1)
                                        lhs_ineq.append(np.multiply(lhs_inequality_equation2[k][v],1.))
                                        rhs_ineq.append(rhs_ineq2)
                                        lhs_ineq.append(np.multiply(lhs_inequality_equation3[k][v],1.))
                                        rhs_ineq.append(rhs_ineq3)
                                        lhs_ineq.append(np.multiply(lhs_inequality_equation4[k][v],1.))
                                        rhs_ineq.append(rhs_ineq4)


                                        #LB
 
                                        rhs_ineq1 = 0. if rhs_inequality_equation1[k][v]- ci_window  < 0 else (
                                                1. if rhs_inequality_equation1[k][v] - ci_window  > 1 else rhs_inequality_equation1[k][v]-ci_window )
                                        rhs_ineq2 = 0. if rhs_inequality_equation2[k][v] - ci_window  < 0 else (
                                                1. if rhs_inequality_equation2[k][v] - ci_window > 1 else rhs_inequality_equation2[k][v]-ci_window )
                                        rhs_ineq3 = 0. if rhs_inequality_equation3[k][v]- ci_window  < 0 else (
                                                1. if rhs_inequality_equation3[k][v] -ci_window > 1 else rhs_inequality_equation3[k][v]-ci_window )
                                        rhs_ineq4 = 0. if rhs_inequality_equation4[k][v] - ci_window < 0 else (
                                                1. if rhs_inequality_equation4[k][v] - ci_window > 1 else rhs_inequality_equation4[k][v]- ci_window )

                                        lhs_ineq.append(np.multiply(lhs_inequality_equation1[k][v],-1.))
                                        rhs_ineq.append(-rhs_ineq1)
                                        lhs_ineq.append(np.multiply(lhs_inequality_equation2[k][v],-1.))
                                        rhs_ineq.append(-rhs_ineq2)
                                        lhs_ineq.append(np.multiply(lhs_inequality_equation3[k][v],-1.))
                                        rhs_ineq.append(-rhs_ineq3)
                                        lhs_ineq.append(np.multiply(lhs_inequality_equation4[k][v],-1.))
                                        rhs_ineq.append(-rhs_ineq4)


                                        #Objectie Function
                                        ls_R.append(np.multiply(lhs_inequality_equation1[k][v],1.))
                                        ls_s.append(rhs_inequality_equation1[k][v])
                                        ls_R.append(np.multiply(lhs_inequality_equation2[k][v],1.))
                                        ls_s.append(rhs_inequality_equation2[k][v])
                                        ls_R.append(np.multiply(lhs_inequality_equation3[k][v],1.))
                                        ls_s.append(rhs_inequality_equation3[k][v])
                                        ls_R.append(np.multiply(lhs_inequality_equation4[k][v],1.))
                                        ls_s.append(rhs_inequality_equation4[k][v])


                                        row = validation(k,v,rhs_inequality_equation2[k][v],condition_val,invars,outvars,window,window_factor,prob_a_given_not_b)
                                        if len(row)  > 0:
                                            validation_row[k][v] = row
                                            #print("row")
                                            #print(row)
                
                #opt = linprog(c=obj, A_ub=lhs_ineq, b_ub=rhs_ineq,A_eq=lhs_eq, b_eq=rhs_eq, bounds=bnd,method="revised simplex")
                #print ("obj")
                #print (obj)
                #print ("P")
                #print(P)
                #print ("q")
                #print (q)

                #print ("lhs_eq")
                #print(lhs_eq)
                #print("rhs_eq")
                #print (rhs_eq)

                #print ("lhs_ineq")
                #print(lhs_ineq)
                #print("rhs_ineq")
                #print (rhs_ineq)
                #print ("bnd")
                #print(bnd)
                #print ("lb")
                #print (lb)
                #print("ub")
                #print (ub)
                lhs_ineq = np.array(lhs_ineq,np.double)
                rhs_ineq = np.array(rhs_ineq,np.double)
                lhs_eq = np.array(lhs_eq,np.double)
                rhs_eq = np.array(rhs_eq,np.double)
                ls_s = np.array(ls_s,np.double)
                ls_R = np.array(ls_R,np.double)

                #print ("lhs_ineq.shape")
                #print (lhs_ineq.shape)
                #print ("rhs_ineq.shape")
                #print (rhs_ineq.shape)
                #print ("lhs_eq.shape")
                #print (lhs_eq.shape)
                #print ("rhs_eq.shape")
                #print (rhs_eq.shape)

                #print ("ls_s.shape")
                #print (ls_s.shape)
                #print ("ls_R.shape")
                #print (ls_R.shape)


                #P = np.add(np.dot(ls_R.T, ls_R), np.multiply(np.identity(size,np.double),1.)) 

                P = np.dot(ls_R.T, ls_R) 
                q = -np.dot(ls_R.T,ls_s )

                #print ("P.shape")
                #print (P.shape)
                #print ("q.shape")
                #print (q.shape)

                #print ("P")
                #print (P)
                #print ("q")
                #print (q)


                #opt = linprog(c=obj, A_eq=lhs_eq, b_eq=rhs_eq, bounds=bnd,method="revised simplex")
                err = False
                opt = None
                try:
                    opt = solve_qp(P, q, lhs_ineq, rhs_ineq, lhs_eq, rhs_eq,lb,ub,solver="osqp")
                    #opt = solve_qp(P, q, lhs_ineq, rhs_ineq, lhs_eq, rhs_eq,lb,ub)
                    #opt = linprog(c=obj, A_ub=lhs_ineq, b_ub=rhs_ineq,A_eq=lhs_eq, b_eq=rhs_eq,  bounds=bnd,method="revised simplex")
                except ValueError as ve:
                    print ("ValueError")
                    print(ve)
                    err = True

                        
                #print("opt")
                #print (opt)
                if window == 1.0 and (err or opt is None): 
                    warning_msg = f"Infeasible at maximum window size for variable val'{condition_val}'"  
                    warnings.warn(warning_msg)
                
                if  err or opt is None: 
                    break
                cut /= 2
                #window = window - cut if not err and opt.success else window + cut
                if not err and opt is not None:# and opt.success:
                    lastTrue = opt
                    #print("lastTrue")
                    #print(lastTrue)
                window = window - cut if not err  else window + cut #fixme if not opt is None
                #print("window")
                #print (window)

        if lastTrue is not None:
            opt = lastTrue
        if opt is not None:
            for i,c in enumerate(cartesian):
                #there are only two values of outvars for relative risk
                cpt_row = []
                cpt_row.extend(c)
                cpt_row.append(list(outvars.items())[0][0])
                val = opt[i] #opt.x[i] 
                cpt_row.append(val)
                cpt_rows.append(cpt_row)
                    
                cpt_row = []
                cpt_row.extend(c)
                cpt_row.append(list(outvars.items())[1][0])
                val =  1.0-opt[i]#opt.x[i]
                cpt_row.append(val)
                cpt_rows.append(cpt_row)

        #if "oesophageal_adenocarcinoma_yes" in outvars:
        #    import ipdb; ipdb.set_trace()
        #print ("cpt_rows")
        #print(cpt_rows)
        toc = time.perf_counter()
        diff = toc - tic
        #print("validation_row")
        #print(validation_row)
        dflist = [valid_dict for k,v_dict in validation_row.items() for v, valid_dict in v_dict.items()]
        #print("dflist")
        #print(dflist)
        if len (dflist) > 0:
            df = pd.DataFrame(dflist)  
            #print(df)
            df.to_csv(f"./bayesnet_initialize_output/{condition_val}_validation.csv", index = False)

        #print (f"{invars} ==> {outvars} took {diff} seconds")
        return (cpt_rows,keylist,outvars,description)
"""
def dependency_direct(bayesianNetwork, cpt, invars, outvars, priors = None, adjust=False, w=0.7, longevity_data=None, data_path='./data/preprocessed_nhanes.csv'):
        
        # Modified dependency_direct function with NHANES data fitting.
        
        # The optimization minimizes:
        # w * sum(wi(pi-pi')^2) + (1-w) * sum(1/dist(i,j) * (pi-pj)^2)
        
        # Where:
        # - pi are the optimized CPT probabilities
        # - pi' are the observed probabilities from NHANES data
        # - wi are weights based on sample sizes
        # - dist(i,j) is the feature distance between CPT slots
        # - w controls the balance between data fitting and smoothness (default 0.7)
        
        # Args:
        #     bayesianNetwork: The Bayesian network
        #     cpt: Conditional probability tables
        #     invars: Input variables
        #     outvars: Output variables
        #     priors: Prior probabilities (optional)
        #     adjust: Whether to adjust probabilities
        #     w: Weight for data fit vs smoothness (0 to 1)
        #     longevity_data: DataFrame with NHANES data (optional)
        #     data_path: Path to load data from if longevity_data not provided (optional)
        
        #print('DEPENDENCY DIRECT')
        #print('invars')
        #print(invars)
        #print('outvar')
        #print(outvars)
        import itertools
        from scipy.optimize import linprog
        from qpsolvers import solve_qp
        import warnings
        import time
        import os

        validation_row = {}
        #print("start timing...")
        tic = time.perf_counter()

        keyset = OrderedSet([])
        frequency_cache = {}
        prevalence_condition_regardless = list(outvars.items())[0][1]
        condition_val = list(outvars.items())[0][0]
        if priors is None:
            priors = get_priors(bayesianNetwork,invars,prevalence_condition_regardless,cpt,adjust)
        #print("priors")
        #print(priors)
        prob_a_given_b, prob_a_given_not_b, prob_not_a_given_b, prob_not_a_given_not_b =  prob_a_and_not_a_given_b_and_not_b(invars, priors, outvars)

        description = "Against the baseline risks, "
        firsttime = True
        for k,v in outvars.items():                        
                phrase = "" if firsttime else " and "
                description + phrase + k + " of " + str(v) + " , "
                firsttime = False
        firsttime = True
        for vardict,numval_dict in invars:
                for val, varlist in vardict.items():
                        for test,num in numval_dict.items():
                                keyset.add(val)
                                phrase = "" if firsttime else (" , and " if varlist [-1] is val else " , " )

                                description= description + phrase + "the "+ test.replace("_"," ")  +" that {0} will be " + condition_val +" for those in the " + val + " category of " 
                                firsttime1 = True
                                sum_priors = 0
                                for var in varlist:                                
                                        phrase = "" if firsttime1 else " or "
                                        description = description + phrase + var
                                        firsttime1 = False
                                        #print("val var to priors")
                                        #print (f"{val}  : {var}")
                                        sum_priors += priors[val][var]
                                description = description + " is " + str(num)

                                firsttime = False
                                        
        description = description + "."
        
        #print("prob_a_given_not_b")
        #print (prob_a_given_not_b)
        #print("prob_a_given_b")
        #print(prob_a_given_b)


        #print("prob_not_a_given_not_b")
        #print (prob_not_a_given_not_b)
        #print("prob_not_a_given_b")
        #print(prob_not_a_given_b)

        keylist = list(keyset)
        pos = {k:n for n,k in enumerate(keylist)}
        vdict = dictVarsAndValues(bayesianNetwork, cpt)

        #print("vdict")
        #print(vdict)
        val_prev = {}
        not_val_prev = {}
        for k in keylist:
                val_prev[k]={}
                not_val_prev[k] = {}
                previous = None
                previous_not = None
                for v in vdict[k]:
                        #natural order is worse to better, and we want to fill in worse first because better is more accurate
                        if k in prob_a_given_b and v in prob_a_given_b[k]:
                                val_prev[k][v] = prob_a_given_b[k][v] 
                                previous = prob_a_given_not_b[k][v]
                        elif previous is not None:
                                val_prev [k][v] = previous
                        else:
                                val_prev [k][v] = prevalence_condition_regardless
                        if k in prob_not_a_given_b and v in prob_not_a_given_b[k]:
                                not_val_prev[k][v] = prob_not_a_given_b[k][v] 
                                previous_not = prob_not_a_given_not_b[k][v]
                        elif previous_not is not None:
                                not_val_prev [k][v] = previous_not
                        else:
                                not_val_prev [k][v] = prevalence_condition_regardless
        #print("val_prev")                        
        #print(val_prev)                            

        vlist = [vdict[v] for v in keylist]
        #print("vlist")
        #print (vlist)
        cartesian = list(itertools.product(*vlist))
       #in cartesian, worst is first and best comes later
        cpt_rows = []
        lhs_inequality_equation1 = {}
        lhs_inequality_equation2 = {}
        lhs_inequality_equation3 = {}
        lhs_inequality_equation4 = {}
        rhs_inequality_equation1 = {}
        rhs_inequality_equation2 = {}
        rhs_inequality_equation3 = {}
        rhs_inequality_equation4 = {}
        obj = np.ones(2*len(cartesian))

        frequencies = get_frequencies(bayesianNetwork,keylist,cpt,adjust)
        #print ("frequencies")
        #print(frequencies)
        #bnd = [(1.0,1.0)] * len(cartesian)
        bnd = []
 
        lhs_eq = []
        rhs_eq = []
                                
        included_vars = {}                        
        for excluded in keylist:
            included_vars[excluded]= keylist[:pos[excluded]] + keylist[pos[excluded]+1:] 
        for i,c in enumerate(cartesian):
                #print ("i:c")
                #print (i)
                #print (c)
                #print ("frequencies[c]")
                #print(frequencies[c])
                #equation1  (doesnt have every one in it )
                #non elderly hbp prevalence  = 
                #  (prevalence of hbp among adult obese psych) * prevalence of adult obese psych 
                #+ (prevalence of hbp among youngadult obese psych) * prevalence of youngadult obese psych
                #+ (prevalence of hbp among teen obese psych) * prevalence of teen obese psych
                #+ (prevalence of hbp among child obese psych) * prevalence of child obese psych
                #+ (prevalence of hbp among adult overweight psych) * prevalence of adult overweight psych
                #+ (prevalence of hbp among youngadult overweight psych) * prevalence of youngadult overweight psych
                #+ (prevalence of hbp among teen overweight psych) * prevalence of teen overweight psych
                #+ (prevalence of hbp among child overweight psych) * prevalence of child overweight psych
                #+ (prevalence of hbp among adult healthy psych) * prevalence of adult healthy psych
                #+ (prevalence of hbp among youngadult healthy psych) * prevalence of youngadult healthy psych
                #+ (prevalence of hbp among teen healthy psych) * prevalence of teen healthy psych
                #+ (prevalence of hbp among child healthy psych) * prevalence of child healthy psych
                #+ (prevalence of hbp among adult obese nonpsych) * prevalence of adult obese nonpsych 
                #+ (prevalence of hbp among youngadult obese nonpsych) * prevalence of youngadult obese nonpsych
                #+ (prevalence of hbp among teen obese nonpsych) * prevalence of teen obese nonpsych
                #+ (prevalence of hbp among child obese nonpsych) * prevalence of child obese nonpsych
                #+ (prevalence of hbp among adult overweight nonpsych) * prevalence of adult overweight nonpsych
                #+ (prevalence of hbp among youngadult overweight nonpsych) * prevalence of youngadult overweight nonpsych
                #+ (prevalence of hbp among teen overweight nonpsych) * prevalence of teen overweight nonpsych
                #+ (prevalence of hbp among child overweight nonpsych) * prevalence of child overweight nonpsych
                #+ (prevalence of hbp among adult healthy nonpsych) * prevalence of adult healthy nonpsych
                #+ (prevalence of hbp among youngadult healthy nonpsych) * prevalence of youngadult healthy nonpsych
                #+ (prevalence of hbp among teen healthy nonpsych) * prevalence of teen healthy nonpsych
                #+ (prevalence of hbp among child healthy nonpsych) * prevalence of child healthy nonpsych
                         
                         
                #equation2 (has the balance)         
                #elderly hbp prevalence  = 
                #  (prevalence of hbp among elderly obese psych) * prevalence of elderly obese psych 
                #+ (prevalence of hbp among elderly overweight psych) * prevalence of elderly overweight psych
                #+ (prevalence of hbp among elderly healthy psych) * prevalence of elderly healthy psych
                #+ (prevalence of hbp among elderly obese nonpsych) * prevalence of elderly obese nonpsych 
                #+ (prevalence of hbp among elderly overweight nonpsych) * prevalence of elderly overweight nonpsych
                #+ (prevalence of hbp among elderly healthy nonpsych) * prevalence of elderly healthy nonpsych
                
                 
                #equation3  (doesnt have every one in it )
                #non elderly non hbp prevalence  = 
                #  (prevalence of non hbp among adult obese psych) * prevalence of adult obese psych 
                #+ (prevalence of non hhbp among youngadult obese psych) * prevalence of youngadult obese psych
                #+ (prevalence of non hhbp among teen obese psych) * prevalence of teen obese psych
                #+ (prevalence of non hhbp among child obese psych) * prevalence of child obese psych
                #+ (prevalence of non hbp among adult overweight psych) * prevalence of adult overweight psych
                #+ (prevalence of non hbp among youngadult overweight psych) * prevalence of youngadult overweight psych
                #+ (prevalence of non hbp among teen overweight psych) * prevalence of teen overweight psych
                #+ (prevalence of non hbp among child overweight psych) * prevalence of child overweight psych
                #+ (prevalence of non hbp among adult healthy psych) * prevalence of adult healthy psych
                #+ (prevalence of non hbp among youngadult healthy psych) * prevalence of youngadult healthy psych
                #+ (prevalence of non hbp among teen healthy psych) * prevalence of teen healthy psych
                #+ (prevalence of non hbp among child healthy psych) * prevalence of child healthy psych
                #+ (prevalence of non hbp among adult obese nonpsych) * prevalence of adult obese nonpsych 
                #+ (prevalence of non hbp among youngadult obese nonpsych) * prevalence of youngadult obese nonpsych
                #+ (prevalence of non hbp among teen obese nonpsych) * prevalence of teen obese nonpsych
                #+ (prevalence of non hbp among child obese nonpsych) * prevalence of child obese nonpsych
                #+ (prevalence of non hbp among adult overweight nonpsych) * prevalence of adult overweight nonpsych
                #+ (prevalence of non hbp among youngadult overweight nonpsych) * prevalence of youngadult overweight nonpsych
                #+ (prevalence of non hbp among teen overweight nonpsych) * prevalence of teen overweight nonpsych
                #+ (prevalence of non hbp among child overweight nonpsych) * prevalence of child overweight nonpsych
                #+ (prevalence of non hbp among adult healthy nonpsych) * prevalence of adult healthy nonpsych
                #+ (prevalence of non hbp among youngadult healthy nonpsych) * prevalence of youngadult healthy nonpsych
                #+ (prevalence of non hbp among teen healthy nonpsych) * prevalence of teen healthy nonpsych
                #+ (prevalence of non hbp among child healthy nonpsych) * prevalence of child healthy nonpsych
                         
                         
                #equation4 (has the balance)         
                #elderly with non hbp prevalence = 
                #  (prevalence of non hbp among elderly obese psych) * prevalence of elderly obese psych 
                #+ (prevalence of non hbp among elderly overweight psych) * prevalence of elderly overweight psych
                #+ (prevalence of non hbp among elderly healthy psych) * prevalence of elderly healthy psych
                #+ (prevalence of non hbp among elderly obese nonpsych) * prevalence of elderly obese nonpsych 
                #+ (prevalence of non hbp among elderly overweight nonpsych) * prevalence of elderly overweight nonpsych
                #+ (prevalence of non hbp among elderly healthy nonpsych) * prevalence of elderly healthy nonpsych
                
                #equations 1-4 are inequalities, but then there are c equalities , equations that say prob a + ~prob a == 1

                rhs_eq.append(1.)
                lhs_eq_list = np.zeros(2*len(cartesian))
                lhs_eq_list[i]=1.
                lhs_eq_list[i+len(cartesian)]=1.
                lhs_eq.append(lhs_eq_list)

               
                for vardict,numval_dict in invars:
                        #relative_risk = numval_dict["relative_risk"] if "relative_risk" in numval_dict else (
                        #    (prevalence_condition_regardless-numval_dict["sensitivity"])/numval_dict["sensitivity"] if "sensitivity" in numval_dict else 1) 
                        for k, varlist in vardict.items():
                        
                                if k not in lhs_inequality_equation1:
                                        lhs_inequality_equation1[k] = {}
                                if k not in lhs_inequality_equation2:
                                        lhs_inequality_equation2[k] = {}
                                if k not in lhs_inequality_equation3:
                                        lhs_inequality_equation3[k] = {}
                                if k not in lhs_inequality_equation4:
                                        lhs_inequality_equation4[k] = {}
                                if k not in rhs_inequality_equation1:
                                        rhs_inequality_equation1[k] = {}
                                if k not in rhs_inequality_equation2:
                                        rhs_inequality_equation2[k] = {}
                                if k not in rhs_inequality_equation3:
                                        rhs_inequality_equation3[k] = {}
                                if k not in rhs_inequality_equation4:
                                        rhs_inequality_equation4[k] = {}
                

                                for v in varlist:
                                        if v not in lhs_inequality_equation1[k]:
                                                lhs_inequality_equation1[k][v] = np.zeros(2*len(cartesian),np.double) #lhs will be list of lists
                                        if v not in lhs_inequality_equation2[k]:
                                                lhs_inequality_equation2[k][v] = np.zeros(2*len(cartesian),np.double)
                                        if v not in lhs_inequality_equation3[k]:
                                                lhs_inequality_equation3[k][v] = np.zeros(2*len(cartesian),np.double) #lhs will be list of lists
                                        if v not in lhs_inequality_equation4[k]:
                                                lhs_inequality_equation4[k][v] = np.zeros(2*len(cartesian),np.double)
                                        if v not in rhs_inequality_equation1[k]:
                                                rhs_inequality_equation1[k][v] = 0 #rhs will be list of floats
                                        if v not in rhs_inequality_equation2[k]:
                                                rhs_inequality_equation2[k][v] = 0
                                        if v not in rhs_inequality_equation3[k]:
                                                rhs_inequality_equation3[k][v] = 0 #rhs will be list of floats
                                        if v not in rhs_inequality_equation4[k]:
                                                rhs_inequality_equation4[k][v] = 0
                                        #print ("c[pos[k]]")
                                        #print (c[pos[k]])
                                        #tup1 = included_vars[k].copy().sort() if len(included_vars[k]) > 0 else ()
                                        #input_tuple = (tup1,v)
                                        #if input_tuple not in frequency_cache:
                                            #prob_others_given_b = get_frequencies(bayesianNetwork,included_vars[k],cpt,preconditionals = {k:v})
                                            #frequency_cache[input_tuple] = prob_others_given_b
                                            #print (f"{input_tuple} stored in frequency cache")
                                        #else:
                                            #prob_others_given_b = frequency_cache[input_tuple]
                                            #print(f"{input_tuple} retrieved from frequency cache of size {len(frequency_cache)}")
                                        #print ("prob_others_given_b")
                                        #print (prob_others_given_b)
                                        #c_no_k = c[:pos[k]]+c[pos[k]+1:]
                                        if c[pos[k]]  == v:
                                                # rhs_equality_equation2[k][v] = priors[k][v]*relative_risk* val_prev[k][v]
                                                rhs_inequality_equation2[k][v] = prob_a_given_b [k][v]
                                                lhs_inequality_equation2[k][v][i] = np.double(frequencies[c]) #prob_others_given_b[c_no_k]
                                                rhs_inequality_equation4[k][v] = prob_not_a_given_b [k][v]  
                                                lhs_inequality_equation4[k][v][i+len(cartesian)] = np.double(frequencies[c])#prob_others_given_b[c_no_k]
                                        else:
                                                # rhs_equality_equation1[k][v] = val_prev[k][v]
                                                rhs_inequality_equation1[k][v] = prob_a_given_not_b[k][v]
                                                lhs_inequality_equation1[k][v][i] = np.double(frequencies[c])  #prob_others_given_b[c_no_k]
                                                rhs_inequality_equation3[k][v] =  prob_not_a_given_not_b [k][v] 
                                                lhs_inequality_equation3[k][v][i+len(cartesian)] = np.double(frequencies[c] )#prob_others_given_b[c_no_k]
                                                                                       

                #make independence the lower bound
                #product = 1
                #for j,k in enumerate(keylist):
                        #product *= 1.-val_prev [k][c[j]] 
                #bnd.append(( 1-product, 1.0))

                minimum = 1.0
                for j,k in enumerate(keylist):
                        if val_prev [k][c[j]] < minimum:
                            minimum = val_prev [k][c[j]]
                #bnd.append((minimum, 1.0))
                bnd.append((0,1.0))

        for i,c in enumerate(cartesian):
                minimum_not = 1.0
                for j,k in enumerate(keylist):
                        if not_val_prev [k][c[j]] < minimum_not:
                            minimum_not = not_val_prev [k][c[j]]
                #bnd.append((minimum_not, 1.0))
                bnd.append((0, 1.0))

        size = 2*len(cartesian)

        # NEW OPTIMIZATION FUNCTION STARTS HERE
        
        # Load data if path provided and longevity_data not already loaded
        if longevity_data is None and data_path is not None and os.path.exists(data_path):
            try:
                if data_path.endswith('.parquet'):
                    longevity_data = pd.read_parquet(data_path)
                elif data_path.endswith('.csv'):
                    # Try to read with index column first
                    try:
                        longevity_data = pd.read_csv(data_path, index_col=0)
                    except:
                        # If that fails, read without index column
                        longevity_data = pd.read_csv(data_path)
                else:
                    print(f"Unsupported file format: {data_path}")
                    longevity_data = None
                
                if longevity_data is not None:
                    print(f"Loaded data from {data_path}, shape: {longevity_data.shape}")
            except Exception as e:
                print(f"Error loading data from {data_path}: {e}")
                longevity_data = None
        
        # Get observed probabilities from data (p')
        observed_probs = {}
        weights = {}
        
        if longevity_data is not None:
            # Use actual data to get observed probabilities
            total_population = len(longevity_data)
            output_var = list(outvars.keys())[0]
            output_val = list(outvars.keys())[0]  # Assuming first key is the positive outcome
            
            # Check if required variables are in the data
            required_vars = keylist + [output_var]
            missing_vars = [var for var in required_vars if var not in longevity_data.columns]
            
            if missing_vars:
                print(f"Warning: Missing variables in data: {missing_vars}")
                # Fall back to using frequencies and priors
                for i, c in enumerate(cartesian):
                    weights[i] = frequencies[c] if frequencies[c] > 0 else 1e-6
                    observed_probs[i] = val_prev[keylist[0]][c[0]]
            else:
                # Process each CPT slot
                for i, c in enumerate(cartesian):
                    # Create input conditions for this CPT slot
                    input_var_dict = {keylist[j]: c[j] for j in range(len(keylist))}
                    output_var_dict = {output_var: output_val}
                    
                    try:
                        # Get observed probability for this slot
                        prob_data = cpt_cell_from_data(output_var_dict, input_var_dict, longevity_data)
                        
                        # Handle different return types from cpt_cell_from_data
                        if isinstance(prob_data, dict):
                            observed_probs[i] = prob_data.get(output_val, 0.0)
                        else:
                            observed_probs[i] = float(prob_data) if prob_data is not None else 0.0
                        
                        # Get percentage of population in this slot
                        percentage = percentage_cpt_cell(output_var_dict, input_var_dict, longevity_data)
                        weights[i] = percentage / 100.0 if percentage > 0 else 1e-6
                        
                    except Exception as e:
                        print(f"Error processing slot {i} with conditions {input_var_dict}: {e}")
                        # Fall back to default values for this slot
                        weights[i] = frequencies[c] if frequencies[c] > 0 else 1e-6
                        observed_probs[i] = val_prev[keylist[0]][c[0]]
                
                # Normalize weights to sum to 1
                total_weight = sum(weights.values())
                if total_weight > 0:
                    weights = {k: v/total_weight for k, v in weights.items()}
                else:
                    # If all weights are 0, use equal weights
                    n_slots = len(cartesian)
                    weights = {i: 1.0/n_slots for i in range(n_slots)}
        else:
            # Fallback: use frequencies as weights and priors as observed probabilities
            for i, c in enumerate(cartesian):
                # Use the frequency of this combination as weight
                weights[i] = frequencies[c] if frequencies[c] > 0 else 1e-6
                
                # Use the prior probability as observed probability
                # This is a reasonable fallback when no data is available
                observed_probs[i] = val_prev[keylist[0]][c[0]]
            
            # Normalize weights
            total_weight = sum(weights.values())
            if total_weight > 0:
                weights = {k: v/total_weight for k, v in weights.items()}
        
        # Calculate feature distances between all pairs of CPT slots
        num_values_per_feature = [len(vdict[key]) for key in keylist]
        feature_distances = {}
        
        for i, c1 in enumerate(cartesian):
            for j, c2 in enumerate(cartesian):
                if i != j:
                    distance = 0.0
                    for k, key in enumerate(keylist):
                        # Find indices of values in the feature
                        idx1 = vlist[k].index(c1[k])
                        idx2 = vlist[k].index(c2[k])
                        # Normalized distance for this feature
                        if num_values_per_feature[k] > 1:
                            norm_dist = abs(idx1 - idx2) / (num_values_per_feature[k] - 1)
                            distance += norm_dist ** 2
                    distance = np.sqrt(distance / len(keylist)) if len(keylist) > 0 else 1.0
                    feature_distances[(i, j)] = max(distance, 0.01)  # Avoid division by zero
        
        # Build P and q matrices for the optimization function:
        # minimize: w * sum(wi(pi-pi')^2) + (1-w) * sum(1/dist(i,j) * (pi-pj)^2)
        
        # Use hyperparameter w (weight between data fit and smoothness) from function parameter
        
        P = np.zeros((size, size), np.double)
        q = np.zeros(size, np.double)
        
        # Part 1: Data fitting term - w * sum(wi(pi-pi')^2)
        for i in range(len(cartesian)):
            # For P(outcome|slot)
            P[i, i] += w * weights[i]
            q[i] -= 2 * w * weights[i] * observed_probs[i]
            
            # For P(~outcome|slot)
            P[i + len(cartesian), i + len(cartesian)] += w * weights[i]
            q[i + len(cartesian)] -= 2 * w * weights[i] * (1 - observed_probs[i])
        
        # Part 2: Smoothness term - (1-w) * sum(1/dist(i,j) * (pi-pj)^2)
        # Only add if we have valid distances and w < 1
        if w < 1.0 and len(feature_distances) > 0:
            for (i, j), dist in feature_distances.items():
                weight = (1 - w) / dist
                
                # For P(outcome|slot)
                P[i, i] += weight
                P[i, j] -= weight
                P[j, i] -= weight  # Symmetric
                P[j, j] += weight
                
                # For P(~outcome|slot)
                P[i + len(cartesian), i + len(cartesian)] += weight
                P[i + len(cartesian), j + len(cartesian)] -= weight
                P[j + len(cartesian), i + len(cartesian)] -= weight  # Symmetric
                P[j + len(cartesian), j + len(cartesian)] += weight
        
        # If no smoothness (w=1) and some weights are 0, use small regularization
        # to ensure positive definite matrix
        if w == 1.0:
            for i in range(size):
                if P[i, i] == 0:
                    P[i, i] = 1e-6
        
        # Check if P is positive semi-definite and fix if needed
        try:
            eigenvalues = np.linalg.eigvals(P)
            min_eigenvalue = np.min(eigenvalues.real)
            if min_eigenvalue < 1e-10:
                # Add small value to diagonal to make positive definite
                P += np.eye(size) * (abs(min_eigenvalue) + 1e-6)
        except:
            # If eigenvalue computation fails, add small regularization
            P += np.eye(size) * 1e-6
        
        # NEW OPTIMIZATION FUNCTION ENDS HERE

        lb = np.zeros(size,np.double)
        ub = np.ones(size,np.double)

        #print("before norm:")       
        #print("rhs_inequality_equation2") 
        #print(rhs_inequality_equation2) 
        #print("lhs_inequality_equation2")
        #print(lhs_inequality_equation2)
        #print("rhs_inequality_equation4") 
        #print(rhs_inequality_equation4) 
        #print("lhs_inequality_equation4")
        #print(lhs_inequality_equation4)
        #print("rhs_inequality_equation1")
        #print(rhs_inequality_equation1)
        #print("lhs_inequality_equation1")
        #print(lhs_inequality_equation1)
        #print("rhs_inequality_equation3")
        #print(rhs_inequality_equation3)
        #print("lhs_inequality_equation3")
        #print(lhs_inequality_equation3)
                                               


        for vardict,numval_dict in invars:
                for k, varlist in vardict.items():
                    for v in varlist:
                        norm = np.sum(lhs_inequality_equation1[k][v])
                        if norm != 0:
                            lhs_inequality_equation1[k][v]=lhs_inequality_equation1[k][v]/norm
                        norm = np.sum(lhs_inequality_equation2[k][v])
                        if norm != 0:
                            lhs_inequality_equation2[k][v]=lhs_inequality_equation2[k][v]/norm
                        norm = np.sum(lhs_inequality_equation3[k][v])
                        if norm != 0:
                            lhs_inequality_equation3[k][v]=lhs_inequality_equation3[k][v]/norm
                        norm = np.sum(lhs_inequality_equation4[k][v])
                        if norm != 0:
                            lhs_inequality_equation4[k][v]=lhs_inequality_equation4[k][v]/norm

        #We also want to include that the first half adds to the prevaence of a and the sencond to the prevalence of ~a


        rhs_eq.append(prevalence_condition_regardless)
        rhs_eq.append(1.-prevalence_condition_regardless)
        prev_a = np.zeros(2*len(cartesian),np.double)
        prev_not_a = np.zeros(2*len(cartesian),np.double)
        for i,c in enumerate(cartesian):
                prev_a[i]=np.double(frequencies[c])
                prev_not_a [i+len(cartesian)] = np.double(frequencies[c])
        lhs_eq.append(prev_a)
        lhs_eq.append(prev_not_a)

        #print("after norm:")       
        #print("rhs_inequality_equation2") 
        #print(rhs_inequality_equation2) 
        #print("lhs_inequality_equation2")
        #print(lhs_inequality_equation2)
        #print("rhs_inequality_equation4") 
        #print(rhs_inequality_equation4) 
        #print("lhs_inequality_equation4")
        #print(lhs_inequality_equation4)
        #print("rhs_inequality_equation1")
        #print(rhs_inequality_equation1)
        #print("lhs_inequality_equation1")
        #print(lhs_inequality_equation1)
        #print("rhs_inequality_equation3")
        #print(rhs_inequality_equation3)
        #print("lhs_inequality_equation3")
        #print(lhs_inequality_equation3)
                                               


        #window = 1.0
        window = 1.0
        #cut = 1.0
        cut = 1.0
        lastTrue = None
        #At first test window at 1.0 to ensure that there is a solution at all , then narrow down on it with binary search to get the smallest feasable window
        #while cut > 0.05: 
        while cut > 0.0005:                                
                lhs_ineq = []
                rhs_ineq = []
               # ls_R=np.zeros((size,size),np.double) 
               # ls_s=np.zeros (size,np.double)	
                ls_R=[]
                ls_s=[]
                    
                for vardict,numval_dict in invars:
                        #relative_risk= numval_dict["relative_risk"] if "relative_risk" in numval_dict else (
                         #           (prevalence_condition_regardless-numval_dict["sensitivity"])/numval_dict["sensitivity"] if "sensitivity" in numval_dict else 1) 
                        for k, varlist in vardict.items():
                                if k not in validation_row:
                                    validation_row[k] = {}
                                for v in varlist:
                                        window_factor = get_window(bayesianNetwork,invars,outvars,prob_a_given_not_b,k,v)
                                        ci_window = window *window_factor[k][v] if k in window_factor and v in window_factor[k] else window
                                        #equality doesnt work
                                        #lhs_eq.append(lhs_equality_equation1[k][v])
                                        #rhs_eq.append(rhs_equality_equation1[k][v])
                                        #lhs_eq.append(lhs_equality_equation2[k][v])
                                        #rhs_eq.append(rhs_equality_equation2[k][v])
                                        
                                        #UB

                                        rhs_ineq1 = 0. if rhs_inequality_equation1[k][v]+ ci_window < 0 else (
                                                1. if rhs_inequality_equation1[k][v] + ci_window > 1 else rhs_inequality_equation1[k][v]+ci_window )
                                        rhs_ineq2 = 0. if rhs_inequality_equation2[k][v] + ci_window  < 0 else (
                                                1. if rhs_inequality_equation2[k][v] + ci_window >  1 else rhs_inequality_equation2[k][v]+ ci_window) 
                                        rhs_ineq3 = 0. if rhs_inequality_equation3[k][v]+ ci_window < 0 else (
                                                1. if rhs_inequality_equation3[k][v] + ci_window > 1 else rhs_inequality_equation3[k][v]+ci_window )
                                        rhs_ineq4 = 0. if rhs_inequality_equation4[k][v] + ci_window < 0 else (
                                                1. if rhs_inequality_equation4[k][v] + ci_window > 1 else rhs_inequality_equation4[k][v]+ci_window )

                                        lhs_ineq.append(np.multiply(lhs_inequality_equation1[k][v],1.))
                                        rhs_ineq.append(rhs_ineq1)
                                        lhs_ineq.append(np.multiply(lhs_inequality_equation2[k][v],1.))
                                        rhs_ineq.append(rhs_ineq2)
                                        lhs_ineq.append(np.multiply(lhs_inequality_equation3[k][v],1.))
                                        rhs_ineq.append(rhs_ineq3)
                                        lhs_ineq.append(np.multiply(lhs_inequality_equation4[k][v],1.))
                                        rhs_ineq.append(rhs_ineq4)


                                        #LB
 
                                        rhs_ineq1 = 0. if rhs_inequality_equation1[k][v]- ci_window  < 0 else (
                                                1. if rhs_inequality_equation1[k][v] - ci_window  > 1 else rhs_inequality_equation1[k][v]-ci_window )
                                        rhs_ineq2 = 0. if rhs_inequality_equation2[k][v] - ci_window  < 0 else (
                                                1. if rhs_inequality_equation2[k][v] - ci_window > 1 else rhs_inequality_equation2[k][v]-ci_window )
                                        rhs_ineq3 = 0. if rhs_inequality_equation3[k][v]- ci_window  < 0 else (
                                                1. if rhs_inequality_equation3[k][v] -ci_window > 1 else rhs_inequality_equation3[k][v]-ci_window )
                                        rhs_ineq4 = 0. if rhs_inequality_equation4[k][v] - ci_window < 0 else (
                                                1. if rhs_inequality_equation4[k][v] - ci_window > 1 else rhs_inequality_equation4[k][v]- ci_window )

                                        lhs_ineq.append(np.multiply(lhs_inequality_equation1[k][v],-1.))
                                        rhs_ineq.append(-rhs_ineq1)
                                        lhs_ineq.append(np.multiply(lhs_inequality_equation2[k][v],-1.))
                                        rhs_ineq.append(-rhs_ineq2)
                                        lhs_ineq.append(np.multiply(lhs_inequality_equation3[k][v],-1.))
                                        rhs_ineq.append(-rhs_ineq3)
                                        lhs_ineq.append(np.multiply(lhs_inequality_equation4[k][v],-1.))
                                        rhs_ineq.append(-rhs_ineq4)


                                        #Objectie Function
                                        ls_R.append(np.multiply(lhs_inequality_equation1[k][v],1.))
                                        ls_s.append(rhs_inequality_equation1[k][v])
                                        ls_R.append(np.multiply(lhs_inequality_equation2[k][v],1.))
                                        ls_s.append(rhs_inequality_equation2[k][v])
                                        ls_R.append(np.multiply(lhs_inequality_equation3[k][v],1.))
                                        ls_s.append(rhs_inequality_equation3[k][v])
                                        ls_R.append(np.multiply(lhs_inequality_equation4[k][v],1.))
                                        ls_s.append(rhs_inequality_equation4[k][v])


                                        row = validation(k,v,rhs_inequality_equation2[k][v],condition_val,invars,outvars,window,window_factor,prob_a_given_not_b)
                                        if len(row)  > 0:
                                            validation_row[k][v] = row
                                            #print("row")
                                            #print(row)
                
                #opt = linprog(c=obj, A_ub=lhs_ineq, b_ub=rhs_ineq,A_eq=lhs_eq, b_eq=rhs_eq, bounds=bnd,method="revised simplex")
                #print ("obj")
                #print (obj)
                #print ("P")
                #print(P)
                #print ("q")
                #print (q)

                #print ("lhs_eq")
                #print(lhs_eq)
                #print("rhs_eq")
                #print (rhs_eq)

                #print ("lhs_ineq")
                #print(lhs_ineq)
                #print("rhs_ineq")
                #print (rhs_ineq)
                #print ("bnd")
                #print(bnd)
                #print ("lb")
                #print (lb)
                #print("ub")
                #print (ub)
                lhs_ineq = np.array(lhs_ineq,np.double)
                rhs_ineq = np.array(rhs_ineq,np.double)
                lhs_eq = np.array(lhs_eq,np.double)
                rhs_eq = np.array(rhs_eq,np.double)
                ls_s = np.array(ls_s,np.double)
                ls_R = np.array(ls_R,np.double)

                #print ("lhs_ineq.shape")
                #print (lhs_ineq.shape)
                #print ("rhs_ineq.shape")
                #print (rhs_ineq.shape)
                #print ("lhs_eq.shape")
                #print (lhs_eq.shape)
                #print ("rhs_eq.shape")
                #print (rhs_eq.shape)

                #print ("ls_s.shape")
                #print (ls_s.shape)
                #print ("ls_R.shape")
                #print (ls_R.shape)


                # P and q matrices are now built using the new optimization function above
                # No need to rebuild them here

                #print ("P.shape")
                #print (P.shape)
                #print ("q.shape")
                #print (q.shape)

                #print ("P")
                #print (P)
                #print ("q")
                #print (q)


                #opt = linprog(c=obj, A_eq=lhs_eq, b_eq=rhs_eq, bounds=bnd,method="revised simplex")
                err = False
                opt = None
                try:
                    opt = solve_qp(P, q, lhs_ineq, rhs_ineq, lhs_eq, rhs_eq,lb,ub,solver="osqp")
                    #opt = solve_qp(P, q, lhs_ineq, rhs_ineq, lhs_eq, rhs_eq,lb,ub)
                    #opt = linprog(c=obj, A_ub=lhs_ineq, b_ub=rhs_ineq,A_eq=lhs_eq, b_eq=rhs_eq,  bounds=bnd,method="revised simplex")
                except ValueError as ve:
                    print ("ValueError")
                    print(ve)
                    err = True

                        
                #print("opt")
                #print (opt)
                if window == 1.0 and (err or opt is None): 
                    warning_msg = f"Infeasible at maximum window size for variable val'{condition_val}'"  
                    warnings.warn(warning_msg)
                
                if  err or opt is None: 
                    break
                cut /= 2
                #window = window - cut if not err and opt.success else window + cut
                if not err and opt is not None:# and opt.success:
                    lastTrue = opt
                    #print("lastTrue")
                    #print(lastTrue)
                window = window - cut if not err  else window + cut #fixme if not opt is None
                #print("window")
                #print (window)

        if lastTrue is not None:
            opt = lastTrue
        if opt is not None:
            for i,c in enumerate(cartesian):
                #there are only two values of outvars for relative risk
                cpt_row = []
                cpt_row.extend(c)
                cpt_row.append(list(outvars.items())[0][0])
                val = opt[i] #opt.x[i] 
                cpt_row.append(val)
                cpt_rows.append(cpt_row)
                    
                cpt_row = []
                cpt_row.extend(c)
                cpt_row.append(list(outvars.items())[1][0])
                val =  1.0-opt[i]#opt.x[i]
                cpt_row.append(val)
                cpt_rows.append(cpt_row)

        #if "oesophageal_adenocarcinoma_yes" in outvars:
        #    import ipdb; ipdb.set_trace()
        #print ("cpt_rows")
        #print(cpt_rows)
        toc = time.perf_counter()
        diff = toc - tic
        #print("validation_row")
        #print(validation_row)
        dflist = [valid_dict for k,v_dict in validation_row.items() for v, valid_dict in v_dict.items()]
        #print("dflist")
        #print(dflist)
        if len (dflist) > 0:
            df = pd.DataFrame(dflist)  
            #print(df)
            df.to_csv(f"./bayesnet_initialize_output/{condition_val}_validation.csv", index = False)

        #print (f"{invars} ==> {outvars} took {diff} seconds")
        return (cpt_rows,keylist,outvars,description)
"""

# Usage examples:

# Option 1: Use default data path './data/preprocessed_nhanes.csv'
# result = dependency_direct(
#     bayesianNetwork=net,
#     cpt=cpt_tables,
#     invars=invars,
#     outvars=outvars,
#     w=0.7
# )

# Option 2: Pass already loaded data
# longevity_data = pd.read_csv('your_data.csv')
# result = dependency_direct(
#     bayesianNetwork=net,
#     cpt=cpt_tables,
#     invars=invars,
#     outvars=outvars,
#     w=0.7,
#     longevity_data=longevity_data
# )

# Option 3: Use a different data path
# result = dependency_direct(
#     bayesianNetwork=net,
#     cpt=cpt_tables,
#     invars=invars,
#     outvars=outvars,
#     w=0.7,
#     data_path='./data/other_nhanes.csv'
# )

# Option 4: No data (explicitly set data_path=None)
# result = dependency_direct(
#     bayesianNetwork=net,
#     cpt=cpt_tables,
#     invars=invars,
#     outvars=outvars,
#     w=0.7,
#     data_path=None
# )