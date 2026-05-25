# EIF UPF Traffic Estimator

## Goal

This document describes the lab-side UPF traffic estimator used to feed the Energy Collector with network-observed activity for EIF `UE_ENERGY` reports.

The EIF itself remains unchanged:

```text
EIF -> GET /energy/v1/report -> Energy Collector
```

The new input path is:

```text
UPF Prometheus metrics -> scripts/upf_traffic_estimator.py -> Energy Collector /samples/traffic
```

## What The Script Does

`scripts/upf_traffic_estimator.py`:

1. reads UPF Prometheus metrics at the start of a window;
2. waits for a configurable interval;
3. reads the metrics again;
4. calculates non-negative deltas for N3 counters;
5. optionally installs temporary per-UE `iptables` counter rules for one UE IP;
6. if Prometheus traffic counters do not move, falls back to UPF interface counters from `ogstun`;
7. builds a traffic sample for one SUPI/UE IP;
8. optionally posts that sample to the Energy Collector.

The Collector then estimates `energyInfo.energy` from the posted traffic sample using its configured traffic energy model.

## Metrics Used

Preferred Prometheus byte counters:

```text
fivegs_ep_n3_gtp_indatavolumeqosleveln3upf
fivegs_ep_n3_gtp_outdatavolumeqosleveln3upf
```

Fallback packet counters:

```text
fivegs_ep_n3_gtp_indatapktn3upf
fivegs_ep_n3_gtp_outdatapktn3upf
```

If byte deltas are zero but packet deltas move, the script estimates bytes as:

```text
packet_delta * avg_packet_bytes
```

The default `avg_packet_bytes` is `1200`.

Runtime note from the current lab: the UPF exposes the N3 Prometheus metrics, but the N3 packet/byte counters may remain at zero even while UE traffic is flowing. For that case, the script defaults to `--source auto` and falls back to Docker interface counters:

```text
docker exec upf cat /sys/class/net/ogstun/statistics/rx_bytes
docker exec upf cat /sys/class/net/ogstun/statistics/tx_bytes
```

For a more UE-specific lab measurement, use:

```bash
python3 scripts/upf_traffic_estimator.py \
  --source ue-iptables \
  --register-mapping \
  --post
```

This installs temporary counter-only rules in the UPF container:

```text
FORWARD -i ogstun -s <ue_ip>
FORWARD -o ogstun -d <ue_ip>
```

The rules are removed when the measurement window ends.

## Direction Mapping

For the traffic sample sent to the Collector from Prometheus metrics:

- UPF N3 incoming data is treated as UE uplink and posted as `tx_bytes`.
- UPF N3 outgoing data is treated as UE downlink and posted as `rx_bytes`.

For the interface fallback:

- UPF `ogstun` RX bytes are treated as UE uplink and posted as `tx_bytes`.
- UPF `ogstun` TX bytes are treated as UE downlink and posted as `rx_bytes`.

For `--source ue-iptables`:

- packets from `ue_ip` entering `FORWARD` through `ogstun` are treated as UE uplink and posted as `tx_bytes`;
- packets to `ue_ip` leaving `FORWARD` through `ogstun` are treated as UE downlink and posted as `rx_bytes`.

The `ue-iptables` source is still a lab measurement, but it is more specific than the global `ogstun` interface counters.

## Basic Usage

Run from the repository root:

```bash
python3 scripts/upf_traffic_estimator.py
```

Default values:

```text
UPF metrics:   http://172.22.0.8:9091/metrics
Collector:     http://172.22.0.44:8088
Source:        auto
UPF container: upf
UPF interface: ogstun
SUPI:          imsi-001011234567895
UE IP:         192.168.100.2
Interval:      10 seconds
```

## Post To Collector

Register the UE mapping and post one sample:

```bash
python3 scripts/upf_traffic_estimator.py \
  --register-mapping \
  --post
```

Force the interface-counter path:

```bash
python3 scripts/upf_traffic_estimator.py \
  --source interface \
  --register-mapping \
  --post
```

Use temporary per-UE UPF counters:

```bash
python3 scripts/upf_traffic_estimator.py \
  --source ue-iptables \
  --register-mapping \
  --post
```

Use a longer window while generating traffic from the UE:

```bash
python3 scripts/upf_traffic_estimator.py \
  --interval 30 \
  --register-mapping \
  --post
```

Tag the sample with PDU/DNN/S-NSSAI scope when testing scoped energy events:

```bash
python3 scripts/upf_traffic_estimator.py \
  --source ue-iptables \
  --register-mapping \
  --post \
  --pdu-session-id 1 \
  --dnn internet \
  --snssai 1-000001
```

Tag the sample with DNN/S-NSSAI plus an application/service identifier when testing `SERVICE_FLOW_ENERGY`:

```bash
python3 scripts/upf_traffic_estimator.py \
  --source ue-iptables \
  --register-mapping \
  --post \
  --dnn internet \
  --snssai 1-000001 \
  --app-id demo-service
```

The Collector can then filter `/energy/v1/report` by the same optional query parameters:

```bash
curl "http://172.22.0.44:8088/energy/v1/report?supi=imsi-001011234567895&event=UE_SNSSAI_ENERGY&start=<start>&end=<end>&dnn=internet&snssai=1-000001"
```

For service flow energy:

```bash
curl "http://172.22.0.44:8088/energy/v1/report?supi=imsi-001011234567895&event=SERVICE_FLOW_ENERGY&start=<start>&end=<end>&dnn=internet&snssai=1-000001&appId=demo-service"
```

Override endpoints if needed:

```bash
python3 scripts/upf_traffic_estimator.py \
  --upf-metrics-url http://172.22.0.8:9091/metrics \
  --collector-url http://172.22.0.44:8088 \
  --supi imsi-001011234567895 \
  --ue-ip 192.168.100.2 \
  --interval 15 \
  --post
```

## Validation Flow

1. Start the Open5GS services, EIF and Energy Collector.
2. Start UERANSIM gNB and UE.
3. Confirm the UE has a PDU session and traffic can be generated.
4. Run the UPF estimator with `--post` while traffic is active.
5. Query the Collector:

```bash
curl "http://172.22.0.44:8088/energy/v1/report?supi=imsi-001011234567895&event=UE_ENERGY&start=<start>&end=<end>"
```

6. Create an EIF `UE_ENERGY` subscription and confirm the callback contains:

```json
{
  "energyInfo": {
    "energy": 0.0
  }
}
```

with a value greater than zero when traffic deltas are observed.

## Limitations

- Metrics are global UPF counters, not per-UE counters.
- In `auto`, `prometheus` and `interface` modes, attribution to a SUPI depends on the supplied `--supi` and `--ue-ip`.
- The `ogstun` interface fallback is global to the UPF interface.
- The `ue-iptables` mode narrows accounting to the configured UE IP, but still depends on a trusted `ue_ip -> supi` mapping.
- `dnn`, `snssai`, `pduSessionId`, `appId` and `flowDescs` are lab tags supplied by the estimator/user; they are not discovered from real PFCP/session state yet.
- The `ue-iptables` mode requires the UPF container to have `iptables`, `iptables-save` and permission to insert/remove temporary rules.
- Packet-to-byte fallback is approximate.
- Counter resets are handled by clamping negative deltas to zero.
- The script is intended for controlled lab validation, not production accounting.
