# collectd CloudHealth memory plugin for AWS

Upload EC2 memory metrics to CloudHealth for better cost optimization!

## Scope

We are aware that there is an official agent to upload metrics. However, the agent failed to fit in our environment due to variuos reasons:

* Runs it's own collectd daemon (besided the one we're already using)
* Misc. compliance requirements

Within AWS, CloudHealth already has knowledge about CPU and network metrics, so we just need to add the memory metrics on our own by using the [API](https://apidocs.cloudhealthtech.com/#metrics_introduction-to-metrics-api).

## CloudHealth API Limitation

* You can only post CPU, memory, and file system metrics.
* You can only post up to 8 days of historical metrics data.
* Metrics must have an hourly resolution.
* An active AWS Instance associated with the metrics must already be present and active in the CloudHealth Platform and not be Chef-managed.
* Metric retrieval is for individual assets only, that is, for AWS EC2 Instances or file systems of AWS EC2 Instances.
* The payload can contain a max of 1000 data points. If there are more than 1000 data points, the entire request is rejected with a 422 response.
* When posting to file systems, the associated instance must be present and active. However, if a file system object does not currently exist, a new one is automatically created and linked to the instance.

## Design

* Designed as plain collectd python plugin which only receives `memory.used.percent` metrics from a filter chain.
* Collects the following metrics for a period of 1 hour (see CloudHealth API Limitation):
  * `memory:used:percent.avg`
  * `memory:used:percent.max`
  * `memory:used:percent.min`
* Uses a background thread for uploading the metrics after 1 hour

## Requirements

* collectd-core
* python-requests
* python-yaml

## Installation

* Install requirements
* Copy `cloudhealthmemory.py` into your collectd plugin path (most probably `/var/lib/collectd`)
* Copy `cloudhealthmemory.conf` into your collectd configuration directory (e.g. `/etc/collectd/collectd.conf.d/`)
* Make sure the config files are being loaded by collectd
* Restart collectd daemon

**Example:**

```
apt-get install -y collectd-core python libpython2.7 python-yaml python-requests
mkdir -p /etc/collectd/collectd.conf.d
curl -Lo /var/lib/collectd/cloudhealthmemory.py https://raw.githubusercontent.com/root360/collectd-cloudhealth-memory-aws-plugin/master/cloudhealthmemory.py
curl -Lo /etc/collectd/collectd.conf.d/cloudhealthmemory.conf https://raw.githubusercontent.com/root360/collectd-cloudhealth-memory-aws-plugin/master/cloudhealthmemory.conf
grep "collectd.conf.d/\*\.conf" /etc/collectd/collectd.conf || echo 'Include "/etc/collectd/collectd.conf.d/*.conf"' >> /etc/collectd/collectd.conf
systemctl restart collectd
```
