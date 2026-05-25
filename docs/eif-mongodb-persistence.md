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

## Notes

This is still laboratory persistence:

- no sample retention policy yet;
- no TTL indexes yet;
- no authentication on MongoDB in the lab network;
- no migration logic because the stored schema is still simple JSON-like telemetry.

For the demo, the important point is that the Collector no longer loses UE mappings or traffic samples when its container restarts.
