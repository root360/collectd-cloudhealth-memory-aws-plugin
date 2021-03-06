#!/usr/bin/env python2

__author__ = "root360 GmbH"
__copyright__ = "Copyright (C) 2020 root360 GmbH"
__license__ = "MIT License"
__version__ = "1.0"

import collectd
import os
import sys
import threading
from datetime import datetime
from distutils import util
from json import dumps as json_dumps
from yaml import load as yaml_load, dump as yaml_dump, SafeLoader
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from time import sleep


REGION_NAME = ''
INSTANCE_ID = ''
AWS_ACCOUNT_ID = ''
CONFIG_FILE = ''
CONFIG = {}
MEMORY_TEMPLATE = {
    'min': None,
    'max': None,
    'avg': None
}
MEMORY = MEMORY_TEMPLATE.copy()
VALUES = {}


class UploadThread(threading.Thread):
    '''
    upload thread
    '''

    def __init__(self):
        super(UploadThread, self).__init__()
        self.upload_interval = CONFIG.get(
            'configuration', {}
        ).get('interval', 3600)
        self.payload = {}

    def run(self):
        while True:
            try:
                self._upload()
                self._refresh_config()
                sleep(self.upload_interval)
            except Exception as err:
                collectd.info(
                    'cloudhealth - upload failed with error:\n{}'.format(
                        err
                    )
                )

    def _refresh_config(self):
        self.upload_interval = CONFIG.get(
            'configuration', {}
        ).get('interval', 3600)

    def _prepare_upload(self):
        now = datetime.now()
        values = []
        periods = []
        for period, data in VALUES.items():
            timestamp = datetime.fromtimestamp(period)
            if timestamp.day < now.day or timestamp.hour < now.hour:
                periods.append(period)
                values.append(
                    [
                        '{}:{}:{}'.format(
                            REGION_NAME,
                            AWS_ACCOUNT_ID,
                            INSTANCE_ID
                        ),
                        timestamp.isoformat(),
                        data.get('avg'),
                        data.get('max'),
                        data.get('min')
                    ]
                )
        if values:
            self.payload = {
                'metrics': {
                    'datasets': [
                        {
                            'metadata': {
                                'assetType': 'aws:ec2:instance',
                                'granularity': 'hour',
                                'keys': [
                                    'assetId',
                                    'timestamp',
                                    'memory:used:percent.avg',
                                    'memory:used:percent.max',
                                    'memory:used:percent.min'
                                ]
                            },
                            'values': values
                        }
                    ]
                }
            }
        return periods, values

    def _upload(self):
        '''
        upload data
        '''
        periods, _ = self._prepare_upload()
        if self.payload:
            res = self._api_request()
            if not res or res.get('failed') > 0 or res.get('errors'):
                collectd.error(
                    'cloudhealth - metrics upload failed: {}'.format(res)
                )
                return False
            collectd.info(
                'cloudhealth - metric upload successful'
            )
            update_timestamp('upload')
            # reset metrics for the next period
            self.payload = {}
            global MEMORY
            MEMORY = MEMORY_TEMPLATE.copy()
            # drop sent metrics from store
            global VALUES
            for period in periods:
                del VALUES[period]
            if CONFIG.get('persistent'):
                dump_config()
            return True
        return True

    def _api_request(self):
        '''
        cloudhealth api request
        '''
        metrics_url = 'https://chapi.cloudhealthtech.com/metrics/v1'
        headers = {
            'Authorization': 'Bearer {}'.format(CONFIG.get('token')),
            'Content-type': 'application/json'
        }
        retries = Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=[429, 500]
        )
        http = Session()
        http.mount('https://', HTTPAdapter(max_retries=retries))
        try:
            res = http.post(
                metrics_url,
                data=json_dumps(self.payload),
                headers=headers)
            if res.status_code != 200:
                collectd.error(
                    'cloudhealth - API request failed with {}\n{}'.format(
                        res.status_code,
                        res.text
                    )
                )
                return False
            try:
                # use yaml loader to get rid of unicode
                # u'' strings in python2
                res_json = yaml_load(res.text, Loader=SafeLoader)
                return res_json
            except Exception as err:
                collectd.error(
                    'cloudhealth - error parsing API response {}\n{}'.format(
                        res.text,
                        err
                    )
                )
                return {}
        except Exception as err:
            collectd.error(
                'cloudhealth - error: {}'.format(err)
            )
        return {}


def update_timestamp(timestamp):
    global CONFIG
    timestamps = CONFIG.get('timestamps', {})
    timestamps.update(
        {
            timestamp: int((
                datetime.now() - datetime(1970, 1, 1)
            ).total_seconds())
        }
    )
    return dump_config()


def update_values(perf_data):
    period = int(
        (
            datetime.now().replace(minute=0, second=0, microsecond=0) - datetime(1970, 1, 1)
        ).total_seconds()
    )
    global VALUES
    VALUES.update(
        {
            period: perf_data
        }
    )
    if CONFIG.get('persistent'):
        dump_config()


def update_min(perf_data, metric_plugin, metric_type,
               metric_type_instance, current):
    '''
    update min values
    '''
    value = 'min'
    item = perf_data.get(value)
    if not item or current < item:
        perf_data.update({value: current})


def update_max(perf_data, metric_plugin, metric_type,
               metric_type_instance, current):
    '''
    update max values
    '''
    value = 'max'
    item = perf_data.get(value)
    if not item or current > item:
        perf_data.update({value: current})


def update_avg(perf_data, metric_plugin, metric_type,
               metric_type_instance, current):
    '''
    update avg values
    '''
    value = 'avg'
    item = perf_data.get(value)
    if not item:
        perf_data.update({value: current})
    else:
        perf_data.update({value: (item + current) / 2})


def dump_config():
    global CONFIG
    if CONFIG.get('persistent'):
        CONFIG.update({'values': VALUES})
    try:
        with open(CONFIG_FILE, 'w') as config:
            yaml_dump(CONFIG, config, default_flow_style=False)
            return True
    except Exception as err:
        collectd.error(
            'cloudhealth - error: {}'.format(err)
        )
        return False


def fetch_ec2_metadata():
    global REGION_NAME
    global INSTANCE_ID
    global AWS_ACCOUNT_ID
    try:
        retries = Retry(
            total=3,
            backoff_factor=2
        )
        http = Session()
        http.mount('http://', HTTPAdapter(max_retries=retries))
        res = http.get('http://169.254.169.254/latest/dynamic/instance-identity/document')
        if res.status_code == 200:
            ec2 = yaml_load(res.text, Loader=SafeLoader)
            REGION_NAME = ec2.get('region')
            INSTANCE_ID = ec2.get('instanceId')
            AWS_ACCOUNT_ID = ec2.get('accountId')
    except Exception as err:
        collectd.warning(
            'cloudhealth - unable to fetch ec2 instance metadata due to\n{}'.format(err)
        )


def config_func(config):
    interval = 0
    persistent = False
    global CONFIG_FILE
    global CONFIG
    for item in config.children:
        key = item.key.lower()
        value = item.values[0]
        if key == 'token':
            token = value
        if key == 'configfile':
            CONFIG_FILE = value
        if key == 'interval':
            interval = value
        if key == 'persistent':
            persistent = bool(util.strtobool(value))
    required = [
        token
    ]
    for item in required:
        if not item:
            collectd.error(
                'cloudhealth - missing config key "{}"'.format(
                    item
                )
            )
            sys.exit(1)
    if not CONFIG_FILE:
        CONFIG_FILE = os.path.join(
            os.path.dirname(__file__),
            '{}.yaml'.format(os.path.basename(__file__).split('.')[0])
        )
    try:
        with open(CONFIG_FILE) as config:
            CONFIG = yaml_load(config, Loader=SafeLoader)
    except Exception:
        CONFIG = {}
        pass
    CONFIG.update({'token': token})
    if interval:
        CONFIG.update({'interval': interval})
    if persistent:
        CONFIG.update({'persistent': True})
    else:
        CONFIG.update({'persistent': False})
    dump_config()
    fetch_ec2_metadata()
    upload_thread = UploadThread()
    upload_thread.daemon = True
    upload_thread.start()
    collectd.info(
        'cloudhealth - plugin configured successfully'
    )


def write_func(values):
    perf_data_mapping = {
        'memory': MEMORY
    }
    type_filter = {
        'memory': ['percent']
    }
    type_instance_filter = {
        'memory': ['used']
    }
    if not (
        CONFIG.get('token')
        and REGION_NAME
        and AWS_ACCOUNT_ID
        and INSTANCE_ID
    ):
        collectd.warning('cloudhealth - plugin not configured properly')
        return
    if values.plugin not in perf_data_mapping.keys():
        return
    if values.type in type_filter.get(values.plugin, []):
        if values.type_instance in type_instance_filter.get(values.plugin, []):
            update_min(
                perf_data_mapping[values.plugin],
                values.plugin,
                values.type,
                values.type_instance,
                values.values[0]
            )
            update_max(
                perf_data_mapping[values.plugin],
                values.plugin,
                values.type,
                values.type_instance,
                values.values[0]
            )
            update_avg(
                perf_data_mapping[values.plugin],
                values.plugin,
                values.type,
                values.type_instance,
                values.values[0]
            )
            update_values(perf_data_mapping[values.plugin])


if __name__ != '__main__':
    collectd.register_config(config_func)
    collectd.register_write(write_func)
