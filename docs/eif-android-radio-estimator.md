# EIF Android Radio Profile Estimator

## Goal

This document describes the Android-side energy estimation approach used as an optional Energy Collector source for EIF `UE_ENERGY` reports.

The goal is not to measure real modem rail energy. The goal is to produce a defensible radio activity estimate using Android user-space APIs and ADB diagnostics.

## Method

The estimator combines:

- BatteryStats radio activity timings;
- BatteryStats `mobile_radio` power attribution;
- Android `power_profile.xml`;
- battery voltage from `dumpsys battery`;
- telephony context from `dumpsys telephony.registry`.

The device-specific `power_profile.xml` was extracted from:

```text
/product/overlay/framework-res__auto_generated_rro_product.apk
```

The preferred Android BatteryStats reference model is:

```text
E_radio[J] = mobile_radio[mAh] * modem.controller.voltage[V] * 3.6
```

This comes from:

```bash
adb shell dumpsys batterystats --usage --model power-profile
```

The previous fallback model is still supported when `mobile_radio` is not available:

```text
E_radio[J] = radio.active[mA] * voltage[mV] * mobile_active_time[s] / 1_000_000
```

On the rooted NR SA test device, `BatteryStats` reports:

```text
per_uid_modem_power_model=modem_activity_info_rx_tx
```

This means Android is using its modem activity model, based on modem RX/TX activity, rather than only a fixed traffic-byte coefficient.

For project-side estimates, the script can also select a traffic/context model:

```text
E_radio[J] =
  baseline_current[mA] * voltage[V] * duration[s] / 1000
  + rx_MB * rx_cost[J/MB]
  + tx_MB * tx_cost[J/MB]
```

This model is not a direct modem rail measurement, but it is more controllable for the EIF/Collector path because it reacts to measured UE traffic and keeps the coefficients explicit.

## Script

Run one collection window:

```bash
python3 scripts/android_radio_estimator.py \
  --duration 45 \
  --output-dir ../docker_open5gs_EIF_artifacts/android-radio-window \
  --supi imsi-001011234567895 \
  --ue-ip 192.168.100.2
```

Post directly to the Energy Collector:

```bash
python3 scripts/android_radio_estimator.py \
  --duration 45 \
  --output-dir ../docker_open5gs_EIF_artifacts/android-radio-window \
  --supi imsi-001011234567895 \
  --ue-ip 192.168.100.2 \
  --collector-url http://172.22.0.44:8088 \
  --post
```

Re-process an existing measurement directory:

```bash
python3 scripts/android_radio_estimator.py \
  --input-dir android-root-probe/hard \
  --output-dir android-root-probe/hard
```

Re-process an `android_energy_v2` directory with prefixed scenario files:

```bash
for scenario in idle light hard; do
  python3 scripts/android_radio_estimator.py \
    --input-dir ../docker_open5gs_EIF_artifacts/android_energy_results/android_energy_v2_20260514_193500 \
    --scenario "$scenario" \
    --energy-model traffic-context \
    --output-dir ../docker_open5gs_EIF_artifacts/android_energy_results/android_energy_v2_20260514_193500
done
```

This writes one estimate per scenario:

```text
idle_android_radio_estimate.json
light_android_radio_estimate.json
hard_android_radio_estimate.json
```

## Latest Rooted Android Run

The `android_energy_v2_20260514_193500` run confirms the phone is attached through NR SA and exposes useful radio context:

- RAT: `NR`;
- band: `n78`;
- signal: `ssRsrp=-83`, `ssRsrq=-11`, `ssSinr=27`;
- Android modem model: `per_uid_modem_power_model=modem_activity_info_rx_tx`.

Using the traffic/context model, the three windows produced:

| Scenario | RX bytes | TX bytes | Cellular kernel active | Voltage | `energyInfo.energy` |
| --- | ---: | ---: | ---: | ---: | ---: |
| idle | 125 B | 694 B | 55.987 s | 4.353 V | 6.536323 J |
| light | 158.48 MB | 435.16 KB | 128.459 s | 4.353 V | 70.282154 J |
| hard | 361.81 MB | 901.02 KB | 130.637 s | 4.353 V | 151.984439 J |

The Android `mobile_radio` attribution is still stored as a reference in `androidBatteryStatsEstimate.energyJ`. In this run it stayed at `49.824 J` for idle, light and hard, so it should not be used as the final value for short traffic windows.

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
- `mobile_radio` is model-derived Android energy attribution, not PMIC rail energy.
- It is device/profile dependent.
