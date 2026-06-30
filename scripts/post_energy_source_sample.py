#!/usr/bin/env python3

import argparse
import json
from urllib import request


def parse_args():
    parser = argparse.ArgumentParser(
        description="Post a normalized energy window to the Energy Collector."
    )
    parser.add_argument(
        "--collector-url",
        default="http://172.22.0.44:8088",
    )
    parser.add_argument("--source", required=True)
    parser.add_argument("--metric", required=True)
    parser.add_argument("--start", required=True, help="ISO 8601 window start")
    parser.add_argument("--end", required=True, help="ISO 8601 window end")
    parser.add_argument("--value", required=True, type=float, help="Energy in joules")
    parser.add_argument(
        "--metadata",
        default="{}",
        help="JSON object with source-specific metadata",
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    return parser.parse_args()


def main():
    args = parse_args()
    metadata = json.loads(args.metadata)
    if not isinstance(metadata, dict):
        raise SystemExit("--metadata must be a JSON object")

    payload = json.dumps({
        "source": args.source,
        "metric": args.metric,
        "unit": "joules",
        "window_start": args.start,
        "window_end": args.end,
        "value": args.value,
        "metadata": metadata,
    }).encode("utf-8")
    endpoint = f"{args.collector_url.rstrip('/')}/energy-sources/samples"
    http_request = request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with request.urlopen(http_request, timeout=args.timeout) as response:
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
