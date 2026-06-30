#!/usr/bin/env python3

import argparse
import csv
import json
import math
import statistics
from pathlib import Path


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def finite_values(rows, field):
    values = []
    for row in rows:
        value = row.get(field)
        if value is None or value == "":
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            values.append(number)
    return values


def stats(rows, field):
    values = finite_values(rows, field)
    if not values:
        return {"samples": 0, "mean": None, "stddev": None, "minimum": None, "maximum": None}
    return {
        "samples": len(values),
        "mean": statistics.fmean(values),
        "stddev": statistics.stdev(values) if len(values) > 1 else 0.0,
        "minimum": min(values),
        "maximum": max(values),
    }


def load_campaign(directory):
    config = read_json(directory / "config.json")
    rows = read_json(directory / "summary.json")
    successful = [row for row in rows if row.get("status") == "ok"]
    return {
        "directory": str(directory),
        "config": config,
        "rows": successful,
        "energyField": f"{config['primary_energy_alias']}_energy_j",
        "powerField": f"{config['primary_energy_alias']}_mean_power_w",
    }


def percent_change(before, after):
    if before is None or after is None or before == 0:
        return None
    return (after - before) / before * 100.0


def compare(before, after):
    before_names = {row["scenario"] for row in before["rows"]}
    after_names = {row["scenario"] for row in after["rows"]}
    scenarios = sorted(before_names | after_names)
    output = []

    for scenario in scenarios:
        before_rows = [row for row in before["rows"] if row["scenario"] == scenario]
        after_rows = [row for row in after["rows"] if row["scenario"] == scenario]
        before_energy = stats(before_rows, before["energyField"])
        after_energy = stats(after_rows, after["energyField"])
        before_power = stats(before_rows, before["powerField"])
        after_power = stats(after_rows, after["powerField"])
        before_throughput = stats(before_rows, "workload_throughput_bps")
        after_throughput = stats(after_rows, "workload_throughput_bps")

        output.append({
            "scenario": scenario,
            "before_samples": len(before_rows),
            "after_samples": len(after_rows),
            "before_energy_mean_j": before_energy["mean"],
            "before_energy_stddev_j": before_energy["stddev"],
            "after_energy_mean_j": after_energy["mean"],
            "after_energy_stddev_j": after_energy["stddev"],
            "energy_delta_j": (
                after_energy["mean"] - before_energy["mean"]
                if before_energy["mean"] is not None and after_energy["mean"] is not None
                else None
            ),
            "energy_change_percent": percent_change(
                before_energy["mean"], after_energy["mean"]
            ),
            "before_power_mean_w": before_power["mean"],
            "after_power_mean_w": after_power["mean"],
            "power_change_percent": percent_change(
                before_power["mean"], after_power["mean"]
            ),
            "before_throughput_mean_mbps": (
                before_throughput["mean"] / 1_000_000
                if before_throughput["mean"] is not None else None
            ),
            "after_throughput_mean_mbps": (
                after_throughput["mean"] / 1_000_000
                if after_throughput["mean"] is not None else None
            ),
        })
    return output


def show(value):
    return f"{value:.3f}" if value is not None else "N/A"


def write_report(path, before, after, rows):
    lines = [
        "# Energy campaign comparison",
        "",
        f"- Before: `{before['config']['campaign']}` ({before['config']['topology']})",
        f"- After: `{after['config']['campaign']}` ({after['config']['topology']})",
        "",
        "| Scenario | Before energy (J) | After energy (J) | Change (%) | Before power (W) | After power (W) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['scenario']} | {show(row['before_energy_mean_j'])} | "
            f"{show(row['after_energy_mean_j'])} | "
            f"{show(row['energy_change_percent'])} | "
            f"{show(row['before_power_mean_w'])} | "
            f"{show(row['after_power_mean_w'])} |"
        )
    lines.extend([
        "",
        "Positive changes mean that the measured primary host consumed more energy after the UPF deployment change. Host composition differs between topologies, so these values must be interpreted together with throughput and raw host metrics.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Compare two exported EIF energy experiment campaigns."
    )
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path, default=Path("energy-comparison"))
    return parser.parse_args()


def main():
    args = parse_args()
    before = load_campaign(args.before.resolve())
    after = load_campaign(args.after.resolve())
    rows = compare(before, after)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    write_json(output / "comparison.json", rows)
    fields = list(rows[0]) if rows else []
    with (output / "comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    write_report(output / "comparison.md", before, after, rows)
    print(output)


if __name__ == "__main__":
    main()
