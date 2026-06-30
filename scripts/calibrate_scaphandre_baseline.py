#!/usr/bin/env python3

import argparse
import json
import math
import statistics
import time
from urllib import parse, request


def parse_args():
    parser = argparse.ArgumentParser(
        description="Estimate an idle RAPL power baseline from Prometheus."
    )
    parser.add_argument(
        "--prometheus-url",
        default="http://localhost:9090",
        help="Prometheus base URL",
    )
    parser.add_argument(
        "--job",
        default="scaphandre-exigence1",
        help="Scaphandre Prometheus job label",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=300,
        help="Idle observation duration in seconds",
    )
    parser.add_argument(
        "--rate-window",
        type=int,
        default=30,
        help="PromQL rate window in seconds",
    )
    parser.add_argument(
        "--step",
        type=int,
        default=5,
        help="Prometheus query step in seconds",
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--env",
        action="store_true",
        help="Also print the recommended .env assignment",
    )
    args = parser.parse_args()

    for name in ("duration", "rate_window", "step"):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be greater than zero")

    return args


def prometheus_values(args):
    end = time.time()
    start = end - args.duration
    escaped_job = args.job.replace("\\", "\\\\").replace('"', '\\"')
    query = (
        "rate(scaph_host_energy_microjoules"
        f'{{job="{escaped_job}"}}[{args.rate_window}s])/1000000'
    )
    params = parse.urlencode({
        "query": query,
        "start": f"{start:.3f}",
        "end": f"{end:.3f}",
        "step": str(args.step),
    })
    url = f"{args.prometheus_url.rstrip('/')}/api/v1/query_range?{params}"

    with request.urlopen(url, timeout=args.timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload}")

    values = []
    for series in payload.get("data", {}).get("result", []):
        for _, raw_value in series.get("values", []):
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value >= 0:
                values.append(value)

    if not values:
        raise RuntimeError("Prometheus returned no finite Scaphandre values")

    return query, values


def percentile(values, fraction):
    ordered = sorted(values)
    position = min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)
    return ordered[max(0, position)]


def main():
    args = parse_args()
    query, values = prometheus_values(args)
    median_w = statistics.median(values)
    result = {
        "source": "scaphandre_prometheus",
        "job": args.job,
        "metric": "host_rapl_power",
        "unit": "watts",
        "query": query,
        "observationDurationSec": args.duration,
        "rateWindowSec": args.rate_window,
        "stepSec": args.step,
        "samples": len(values),
        "minimumWatts": round(min(values), 6),
        "meanWatts": round(statistics.fmean(values), 6),
        "medianWatts": round(median_w, 6),
        "p95Watts": round(percentile(values, 0.95), 6),
        "standardDeviationWatts": round(
            statistics.pstdev(values) if len(values) > 1 else 0.0,
            6,
        ),
        "recommendedBaselineWatts": round(median_w, 6),
    }
    print(json.dumps(result, indent=2))

    if args.env:
        print(
            "\nENERGY_HOST_IDLE_BASELINE_W="
            f"{result['recommendedBaselineWatts']}"
        )


if __name__ == "__main__":
    main()
