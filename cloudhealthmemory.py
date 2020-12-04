#!/usr/bin/env python2

__author__ = "root360 GmbH"
__copyright__ = "Copyright (C) 2020 root360 GmbH"
__license__ = "MIT License"
__version__ = "1.0"

import collectd
import os
import sys
import threading
from datetime import datetime, timedelta
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
            self._prepare_upload()
            self._upload()
            self._refresh_config()
            collectd.info(
                'cloudhealth - UploadThread sleeping for {}'.format(
                    self.upload_interval
                )
            )
            sleep(self.upload_interval)

    def _refresh_config(self):
        self.upload_interval = CONFIG.get(
            'configuration', {}
        ).get('interval', 3600)

    def _prepare_upload(self):
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
                        'values': [
                            '{}:{}:{}'.format(
                                REGION_NAME,
                                AWS_ACCOUNT_ID,
                                INSTANCE_ID
                            ),
                            datetime.now().replace(microsecond=0).isoformat(),
                            MEMORY.get('avg'),
                            MEMORY.get('max'),
                            MEMORY.get('min')
                        ]
                    }
                ]
            }
        }

    def _upload(self):
        '''
        upload data
        '''
        now = datetime.now()
        last_upload = datetime.fromtimestamp(
            CONFIG.get('timestamps', {}).get('upload', 0)
        )
        next_upload = last_upload + timedelta(seconds=self.upload_interval)
        if (
            next_upload <= now
            and MEMORY.get('avg')
            and MEMORY.get('max')
            and MEMORY.get('min')
        ):
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
            global MEMORY
            MEMORY = MEMORY_TEMPLATE.copy()

    def _api_request(self):
        '''
        cloudhealth api request
        '''
        metrics_url = 'https://chapi.cloudhealthtech.com/metrics/v1'
        headers = {
            'Authorization': 'Bearer {}'.format(CONFIG.get('token'))
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
                json=self.payload,
                headers=headers)
            if res.status_code == 200:
                try:
                    # use yaml loader to get rid of unicode
                    # u'' strings in python2
                    res_json = yaml_load(res.text, Loader=SafeLoader)
                    return res_json
                except Exception as err:
                    collectd.error(
                        'cloudhealth - error parsing response {}\n{}'.format(
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
            timestamp: (
                datetime.now() - datetime(1970, 1, 1)
            ).total_seconds()
        }
    )
    return dump_config()


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
        pass
    if not CONFIG.get('token'):
        CONFIG.update({'token': token})
    if interval:
        CONFIG.update({'interval': interval})
    dump_config()
    fetch_ec2_metadata()
    upload_thread = UploadThread()
    upload_thread.daemon = True
    upload_thread.start()


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


if __name__ != '__main__':
    collectd.register_config(config_func)
    collectd.register_write(write_func)
