# EIF Android radio profile estimator

## Goal

This document describes the Android-side energy estimation approach used as an optional Energy Collector source for EIF `UE_ENERGY` reports.

The goal is not to measure real modem rail energy. The goal is to produce a defensible Android-based radio activity estimate using metrics exposed by Android user-space APIs and ADB diagnostics.

## Method

The estimator combines:

- BatteryStats radio activity timings;
- Android `power_profile.xml`;
- battery voltage from `dumpsys battery`;
- telephony context from `dumpsys telephony.registry`.

The device-specific `power_profile.xml` was extracted from:

```text
/product/overlay/framework-res__auto_generated_rro_product.apk