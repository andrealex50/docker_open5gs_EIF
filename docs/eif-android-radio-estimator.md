# EIF Android Radio Profile Estimator

## Goal

This document describes the Android-side energy estimation approach used as an optional Energy Collector source for EIF `UE_ENERGY` reports.

The goal is not to measure real modem rail energy. The goal is to produce a defensible radio activity estimate using Android user-space APIs and ADB diagnostics.

## Method

The estimator combines:

- BatteryStats radio activity timings;
- Android `power_profile.xml`;
- battery voltage from `dumpsys battery`;
- telephony context from `dumpsys telephony.registry`.

The device-specific `power_profile.xml` was extracted from:

```text
/product/overlay/framework-res__auto_generated_rro_product.apk
```

The working model is:

```text
E_radio[J] = radio.active[mA] * voltage[mV] * mobile_active_time[s] / 1_000_000
```

## Script

Run one collection window:

```bash
python3 scripts/android_radio_estimator.py \
  --duration 45 \
  --output-dir android-radio-window \
  --supi imsi-001011234567895 \
  --ue-ip 192.168.100.2
```

Post directly to the Energy Collector:

```bash
python3 scripts/android_radio_estimator.py \
  --duration 45 \
  --output-dir android-radio-window \
  --supi imsi-001011234567895 \
  --ue-ip 192.168.100.2 \
  --collector-url http://172.22.0.44:8088 \
  --post
```

## Output Contract

The Collector-facing sample carries an Android-derived energy value. The final EIF notification must still expose:

```json
{
  "energyInfo": {
    "energy": 18.515095
  }
}
```

The JSON field must be `energy`, not `energyConsumption`.

## Limitations

- This is an estimator, not direct modem rail measurement.
- It depends on Android counters exposed to normal ADB access.
- It is device/profile dependent.