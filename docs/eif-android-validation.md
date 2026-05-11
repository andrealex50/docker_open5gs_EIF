# EIF Android Validation

## Purpose

This document records the Android-based energy input path that was validated before the UPF estimator work.

Validated chain:

```text
Android radio estimator
  -> Energy Collector /samples/android
  -> EIF /energy/v1/report query
  -> HTTP/2 h2c notifUri callback
```

The Android path is an estimator, not a direct modem rail measurement.

## Android Energy Model

Source name:

```text
android-radio-profile-estimator
```

Method:

```text
BatteryStats + power_profile radio.active
```

Formula:

```text
E_radio[J] = radio.active[mA] * voltage[mV] * mobile_active_time[s] / 1_000_000
```

Important limitations:

- It does not measure modem rail energy directly.
- It does not isolate pure 5G energy.
- `modem.controller.*` did not expose useful values on the tested device.
- TX power bins are kept as context metadata in this version.

## Validated Result

A real callback was received on `notifUri` with:

```text
event = UE_ENERGY
subscSetId = set2
subId = 2
energyInfo.energy = 18.515095
```

Notification body:

```json
{
  "subId": "2",
  "reports": [
    {
      "event": "UE_ENERGY",
      "subscSetId": "set2",
      "timeStamp": "2026-04-30T20:37:41.898216Z",
      "energyInfo": {
        "energy": 18.515095,
        "energyReportTimeStamp": "2026-04-30T20:37:41.898216Z"
      }
    }
  ]
}
```

This confirms that the external JSON field is `energyInfo.energy`.

## Why Some Reports Showed 0.25 J

During the test, several notifications showed:

```text
energyInfo.energy = 0.25
```

That happened because the EIF notifies periodically. The Collector returns the Android sample only when the EIF query window includes the Android sample timestamp.

When the query window does not include that Android sample, the Collector falls back to the traffic model, which returned approximately:

```text
0.05 W * 5 s = 0.25 J
```

So:

- `18.515095 J` is the Android sample.
- `0.25 J` is the fallback value outside the Android sample window.

## Example Android Sample

```json
{
  "supi": "imsi-001011234567895",
  "ue_ip": "192.168.100.2",
  "source": "android-radio-profile-estimator",
  "method": "BatteryStats + power_profile radio.active",
  "start": "2026-04-30T20:36:54.184103Z",
  "end": "2026-04-30T20:37:39.184286Z",
  "radioActiveMa": 103.0,
  "voltageMv": 3930.0,
  "mobileActiveTimeSec": 45.74,
  "mobileActive5gTimeSec": 43.551,
  "cellularRxTimeSec": 26.493,
  "cellularRxBytes": 14450000,
  "cellularTxBytes": 1090000,
  "energyInfo": {
    "energy": 18.515095
  }
}
```

Collector storage response:

```json
{
  "status": "stored",
  "source": "android",
  "sample": {
    "supi": "imsi-001011234567895",
    "ue_ip": "192.168.100.2",
    "timestamp": "2026-04-30T20:37:39.184286Z",
    "energy_joules": 18.515095,
    "source": "android"
  },
  "total_samples": 3
}
```

## Commands

Run the Collector locally for early tests:

```bash
cd energy-collector
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install fastapi "uvicorn[standard]" pydantic
python -m uvicorn app:app --host 0.0.0.0 --port 8088
```

Run one Android collection window and post it:

```bash
python3 scripts/android_radio_estimator.py \
  --duration 45 \
  --output-dir android-radio-window \
  --supi imsi-001011234567895 \
  --ue-ip 192.168.100.2 \
  --collector-url http://172.22.0.44:8088 \
  --post
```

Query the Collector with an exact window:

```bash
curl "http://172.22.0.44:8088/energy/v1/report?supi=imsi-001011234567895&event=UE_ENERGY&start=2026-04-30T20:09:29.395857Z&end=2026-04-30T20:10:14.396102Z"
```

Create the EIF subscription:

```bash
curl --http2-prior-knowledge -i \
  http://172.22.0.43:7777/neif-ee/v1/subscriptions \
  -H "Content-Type: application/json" \
  -d '{
    "notifUri": "http://172.22.0.45:9998/notify",
    "eventsSubscSets": {
      "set2": {
        "event": "UE_ENERGY",
        "subscSetId": "set2",
        "supi": "imsi-001011234567895",
        "repPeriod": 5
      }
    }
  }'
```

## Study Notes

- The Android route is useful as a source-specific estimator.
- The UPF route is better for repeatable lab traffic because it observes traffic in the core.
- Both routes feed the same Collector abstraction.
- The EIF should not need to know whether the energy came from Android or UPF.
