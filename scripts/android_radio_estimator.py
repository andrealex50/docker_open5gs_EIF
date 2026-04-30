#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import request


RADIO_ACTIVE_MA_DEFAULT = 103.0
VOLTAGE_MV_DEFAULT = 3700.0


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


def find_voltage_mv(content):
    for line in content.splitlines():
        match = re.search(r"^\s*voltage:\s+(\d+)\s*$", line, re.IGNORECASE)
        if match:
            return float(match.group(1))

    return VOLTAGE_MV_DEFAULT


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
        context["lteRsrp"] = int(lte.group(1))
        context["lteRsrq"] = int(lte.group(2))
        context["lteRssnr"] = int(lte.group(3))

    nr = re.search(
        r"CellSignalStrengthNr:\{.*?ssRsrp\s+=\s+([-\d]+)\s+ssRsrq\s+=\s+([-\d]+)\s+ssSinr\s+=\s+([-\d]+)",
        content,
        re.DOTALL,
    )

    if nr:
        context["nrSsRsrp"] = int(nr.group(1))
        context["nrSsRsrq"] = int(nr.group(2))
        context["nrSsSinr"] = int(nr.group(3))

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
    telephony = run_command("adb shell dumpsys telephony.registry")

    print("[4/4] A guardar ficheiros")
    (output_dir / "battery.txt").write_text(battery)
    (output_dir / "batterystats.txt").write_text(batterystats)
    (output_dir / "telephony.txt").write_text(telephony)
    (output_dir / "window.json").write_text(json.dumps({"start": start, "end": end}, indent=2))

    return battery, batterystats, telephony, start, end


def read_existing_files(input_dir):
    battery = (input_dir / "battery.txt").read_text(errors="ignore")
    batterystats = (input_dir / "batterystats.txt").read_text(errors="ignore")
    telephony = (input_dir / "telephony.txt").read_text(errors="ignore")

    window_path = input_dir / "window.json"
    if window_path.exists():
        window = json.loads(window_path.read_text())
        start = window.get("start", utc_now())
        end = window.get("end", utc_now())
    else:
        start = utc_now()
        end = utc_now()

    return battery, batterystats, telephony, start, end


def build_estimate(battery, batterystats, telephony, start, end, supi, ue_ip, radio_active_ma):
    voltage_mv = find_voltage_mv(battery)

    mobile_active_s = find_duration(r"Mobile active time:\s+(.+)", batterystats)
    mobile_active_5g_s = find_duration(r"Mobile active 5G time:\s+(.+)", batterystats)
    cellular_rx_s = find_duration(r"Cellular Rx time:\s+(.+?)\s+\(", batterystats)

    rx_bytes = find_bytes(r"Cellular data received:\s+(.+)", batterystats)
    tx_bytes = find_bytes(r"Cellular data sent:\s+(.+)", batterystats)

    tx_bins = parse_tx_bins(batterystats)
    telephony_context = parse_telephony_context(telephony)

    estimated_energy_j = radio_active_ma * voltage_mv * mobile_active_s / 1_000_000

    return {
        "supi": supi,
        "ue_ip": ue_ip,
        "source": "android-radio-profile-estimator",
        "method": "BatteryStats + power_profile radio.active",
        "start": start,
        "end": end,
        "radioActiveMa": radio_active_ma,
        "voltageMv": voltage_mv,
        "mobileActiveTimeSec": round(mobile_active_s, 6),
        "mobileActive5gTimeSec": round(mobile_active_5g_s, 6),
        "cellularRxTimeSec": round(cellular_rx_s, 6),
        "cellularTxPowerBinsSec": tx_bins,
        "cellularRxBytes": rx_bytes,
        "cellularTxBytes": tx_bytes,
        "telephony": telephony_context,
        "energyInfo": {
            "energy": round(estimated_energy_j, 6)
        },
        "limitations": [
            "This is not a direct modem rail measurement.",
            "This is not isolated 5G modem energy.",
            "modem.controller.rx/tx/voltage values were not available in the device power_profile.",
            "TX power bins are kept as metadata and are not directly converted to energy in this version."
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
    parser.add_argument("--output-dir", default="android-radio-window")
    parser.add_argument("--supi", default="imsi-001011234567895")
    parser.add_argument("--ue-ip", default="192.168.100.2")
    parser.add_argument("--radio-active-ma", type=float, default=RADIO_ACTIVE_MA_DEFAULT)
    parser.add_argument("--collector-url", default="")
    parser.add_argument("--post", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)

    if args.input_dir:
        battery, batterystats, telephony, start, end = read_existing_files(Path(args.input_dir))
    else:
        if args.duration <= 0:
            raise SystemExit("Use --duration N or --input-dir DIR")

        battery, batterystats, telephony, start, end = collect_adb_window(args.duration, output_dir)

    estimate = build_estimate(
        battery=battery,
        batterystats=batterystats,
        telephony=telephony,
        start=start,
        end=end,
        supi=args.supi,
        ue_ip=args.ue_ip,
        radio_active_ma=args.radio_active_ma,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    estimate_path = output_dir / "android_radio_estimate.json"
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