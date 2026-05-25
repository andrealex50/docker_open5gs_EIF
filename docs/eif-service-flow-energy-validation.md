# EIF SERVICE_FLOW_ENERGY Validation

## Objective

Validate that the EIF can create a `SERVICE_FLOW_ENERGY` subscription, query the Energy Collector, and deliver an HTTP/2 h2c notification containing `energyInfo.energy`.

## Validation Summary

Validation completed successfully:

- Docker image rebuild: OK.
- EIF recreated with the new image: OK.
- Direct Energy Collector query returned `SERVICE_FLOW_ENERGY` with `energyInfo.energy`.
- EIF created a `SERVICE_FLOW_ENERGY` subscription.
- Notify server received an HTTP/2 h2c callback containing:
  - `event`: `SERVICE_FLOW_ENERGY`
  - `subscSetId`: `svc1`
  - `energyInfo.energy`: `3.05`
- Test subscription was deleted at the end.
- Subscription list after cleanup: `[]`.

## Scope Metadata

`pduSessionId` was not added to the EIF subscription model because it is not present in the TS 29.566 `EnergyEeSubscSet` schema used by this implementation.

Instead, `pduSessionId` is kept as laboratory metadata in the UPF estimator and Energy Collector. This allows traffic samples to be filtered or tagged internally without changing the 3GPP-facing EIF subscription contract.

## Validation Checks

The following checks passed:

```bash
python3 -m py_compile energy-collector/app.py scripts/upf_traffic_estimator.py
python3 scripts/check_eif_3gpp_json.py
git diff --check
```

## Interpretation

This validates the service-flow energy path at the EIF notification level. The `SERVICE_FLOW_ENERGY` event is exposed through the EIF notification pipeline, while service-flow scoping metadata remains internal to the laboratory Collector/estimator path.
