# Energy Source Integration Plan

## Objective

Improve the Energy Collector energy source so the EIF pipeline can use measured or calibrated infrastructure energy instead of only traffic-based estimates.

Current validated pipeline:

```text
UPF estimator / manual / Android samples
        |
        v
Energy Collector
        |
        v
MongoDB
        |
        v
EIF GET /energy/v1/report
        |
        v
HTTP/2 h2c notification to notifUri
```

Current traffic model:

```text
E = P_idle * duration + alpha_rx * rx_bytes + alpha_tx * tx_bytes
```

Main limitation:

```text
UPF counters provide traffic attribution, but not direct energy measurements.
```

The next step is to add a real or semi-real energy source and use the UPF counters mainly for attribution.

## Available Test Servers

```text
server1: 10.255.35.93
server2: 10.255.35.34
```

Initial checks on both servers:

```bash
ssh <user>@10.255.35.93
ssh <user>@10.255.35.34

lscpu
uname -a
ls /sys/class/powercap
find /sys/class/powercap -maxdepth 3 -type f | sort
docker --version
docker compose version
```

Decision point:

- If `/sys/class/powercap` exposes CPU/package energy counters, prioritize Scaphandre.
- If no hardware counters are exposed, keep traffic estimation as fallback and consider PowerAPI/SmartWatts only as a comparative source.
- Avoid running the energy source inside a VM unless hardware counters are confirmed to be exposed.

Current conclusion:

```text
server1 and server2 both expose Intel RAPL through /sys/class/powercap.
This makes Scaphandre a viable primary infrastructure energy source.
```

## Energy Source Priority

Recommended hierarchy:

1. Scaphandre / hardware energy counters.
2. Prometheus as the collection/query layer.
3. node_exporter for context metrics.
4. PowerAPI SmartWatts only as secondary/comparative source, unless calibrated for the target CPU.
5. External power meter as ground truth if available.
6. Current UPF traffic model as fallback.

Reasoning:

- Scaphandre reads Linux powercap/RAPL-like counters when available.
- Prometheus gives stable time-window queries.
- PowerAPI can be useful conceptually, but may overestimate without CPU-specific calibration.
- UPF counters are still useful for attributing energy to UE/PDU/session/service flow.

## Target Architecture

```text
Scaphandre / PowerAPI / external meter / traffic model
        |
        v
Prometheus or direct collector adapter
        |
        v
Energy Collector energy-source abstraction
        |
        v
Normalized energy sample in MongoDB
        |
        v
EIF /energy/v1/report
```

Normalized energy-source schema:

```json
{
  "source": "scaphandre",
  "metric": "host_rapl_energy",
  "unit": "joules",
  "window_start": "...",
  "window_end": "...",
  "value": 12.34,
  "metadata": {
    "host": "server1",
    "promql": "increase(scaph_host_energy_microjoules[30s]) / 1000000"
  }
}
```

## Scaphandre + Prometheus Experiment

### 1. Deploy Scaphandre on Bare Metal

Scaphandre should run on the host or in a privileged container with access to powercap:

```bash
docker run -d \
  --name scaphandre \
  --privileged \
  -v /sys/class/powercap:/sys/class/powercap:ro \
  -p 8080:8080 \
  hubblo/scaphandre prometheus
```

Check metrics:

```bash
curl -sS http://localhost:8080/metrics | grep -i scaph | head
curl -sS http://localhost:8080/metrics | grep -E 'scaph_.*energy|scaph_host_energy_microjoules' | head
```

Run this on `server1` and `server2`:

```bash
ssh <user>@10.255.35.93
docker rm -f scaphandre 2>/dev/null || true
docker run -d \
  --name scaphandre \
  --privileged \
  -v /sys/class/powercap:/sys/class/powercap:ro \
  -p 8080:8080 \
  hubblo/scaphandre prometheus
curl -sS http://localhost:8080/metrics | grep -E 'scaph_.*energy|scaph_host_energy_microjoules' | head

ssh <user>@10.255.35.34
docker rm -f scaphandre 2>/dev/null || true
docker run -d \
  --name scaphandre \
  --privileged \
  -v /sys/class/powercap:/sys/class/powercap:ro \
  -p 8080:8080 \
  hubblo/scaphandre prometheus
curl -sS http://localhost:8080/metrics | grep -E 'scaph_.*energy|scaph_host_energy_microjoules' | head
```

### 2. Prometheus Queries

Instantaneous power estimate:

```promql
rate(scaph_host_energy_microjoules[30s]) / 1000000
```

Energy over a window:

```promql
increase(scaph_host_energy_microjoules[1m]) / 1000000
```

Notes:

- `rate()` gives power-like values over a rolling interval.
- `increase()` gives energy for the selected interval.
- The Energy Collector should prefer `increase()` when answering EIF reports for a fixed `start/end` window.

Example Prometheus scrape config:

```yaml
scrape_configs:
  - job_name: scaphandre-server1
    static_configs:
      - targets: ["10.255.35.93:8080"]

  - job_name: scaphandre-server2
    static_configs:
      - targets: ["10.255.35.34:8080"]
```

Prometheus query checks:

```bash
curl -G http://<prometheus-host>:9090/api/v1/query \
  --data-urlencode 'query=rate(scaph_host_energy_microjoules[30s]) / 1000000' | jq .

curl -G http://<prometheus-host>:9090/api/v1/query \
  --data-urlencode 'query=increase(scaph_host_energy_microjoules[1m]) / 1000000' | jq .
```

## Energy Collector Changes

Add an energy-source abstraction without changing the EIF:

```text
EnergySource
  - TrafficEnergySource
  - PrometheusScaphandreEnergySource
  - FuturePowerApiEnergySource
  - FutureExternalMeterEnergySource
```

New internal endpoints:

```text
GET  /energy-sources/status
GET  /energy-sources/window
```

Possible MongoDB collection:

```text
energy_source_samples
```

Minimal implementation path:

1. Add a Prometheus client helper in the Collector.
2. Add environment variables:
   - `ENERGY_SOURCE=scaphandre_prometheus`
   - `PROMETHEUS_URL=http://<host>:9090`
   - `SCAPHANDRE_PROMQL_TEMPLATE=increase(scaph_host_energy_microjoules[{window}]) / 1000000`
3. Query Prometheus for the report window.
4. Normalize the returned value to joules.
5. Combine it with UPF attribution.

Implemented Collector behaviour:

- Default mode remains `ENERGY_SOURCE=traffic`.
- Prometheus mode is enabled with `ENERGY_SOURCE=scaphandre_prometheus`.
- The Collector exposes `GET /energy-sources/status`.
- The Collector exposes `GET /energy-sources/window?start=...&end=...`.
- `/energy/v1/report` still returns `energyInfo.energy`.
- If Prometheus returns a valid energy window and UPF traffic exists, the Collector attributes measured energy by traffic share.
- If Prometheus is disabled or unavailable, the existing traffic estimator remains the fallback.
- Normalized infrastructure energy windows are stored in MongoDB collection `energy_source_samples`.

Collector configuration example:

```bash
ENERGY_SOURCE=scaphandre_prometheus
PROMETHEUS_URL=http://<prometheus-host>:9090
SCAPHANDRE_PROMQL_TEMPLATE='increase(scaph_host_energy_microjoules[{window}]) / 1000000'
PROMETHEUS_TIMEOUT_S=2
```

Collector checks:

```bash
curl -sS http://172.22.0.44:8088/energy-sources/status | jq .

START=$(date -u -d '1 minute ago' +%Y-%m-%dT%H:%M:%SZ)
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)

curl -sS "http://172.22.0.44:8088/energy-sources/window?start=${START}&end=${END}" | jq .
```

Expected response when Prometheus is configured:

```json
{
  "status": "ok",
  "sample": {
    "source": "scaphandre_prometheus",
    "metric": "host_rapl_energy",
    "unit": "joules",
    "window_start": "...",
    "window_end": "...",
    "value": 12.34
  }
}
```

Expected response when Prometheus is not configured:

```json
{
  "status": "unavailable",
  "reason": "energy source disabled or no Prometheus value returned"
}
```

## Attribution Strategy

Measured host/package energy is not naturally per-UE. Attribution must be explicit.

Possible attribution models:

### UE Energy

Use traffic share:

```text
UE_energy = measured_window_energy * (UE_bytes / total_UPF_bytes)
```

Where:

```text
UE_bytes = rx_bytes + tx_bytes for the UE
total_UPF_bytes = sum of rx_bytes + tx_bytes across all tracked UEs in the same window
```

### PDU_SESSION_ENERGY / UE_SNSSAI_ENERGY

Use the same model but filter UPF samples by:

```text
dnn
snssai
pduSessionId (internal lab metadata only)
```

### SERVICE_FLOW_ENERGY

Use the same model but filter samples by:

```text
appId
flowDescs
```

Important limitation:

```text
This is attribution, not direct per-UE hardware measurement.
```

The report/demo should clearly distinguish:

- measured host/package energy;
- estimated attribution to UE/session/service flow;
- fallback traffic-only model.

## Calibration Plan

Use controlled traffic windows:

1. Idle baseline.
2. Low traffic.
3. Medium traffic.
4. High traffic.
5. Multiple UEs if available.

For each window collect:

- Scaphandre energy via Prometheus.
- UPF rx/tx bytes.
- CPU load and thermal context via node_exporter.
- Energy Collector report.

Then fit or adjust:

```text
P_idle
alpha_rx
alpha_tx
```

Goal:

```text
Make traffic-model estimates closer to measured Scaphandre/window energy.
```

## Validation Checklist

### Host Energy Source

```bash
curl -sS http://<scaphandre-host>:8080/metrics | grep scaph_host_energy_microjoules
```

### Prometheus

```bash
curl -G http://<prometheus-host>:9090/api/v1/query \
  --data-urlencode 'query=increase(scaph_host_energy_microjoules[1m]) / 1000000' | jq .
```

### Collector

```bash
curl -sS http://172.22.0.44:8088/health | jq .
curl -sS http://172.22.0.44:8088/energy-sources/status | jq .
curl -sS "http://172.22.0.44:8088/energy/v1/report?supi=imsi-001011234567895&event=UE_ENERGY&start=<start>&end=<end>" | jq .
curl -sS http://172.22.0.44:8088/energy-sources/attributions?limit=5 | jq .
```

MongoDB check for normalized energy-source samples:

```bash
docker exec mongo mongosh energy_collector --quiet --eval '
db.energy_source_samples.find({}, {_id:0}).sort({window_end:-1}).limit(3).pretty()
db.energy_attributions.find({}, {_id:0}).sort({timestamp:-1}).limit(3).pretty()
'
```

Full validation helper:

```bash
./scripts/validate_energy_source_integration.sh
```

Optional EIF callback validation:

```bash
CREATE_EIF_SUBSCRIPTION=true ./scripts/validate_energy_source_integration.sh
```

### EIF

```bash
curl --http2-prior-knowledge -i \
  http://172.22.0.43:7777/neif-ee/v1/subscriptions \
  -H "Content-Type: application/json" \
  -d '{
    "notifUri": "http://172.22.0.45:9998/notify",
    "eventsSubscSets": {
      "demo1": {
        "subscSetId": "demo1",
        "event": "UE_ENERGY",
        "supi": "imsi-001011234567895",
        "repPeriod": 5
      }
    }
  }'
```

Expected result:

```text
Notify server receives EnergyEeNotif with energyInfo.energy.
```

## Milestones

### Milestone 1: Server Capability Check

- Check CPU model on `server1` and `server2`.     : Intel(R) Core(TM) i7-7700K CPU @ 4.20GHz
- Check `/sys/class/powercap`.     :
atnoguser@exigence1:~$ ls -la /sys/class/powercap
total 0
drwxr-xr-x  2 root root 0 May  6 16:58 .
drwxr-xr-x 80 root root 0 May  6 16:58 ..
lrwxrwxrwx  1 root root 0 May  6 17:01 intel-rapl -> ../../devices/virtual/powercap/intel-rapl
lrwxrwxrwx  1 root root 0 Jun 24 15:51 intel-rapl:0 -> ../../devices/virtual/powercap/intel-rapl/intel-rapl:0
lrwxrwxrwx  1 root root 0 Jun 24 15:51 intel-rapl:0:0 -> ../../devices/virtual/powercap/intel-rapl/intel-rapl:0/intel-rapl:0:0
lrwxrwxrwx  1 root root 0 Jun 24 15:51 intel-rapl:0:1 -> ../../devices/virtual/powercap/intel-rapl/intel-rapl:0/intel-rapl:0:1
lrwxrwxrwx  1 root root 0 Jun 24 15:51 intel-rapl:0:2 -> ../../devices/virtual/powercap/intel-rapl/intel-rapl:0/intel-rapl:0:2


atnoguser@exigence2:~$ ls -la /sys/class/powercap
total 0
drwxr-xr-x  2 root root 0 May 15 08:06 .
drwxr-xr-x 80 root root 0 May 15 08:06 ..
lrwxrwxrwx  1 root root 0 May 15 08:06 intel-rapl -> ../../devices/virtual/powercap/intel-rapl
lrwxrwxrwx  1 root root 0 Jun 24 15:20 intel-rapl:0 -> ../../devices/virtual/powercap/intel-rapl/intel-rapl:0
lrwxrwxrwx  1 root root 0 Jun 24 15:20 intel-rapl:0:0 -> ../../devices/virtual/powercap/intel-rapl/intel-rapl:0/intel-rapl:0:0
lrwxrwxrwx  1 root root 0 Jun 24 15:20 intel-rapl:0:1 -> ../../devices/virtual/powercap/intel-rapl/intel-rapl:0/intel-rapl:0:1
lrwxrwxrwx  1 root root 0 Jun 24 15:20 intel-rapl:0:2 -> ../../devices/virtual/powercap/intel-rapl/intel-rapl:0/intel-rapl:0:2

- Decide which server can run Scaphandre.    

### Milestone 2: Scaphandre Metrics

- Run Scaphandre.
- Confirm Prometheus-compatible metrics.
- Add Prometheus scrape config if needed.
- Validate `scaph_host_energy_microjoules` on both servers.
- Validate `increase(scaph_host_energy_microjoules[1m]) / 1000000` through Prometheus.

### Milestone 3: Collector Energy-Source Abstraction

- Add Prometheus query support to the Collector.
- Normalize energy samples to joules.
- Keep traffic model as fallback.
- Store normalized windows in `energy_source_samples`.
- Expose source status through `/energy-sources/status`.
- Expose direct window query through `/energy-sources/window`.
- Keep EIF unchanged: EIF still consumes only `energyInfo.energy`.

### Milestone 4: Attribution

- Attribute measured window energy to UE/PDU/session/service flow using UPF traffic shares.
- Store attribution metadata in MongoDB.

Implemented attribution behaviour:

```text
attributed_energy = measured_window_energy * selectedBytes / totalTrackedBytes
```

Where:

```text
selectedBytes = txBytes + rxBytes after SUPI/event/scope filters
totalTrackedBytes = txBytes + rxBytes across all tracked traffic samples in the same window
```

The Collector response keeps the 3GPP-facing value in:

```json
{
  "energyInfo": {
    "energy": 1.23
  }
}
```

and adds laboratory/debug metadata:

```json
{
  "source": "scaphandre_prometheus",
  "trafficEstimateEnergy": 0.25,
  "energySource": {
    "source": "scaphandre_prometheus",
    "metric": "host_rapl_energy",
    "unit": "joules",
    "value": 12.34
  },
  "attribution": {
    "method": "traffic_share",
    "selectedBytes": 6000000,
    "totalTrackedBytes": 12000000,
    "ratio": 0.5,
    "measuredWindowEnergy": 12.34,
    "trafficEstimateEnergy": 0.25
  }
}
```

MongoDB collection:

```text
energy_attributions
```

Inspection endpoint:

```bash
curl -sS http://172.22.0.44:8088/energy-sources/attributions?limit=5 | jq .
```

### Milestone 5: Validation

- Validate direct Collector reports.
- Validate EIF notification path.
- Compare measured Scaphandre energy against traffic-only estimates.

Validation sequence:

1. Confirm Scaphandre exposes energy counters.
2. Confirm Prometheus can query `increase(scaph_host_energy_microjoules[1m])`.
3. Start the Collector with:

```bash
ENERGY_SOURCE=scaphandre_prometheus
PROMETHEUS_URL=http://<prometheus-host>:9090
```

4. Recreate the Collector:

```bash
docker compose -f sa-deploy.yaml up -d --build --force-recreate energy-collector
```

5. Run:

```bash
./scripts/validate_energy_source_integration.sh
```

6. Check expected fields:

```text
energyInfo.energy
source=scaphandre_prometheus
trafficEstimateEnergy
energySource.value
attribution.method=traffic_share
attribution.ratio
```

7. Validate EIF callback if needed:

```bash
./scripts/notify_h2_server.sh
CREATE_EIF_SUBSCRIPTION=true ./scripts/validate_energy_source_integration.sh
```

Expected result:

```text
The notify server receives an EnergyEeNotif with energyInfo.energy.
The Collector stores energy_source_samples and energy_attributions in MongoDB.
The response includes both measured attributed energy and the traffic-only estimate.
```

## Report Wording

```text
The Energy Collector was designed to decouple EIF event exposure from the actual energy source. The current implementation supports traffic-based estimation using UPF counters and MongoDB persistence. The next step is to integrate host-level energy measurements, preferably through Scaphandre and Prometheus when hardware counters are available. Since host energy is not naturally per-UE, UPF counters will be used to attribute measured window energy to UE, PDU session, S-NSSAI or service-flow scopes.
```
