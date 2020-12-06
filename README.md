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

Additionally to the information provided by CloudHealth, the API expects the *"hourly resolution"* to be sliced to the full hour (e.g. `2020-12-04T17:00:00`). If not, the API will respond:

<details>
  <summary>Click to expand!</summary>
```json
{
  "errors": [],
  "succeeded": 0,
  "failed": 1,
  "datasets": [
    {
      "errors": [],
      "succeeded": 0,
      "failures": [
        {
          "error": "Date/time value '2020-12-04T17:43:35' cannot have a non-zero minute value.",
          "row": [
            "<region>:<aws-account-id>:<instance-id>",
            "2020-12-04T17:43:35",
            37.088733582900176,
            52.81394681853567,
            33.08149301429918
          ]
        }
      ]
    }
  ]
}
```
</details>

## Design

* Runs as plain collectd python plugin which only receives `memory.used.percent` metrics from a filter chain.
* Collects the following metrics for a period of 1 hour (see CloudHealth API Limitation):
  * `memory:used:percent.avg`
  * `memory:used:percent.max`
  * `memory:used:percent.min`
* Stores metrics in memory by default (see `cloudhealthmemory.conf` for persistence)
* Uses a background thread for uploading the metrics after 1 hour
  * including retry on connection issues
  * if upload does not work, the next upload cycle will include all missing metrics

![Design - can be edited with draw.io](/design.png)

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
