#!/usr/bin/env python3

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import re
import signal
import statistics
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import parse, request
from urllib.error import HTTPError, URLError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "experiments" / "energy-baseline-local-upf.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "results" / "energy-experiments"


def utc_now():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def http_json(url, timeout=10.0):
    try:
        with request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return {
                "ok": True,
                "status": response.status,
                "url": url,
                "body": json.loads(body),
            }
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": exc.code,
            "url": url,
            "error": body,
        }
    except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "url": url, "error": str(exc)}


def command_result(command, timeout=30.0):
    started_at = utc_now()
    try:
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
            "startedAt": started_at,
            "endedAt": utc_now(),
            "returnCode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "command": command,
            "startedAt": started_at,
            "endedAt": utc_now(),
            "returnCode": None,
            "error": str(exc),
        }


def load_config(path):
    config = read_json(path)
    required = (
        "campaign",
        "topology",
        "collector_url",
        "prometheus_url",
        "upf_metrics_url",
        "supi",
        "ue_ip",
        "ue_container",
        "ue_tunnel",
        "window_seconds",
        "workload_seconds",
        "repetitions",
        "scenarios",
        "events",
    )
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"missing configuration fields: {', '.join(missing)}")

    if config["window_seconds"] <= 0 or config["workload_seconds"] <= 0:
        raise ValueError("window_seconds and workload_seconds must be positive")
    if config["workload_seconds"] >= config["window_seconds"]:
        raise ValueError("workload_seconds must be shorter than window_seconds")
    if config["repetitions"] <= 0:
        raise ValueError("repetitions must be positive")

    names = [scenario["name"] for scenario in config["scenarios"]]
    if len(names) != len(set(names)):
        raise ValueError("scenario names must be unique")

    return config


def selected_scenarios(config, requested):
    scenarios = config["scenarios"]
    if not requested:
        return scenarios

    by_name = {scenario["name"]: scenario for scenario in scenarios}
    unknown = sorted(set(requested) - set(by_name))
    if unknown:
        raise ValueError(f"unknown scenarios: {', '.join(unknown)}")
    return [by_name[name] for name in requested]


def prometheus_query(base_url, query, evaluation_time, timeout):
    params = parse.urlencode({
        "query": query,
        "time": str(evaluation_time.timestamp()),
    })
    return http_json(f"{base_url.rstrip('/')}/api/v1/query?{params}", timeout)


def prometheus_scalar(response):
    if not response.get("ok"):
        return None
    body = response.get("body", {})
    if body.get("status") != "success":
        return None

    result = body.get("data", {}).get("result", [])
    values = []
    for item in result:
        raw = item.get("value")
        if not raw or len(raw) < 2:
            continue
        try:
            value = float(raw[1])
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)
    return sum(values) if values else None


def metric_queries(config, duration_s):
    window = f"{max(1, int(math.ceil(duration_s)))}s"
    queries = {}

    for alias, job in config.get("scaphandre_jobs", {}).items():
        queries[f"{alias}_energy_j"] = (
            "increase(scaph_host_energy_microjoules"
            f'{{job="{job}"}}[{window}])/1000000'
        )

    for alias, job in config.get("node_exporter_jobs", {}).items():
        queries[f"{alias}_cpu_percent"] = (
            "100*(1-avg(rate(node_cpu_seconds_total"
            f'{{job="{job}",mode="idle"}}[{window}])))'
        )
        queries[f"{alias}_network_rx_bytes"] = (
            "sum(increase(node_network_receive_bytes_total"
            f'{{job="{job}",device!="lo"}}[{window}]))'
        )
        queries[f"{alias}_network_tx_bytes"] = (
            "sum(increase(node_network_transmit_bytes_total"
            f'{{job="{job}",device!="lo"}}[{window}]))'
        )
        queries[f"{alias}_memory_used_bytes"] = (
            f'node_memory_MemTotal_bytes{{job="{job}"}}-'
            f'node_memory_MemAvailable_bytes{{job="{job}"}}'
        )

    queries["upf_active_sessions"] = (
        'max(fivegs_upffunction_upf_sessionnbr{job="upf"})'
    )
    queries["upf_pfcp_peers"] = 'max(pfcp_peers_active{job="upf"})'
    return queries


def collect_prometheus_metrics(config, start, end):
    duration_s = (end - start).total_seconds()
    raw = {}
    values = {}
    for name, query in metric_queries(config, duration_s).items():
        response = prometheus_query(
            config["prometheus_url"],
            query,
            end,
            config.get("http_timeout_seconds", 10.0),
        )
        raw[name] = {"query": query, "response": response}
        values[name] = prometheus_scalar(response)

    for alias in config.get("scaphandre_jobs", {}):
        energy = values.get(f"{alias}_energy_j")
        values[f"{alias}_mean_power_w"] = (
            energy / duration_s if energy is not None and duration_s > 0 else None
        )

    return values, raw


def collector_report(config, event, start, end):
    params = {
        "supi": config["supi"],
        "event": event["event"],
        "start": start,
        "end": end,
    }
    for key, value in event.get("params", {}).items():
        params[key] = value

    query = parse.urlencode(params, doseq=True)
    return http_json(
        f"{config['collector_url'].rstrip('/')}/energy/v1/report?{query}",
        config.get("http_timeout_seconds", 10.0),
    )


def workload_command(config, scenario):
    docker = config.get("docker_command", ["docker"])
    prefix = [*docker, "exec", config["ue_container"]]
    duration = int(config["workload_seconds"])
    kind = scenario["kind"]

    if kind == "idle":
        return None

    if kind == "ping":
        interval = float(scenario.get("interval_seconds", 0.2))
        count = max(1, int(duration / interval))
        return [
            *prefix,
            "ping",
            "-I",
            config["ue_tunnel"],
            "-c",
            str(count),
            "-i",
            str(interval),
            scenario.get("target", config["iperf_server"]),
        ]

    if kind == "iperf3":
        command = [
            *prefix,
            "iperf3",
            "-c",
            scenario.get("target", config["iperf_server"]),
            "-B",
            config["ue_ip"],
            "-t",
            str(duration),
            "-J",
        ]
        if scenario.get("protocol", "tcp") == "udp":
            command.extend(["-u", "-b", scenario["bitrate"]])
        if scenario.get("direction", "uplink") == "downlink":
            command.append("-R")
        return command

    raise ValueError(f"unsupported scenario kind: {kind}")


def parse_workload_output(scenario, stdout):
    result = {
        "kind": scenario["kind"],
        "throughputBitsPerSecond": None,
        "sentBitsPerSecond": None,
        "receivedBitsPerSecond": None,
        "lostPercent": None,
        "pingPacketLossPercent": None,
        "pingAverageMs": None,
    }

    if scenario["kind"] == "iperf3":
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            end = payload.get("end", {})
            sent = end.get("sum_sent", {}).get("bits_per_second")
            received = end.get("sum_received", {}).get("bits_per_second")
            aggregate = end.get("sum", {}).get("bits_per_second")
            result["sentBitsPerSecond"] = sent
            result["receivedBitsPerSecond"] = received
            result["throughputBitsPerSecond"] = aggregate or received or sent
            result["lostPercent"] = end.get("sum", {}).get("lost_percent")
            if payload.get("error"):
                result["error"] = payload["error"]

    if scenario["kind"] == "ping":
        loss = re.search(r"([0-9.]+)% packet loss", stdout)
        timing = re.search(
            r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = "
            r"[0-9.]+/([0-9.]+)/",
            stdout,
        )
        if loss:
            result["pingPacketLossPercent"] = float(loss.group(1))
        if timing:
            result["pingAverageMs"] = float(timing.group(1))

    return result


def estimator_command(config, sample_path):
    command = [
        sys.executable,
        "-u",
        str(PROJECT_ROOT / "scripts" / "upf_traffic_estimator.py"),
        "--source",
        config.get("estimator_source", "ue-iptables"),
        "--upf-metrics-url",
        config["upf_metrics_url"],
        "--collector-url",
        config["collector_url"],
        "--supi",
        config["supi"],
        "--ue-ip",
        config["ue_ip"],
        "--interval",
        str(config["window_seconds"]),
        "--timeout",
        str(config.get("http_timeout_seconds", 10.0)),
        "--upf-container",
        config.get("upf_container", "upf"),
        "--upf-interface",
        config.get("upf_interface", "ogstun"),
        "--pdu-session-id",
        config["scope"]["pduSessionId"],
        "--dnn",
        config["scope"]["dnn"],
        "--snssai",
        config["scope"]["snssai"],
        "--app-id",
        config["scope"]["appId"],
        "--register-mapping",
        "--post",
        "--output-json",
        str(sample_path),
    ]
    for flow_desc in config["scope"].get("flowDescs", []):
        command.extend(["--flow-desc", flow_desc])
    return command


def wait_for_estimator(proc, log_path, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
            raise RuntimeError(f"estimator exited before measurement window:\n{text}")
        if log_path.exists() and "Waiting " in log_path.read_text(
            encoding="utf-8", errors="replace"
        ):
            return
        time.sleep(0.1)
    raise TimeoutError("estimator did not start its measurement window in time")


def run_one(config, scenario, repetition, run_dir):
    run_dir.mkdir(parents=True, exist_ok=False)
    sample_path = run_dir / "traffic-sample.json"
    estimator_log = run_dir / "estimator.log"
    workload_stdout_path = run_dir / "workload.stdout"
    workload_stderr_path = run_dir / "workload.stderr"
    result = {
        "scenario": scenario,
        "repetition": repetition,
        "status": "running",
        "startedAt": utc_now(),
    }
    write_json(run_dir / "result.json", result)

    estimator_proc = None
    workload_proc = None
    estimator_handle = None
    workload_stdout = None
    workload_stderr = None
    try:
        estimator_handle = estimator_log.open("w", encoding="utf-8")
        estimator_proc = subprocess.Popen(
            estimator_command(config, sample_path),
            cwd=PROJECT_ROOT,
            text=True,
            stdout=estimator_handle,
            stderr=subprocess.STDOUT,
        )
        wait_for_estimator(
            estimator_proc,
            estimator_log,
            config.get("estimator_start_timeout_seconds", 20.0),
        )

        command = workload_command(config, scenario)
        workload_started = utc_now()
        if command is not None:
            workload_stdout = workload_stdout_path.open("w", encoding="utf-8")
            workload_stderr = workload_stderr_path.open("w", encoding="utf-8")
            workload_proc = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                text=True,
                stdout=workload_stdout,
                stderr=workload_stderr,
            )

        estimator_return_code = estimator_proc.wait(
            timeout=config["window_seconds"] + 30
        )
        estimator_handle.flush()
        workload_return_code = 0
        if workload_proc is not None:
            workload_return_code = workload_proc.wait(timeout=15)
        workload_ended = utc_now()

        if estimator_return_code != 0:
            raise RuntimeError(
                f"estimator failed with return code {estimator_return_code}"
            )
        if not sample_path.exists():
            raise RuntimeError("estimator did not create traffic-sample.json")

        if workload_stdout:
            workload_stdout.flush()
        if workload_stderr:
            workload_stderr.flush()
        stdout = (
            workload_stdout_path.read_text(encoding="utf-8", errors="replace")
            if workload_stdout_path.exists()
            else ""
        )
        stderr = (
            workload_stderr_path.read_text(encoding="utf-8", errors="replace")
            if workload_stderr_path.exists()
            else ""
        )
        workload = {
            "command": command,
            "startedAt": workload_started,
            "endedAt": workload_ended,
            "returnCode": workload_return_code,
            "parsed": parse_workload_output(scenario, stdout),
            "stderr": stderr,
        }
        write_json(run_dir / "workload.json", workload)

        sample = read_json(sample_path)
        start = parse_time(sample["metadata"]["start"])
        end = parse_time(sample["metadata"]["end"])
        duration_s = (end - start).total_seconds()

        time.sleep(config.get("metrics_settle_seconds", 2.0))
        metric_values, metric_raw = collect_prometheus_metrics(config, start, end)
        write_json(run_dir / "prometheus-metrics.json", metric_values)
        write_json(run_dir / "prometheus-raw.json", metric_raw)

        reports = {}
        for event in config["events"]:
            reports[event["event"]] = collector_report(
                config,
                event,
                sample["metadata"]["start"],
                sample["metadata"]["end"],
            )
        write_json(run_dir / "collector-reports.json", reports)

        result.update({
            "status": "ok" if workload_return_code == 0 else "workload_failed",
            "endedAt": utc_now(),
            "windowStart": sample["metadata"]["start"],
            "windowEnd": sample["metadata"]["end"],
            "durationSeconds": duration_s,
            "trafficSample": sample,
            "workload": workload,
            "metrics": metric_values,
            "collectorReports": reports,
        })
    except Exception as exc:
        result.update({
            "status": "failed",
            "endedAt": utc_now(),
            "error": str(exc),
        })
    finally:
        if workload_proc is not None and workload_proc.poll() is None:
            workload_proc.terminate()
            try:
                workload_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                workload_proc.kill()
        if estimator_proc is not None and estimator_proc.poll() is None:
            estimator_proc.send_signal(signal.SIGINT)
            try:
                estimator_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                estimator_proc.kill()
        for handle in (estimator_handle, workload_stdout, workload_stderr):
            if handle is not None:
                handle.close()
        write_json(run_dir / "result.json", result)

    return result


def report_body(result, event="UE_ENERGY"):
    response = result.get("collectorReports", {}).get(event, {})
    return response.get("body", {}) if response.get("ok") else {}


def summary_row(config, run_id, result):
    sample = result.get("trafficSample", {})
    metadata = sample.get("metadata", {})
    workload = result.get("workload", {}).get("parsed", {})
    metrics = result.get("metrics", {})
    report = report_body(result)
    attribution = report.get("attribution", {})
    energy_info = report.get("energyInfo", {})
    primary_alias = config["primary_energy_alias"]
    primary_energy = metrics.get(f"{primary_alias}_energy_j")
    primary_power = metrics.get(f"{primary_alias}_mean_power_w")
    baseline_power = config.get("idle_baseline_w")
    duration_s = result.get("durationSeconds")
    baseline_window_energy = (
        min(primary_energy, baseline_power * duration_s)
        if primary_energy is not None and baseline_power is not None and duration_s
        else None
    )
    host_dynamic_energy = (
        max(0.0, primary_energy - baseline_window_energy)
        if primary_energy is not None and baseline_window_energy is not None
        else None
    )

    row = {
        "campaign": config["campaign"],
        "topology": config["topology"],
        "run_id": run_id,
        "scenario": result["scenario"]["name"],
        "repetition": result["repetition"],
        "status": result["status"],
        "window_start": result.get("windowStart"),
        "window_end": result.get("windowEnd"),
        "duration_s": result.get("durationSeconds"),
        "tx_bytes": sample.get("tx_bytes"),
        "rx_bytes": sample.get("rx_bytes"),
        "uplink_packets": metadata.get("uplink_packets_delta"),
        "downlink_packets": metadata.get("downlink_packets_delta"),
        "estimator_source": metadata.get("estimator_source"),
        "workload_throughput_bps": workload.get("throughputBitsPerSecond"),
        "workload_sent_bps": workload.get("sentBitsPerSecond"),
        "workload_received_bps": workload.get("receivedBitsPerSecond"),
        "workload_lost_percent": workload.get("lostPercent"),
        "ping_loss_percent": workload.get("pingPacketLossPercent"),
        "ping_avg_ms": workload.get("pingAverageMs"),
        "collector_source": report.get("source"),
        "attributed_energy_j": energy_info.get("energy"),
        "traffic_estimate_energy_j": report.get("trafficEstimateEnergy"),
        "attribution_method": attribution.get("method"),
        "baseline_energy_j": attribution.get("baselineEnergy"),
        "dynamic_energy_j": attribution.get("dynamicEnergy"),
        "attribution_ratio": attribution.get("ratio"),
        "primary_host_energy_j": primary_energy,
        "primary_host_mean_power_w": primary_power,
        "configured_idle_baseline_w": baseline_power,
        "baseline_window_energy_j": baseline_window_energy,
        "host_dynamic_energy_j": host_dynamic_energy,
    }
    row.update(metrics)
    for event, response in result.get("collectorReports", {}).items():
        body = response.get("body", {}) if response.get("ok") else {}
        row[f"event_{event.lower()}_energy_j"] = body.get("energyInfo", {}).get("energy")
    return row


def write_summary(campaign_dir, config, runs):
    rows = [summary_row(config, run_id, result) for run_id, result in runs]
    write_json(campaign_dir / "summary.json", rows)

    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with (campaign_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    numeric_fields = sorted({
        key
        for row in rows
        for key, value in row.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        and key != "repetition"
    })
    aggregates = []
    for scenario in config["scenarios"]:
        selected = [
            row for row in rows
            if row["scenario"] == scenario["name"] and row["status"] == "ok"
        ]
        aggregate = {
            "scenario": scenario["name"],
            "successfulRuns": len(selected),
            "metrics": {},
        }
        for field in numeric_fields:
            values = [row.get(field) for row in selected]
            values = [
                float(value) for value in values
                if value is not None and math.isfinite(float(value))
            ]
            if not values:
                continue
            aggregate["metrics"][field] = {
                "samples": len(values),
                "mean": statistics.fmean(values),
                "stddev": statistics.stdev(values) if len(values) > 1 else 0.0,
                "minimum": min(values),
                "maximum": max(values),
            }
        aggregates.append(aggregate)
    write_json(campaign_dir / "aggregates.json", aggregates)

    aggregate_rows = []
    for aggregate in aggregates:
        row = {
            "scenario": aggregate["scenario"],
            "successful_runs": aggregate["successfulRuns"],
        }
        for metric, values in aggregate["metrics"].items():
            for statistic, value in values.items():
                row[f"{metric}_{statistic}"] = value
        aggregate_rows.append(row)
    aggregate_fields = []
    for row in aggregate_rows:
        for key in row:
            if key not in aggregate_fields:
                aggregate_fields.append(key)
    with (campaign_dir / "aggregates.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=aggregate_fields)
        writer.writeheader()
        writer.writerows(aggregate_rows)

    valid_rows = [row for row in rows if row["status"] == "ok"]
    idle_values = [
        float(row["primary_host_energy_j"])
        for row in valid_rows
        if row["scenario"] == "idle" and row.get("primary_host_energy_j") is not None
    ]
    idle_energy_mean = statistics.fmean(idle_values) if idle_values else None
    derived_rows = []
    for scenario in config["scenarios"]:
        selected = [
            row for row in valid_rows if row["scenario"] == scenario["name"]
        ]

        def values(field):
            return [float(row[field]) for row in selected if row.get(field) is not None]

        def mean(field):
            collected = values(field)
            return statistics.fmean(collected) if collected else None

        host_energy = mean("primary_host_energy_j")
        tx_bytes = mean("tx_bytes")
        rx_bytes = mean("rx_bytes")
        total_bytes = (
            tx_bytes + rx_bytes
            if tx_bytes is not None and rx_bytes is not None else None
        )
        incremental_energy = (
            host_energy - idle_energy_mean
            if host_energy is not None and idle_energy_mean is not None else None
        )
        incremental_per_mb = (
            incremental_energy / (total_bytes / 1_000_000)
            if incremental_energy is not None and total_bytes else None
        )
        expected_workload_bytes = None
        throughput = mean("workload_throughput_bps")
        if throughput is not None:
            expected_workload_bytes = (
                throughput * config["workload_seconds"] / 8
            )
        counter_ratio = (
            total_bytes / expected_workload_bytes
            if total_bytes is not None and expected_workload_bytes else None
        )
        derived_rows.append({
            "scenario": scenario["name"],
            "successfulRuns": len(selected),
            "hostEnergyMeanJ": host_energy,
            "idleAdjustedEnergyJ": incremental_energy,
            "meanTxBytes": tx_bytes,
            "meanRxBytes": rx_bytes,
            "meanTotalBytes": total_bytes,
            "idleAdjustedJPerMB": incremental_per_mb,
            "meanThroughputBitsPerSecond": throughput,
            "counterToWorkloadByteRatio": counter_ratio,
        })

    event_fields = [
        key for key in fields if key.startswith("event_") and key.endswith("_energy_j")
    ]
    event_spreads = []
    for row in valid_rows:
        event_values = [float(row[key]) for key in event_fields if row.get(key) is not None]
        if event_values:
            event_spreads.append(max(event_values) - min(event_values))

    by_scenario = {row["scenario"]: row for row in derived_rows}

    def alpha_candidate(scenario, byte_field):
        row = by_scenario.get(scenario)
        if not row or row["idleAdjustedEnergyJ"] is None:
            return None
        byte_count = row[byte_field]
        if not byte_count:
            return None
        return row["idleAdjustedEnergyJ"] / byte_count

    derived = {
        "idleHostEnergyMeanJ": idle_energy_mean,
        "scenarios": derived_rows,
        "candidateCoefficients": {
            "basis": "idle-adjusted 10 Mbit/s UDP scenarios",
            "alphaTxJPerByte": alpha_candidate("udp-ul-10m", "meanTxBytes"),
            "alphaRxJPerByte": alpha_candidate("udp-dl-10m", "meanRxBytes"),
            "warning": (
                "These are scenario-specific calibration candidates, not "
                "universal constants. Rate, direction and host activity affect them."
            ),
        },
        "quality": {
            "plannedRuns": len(runs),
            "successfulRuns": len(valid_rows),
            "missingPrimaryEnergyRuns": sum(
                row.get("primary_host_energy_j") is None for row in rows
            ),
            "maximumEventEnergyDifferenceJ": max(event_spreads) if event_spreads else None,
        },
    }
    write_json(campaign_dir / "derived-analysis.json", derived)
    derived_fields = list(derived_rows[0]) if derived_rows else []
    with (campaign_dir / "derived-analysis.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=derived_fields)
        writer.writeheader()
        writer.writerows(derived_rows)

    primary_energy_field = f"{config['primary_energy_alias']}_energy_j"
    primary_power_field = f"{config['primary_energy_alias']}_mean_power_w"
    lines = [
        f"# Energy experiment: {config['campaign']}",
        "",
        f"- Topology: `{config['topology']}`",
        f"- Runs exported: {len(rows)}",
        f"- Primary energy host: `{config['primary_energy_alias']}`",
        "",
        "## Scenario summary",
        "",
        "| Scenario | Valid runs | Host energy mean +/- SD (J) | Dynamic energy mean +/- SD (J) | Power mean +/- SD (W) | Throughput mean +/- SD (Mbit/s) | TX mean +/- SD (MB) | RX mean +/- SD (MB) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for scenario in config["scenarios"]:
        selected = [
            row for row in rows
            if row["scenario"] == scenario["name"] and row["status"] == "ok"
        ]

        def metric_stats(field, scale=1.0):
            values = [row.get(field) for row in selected]
            values = [float(value) / scale for value in values if value is not None]
            if not values:
                return None, None
            return (
                statistics.fmean(values),
                statistics.stdev(values) if len(values) > 1 else 0.0,
            )

        def show(field, scale=1.0):
            mean, stddev = metric_stats(field, scale)
            return (
                f"{mean:.3f} +/- {stddev:.3f}"
                if mean is not None else "N/A"
            )

        lines.append(
            f"| {scenario['name']} | {len(selected)} | "
            f"{show(primary_energy_field)} | "
            f"{show('host_dynamic_energy_j')} | "
            f"{show(primary_power_field)} | "
            f"{show('workload_throughput_bps', 1_000_000)} | "
            f"{show('tx_bytes', 1_000_000)} | "
            f"{show('rx_bytes', 1_000_000)} |"
        )

    lines.extend([
        "",
        "## Idle-adjusted analysis",
        "",
        "| Scenario | Incremental energy vs idle (J) | Incremental J/MB | Counter/workload byte ratio |",
        "|---|---:|---:|---:|",
    ])

    def show_derived(value, digits=3):
        return f"{value:.{digits}f}" if value is not None else "N/A"

    for row in derived_rows:
        lines.append(
            f"| {row['scenario']} | "
            f"{show_derived(row['idleAdjustedEnergyJ'])} | "
            f"{show_derived(row['idleAdjustedJPerMB'], 6)} | "
            f"{show_derived(row['counterToWorkloadByteRatio'])} |"
        )
    lines.extend([
        "",
        "The candidate byte coefficients in `derived-analysis.json` are based on the 10 Mbit/s UDP scenarios. They must not be treated as universal constants because power is non-linear across traffic rates and directions.",
        "",
        "## Exported data",
        "",
        "`summary.csv` contains one row per run. `aggregates.csv` contains mean, standard deviation, minimum and maximum per scenario. Each run directory preserves raw workload, Prometheus and Collector responses.",
        "",
        "The measurements represent host RAPL energy and a laboratory traffic-based attribution. They are not direct UE modem energy measurements.",
        "",
    ])
    (campaign_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return rows


def environment_snapshot(config):
    docker = config.get("docker_command", ["docker"])
    iperf_probe = [
        *docker,
        "exec",
        config["ue_container"],
        "iperf3",
        "-c",
        config["iperf_server"],
        "-B",
        config["ue_ip"],
        "-t",
        "1",
        "-J",
    ]
    commands = {
        "uname": ["uname", "-a"],
        "lscpu": ["lscpu"],
        "git_revision": ["git", "rev-parse", "HEAD"],
        "git_status": ["git", "status", "--short"],
        "docker_version": ["docker", "version"],
        "docker_compose_version": ["docker", "compose", "version"],
        "docker_ps": ["docker", "ps", "--no-trunc"],
        "ue_address": [
            *docker,
            "exec",
            config["ue_container"],
            "ip",
            "addr",
            "show",
            config["ue_tunnel"],
        ],
        "iperf_probe": iperf_probe,
    }
    return {
        "capturedAt": utc_now(),
        "hostname": platform.node(),
        "python": sys.version,
        "commands": {name: command_result(command) for name, command in commands.items()},
        "collectorHealth": http_json(f"{config['collector_url']}/health"),
        "energySourceStatus": http_json(
            f"{config['collector_url']}/energy-sources/status"
        ),
        "prometheusTargets": http_json(
            f"{config['prometheus_url']}/api/v1/targets"
        ),
    }


def validate_preflight(config, snapshot, allow_source_mismatch):
    errors = []
    if not snapshot["collectorHealth"].get("ok"):
        errors.append("Energy Collector health endpoint is unavailable")
    if not snapshot["energySourceStatus"].get("ok"):
        errors.append("Energy Collector source status is unavailable")
    else:
        status = snapshot["energySourceStatus"]["body"]
        expected_job = config.get("primary_energy_job")
        if expected_job and expected_job not in status.get("promqlTemplate", ""):
            message = (
                f"Collector PromQL does not select expected job {expected_job!r}"
            )
            if allow_source_mismatch:
                print(f"WARNING: {message}", file=sys.stderr)
            else:
                errors.append(message)
        expected_mode = config.get("expected_energy_mode")
        if expected_mode and status.get("mode") != expected_mode:
            errors.append(
                f"Collector energy mode is {status.get('mode')!r}, expected {expected_mode!r}"
            )
        expected_attribution = config.get("expected_attribution_mode")
        if expected_attribution and status.get("attributionMode") != expected_attribution:
            errors.append(
                "Collector attribution mode is "
                f"{status.get('attributionMode')!r}, expected {expected_attribution!r}"
            )
        minimum_baseline = config.get("minimum_idle_baseline_w", 0)
        actual_baseline = status.get("idleBaselinePowerWatts", 0)
        if actual_baseline < minimum_baseline:
            errors.append(
                "Collector idle baseline is below the configured minimum "
                f"({minimum_baseline} W)"
            )
        expected_baseline = config.get("idle_baseline_w")
        if (
            expected_baseline is not None and
            not math.isclose(actual_baseline, expected_baseline, rel_tol=0, abs_tol=1e-6)
        ):
            errors.append(
                f"Collector idle baseline is {actual_baseline} W, "
                f"but campaign config expects {expected_baseline} W"
            )
        expected_storage = config.get("expected_storage")
        if expected_storage and status.get("storage") != expected_storage:
            errors.append(
                f"Collector storage is {status.get('storage')!r}, expected {expected_storage!r}"
            )
    if not snapshot["prometheusTargets"].get("ok"):
        errors.append("Prometheus targets endpoint is unavailable")
    else:
        targets = snapshot["prometheusTargets"]["body"].get("data", {}).get(
            "activeTargets", []
        )
        health = {
            target.get("labels", {}).get("job"): target.get("health")
            for target in targets
        }
        required_jobs = {
            config.get("primary_energy_job"),
            "upf",
            *config.get("scaphandre_jobs", {}).values(),
            *config.get("node_exporter_jobs", {}).values(),
        }
        for job in sorted(job for job in required_jobs if job):
            if health.get(job) != "up":
                errors.append(
                    f"Prometheus target {job!r} is {health.get(job, 'missing')!r}"
                )
    ue = snapshot["commands"]["ue_address"]
    if ue.get("returnCode") != 0:
        errors.append(f"UE tunnel {config['ue_tunnel']} is unavailable")
    iperf_probe = snapshot["commands"]["iperf_probe"]
    if iperf_probe.get("returnCode") != 0:
        errors.append(f"iperf3 endpoint {config['iperf_server']} is unavailable from the UE")

    if errors:
        raise RuntimeError("preflight failed:\n- " + "\n- ".join(errors))


def write_checksums(campaign_dir):
    checksum_path = campaign_dir / "checksums.sha256"
    lines = []
    for path in sorted(campaign_dir.rglob("*")):
        if not path.is_file() or path == checksum_path:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(campaign_dir)}")
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_archive(campaign_dir):
    archive_path = campaign_dir.with_suffix(".tar.gz")
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(campaign_dir, arcname=campaign_dir.name)
    return archive_path


def restore_sudo_ownership(paths):
    uid = os.getenv("SUDO_UID")
    gid = os.getenv("SUDO_GID")
    if uid is None or gid is None:
        return

    owner = (int(uid), int(gid))
    for root in paths:
        if root is None or not root.exists():
            continue
        os.chown(root, *owner)
        if root.is_dir():
            for path in root.rglob("*"):
                os.chown(path, *owner)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run and export repeatable EIF/UPF energy experiments."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--scenario", action="append", dest="scenarios")
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--label")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--allow-source-mismatch", action="store_true")
    parser.add_argument("--no-archive", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    config_path = args.config.resolve()
    config = load_config(config_path)
    scenarios = selected_scenarios(config, args.scenarios)
    repetitions = args.repetitions or config["repetitions"]
    if repetitions <= 0:
        raise SystemExit("--repetitions must be positive")

    if args.dry_run:
        print(json.dumps({
            "config": str(config_path),
            "campaign": config["campaign"],
            "topology": config["topology"],
            "repetitions": repetitions,
            "scenarios": [scenario["name"] for scenario in scenarios],
            "totalRuns": repetitions * len(scenarios),
            "estimatedMinutes": round(
                repetitions * len(scenarios) *
                (config["window_seconds"] + config.get("cooldown_seconds", 0)) / 60,
                1,
            ),
            "workloadCommands": {
                scenario["name"]: workload_command(config, scenario)
                for scenario in scenarios
            },
        }, indent=2))
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = safe_name(args.label or config["campaign"])
    campaign_dir = args.output_root.resolve() / f"{label}-{timestamp}"
    campaign_dir.mkdir(parents=True, exist_ok=False)
    (campaign_dir / "config.json").write_bytes(config_path.read_bytes())

    manifest = {
        "campaign": config["campaign"],
        "topology": config["topology"],
        "startedAt": utc_now(),
        "status": "running",
        "config": str(config_path),
        "runsPlanned": repetitions * len(scenarios),
    }
    write_json(campaign_dir / "manifest.json", manifest)

    snapshot = environment_snapshot(config)
    write_json(campaign_dir / "environment.json", snapshot)
    validate_preflight(config, snapshot, args.allow_source_mismatch)
    initial_cooldown = config.get("initial_cooldown_seconds", 0)
    if initial_cooldown > 0:
        print(f"Initial cooldown after preflight: {initial_cooldown}s", flush=True)
        time.sleep(initial_cooldown)

    runs = []
    stop = False
    try:
        run_number = 0
        for repetition in range(1, repetitions + 1):
            for scenario in scenarios:
                run_number += 1
                run_id = f"{run_number:03d}-{safe_name(scenario['name'])}-r{repetition}"
                print(
                    f"[{run_number}/{manifest['runsPlanned']}] "
                    f"{scenario['name']} repetition {repetition}",
                    flush=True,
                )
                result = run_one(
                    config,
                    scenario,
                    repetition,
                    campaign_dir / "runs" / run_id,
                )
                runs.append((run_id, result))
                write_summary(campaign_dir, config, runs)
                if result["status"] == "failed" and not args.continue_on_error:
                    stop = True
                    break
                cooldown = config.get("cooldown_seconds", 0)
                if cooldown > 0 and run_number < manifest["runsPlanned"]:
                    print(f"Cooling down for {cooldown}s", flush=True)
                    time.sleep(cooldown)
            if stop:
                break
    except KeyboardInterrupt:
        manifest["status"] = "interrupted"
    finally:
        write_summary(campaign_dir, config, runs)
        if manifest["status"] == "running":
            manifest["status"] = (
                "complete"
                if len(runs) == manifest["runsPlanned"] and
                all(result["status"] == "ok" for _, result in runs)
                else "partial"
            )
        manifest["endedAt"] = utc_now()
        manifest["runsCompleted"] = len(runs)
        manifest["runsSuccessful"] = sum(
            result["status"] == "ok" for _, result in runs
        )
        write_json(campaign_dir / "manifest.json", manifest)
        write_checksums(campaign_dir)
        archive_path = None if args.no_archive else create_archive(campaign_dir)
        restore_sudo_ownership([campaign_dir, archive_path])

    print(f"Results: {campaign_dir}")
    if archive_path:
        print(f"Archive: {archive_path}")


if __name__ == "__main__":
    main()
