# EIF UPF Technical Status

## Current Status

The UPF-based lab path is functional and validated end to end:

```text
UERANSIM UE traffic
  -> Open5GS UPF
  -> scripts/upf_traffic_estimator.py
  -> Energy Collector /samples/traffic
  -> EIF /energy/v1/report query
  -> HTTP/2 h2c notifUri callback
```

The EIF remains decoupled from the traffic source. It only asks the Energy Collector for:

```text
GET /energy/v1/report?supi=...&event=UE_ENERGY&start=...&end=...
```

and then builds the `EnergyEeReport` with `energyInfo.energy`.

## Validated Components

- `mongo`
- `nrf`
- `scp`
- `amf`
- `smf`
- `upf`
- `eif`
- `energy-collector`
- `nr_gnb`
- `nr_ue`
- h2c notify server on `172.22.0.45:9998`

Validated fixed lab endpoints:

```text
EIF:              http://172.22.0.43:7777
Energy Collector: http://172.22.0.44:8088
UPF metrics:      http://172.22.0.8:9091/metrics
Notify server:    http://172.22.0.45:9998/notify
UE IP:            192.168.100.2
SUPI:             imsi-001011234567895
```

## What Was Validated

- The UE registers successfully through UERANSIM.
- The UE establishes a PDU session.
- `uesimtun0` receives `192.168.100.2`.
- UE traffic to `8.8.8.8` works through the UPF.
- The UPF exposes Prometheus metrics on port `9091`.
- `fivegs_upffunction_upf_sessionnbr` reaches `1` while the UE session is active.
- In the current lab, the N3 byte/packet Prometheus counters may stay at zero even when traffic is flowing.
- The estimator supports `--source ue-iptables` for temporary per-UE counters keyed by `ue_ip`.
- The estimator still supports `--source auto`, which falls back to `upf:ogstun` interface counters.
- The Energy Collector stores the resulting `/samples/traffic` sample.
- The EIF queries the Collector and receives `energyInfo.energy`.
- The final notification is sent directly to `notifUri`, not via SCP.
- The final JSON uses `energyInfo.energy`, not `energyConsumption`.

## UPF Estimator

Main script:

```bash
python3 scripts/upf_traffic_estimator.py --register-mapping --post
```

More UE-specific lab mode:

```bash
python3 scripts/upf_traffic_estimator.py \
  --source ue-iptables \
  --register-mapping \
  --post
```

Recommended traffic generation during the estimator window:

```bash
docker exec nr_ue sh -lc 'ping -c 20 -I uesimtun0 8.8.8.8'
```

Typical validated output:

```json
{
  "supi": "imsi-001011234567895",
  "ue_ip": "192.168.100.2",
  "tx_bytes": 840,
  "rx_bytes": 840,
  "source": "upf",
  "metadata": {
    "estimator_source": "interface",
    "upf_container": "upf",
    "upf_interface": "ogstun",
    "active_sessions": 1.0,
    "pfcp_peers_active": 1.0
  }
}
```

Direction mapping for the interface fallback:

- `ogstun` RX bytes are treated as UE uplink and posted as `tx_bytes`.
- `ogstun` TX bytes are treated as UE downlink and posted as `rx_bytes`.

Direction mapping for `--source ue-iptables`:

- `FORWARD -i ogstun -s <ue_ip>` bytes are treated as UE uplink and posted as `tx_bytes`.
- `FORWARD -o ogstun -d <ue_ip>` bytes are treated as UE downlink and posted as `rx_bytes`.

This is a better lab approximation than global interface counters. It is still not production accounting because it depends on the configured `ue_ip -> supi` mapping and on Linux firewall counters inside the UPF container.

## End-To-End Validation Commands

Start the core, EIF and Collector:

```bash
docker compose -f sa-deploy.yaml up -d mongo nrf scp amf smf upf eif energy-collector
```

Start UERANSIM:

```bash
docker compose -f nr-gnb.yaml up -d
docker compose -f nr-ue.yaml up -d
```

Confirm UE tunnel and traffic:

```bash
docker exec nr_ue ip addr show uesimtun0
docker exec nr_ue ping -c 3 -I uesimtun0 8.8.8.8
```

Register the mapping and post one UPF-derived traffic sample:

```bash
docker exec nr_ue sh -lc 'ping -c 20 -I uesimtun0 8.8.8.8 >/tmp/nr_ue_ping.log 2>&1 &' \
  && python3 scripts/upf_traffic_estimator.py --register-mapping --post
```

Create an EIF subscription:

```bash
curl --http2-prior-knowledge -i \
  http://172.22.0.43:7777/neif-ee/v1/subscriptions \
  -H "Content-Type: application/json" \
  -d '{
    "notifUri": "http://172.22.0.45:9998/notify",
    "eventsSubscSets": {
      "upf1": {
        "subscSetId": "upf1",
        "event": "UE_ENERGY",
        "supi": "imsi-001011234567895",
        "repPeriod": 5
      }
    }
  }'
```

Expected notification body:

```json
{
  "subId": "1",
  "reports": [
    {
      "event": "UE_ENERGY",
      "subscSetId": "upf1",
      "timeStamp": "2026-05-11T19:43:49.301047Z",
      "energyInfo": {
        "energy": 0.251008,
        "energyReportTimeStamp": "2026-05-11T19:43:49.301047Z"
      }
    }
  ]
}
```

## Useful Checks

UPF metrics:

```bash
docker exec eif sh -c 'curl -s http://172.22.0.8:9091/metrics | grep -E "fivegs_ep_n3_gtp|upf_sessionnbr|pfcp_peers_active"'
```

Notify server output:

```bash
docker logs -f notify-server
```

EIF logs:

```bash
docker logs eif 2>&1 | grep -E "EIF notify|energyInfo|Energy Collector|Notification failed"
```

After the notification callback fix, successful h2c callbacks should not produce false warnings such as:

```text
Notification failed with status: 0
Cannot parse notification HTTP response
```

## Limitations

- Prometheus N3 counters are global UPF metrics and may not move in this lab setup.
- The `ogstun` fallback is also global to the UPF interface, not per UE.
- The `ue-iptables` mode is per UE IP, but SUPI attribution is still supplied by the lab mapping, not discovered from PFCP session state.
- The Collector store is in memory.
- The EIF Collector endpoint is configurable through `EIF_ENERGY_COLLECTOR_HOST`, `EIF_ENERGY_COLLECTOR_PORT`, `EIF_ENERGY_COLLECTOR_PATH` and `EIF_ENERGY_COLLECTOR_TIMEOUT_SEC`.
- Direct callback to `notifUri` bypasses SCP intentionally for lab testing.

## Production Work Left

- Replace lab `ue_ip` attribution with per-session/per-UE accounting discovered from PFCP/session state.
- Add retry/backoff and metrics around Collector queries.
- Decide whether production notification callbacks should go direct or via SCP.
- Add persistence or an external store for Collector samples.
