# Energy Collector MongoDB Persistence

## Objective

Persist Energy Collector state outside the Python process so UE mappings and energy samples survive an `energy-collector` container restart.

## Current Design

The Energy Collector stores its laboratory data in MongoDB when available:

- database: `energy_collector`
- collection: `ue_mappings`
- collection: `traffic_samples`
- collection: `android_samples`

The Collector keeps the same HTTP API:

- `POST /ue-mappings`
- `GET /ue-mappings`
- `POST /samples/traffic`
- `POST /samples/android`
- `GET /energy/v1/report`

If MongoDB is unavailable, the Collector falls back to in-memory storage so the lab demo does not crash.

## Configuration

In `sa-deploy.yaml`, the Collector receives:

```yaml
ENERGY_COLLECTOR_MONGO_URI=mongodb://${MONGO_IP}:27017
ENERGY_COLLECTOR_MONGO_DB=energy_collector
```

The `/health` endpoint reports the active backend:

```bash
curl -sS http://172.22.0.44:8088/health | jq .
```

Expected result:

```json
{
  "status": "ok",
  "storage": "mongodb"
}
```

## Validation Performed

Validation completed successfully:

1. Rebuilt the `energy-collector` image with `pymongo`.
2. Recreated only the `energy-collector` container.
3. Confirmed `/health` returned `storage: mongodb`.
4. Posted a UE mapping.
5. Posted a scoped traffic sample with:
   - `supi`
   - `dnn`
   - `snssai`
   - `appId`
   - `tx_bytes`
   - `rx_bytes`
6. Queried `/energy/v1/report` and received `energyInfo.energy`.
7. Restarted the `energy-collector` container.
8. Queried the same report again and received the same persisted sample result.

Result after restart:

```json
{
  "source": "traffic-estimator",
  "storage": "mongodb",
  "txBytes": 1000000,
  "rxBytes": 5000000,
  "energyInfo": {
    "energy": 35.8
  }
}
```

Latest restart-tolerant check:

```text
Before restart:
storage=mongodb
ue_mappings=1
traffic_samples=4
android_samples=0

After docker restart energy-collector:
storage=mongodb
ue_mappings=1
traffic_samples=4
android_samples=0
```

Conclusion:

```text
MongoDB persistence validated across Energy Collector restart.
```

## End-to-End Post-Mongo Test

After MongoDB persistence was added, the full path was tested again:

```text
POST /samples/traffic
GET /energy/v1/report
restart energy-collector
GET /energy/v1/report again
create EIF subscription
receive HTTP/2 h2c callback with energyInfo.energy
delete test subscription
```

This confirms that persistence is not only visible in MongoDB, but also remains compatible with the EIF notification path.

The final architecture is:

```text
UPF estimator / Android / manual samples
        |
        v
Energy Collector
        |
        v
MongoDB
        |
        v
EIF
        |
        v
notifUri
```

Demo/report wording:

```text
Initially, the Energy Collector stored UE mappings and samples in memory. It has now been extended to persist UE mappings, traffic samples and Android samples in MongoDB, with an in-memory fallback if MongoDB is unavailable.
```

```text
The Energy Collector persists UE mappings and energy-related samples in MongoDB, while keeping an in-memory fallback. This makes the collector restart-tolerant and closer to a realistic telemetry backend.
```

## Notes

This is still laboratory persistence:

- no sample retention policy yet;
- no TTL indexes yet;
- no authentication on MongoDB in the lab network;
- no migration logic because the stored schema is still simple JSON-like telemetry.

For the demo, the important point is that the Collector no longer loses UE mappings or traffic samples when its container restarts.
