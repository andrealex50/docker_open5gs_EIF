# EIF Energy Reporting Validation

## Objective

This document records the validated end-to-end lab path for energy reporting through the EIF:

1. An AF/client creates a `NEIF Event Exposure` subscription in the EIF.
2. The EIF reads the `UE_ENERGY` subscription set.
3. The EIF queries the Energy Collector for `energyInfo.energy`.
4. The EIF builds an `EnergyEeReport`.
5. The EIF sends an HTTP/2 cleartext callback notification to the subscribed `notifUri`.

The validated JSON field for the reported energy is `energyInfo.energy`. The legacy/generated internal C field name may remain `energy_consumption`, but the external JSON must not emit `energyConsumption`.

## Validated Architecture

```text
AF / test client
  |
  | POST /neif-ee/v1/subscriptions over h2c
  v
EIF 172.22.0.43:7777
  |
  | GET /energy/v1/report?supi=...&event=UE_ENERGY&start=...&end=...
  v
Energy Collector 172.22.0.44:8088
  |
  | returns energyInfo.energy
  v
EIF
  |
  | POST notifUri over h2c, direct client send, no SCP forwarding
  v
Notify server 172.22.0.45:9998
```

## Components

- EIF: Open5GS EIF service, container `eif`, IP `172.22.0.43`, SBI port `7777`.
- Energy Collector: FastAPI service, container `energy-collector`, IP `172.22.0.44`, port `8088`.
- Notify server: lab HTTP/2/h2c receiver, IP `172.22.0.45`, port `9998`.
- SCP: present in the Open5GS deployment, but not used for this lab notification delivery. The EIF sends notifications directly to the stored `notifUri`.

## Subscription Payload

```json
{
  "notifUri": "http://172.22.0.45:9998/notify",
  "eventsSubscSets": {
    "set1": {
      "subscSetId": "set1",
      "event": "UE_ENERGY",
      "supi": "imsi-001011234567895",
      "repPeriod": 10
    }
  }
}
```

Example command:

```bash
curl --http2-prior-knowledge -i \
  http://172.22.0.43:7777/neif-ee/v1/subscriptions \
  -H "Content-Type: application/json" \
  -d '{
    "notifUri": "http://172.22.0.45:9998/notify",
    "eventsSubscSets": {
      "set1": {
        "subscSetId": "set1",
        "event": "UE_ENERGY",
        "supi": "imsi-001011234567895",
        "repPeriod": 10
      }
    }
  }'
```

## Example 201 Created

The EIF returns `201 Created` and a subscription location. The exact `subId` varies per run.

```http
HTTP/2 201
content-type: application/json
location: http://172.22.0.43:7777/neif-ee/v1/subscriptions/1

{
  "notifUri": "http://172.22.0.45:9998/notify",
  "eventsSubscSets": {
    "set1": {
      "event": "UE_ENERGY",
      "supi": "imsi-001011234567895",
      "repPeriod": 10
    }
  }
}
```

## Example Notification

```json
{
  "subId": "1",
  "reports": [
    {
      "event": "UE_ENERGY",
      "subscSetId": "set1",
      "timeStamp": "2026-04-27T23:11:40.537621Z",
      "energyInfo": {
        "energy": 0.5,
        "energyReportTimeStamp": "2026-04-27T23:11:40.537621Z"
      }
    }
  ]
}
```

## HTTP/2/h2c Callback

Open5GS SBI uses HTTP/2. In this lab the callback server uses h2c: HTTP/2 without TLS. Use `curl --http2-prior-knowledge` for manual calls into the EIF, and run the notify server as an h2c listener on `172.22.0.45:9998`.

The EIF notification path intentionally sends directly to `notifUri` with `ogs_sbi_client_send_request()`. This avoids SCP rewriting of callback URLs during lab validation.

## Collector Online Test

1. Start the Energy Collector.
2. Insert a traffic sample:

```bash
curl -sS -X POST http://localhost:8088/samples/traffic \
  -H "Content-Type: application/json" \
  -d '{
    "supi": "imsi-001011234567895",
    "tx_bytes": 0,
    "rx_bytes": 0
  }'
```

3. Create the EIF subscription.
4. Confirm:
   - Energy Collector logs show `GET /energy/v1/report`.
   - EIF logs show `energyInfo.energy`.
   - Notify server receives a POST to `/notify`.
   - Notification JSON contains `energyInfo.energy`.

## Collector Offline Test

1. Stop only the Energy Collector.
2. Keep EIF and notify server running.
3. Create or keep a subscription active.
4. Confirm:
   - EIF does not crash.
   - EIF logs a Collector/connectivity failure.
   - Invalid or missing energy causes the report to be discarded.
   - If there are no valid reports, the notification is skipped.

## Current Limitations

- Energy Collector host, port and path are currently hardcoded in EIF C code.
- Collector access is HTTP/1.1 via a small synchronous socket client.
- No TLS, DNS discovery, retry/backoff, chunked response parsing or circuit breaker.
- Collector query happens in the notification timer path.
- Notification delivery is direct to `notifUri` for lab mode, bypassing SCP.
- Only the validated `UE_ENERGY` path has been exercised end to end.
- The current in-memory Collector sample store is suitable for lab validation, not persistence.

## Next Steps

- Move Collector endpoint configuration into EIF config/env.
- Replace the lab socket client with a proper reusable HTTP client path or an Open5GS-style SBI client where appropriate.
- Add explicit policy for SCP vs direct callback delivery.
- Expand event support beyond `UE_ENERGY`.
- Add tests for multiple subscription sets, multiple SUPIs and non-zero traffic samples.
- Add production-grade error handling: retry, timeout tuning, metrics and rate limiting.
