#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request


RADIO_ACTIVE_MA_DEFAULT = 103.0
VOLTAGE_MV_DEFAULT = 3700.0
MODEM_CONTROLLER_VOLTAGE_DEFAULT = 4.0
TRAFFIC_BASELINE_CURRENT_MA_DEFAULT = 11.5
TRAFFIC_RX_J_PER_MB_DEFAULT = 0.4
TRAFFIC_TX_J_PER_MB_DEFAULT = 0.8
BYTES_PER_MB = 1_000_000


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_command(command):
    result = subprocess.run(
        command,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if result.returncode != 0:
        raise RuntimeError(f"command failed: {command}\n{result.stderr}")

    return result.stdout


def parse_duration_to_seconds(value):
    value = value.strip()

    total = 0.0

    hours = re.search(r"(\d+)h", value)
    minutes = re.search(r"(\d+)m(?!s)", value)
    seconds = re.search(r"(\d+)s", value)
    milliseconds = re.search(r"(\d+)ms", value)

    if hours:
        total += int(hours.group(1)) * 3600

    if minutes:
        total += int(minutes.group(1)) * 60

    if seconds:
        total += int(seconds.group(1))

    if milliseconds:
        total += int(milliseconds.group(1)) / 1000

    return total


def parse_bytes(value):
    value = value.strip().replace(",", ".")

    match = re.search(r"([\d.]+)\s*(B|KB|MB|GB)", value, re.IGNORECASE)
    if not match:
        return 0

    number = float(match.group(1))
    unit = match.group(2).upper()

    if unit == "B":
        return int(number)

    if unit == "KB":
        return int(number * 1000)

    if unit == "MB":
        return int(number * 1000 * 1000)

    if unit == "GB":
        return int(number * 1000 * 1000 * 1000)

    return 0


def find_duration(pattern, content):
    match = re.search(pattern, content, re.IGNORECASE)
    if not match:
        return 0.0

    return parse_duration_to_seconds(match.group(1))


def find_bytes(pattern, content):
    match = re.search(pattern, content, re.IGNORECASE)
    if not match:
        return 0

    return parse_bytes(match.group(1))


def find_float(pattern, content):
    match = re.search(pattern, content, re.IGNORECASE | re.MULTILINE)
    if not match:
        return None

    return float(match.group(1).replace(",", "."))


def find_voltage_mv(content):
    for line in content.splitlines():
        match = re.search(r"^\s*voltage:\s+(\d+)\s*$", line, re.IGNORECASE)
        if match:
            return float(match.group(1))

    return VOLTAGE_MV_DEFAULT


def find_voltage_uv(content):
    for line in content.splitlines():
        match = re.search(r"^\s*(\d+)\s*$", line)
        if match:
            value = float(match.group(1))
            if value > 100_000:
                return value

    return None


def find_modem_controller_voltage(power_profile):
    value = find_float(r"^\s*modem\.controller\.voltage=([\d.,]+)\s*$", power_profile)
    if value is not None and value > 0:
        return value

    return MODEM_CONTROLLER_VOLTAGE_DEFAULT


def find_mobile_radio_mah(usage_power_profile):
    value = find_float(r"^\s*mobile_radio:\s+([\d.,]+)\s+", usage_power_profile)
    if value is not None and value >= 0:
        return value

    return None


def parse_start_clock_time(content):
    match = re.search(
        r"Start clock time:\s+(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})",
        content,
    )
    if not match:
        return None

    year, month, day, hour, minute, second = (int(value) for value in match.groups())
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def parse_batterystats_window(content):
    start_dt = parse_start_clock_time(content)
    duration_s = find_duration(r"Time on battery:\s+(.+?)\s+\(", content)

    if not start_dt or duration_s <= 0:
        now = utc_now()
        return now, now

    end_dt = start_dt + timedelta(seconds=duration_s)
    return (
        start_dt.isoformat().replace("+00:00", "Z"),
        end_dt.isoformat().replace("+00:00", "Z"),
    )


def parse_tx_bins(content):
    bins = {
        "lt0dBm": 0.0,
        "dBm0To8": 0.0,
        "dBm8To15": 0.0,
        "dBm15To20": 0.0,
        "gt20dBm": 0.0,
    }

    patterns = {
        "lt0dBm": r"less than 0dBm:\s+(.+?)\s+\(",
        "dBm0To8": r"0dBm to 8dBm:\s+(.+?)\s+\(",
        "dBm8To15": r"8dBm to 15dBm:\s+(.+?)\s+\(",
        "dBm15To20": r"15dBm to 20dBm:\s+(.+?)\s+\(",
        "gt20dBm": r"above 20dBm:\s+(.+?)\s+\(",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            bins[key] = parse_duration_to_seconds(match.group(1))

    return bins


def parse_telephony_context(content):
    context = {
        "network": None,
        "overrideNetwork": None,
        "lteRsrp": None,
        "lteRsrq": None,
        "lteRssnr": None,
        "nrSsRsrp": None,
        "nrSsRsrq": None,
        "nrSsSinr": None,
        "primaryServing": None,
        "secondaryServing": None,
    }

    display = re.search(
        r"TelephonyDisplayInfo\s+\{network=([^,}]+),\s+overrideNetwork=([^,}]+)",
        content,
    )

    if display:
        context["network"] = display.group(1)
        context["overrideNetwork"] = display.group(2)

    lte = re.search(
        r"CellSignalStrengthLte:\s+rssi=[-\d]+\s+rsrp=([-\d]+)\s+rsrq=([-\d]+)\s+rssnr=([-\d]+)",
        content,
    )

    if lte:
        lte_rsrp = int(lte.group(1))
        lte_rsrq = int(lte.group(2))
        lte_rssnr = int(lte.group(3))
        context["lteRsrp"] = None if lte_rsrp == 2147483647 else lte_rsrp
        context["lteRsrq"] = None if lte_rsrq == 2147483647 else lte_rsrq
        context["lteRssnr"] = None if lte_rssnr == 2147483647 else lte_rssnr

    nr = re.search(
        r"CellSignalStrengthNr:\{.*?ssRsrp\s+=\s+([-\d]+)\s+ssRsrq\s+=\s+([-\d]+)\s+ssSinr\s+=\s+([-\d]+)",
        content,
        re.DOTALL,
    )

    if nr:
        nr_ss_rsrp = int(nr.group(1))
        nr_ss_rsrq = int(nr.group(2))
        nr_ss_sinr = int(nr.group(3))
        context["nrSsRsrp"] = None if nr_ss_rsrp == 2147483647 else nr_ss_rsrp
        context["nrSsRsrq"] = None if nr_ss_rsrq == 2147483647 else nr_ss_rsrq
        context["nrSsSinr"] = None if nr_ss_sinr == 2147483647 else nr_ss_sinr

    if "mConnectionStatus=PrimaryServing" in content:
        primary = re.search(
            r"mConnectionStatus=PrimaryServing.*?mNetworkType=([^,}]+)",
            content,
            re.DOTALL,
        )

        if primary:
            context["primaryServing"] = primary.group(1)

    if "mConnectionStatus=SecondaryServing" in content:
        secondary = re.search(
            r"mConnectionStatus=SecondaryServing.*?mNetworkType=([^,}]+)",
            content,
            re.DOTALL,
        )

        if secondary:
            context["secondaryServing"] = secondary.group(1)

    return context


def collect_adb_window(duration_s, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)

    print("[1/4] Reset BatteryStats")
    run_command("adb shell dumpsys batterystats --reset")
    run_command("adb shell dumpsys battery unplug")

    start = utc_now()
    print(f"[2/4] Janela ativa durante {duration_s}s")
    print("      Gera tráfego móvel agora, se quiseres medir uma janela com tráfego.")
    time.sleep(duration_s)
    end = utc_now()

    print("[3/4] A recolher dumps")
    battery = run_command("adb shell dumpsys battery")
    batterystats = run_command("adb shell dumpsys batterystats --charged")
    usage_power_profile = run_command(
        "adb shell dumpsys batterystats --usage --model power-profile"
    )
    power_profile = run_command("adb shell dumpsys batterystats --power-profile")
    telephony = run_command("adb shell dumpsys telephony.registry")

    print("[4/4] A guardar ficheiros")
    (output_dir / "battery.txt").write_text(battery)
    (output_dir / "batterystats.txt").write_text(batterystats)
    (output_dir / "batterystats_charged.txt").write_text(batterystats)
    (output_dir / "batterystats_usage_power_profile.txt").write_text(usage_power_profile)
    (output_dir / "power_profile.txt").write_text(power_profile)
    (output_dir / "telephony.txt").write_text(telephony)
    (output_dir / "window.json").write_text(json.dumps({"start": start, "end": end}, indent=2))

    return battery, batterystats, usage_power_profile, power_profile, telephony, start, end


def read_optional_text(path):
    if path.exists():
        return path.read_text(errors="ignore")

    return ""


def read_scenario_files(input_dir, scenario):
    batterystats_path = input_dir / f"{scenario}_batterystats.txt"
    if not batterystats_path.exists():
        raise SystemExit(f"Missing scenario file: {batterystats_path}")

    batterystats = batterystats_path.read_text(errors="ignore")
    battery = (
        read_optional_text(input_dir / f"{scenario}_battery_after.txt")
        or read_optional_text(input_dir / f"{scenario}_battery_before.txt")
    )
    voltage_uv = (
        find_voltage_uv(read_optional_text(input_dir / f"{scenario}_voltage_after.txt"))
        or find_voltage_uv(read_optional_text(input_dir / f"{scenario}_voltage_before.txt"))
    )
    if not battery and voltage_uv is not None:
        battery = f"voltage: {int(round(voltage_uv / 1000))}\n"
    telephony = (
        read_optional_text(input_dir / f"{scenario}_telephony_after.txt")
        or read_optional_text(input_dir / f"{scenario}_telephony_before.txt")
    )
    power_profile = read_optional_text(input_dir / f"{scenario}_power_profile.txt")
    start, end = parse_batterystats_window(batterystats)

    return battery, batterystats, batterystats, power_profile, telephony, start, end


def read_existing_files(input_dir, scenario=""):
    if scenario:
        return read_scenario_files(input_dir, scenario)

    battery = (input_dir / "battery.txt").read_text(errors="ignore")
    if (input_dir / "batterystats.txt").exists():
        batterystats = (input_dir / "batterystats.txt").read_text(errors="ignore")
    else:
        batterystats = (input_dir / "batterystats_charged.txt").read_text(errors="ignore")

    usage_power_profile = ""
    if (input_dir / "batterystats_usage_power_profile.txt").exists():
        usage_power_profile = (input_dir / "batterystats_usage_power_profile.txt").read_text(
            errors="ignore"
        )

    power_profile = ""
    if (input_dir / "power_profile.txt").exists():
        power_profile = (input_dir / "power_profile.txt").read_text(errors="ignore")

    telephony = ""
    if (input_dir / "telephony.txt").exists():
        telephony = (input_dir / "telephony.txt").read_text(errors="ignore")

    window_path = input_dir / "window.json"
    if window_path.exists():
        window = json.loads(window_path.read_text())
        start = window.get("start", utc_now())
        end = window.get("end", utc_now())
    elif (input_dir / "start.txt").exists() and (input_dir / "end.txt").exists():
        start = (input_dir / "start.txt").read_text().strip()
        end = (input_dir / "end.txt").read_text().strip()
    else:
        start = utc_now()
        end = utc_now()

    return battery, batterystats, usage_power_profile, power_profile, telephony, start, end


def build_estimate(
    battery,
    batterystats,
    usage_power_profile,
    power_profile,
    telephony,
    start,
    end,
    supi,
    ue_ip,
    radio_active_ma,
    energy_model,
    traffic_baseline_current_ma,
    traffic_rx_j_per_mb,
    traffic_tx_j_per_mb,
):
    voltage_mv = find_voltage_mv(battery)
    voltage_v = voltage_mv / 1000
    modem_controller_voltage_v = find_modem_controller_voltage(power_profile)
    mobile_radio_mah = find_mobile_radio_mah(usage_power_profile)

    mobile_active_s = find_duration(r"Mobile active time:\s+(.+)", batterystats)
    mobile_active_5g_s = find_duration(r"Mobile active 5G time:\s+(.+)", batterystats)
    cellular_kernel_active_s = find_duration(
        r"Cellular kernel active time:\s+(.+?)\s+\(", batterystats
    )
    cellular_rx_s = find_duration(r"Cellular Rx time:\s+(.+?)\s+\(", batterystats)

    rx_bytes = find_bytes(r"Cellular data received:\s+(.+)", batterystats)
    tx_bytes = find_bytes(r"Cellular data sent:\s+(.+)", batterystats)

    tx_bins = parse_tx_bins(batterystats)
    telephony_context = parse_telephony_context(telephony)
    duration_s = find_duration(r"Time on battery:\s+(.+?)\s+\(", batterystats)

    android_energy_j = None
    android_method = None

    if mobile_radio_mah is not None:
        android_energy_j = mobile_radio_mah * modem_controller_voltage_v * 3.6
        android_method = "BatteryStats --usage --model power-profile mobile_radio"
    else:
        android_energy_j = radio_active_ma * voltage_mv * mobile_active_s / 1_000_000
        android_method = "BatteryStats + power_profile radio.active"

    rx_mb = rx_bytes / BYTES_PER_MB
    tx_mb = tx_bytes / BYTES_PER_MB
    baseline_energy_j = traffic_baseline_current_ma * voltage_v * duration_s / 1000
    rx_energy_j = traffic_rx_j_per_mb * rx_mb
    tx_energy_j = traffic_tx_j_per_mb * tx_mb
    traffic_context_energy_j = baseline_energy_j + rx_energy_j + tx_energy_j

    traffic_context_estimate = {
        "formula": "baseline_current_mA * voltage_V * duration_s / 1000 + rx_MB * rx_J_per_MB + tx_MB * tx_J_per_MB",
        "baselineCurrentMa": traffic_baseline_current_ma,
        "rxCostJPerMb": traffic_rx_j_per_mb,
        "txCostJPerMb": traffic_tx_j_per_mb,
        "voltageV": round(voltage_v, 6),
        "durationSec": round(duration_s, 6),
        "rxMb": round(rx_mb, 6),
        "txMb": round(tx_mb, 6),
        "baselineEnergyJ": round(baseline_energy_j, 6),
        "rxEnergyJ": round(rx_energy_j, 6),
        "txEnergyJ": round(tx_energy_j, 6),
        "energyJ": round(traffic_context_energy_j, 6),
        "confidence": "modelled",
    }

    android_batterystats_estimate = {
        "method": android_method,
        "mobileRadioMah": mobile_radio_mah,
        "modemControllerVoltageV": modem_controller_voltage_v,
        "energyJ": round(android_energy_j, 6),
        "confidence": "reference-only",
    }

    if energy_model == "traffic-context":
        estimated_energy_j = traffic_context_energy_j
        source = "android-traffic-context-estimator"
        method = traffic_context_estimate["formula"]
    else:
        estimated_energy_j = android_energy_j
        source = "android-modem-activity-estimator"
        method = android_method

    return {
        "supi": supi,
        "ue_ip": ue_ip,
        "source": source,
        "method": method,
        "start": start,
        "end": end,
        "radioActiveMa": radio_active_ma,
        "voltageMv": voltage_mv,
        "voltageV": round(voltage_v, 6),
        "modemControllerVoltageV": modem_controller_voltage_v,
        "mobileRadioMah": mobile_radio_mah,
        "mobileActiveTimeSec": round(mobile_active_s, 6),
        "mobileActive5gTimeSec": round(mobile_active_5g_s, 6),
        "cellularKernelActiveTimeSec": round(cellular_kernel_active_s, 6),
        "cellularRxTimeSec": round(cellular_rx_s, 6),
        "cellularTxPowerBinsSec": tx_bins,
        "cellularRxBytes": rx_bytes,
        "cellularTxBytes": tx_bytes,
        "telephony": telephony_context,
        "androidBatteryStatsEstimate": android_batterystats_estimate,
        "trafficContextEstimate": traffic_context_estimate,
        "energyInfo": {
            "energy": round(estimated_energy_j, 6)
        },
        "limitations": [
            "This is not a direct modem rail measurement.",
            "This is not isolated 5G modem energy.",
            "BatteryStats mobile_radio is Android model-derived energy, not PMIC rail energy.",
            "Traffic-context energy is a tunable model, not a hardware measurement.",
            "TX power bins are kept as metadata and are not directly converted to energy in this version.",
        ]
    }


def post_to_collector(collector_url, estimate):
    payload = {
        "supi": estimate["supi"],
        "ue_ip": estimate["ue_ip"],
        "timestamp": estimate["end"],
        "energy_joules": estimate["energyInfo"]["energy"],
        "source": "android"
    }

    data = json.dumps(payload).encode("utf-8")

    req = request.Request(
        f"{collector_url}/samples/android",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with request.urlopen(req, timeout=10) as response:
        return response.read().decode("utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=0)
    parser.add_argument("--input-dir", default="")
    parser.add_argument(
        "--scenario",
        choices=["idle", "light", "hard"],
        default="",
        help="Read prefixed android_energy_v2 files from --input-dir.",
    )
    parser.add_argument("--output-dir", default="../docker_open5gs_EIF_artifacts/android-radio-window")
    parser.add_argument("--supi", default="imsi-001011234567895")
    parser.add_argument("--ue-ip", default="192.168.100.2")
    parser.add_argument("--radio-active-ma", type=float, default=RADIO_ACTIVE_MA_DEFAULT)
    parser.add_argument(
        "--energy-model",
        choices=["android", "traffic-context"],
        default="android",
        help="Choose the value written to energyInfo.energy.",
    )
    parser.add_argument(
        "--traffic-baseline-current-ma",
        type=float,
        default=TRAFFIC_BASELINE_CURRENT_MA_DEFAULT,
    )
    parser.add_argument("--traffic-rx-j-per-mb", type=float, default=TRAFFIC_RX_J_PER_MB_DEFAULT)
    parser.add_argument("--traffic-tx-j-per-mb", type=float, default=TRAFFIC_TX_J_PER_MB_DEFAULT)
    parser.add_argument("--collector-url", default="")
    parser.add_argument("--post", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)

    if args.input_dir:
        (
            battery,
            batterystats,
            usage_power_profile,
            power_profile,
            telephony,
            start,
            end,
        ) = read_existing_files(Path(args.input_dir), args.scenario)
    else:
        if args.duration <= 0:
            raise SystemExit("Use --duration N or --input-dir DIR")

        (
            battery,
            batterystats,
            usage_power_profile,
            power_profile,
            telephony,
            start,
            end,
        ) = collect_adb_window(args.duration, output_dir)

    estimate = build_estimate(
        battery=battery,
        batterystats=batterystats,
        usage_power_profile=usage_power_profile,
        power_profile=power_profile,
        telephony=telephony,
        start=start,
        end=end,
        supi=args.supi,
        ue_ip=args.ue_ip,
        radio_active_ma=args.radio_active_ma,
        energy_model=args.energy_model,
        traffic_baseline_current_ma=args.traffic_baseline_current_ma,
        traffic_rx_j_per_mb=args.traffic_rx_j_per_mb,
        traffic_tx_j_per_mb=args.traffic_tx_j_per_mb,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    estimate_filename = (
        f"{args.scenario}_android_radio_estimate.json"
        if args.scenario
        else "android_radio_estimate.json"
    )
    estimate_path = output_dir / estimate_filename
    estimate_path.write_text(json.dumps(estimate, indent=2))

    print(json.dumps(estimate, indent=2))
    print(f"\nSaved: {estimate_path}")

    if args.post:
        if not args.collector_url:
            raise SystemExit("--collector-url is required with --post")

        response = post_to_collector(args.collector_url, estimate)
        print("\nCollector response:")
        print(response)


if __name__ == "__main__":
    main()
