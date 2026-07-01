#!/usr/bin/env python3

import argparse
import csv
import ipaddress
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib import parse

import run_energy_experiments as common
import upf_traffic_estimator as estimator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "experiments" / "energy-multi-ue-attribution.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "results" / "energy-experiments"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_config(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    if len(config.get("ues", [])) != 2:
        raise ValueError("multi-UE test requires exactly two UEs")
    names = {ue["name"] for ue in config["ues"]}
    for scenario in config["scenarios"]:
        if set(scenario["bitrates"]) != names:
            raise ValueError(f"scenario {scenario['name']} must define both UE bitrates")
    return config


def docker_result(command, timeout=15):
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    return {
        "command": command,
        "returnCode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def preflight(config):
    checks = {}
    for ue in config["ues"]:
        interface_check = docker_result([
            "docker", "exec", ue["container"], "ip", "-j", "-4", "addr", "show", "uesimtun0"
        ])
        iperf_check = docker_result([
            "docker", "exec", ue["container"], "sh", "-lc", "command -v iperf3"
        ])
        checks[ue["name"]] = {
            "interface": interface_check,
            "iperf3": iperf_check,
        }
        if interface_check["returnCode"] != 0:
            raise RuntimeError(f"{ue['container']} has no uesimtun0 interface")
        addresses = json.loads(interface_check["stdout"])[0].get("addr_info", [])
        ue_addresses = [
            address["local"] for address in addresses if address.get("family") == "inet"
        ]
        if len(ue_addresses) != 1:
            raise RuntimeError(
                f"{ue['container']} must have exactly one IPv4 address on uesimtun0"
            )
        ipaddress.ip_address(ue_addresses[0])
        ue["ue_ip"] = ue_addresses[0]
        checks[ue["name"]]["detectedUeIp"] = ue["ue_ip"]
        if iperf_check["returnCode"] != 0:
            raise RuntimeError(f"{ue['container']} does not provide iperf3")
    health = common.http_json(f"{config['collector_url']}/energy-sources/status")
    if not health.get("ok") or health["body"].get("attributionMode") != "dynamic_traffic_share":
        raise RuntimeError("Collector dynamic_traffic_share mode is not active")
    return {"ues": checks, "energySource": health}


def estimator_args(config, ue):
    return SimpleNamespace(
        ue_ip=ue["ue_ip"],
        supi=ue["supi"],
        upf_container=config["upf_container"],
        upf_interface=config["upf_interface"],
        upf_metrics_url=config["upf_metrics_url"],
        collector_url=config["collector_url"],
        timeout=config["timeout_seconds"],
        pdu_session_id=config["scope"]["pduSessionId"],
        dnn=config["scope"]["dnn"],
        snssai=config["scope"]["snssai"],
        app_id=config["scope"]["appId"],
        flow_descs=config["scope"]["flowDescs"],
    )


def register_mapping(config, ue):
    args = estimator_args(config, ue)
    status, body = estimator.register_mapping(args)
    if status not in (200, 201):
        raise RuntimeError(f"mapping failed for {ue['name']}: HTTP {status} {body}")
    return json.loads(body)


def start_workload(config, ue, bitrate, run_dir):
    command = [
        "docker", "exec", ue["container"], "iperf3",
        "-c", config["iperf_server"],
        "-p", str(ue["iperf_port"]),
        "-B", ue["ue_ip"],
        "-t", str(config["workload_seconds"]),
        "-J", "-u", "-b", bitrate,
    ]
    stdout = (run_dir / f"{ue['name']}-workload.stdout").open("w", encoding="utf-8")
    stderr = (run_dir / f"{ue['name']}-workload.stderr").open("w", encoding="utf-8")
    process = subprocess.Popen(command, stdout=stdout, stderr=stderr, text=True)
    return process, stdout, stderr, command


def post_sample(config, sample):
    payload = estimator.collector_traffic_payload(sample)
    status, body = estimator.post_json(
        f"{config['collector_url']}/samples/traffic",
        payload,
        config["timeout_seconds"],
    )
    if status not in (200, 201):
        raise RuntimeError(f"sample POST failed: HTTP {status} {body}")
    return json.loads(body)


def energy_report(config, ue, start, end):
    params = parse.urlencode({
        "supi": ue["supi"],
        "event": "UE_ENERGY",
        "start": start,
        "end": end,
    })
    response = common.http_json(
        f"{config['collector_url']}/energy/v1/report?{params}",
        config["timeout_seconds"],
    )
    if not response.get("ok"):
        raise RuntimeError(f"energy report failed for {ue['name']}: {response}")
    return response["body"]


def run_scenario(config, scenario, repetition, run_dir):
    run_dir.mkdir(parents=True, exist_ok=False)
    ue_args = {ue["name"]: estimator_args(config, ue) for ue in config["ues"]}
    counters = {}
    workloads = {}
    samples = {}
    result = {
        "scenario": scenario["name"],
        "repetition": repetition,
        "status": "running",
        "startedAt": common.utc_now(),
    }
    write_json(run_dir / "result.json", result)

    try:
        mappings = {ue["name"]: register_mapping(config, ue) for ue in config["ues"]}
        for ue in config["ues"]:
            counters[ue["name"]] = estimator.setup_iptables_counters(ue_args[ue["name"]])

        start = common.utc_now()
        started_monotonic = time.monotonic()
        for ue in config["ues"]:
            workloads[ue["name"]] = start_workload(
                config, ue, scenario["bitrates"][ue["name"]], run_dir
            )

        for ue in config["ues"]:
            process = workloads[ue["name"]][0]
            process.wait(timeout=config["workload_seconds"] + 15)

        remaining = config["window_seconds"] - (time.monotonic() - started_monotonic)
        if remaining > 0:
            time.sleep(remaining)

        end_metrics = estimator.fetch_text(
            config["upf_metrics_url"], config["timeout_seconds"]
        )
        end = common.utc_now()
        for ue in config["ues"]:
            sample = estimator.build_iptables_sample(
                counters[ue["name"]], end_metrics, ue_args[ue["name"]], start, end
            )
            estimator.add_scope_fields(sample, ue_args[ue["name"]])
            samples[ue["name"]] = sample

        for ue in config["ues"]:
            post_sample(config, samples[ue["name"]])

        time.sleep(2)
        reports = {
            ue["name"]: energy_report(config, ue, start, end)
            for ue in config["ues"]
        }
        workload_results = {}
        for ue in config["ues"]:
            process, stdout, stderr, command = workloads[ue["name"]]
            stdout.close()
            stderr.close()
            raw = (run_dir / f"{ue['name']}-workload.stdout").read_text(
                encoding="utf-8", errors="replace"
            )
            workload_results[ue["name"]] = {
                "command": command,
                "returnCode": process.returncode,
                "parsed": common.parse_workload_output(
                    {"kind": "iperf3"}, raw
                ),
            }

        ratios = {
            name: report.get("attribution", {}).get("ratio")
            for name, report in reports.items()
        }
        energies = {
            name: report.get("energyInfo", {}).get("energy")
            for name, report in reports.items()
        }
        allocatable = [
            report.get("attribution", {}).get("allocatableEnergy")
            for report in reports.values()
        ]
        actual_total_bytes = sum(
            sample["tx_bytes"] + sample["rx_bytes"] for sample in samples.values()
        )
        if actual_total_bytes <= 0:
            raise RuntimeError("both per-UE traffic counters are zero")
        expected_ratios = {
            name: (sample["tx_bytes"] + sample["rx_bytes"]) / actual_total_bytes
            for name, sample in samples.items()
        }
        ratio_sum = sum(value for value in ratios.values() if value is not None)
        energy_sum = sum(value for value in energies.values() if value is not None)
        conservation_error = (
            energy_sum - allocatable[0] if allocatable and allocatable[0] is not None else None
        )
        ratio_errors = {
            name: abs(ratios[name] - expected_ratios[name])
            if ratios[name] is not None else None
            for name in expected_ratios
        }
        checks = {
            "ratiosReported": all(value is not None for value in ratios.values()),
            "ratioSumWithinTolerance": abs(ratio_sum - 1.0) <= 0.000002,
            "ratiosMatchCounters": all(
                value is not None and value <= 0.000002
                for value in ratio_errors.values()
            ),
            "sameAllocatableEnergy": (
                len(allocatable) == 2
                and None not in allocatable
                and abs(allocatable[0] - allocatable[1]) <= 0.000002
            ),
            "energyConserved": (
                conservation_error is not None
                and abs(conservation_error) <= 0.000002
            ),
        }
        workload_ok = all(
            item["returnCode"] == 0 for item in workload_results.values()
        )

        result.update({
            "status": "ok" if workload_ok and all(checks.values()) else "validation_failed",
            "endedAt": common.utc_now(),
            "windowStart": start,
            "windowEnd": end,
            "mappings": mappings,
            "samples": samples,
            "workloads": workload_results,
            "reports": reports,
            "ratios": ratios,
            "expectedRatiosFromCounters": expected_ratios,
            "ratioErrors": ratio_errors,
            "ratioSum": ratio_sum,
            "allocatedEnergySumJ": energy_sum,
            "allocatableEnergyJ": allocatable[0] if allocatable else None,
            "energyConservationErrorJ": conservation_error,
            "checks": checks,
        })
    except Exception as exc:
        result.update({"status": "failed", "endedAt": common.utc_now(), "error": str(exc)})
    finally:
        for ue in config["ues"]:
            workload = workloads.get(ue["name"])
            if workload:
                process, stdout, stderr, _ = workload
                if process.poll() is None:
                    process.terminate()
                if not stdout.closed:
                    stdout.close()
                if not stderr.closed:
                    stderr.close()
            estimator.cleanup_iptables_counters(
                ue_args[ue["name"]], counters.get(ue["name"])
            )
        write_json(run_dir / "result.json", result)
    return result


def summary_row(run_id, result):
    row = {
        "run_id": run_id,
        "scenario": result["scenario"],
        "repetition": result["repetition"],
        "status": result["status"],
        "ratio_sum": result.get("ratioSum"),
        "allocated_energy_sum_j": result.get("allocatedEnergySumJ"),
        "allocatable_energy_j": result.get("allocatableEnergyJ"),
        "energy_conservation_error_j": result.get("energyConservationErrorJ"),
    }
    for ue_name in ("ue1", "ue2"):
        sample = result.get("samples", {}).get(ue_name, {})
        workload = result.get("workloads", {}).get(ue_name, {}).get("parsed", {})
        report = result.get("reports", {}).get(ue_name, {})
        row.update({
            f"{ue_name}_tx_bytes": sample.get("tx_bytes"),
            f"{ue_name}_rx_bytes": sample.get("rx_bytes"),
            f"{ue_name}_throughput_bps": workload.get("throughputBitsPerSecond"),
            f"{ue_name}_ratio": result.get("ratios", {}).get(ue_name),
            f"{ue_name}_expected_ratio": result.get("expectedRatiosFromCounters", {}).get(ue_name),
            f"{ue_name}_energy_j": report.get("energyInfo", {}).get("energy"),
        })
    return row


def export_summary(campaign_dir, runs):
    rows = [summary_row(run_id, result) for run_id, result in runs]
    write_json(campaign_dir / "summary.json", rows)
    fields = list(rows[0]) if rows else []
    with (campaign_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Multi-UE energy attribution",
        "",
        "| Scenario | UE1 ratio | UE2 ratio | Ratio sum | Energy conservation error (J) |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        def show(value):
            return f"{value:.6f}" if value is not None else "N/A"
        lines.append(
            f"| {row['scenario']} r{row['repetition']} | "
            f"{show(row['ue1_ratio'])} | {show(row['ue2_ratio'])} | "
            f"{show(row['ratio_sum'])} | {show(row['energy_conservation_error_j'])} |"
        )
    (campaign_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser(description="Validate proportional energy attribution with two UEs.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repetitions", type=int)
    return parser.parse_args()


def main():
    args = parse_args()
    config_path = args.config.resolve()
    config = load_config(config_path)
    repetitions = args.repetitions or config["repetitions"]
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    campaign_dir = args.output_root.resolve() / f"{config['campaign']}-{timestamp}"
    campaign_dir.mkdir(parents=True)
    (campaign_dir / "config.json").write_bytes(config_path.read_bytes())
    write_json(campaign_dir / "preflight.json", preflight(config))

    runs = []
    number = 0
    for repetition in range(1, repetitions + 1):
        for scenario in config["scenarios"]:
            number += 1
            run_id = f"{number:03d}-{scenario['name']}-r{repetition}"
            print(f"[{number}/{repetitions * len(config['scenarios'])}] {run_id}", flush=True)
            result = run_scenario(
                config, scenario, repetition, campaign_dir / "runs" / run_id
            )
            runs.append((run_id, result))
            export_summary(campaign_dir, runs)
            if config["cooldown_seconds"]:
                time.sleep(config["cooldown_seconds"])

    manifest = {
        "campaign": config["campaign"],
        "completedAt": common.utc_now(),
        "runs": len(runs),
        "successfulRuns": sum(result["status"] == "ok" for _, result in runs),
        "status": "complete" if all(result["status"] == "ok" for _, result in runs) else "partial",
    }
    write_json(campaign_dir / "manifest.json", manifest)
    common.write_checksums(campaign_dir)
    archive = common.create_archive(campaign_dir)
    common.restore_sudo_ownership([campaign_dir, archive])
    print(f"Results: {campaign_dir}")
    print(f"Archive: {archive}")


if __name__ == "__main__":
    main()
