# EIF 3GPP Message Compliance

## Scope

This document lists the 3GPP-facing JSON messages used by the EIF lab implementation and maps them to their OpenAPI schemas.

The Energy Collector endpoints are lab APIs. They are not 3GPP service definitions. Their role is to provide an energy value to the EIF so that the EIF can produce 3GPP-shaped `EnergyEeNotif` callbacks.

Reference inputs used for this check:

- `/29566-j10/TS29566_Neif_EventExposure.yaml`
- `base/open5gs-EIF/lib/sbi/support/r17-20230301-openapitools-6.4.0/modified/TS29566_Neif_EventExposure.yaml`
- `base/open5gs-EIF/lib/sbi/support/r17-20230301-openapitools-6.4.0/modified/TS29122_MonitoringEvent.yaml`

## Schema Matrix

| Message | Source schema | Required fields | Notes |
| --- | --- | --- | --- |
| `EnergyEeSubsc` | TS 29.566 `EnergyEeSubsc` | `notifUri`, `eventsSubscSets` | Request body for `POST /neif-ee/v1/subscriptions`. |
| `EnergyEeSubscSet` | TS 29.566 `EnergyEeSubscSet` | `event`, `subscSetId`, plus one of `supi` or `gpsi` | In the lab we use `supi`. The map key in `eventsSubscSets` should match `subscSetId`. |
| `EnergyEeNotif` | TS 29.566 `EnergyEeNotif` | `subId`, `reports` | Callback body sent to `notifUri`. |
| `EnergyEeReport` | TS 29.566 `EnergyEeReport` | `event`, `subscSetId`, `timeStamp` | `energyInfo` is optional in the schema, but the lab only sends reports when valid energy exists. |
| `EnergyInfo` | TS 29.122 `MonitoringEvent.yaml#/components/schemas/EnergyInfo` | `energy` | JSON field is `energy`, type `number/float`, minimum `0`. |

## Subscription Example

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

Compliance notes:

- `notifUri` uses the TS 29.566 field name.
- `eventsSubscSets` is a map.
- The map key `set1` matches `subscSetId`.
- `event` uses the `EnergyEeEvent` enum value `UE_ENERGY`.
- `supi` uses the TS 23.003-style `imsi-<imsi>` format.
- `repPeriod` is a duration in seconds.

## Notification Example

```json
{
  "subId": "1",
  "reports": [
    {
      "event": "UE_ENERGY",
      "subscSetId": "set1",
      "timeStamp": "2026-05-11T19:43:49.301047Z",
      "energyInfo": {
        "energy": 0.251008,
        "energyReportTimeStamp": "2026-05-11T19:43:49.301047Z"
      }
    }
  ]
}
```

Compliance notes:

- `subId` identifies the EIF subscription.
- `reports` is an array of `EnergyEeReport`.
- `timeStamp` and `energyReportTimeStamp` use OpenAPI `date-time` format.
- `energyInfo.energy` is the TS 29.122 field.
- `energyInfo.energyConsumption` must not be emitted.

## Lab-Only Messages

These messages are intentionally outside the 3GPP contract:

- `POST /samples/traffic`
- `POST /samples/android`
- `POST /ue-mappings`
- the extra diagnostic fields in `GET /energy/v1/report`, such as `source`, `txBytes`, `rxBytes` and `durationSec`

The only field consumed by the EIF from the Collector response is:

```json
{
  "energyInfo": {
    "energy": 0.251008
  }
}
```

## Regression Rule

Any generated EIF callback body fails compliance if it contains:

```json
{
  "energyInfo": {
    "energyConsumption": 0.251008
  }
}
```

The internal C member name may remain `energy_consumption`; only the external JSON name matters for TS 29.122/TS 29.566 compliance.
