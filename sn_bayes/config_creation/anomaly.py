import pandas as pd
import json

from .utils import (
    is_int,
    is_float,
)


available_anomaly_detectors = {
    'LevelShiftAD',
    'InterQuartileRangeAD',
    'AutoregressionAD',
    'ThresholdAD',
    'QuantileAD',
}
    

def parse_anomaly_data(data: pd.DataFrame) -> dict:

    anomalies_config = {}
    anomalies = data[data['Type']=='anomaly']['output'].unique()

    if len(anomalies) == 0:
        return anomalies_config

    for anomaly in anomalies:
        anomaly_data = {}
        anomaly_rows = data[(data['output'] == anomaly) & (data['Type'] == 'anomaly')]
        
        detectors = []
        for elem in anomaly_rows['input']:
            if elem in available_anomaly_detectors:
                detectors.append(elem)
            else:
                params = json.loads(elem)

        for param, value in params.items():
            if is_int(value):
                params[param] = int(value)
            elif is_float(value):
                params[param] = float(value)

        anomaly_data['detectors'] = detectors
        anomaly_data['parameters'] = params
        anomalies_config[anomaly] = anomaly_data

    for param, value in anomaly_data['parameters'].items():
        if is_int(value):
            anomaly_data['parameters'][param] = int(value)
        elif is_float(value):
            anomaly_data['parameters'][param] = float(value)

    return anomalies_config