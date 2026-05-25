#!/usr/bin/env python3

import argparse
import ipaddress
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from urllib import request


UPF_METRICS_URL_DEFAULT = "http://172.22.0.8:9091/metrics"
COLLECTOR_URL_DEFAULT = "http://172.22.0.44:8088"
SUPI_DEFAULT = "imsi-001011234567895"
UE_IP_DEFAULT = "192.168.100.2"
UPF_CONTAINER_DEFAULT = "upf"
UPF_INTERFACE_DEFAULT = "ogstun"

METRIC_UPLINK_BYTES = "fivegs_ep_n3_gtp_indatavolumeqosleveln3upf"
METRIC_DOWNLINK_BYTES = "fivegs_ep_n3_gtp_outdatavolumeqosleveln3upf"
METRIC_UPLINK_PACKETS = "fivegs_ep_n3_gtp_indatapktn3upf"
METRIC_DOWNLINK_PACKETS = "fivegs_ep_n3_gtp_outdatapktn3upf"
METRIC_UPF_SESSIONS = "fivegs_upffunction_upf_sessionnbr"
METRIC_PFCP_PEERS = "pfcp_peers_active"


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_text(url, timeout_s):
    with request.urlopen(url, timeout=timeout_s) as response:
        return response.read().decode("utf-8")


def read_container_file(container, path, timeout_s):
    return run_container_command(container, ["cat", path], timeout_s)


def run_container_command(container, command, timeout_s):
    result = subprocess.run(
        ["docker", "exec", container, *command],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"failed to run in {container}: {' '.join(command)}: {result.stderr.strip()}"
        )

    return result.stdout.strip()


def fetch_interface_stats(container, interface_name, timeout_s):
    base_path = f"/sys/class/net/{interface_name}/statistics"

    rx_bytes = int(read_container_file(container, f"{base_path}/rx_bytes", timeout_s))
    tx_bytes = int(read_container_file(container, f"{base_path}/tx_bytes", timeout_s))
    rx_packets = int(read_container_file(container, f"{base_path}/rx_packets", timeout_s))
    tx_packets = int(read_container_file(container, f"{base_path}/tx_packets", timeout_s))

    return {
        "rx_bytes": rx_bytes,
        "tx_bytes": tx_bytes,
        "rx_packets": rx_packets,
        "tx_packets": tx_packets,
    }


def iptables_comment(prefix, direction):
    return f"{prefix}-{direction}"


def add_iptables_rule(args, direction, comment):
    if direction == "ul":
        match_args = ["-i", args.upf_interface, "-s", args.ue_ip]
    elif direction == "dl":
        match_args = ["-o", args.upf_interface, "-d", args.ue_ip]
    else:
        raise ValueError(f"invalid direction {direction}")

    run_container_command(
        args.upf_container,
        [
            "iptables",
            "-I",
            "FORWARD",
            "1",
            *match_args,
            "-m",
            "comment",
            "--comment",
            comment,
        ],
        args.timeout,
    )


def delete_iptables_rule(args, direction, comment):
    if direction == "ul":
        match_args = ["-i", args.upf_interface, "-s", args.ue_ip]
    elif direction == "dl":
        match_args = ["-o", args.upf_interface, "-d", args.ue_ip]
    else:
        raise ValueError(f"invalid direction {direction}")

    try:
        run_container_command(
            args.upf_container,
            [
                "iptables",
                "-D",
                "FORWARD",
                *match_args,
                "-m",
                "comment",
                "--comment",
                comment,
            ],
            args.timeout,
        )
    except RuntimeError as exc:
        print(f"Warning: failed to remove iptables rule {comment}: {exc}")


def setup_iptables_counters(args):
    ipaddress.ip_address(args.ue_ip)

    prefix = f"eif-upf-{int(time.time() * 1000)}"
    ul_comment = iptables_comment(prefix, "ul")
    dl_comment = iptables_comment(prefix, "dl")

    run_container_command(
        args.upf_container,
        ["sh", "-lc", "command -v iptables && command -v iptables-save"],
        args.timeout,
    )
    add_iptables_rule(args, "ul", ul_comment)

    try:
        add_iptables_rule(args, "dl", dl_comment)
    except Exception:
        delete_iptables_rule(args, "ul", ul_comment)
        raise

    return {
        "prefix": prefix,
        "ul_comment": ul_comment,
        "dl_comment": dl_comment,
    }


def cleanup_iptables_counters(args, counters):
    if not counters:
        return

    delete_iptables_rule(args, "ul", counters["ul_comment"])
    delete_iptables_rule(args, "dl", counters["dl_comment"])


def fetch_iptables_counter_bytes(args, comment):
    output = run_container_command(args.upf_container, ["iptables-save", "-c"], args.timeout)

    for line in output.splitlines():
        if comment not in line:
            continue

        match = re.match(r"^\[(\d+):(\d+)\]\s+-A\s+FORWARD\b", line)
        if not match:
            continue

        return int(match.group(1)), int(match.group(2))

    raise RuntimeError(f"iptables counter rule not found for comment {comment}")


def post_json(url, payload, timeout_s):
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with request.urlopen(req, timeout=timeout_s) as response:
        return response.status, response.read().decode("utf-8")


def metric_values(metrics_text, metric_name):
    values = []
    pattern = re.compile(rf"^{re.escape(metric_name)}(?:\{{[^}}]*\}})?\s+([-+0-9.eE]+)\s*$")

    for line in metrics_text.splitlines():
        if not line or line.startswith("#"):
            continue

        match = pattern.match(line)
        if not match:
            continue

        try:
            values.append(float(match.group(1)))
        except ValueError:
            continue

    return values


def metric_sum(metrics_text, metric_name):
    return sum(metric_values(metrics_text, metric_name))


def metric_max(metrics_text, metric_name):
    values = metric_values(metrics_text, metric_name)
    if not values:
        return 0.0
    return max(values)


def non_negative_delta(start_value, end_value):
    return max(0.0, end_value - start_value)


def build_prometheus_sample(start_metrics, end_metrics, args, start_ts, end_ts):
    uplink_bytes_delta = non_negative_delta(
        metric_sum(start_metrics, METRIC_UPLINK_BYTES),
        metric_sum(end_metrics, METRIC_UPLINK_BYTES),
    )
    downlink_bytes_delta = non_negative_delta(
        metric_sum(start_metrics, METRIC_DOWNLINK_BYTES),
        metric_sum(end_metrics, METRIC_DOWNLINK_BYTES),
    )

    uplink_packets_delta = non_negative_delta(
        metric_sum(start_metrics, METRIC_UPLINK_PACKETS),
        metric_sum(end_metrics, METRIC_UPLINK_PACKETS),
    )
    downlink_packets_delta = non_negative_delta(
        metric_sum(start_metrics, METRIC_DOWNLINK_PACKETS),
        metric_sum(end_metrics, METRIC_DOWNLINK_PACKETS),
    )

    used_packet_estimate = False
    if uplink_bytes_delta == 0 and uplink_packets_delta > 0:
        uplink_bytes_delta = uplink_packets_delta * args.avg_packet_bytes
        used_packet_estimate = True

    if downlink_bytes_delta == 0 and downlink_packets_delta > 0:
        downlink_bytes_delta = downlink_packets_delta * args.avg_packet_bytes
        used_packet_estimate = True

    return {
        "supi": args.supi,
        "ue_ip": args.ue_ip,
        "timestamp": end_ts,
        "tx_bytes": int(round(uplink_bytes_delta)),
        "rx_bytes": int(round(downlink_bytes_delta)),
        "source": "upf",
        "metadata": {
            "estimator_source": "prometheus",
            "start": start_ts,
            "end": end_ts,
            "upf_metrics_url": args.upf_metrics_url,
            "uplink_packets_delta": int(round(uplink_packets_delta)),
            "downlink_packets_delta": int(round(downlink_packets_delta)),
            "avg_packet_bytes": args.avg_packet_bytes,
            "used_packet_estimate": used_packet_estimate,
            "active_sessions": metric_max(end_metrics, METRIC_UPF_SESSIONS),
            "pfcp_peers_active": metric_max(end_metrics, METRIC_PFCP_PEERS),
        },
    }


def build_interface_sample(start_stats, end_stats, end_metrics, args, start_ts, end_ts):
    interface_rx_bytes_delta = non_negative_delta(start_stats["rx_bytes"], end_stats["rx_bytes"])
    interface_tx_bytes_delta = non_negative_delta(start_stats["tx_bytes"], end_stats["tx_bytes"])
    interface_rx_packets_delta = non_negative_delta(start_stats["rx_packets"], end_stats["rx_packets"])
    interface_tx_packets_delta = non_negative_delta(start_stats["tx_packets"], end_stats["tx_packets"])

    return {
        "supi": args.supi,
        "ue_ip": args.ue_ip,
        "timestamp": end_ts,
        "tx_bytes": int(round(interface_rx_bytes_delta)),
        "rx_bytes": int(round(interface_tx_bytes_delta)),
        "source": "upf",
        "metadata": {
            "estimator_source": "interface",
            "start": start_ts,
            "end": end_ts,
            "upf_container": args.upf_container,
            "upf_interface": args.upf_interface,
            "interface_rx_bytes_delta": int(round(interface_rx_bytes_delta)),
            "interface_tx_bytes_delta": int(round(interface_tx_bytes_delta)),
            "interface_rx_packets_delta": int(round(interface_rx_packets_delta)),
            "interface_tx_packets_delta": int(round(interface_tx_packets_delta)),
            "direction_note": "UPF interface RX is treated as UE uplink tx_bytes; UPF interface TX is treated as UE downlink rx_bytes.",
            "active_sessions": metric_max(end_metrics, METRIC_UPF_SESSIONS),
            "pfcp_peers_active": metric_max(end_metrics, METRIC_PFCP_PEERS),
        },
    }


def build_iptables_sample(counters, end_metrics, args, start_ts, end_ts):
    uplink_packets, uplink_bytes = fetch_iptables_counter_bytes(args, counters["ul_comment"])
    downlink_packets, downlink_bytes = fetch_iptables_counter_bytes(args, counters["dl_comment"])

    return {
        "supi": args.supi,
        "ue_ip": args.ue_ip,
        "timestamp": end_ts,
        "tx_bytes": uplink_bytes,
        "rx_bytes": downlink_bytes,
        "source": "upf",
        "metadata": {
            "estimator_source": "ue-iptables",
            "start": start_ts,
            "end": end_ts,
            "upf_container": args.upf_container,
            "upf_interface": args.upf_interface,
            "iptables_chain": "FORWARD",
            "uplink_match": f"-i {args.upf_interface} -s {args.ue_ip}",
            "downlink_match": f"-o {args.upf_interface} -d {args.ue_ip}",
            "uplink_packets_delta": uplink_packets,
            "downlink_packets_delta": downlink_packets,
            "direction_note": (
                f"UPF FORWARD packets from UE IP via {args.upf_interface} are "
                f"treated as tx_bytes; packets to UE IP via {args.upf_interface} "
                "are treated as rx_bytes."
            ),
            "active_sessions": metric_max(end_metrics, METRIC_UPF_SESSIONS),
            "pfcp_peers_active": metric_max(end_metrics, METRIC_PFCP_PEERS),
        },
    }


def collector_traffic_payload(sample):
    payload = {
        "supi": sample["supi"],
        "ue_ip": sample["ue_ip"],
        "timestamp": sample["timestamp"],
        "tx_bytes": sample["tx_bytes"],
        "rx_bytes": sample["rx_bytes"],
        "source": sample["source"],
    }

    for field in ("pduSessionId", "dnn", "snssai", "appId", "flowDescs"):
        if sample.get(field):
            payload[field] = sample[field]

    return payload


def add_scope_fields(sample, args):
    if args.pdu_session_id:
        sample["pduSessionId"] = args.pdu_session_id

    if args.dnn:
        sample["dnn"] = args.dnn

    if args.snssai:
        sample["snssai"] = args.snssai

    if args.app_id:
        sample["appId"] = args.app_id

    if args.flow_descs:
        sample["flowDescs"] = args.flow_descs

    return sample


def register_mapping(args):
    payload = {
        "supi": args.supi,
        "ue_ip": args.ue_ip,
        "source": "upf",
        "timestamp": utc_now(),
    }
    return post_json(f"{args.collector_url}/ue-mappings", payload, args.timeout)


def emit_sample(sample, args):
    print(json.dumps(sample, indent=2))

    if args.post:
        payload = collector_traffic_payload(sample)
        status, body = post_json(f"{args.collector_url}/samples/traffic", payload, args.timeout)
        print(f"\nCollector response: HTTP {status}")
        print(body)


def main():
    parser = argparse.ArgumentParser(
        description="Estimate UE traffic from UPF Prometheus counters and optionally post it to the Energy Collector."
    )
    parser.add_argument("--upf-metrics-url", default=UPF_METRICS_URL_DEFAULT)
    parser.add_argument("--collector-url", default=COLLECTOR_URL_DEFAULT)
    parser.add_argument("--supi", default=SUPI_DEFAULT)
    parser.add_argument("--ue-ip", default=UE_IP_DEFAULT)
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--avg-packet-bytes", type=int, default=1200)
    parser.add_argument(
        "--source",
        choices=["auto", "prometheus", "interface", "ue-iptables"],
        default="auto",
    )
    parser.add_argument("--upf-container", default=UPF_CONTAINER_DEFAULT)
    parser.add_argument("--upf-interface", default=UPF_INTERFACE_DEFAULT)
    parser.add_argument("--pdu-session-id")
    parser.add_argument("--dnn")
    parser.add_argument("--snssai")
    parser.add_argument("--app-id")
    parser.add_argument("--flow-desc", dest="flow_descs", action="append")
    parser.add_argument("--register-mapping", action="store_true")
    parser.add_argument("--post", action="store_true")
    args = parser.parse_args()

    if args.interval <= 0:
        raise SystemExit("--interval must be > 0")

    if args.avg_packet_bytes <= 0:
        raise SystemExit("--avg-packet-bytes must be > 0")

    if args.register_mapping:
        status, body = register_mapping(args)
        print(f"Mapping response: HTTP {status}")
        print(body)

    if args.source == "ue-iptables":
        counters = None
        try:
            print(
                f"Installing temporary per-UE iptables counters: "
                f"{args.upf_container}:{args.upf_interface} ue_ip={args.ue_ip}"
            )
            counters = setup_iptables_counters(args)
            start_ts = utc_now()

            print(f"Waiting {args.interval:.3f}s")
            time.sleep(args.interval)

            end_metrics = fetch_text(args.upf_metrics_url, args.timeout)
            end_ts = utc_now()
            sample = build_iptables_sample(counters, end_metrics, args, start_ts, end_ts)
        finally:
            cleanup_iptables_counters(args, counters)

        add_scope_fields(sample, args)
        emit_sample(sample, args)
        return

    print(f"Reading UPF metrics: {args.upf_metrics_url}")
    start_ts = utc_now()
    start_metrics = fetch_text(args.upf_metrics_url, args.timeout)
    start_interface_stats = None

    if args.source in ("auto", "interface"):
        print(f"Reading UPF interface stats: {args.upf_container}:{args.upf_interface}")
        start_interface_stats = fetch_interface_stats(
            args.upf_container,
            args.upf_interface,
            args.timeout,
        )

    print(f"Waiting {args.interval:.3f}s")
    time.sleep(args.interval)

    end_metrics = fetch_text(args.upf_metrics_url, args.timeout)
    end_interface_stats = None
    if args.source in ("auto", "interface"):
        end_interface_stats = fetch_interface_stats(
            args.upf_container,
            args.upf_interface,
            args.timeout,
        )
    end_ts = utc_now()

    prometheus_sample = build_prometheus_sample(start_metrics, end_metrics, args, start_ts, end_ts)

    if args.source == "prometheus":
        sample = prometheus_sample
    elif args.source == "interface":
        sample = build_interface_sample(
            start_interface_stats,
            end_interface_stats,
            end_metrics,
            args,
            start_ts,
            end_ts,
        )
    elif prometheus_sample["tx_bytes"] > 0 or prometheus_sample["rx_bytes"] > 0:
        sample = prometheus_sample
    else:
        sample = build_interface_sample(
            start_interface_stats,
            end_interface_stats,
            end_metrics,
            args,
            start_ts,
            end_ts,
        )

    add_scope_fields(sample, args)
    emit_sample(sample, args)


if __name__ == "__main__":
    main()
