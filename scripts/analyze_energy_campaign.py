#!/usr/bin/env python3

import argparse
from pathlib import Path

import run_energy_experiments as runner


def parse_args():
    parser = argparse.ArgumentParser(
        description="Regenerate summaries and derived analysis for an exported campaign."
    )
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--archive", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    campaign = args.campaign.resolve()
    config = runner.read_json(campaign / "config.json")
    runs = []
    for result_path in sorted((campaign / "runs").glob("*/result.json")):
        runs.append((result_path.parent.name, runner.read_json(result_path)))

    runner.write_summary(campaign, config, runs)
    runner.write_checksums(campaign)
    archive = runner.create_archive(campaign) if args.archive else None
    runner.restore_sudo_ownership([campaign, archive])
    print(f"Campaign: {campaign}")
    if archive:
        print(f"Archive: {archive}")


if __name__ == "__main__":
    main()
