# EIF Energy Manual Test Commands

Run these commands from the repository root unless noted otherwise.

## Rebuild Image

```bash
cd ~/docker_open5gs_EIF/base
docker build --no-cache --force-rm -t docker_open5gs .
```

## Recreate EIF

```bash
cd ~/docker_open5gs_EIF
docker compose -f sa-deploy.yaml up -d --force-recreate --no-deps eif
```

If the full stack is not already running:

```bash
docker compose -f sa-deploy.yaml up -d nrf scp mongo eif energy-collector
```

## Start Notify Server

```bash
cd ~/docker_open5gs_EIF
./scripts/notify_h2_server.sh
```

The server listens on `172.22.0.45:9998` in the Docker network and prints method, path, headers and body for each callback.

## Insert Collector Sample

```bash
curl -sS -X POST http://localhost:8088/samples/traffic \
  -H "Content-Type: application/json" \
  -d '{
    "supi": "imsi-001011234567895",
    "tx_bytes": 0,
    "rx_bytes": 0
  }' | jq .
```

## Create Subscription

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

## EIF Logs

```bash
docker logs -f eif
```

If logs are mounted to `./log`:

```bash
tail -f log/eif.log
```

Useful filters:

```bash
docker logs eif 2>&1 | grep -E "Energy Collector|EIF notify|energyInfo"
```

## Collector Logs

```bash
docker logs -f energy-collector
```

Useful filter:

```bash
docker logs energy-collector 2>&1 | grep "/energy/v1/report"
```

## Stop Collector And Confirm Fallback

This stops only the Collector. It does not remove containers or volumes.

```bash
docker stop energy-collector
docker logs -f eif
```

Expected result:

- EIF remains running.
- Collector query fails.
- Invalid/missing energy report is skipped.
- If no valid reports exist, no callback notification is sent.

Restart Collector:

```bash
docker start energy-collector
```

## Delete Old Subscriptions

List subscriptions:

```bash
curl --http2-prior-knowledge -sS \
  http://172.22.0.43:7777/neif-ee/v1/subscriptions | jq .
```

Delete a known subscription ID:

```bash
curl --http2-prior-knowledge -i -X DELETE \
  http://172.22.0.43:7777/neif-ee/v1/subscriptions/1
```

Repeat for any old IDs returned by the list command.
