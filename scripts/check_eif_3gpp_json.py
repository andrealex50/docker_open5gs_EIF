#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


DEFAULT_NOTIFICATION = {
    "subId": "1",
    "reports": [
        {
            "event": "UE_ENERGY",
            "subscSetId": "set1",
            "timeStamp": "2026-05-11T19:43:49.301047Z",
            "energyInfo": {
                "energy": 0.251008,
                "energyReportTimeStamp": "2026-05-11T19:43:49.301047Z",
            },
        }
    ],
}


def fail(message: str) -> None:
    raise AssertionError(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def validate_energy_info(energy_info: object, path: str) -> None:
    require(isinstance(energy_info, dict), f"{path} must be an object")
    require("energyConsumption" not in energy_info, f"{path}.energyConsumption is not 3GPP compliant")
    require("energy" in energy_info, f"{path}.energy is required")
    require(isinstance(energy_info["energy"], (int, float)), f"{path}.energy must be numeric")
    require(energy_info["energy"] >= 0, f"{path}.energy must be >= 0")

    if "energyReportTimeStamp" in energy_info:
        require(
            isinstance(energy_info["energyReportTimeStamp"], str),
            f"{path}.energyReportTimeStamp must be a string",
        )


def validate_notification(payload: object) -> None:
    require(isinstance(payload, dict), "notification must be a JSON object")
    require(isinstance(payload.get("subId"), str), "subId string is required")
    require(isinstance(payload.get("reports"), list), "reports array is required")
    require(len(payload["reports"]) > 0, "reports must not be empty")

    for index, report in enumerate(payload["reports"]):
        path = f"reports[{index}]"
        require(isinstance(report, dict), f"{path} must be an object")
        require(isinstance(report.get("event"), str), f"{path}.event string is required")
        require(isinstance(report.get("subscSetId"), str), f"{path}.subscSetId string is required")
        require(isinstance(report.get("timeStamp"), str), f"{path}.timeStamp string is required")

        if "energyInfo" in report:
            validate_energy_info(report["energyInfo"], f"{path}.energyInfo")


def load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def check_repo_static(root: Path) -> None:
    ts29566 = root / "base/open5gs-EIF/lib/sbi/support/r17-20230301-openapitools-6.4.0/modified/TS29566_Neif_EventExposure.yaml"
    ts29122 = root / "base/open5gs-EIF/lib/sbi/support/r17-20230301-openapitools-6.4.0/modified/TS29122_MonitoringEvent.yaml"
    energy_info_c = root / "base/open5gs-EIF/lib/sbi/openapi/model/energy_info.c"

    for path in (ts29566, ts29122, energy_info_c):
        require(path.exists(), f"missing file: {path}")

    ts29566_text = ts29566.read_text(encoding="utf-8")
    ts29122_text = ts29122.read_text(encoding="utf-8")
    energy_info_c_text = energy_info_c.read_text(encoding="utf-8")

    require(
        "TS29122_MonitoringEvent.yaml#/components/schemas/EnergyInfo" in ts29566_text,
        "TS 29.566 EnergyInfo must reference TS 29.122 MonitoringEvent EnergyInfo",
    )
    require(
        "energyConsumption:" not in ts29566_text,
        "TS 29.566 schema must not define energyConsumption",
    )
    require("EnergyInfo:" in ts29122_text, "TS 29.122 modified schema must define EnergyInfo")
    require("energy:" in ts29122_text, "TS 29.122 EnergyInfo must define energy")
    require("- energy" in ts29122_text, "TS 29.122 EnergyInfo must require energy")
    require(
        'cJSON_AddNumberToObject(item, "energy",' in energy_info_c_text,
        "EnergyInfo serializer must emit JSON key 'energy'",
    )
    require(
        '"energyConsumption"' not in energy_info_c_text,
        "EnergyInfo model must not serialize or parse JSON key 'energyConsumption'",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check EIF Energy Event Exposure JSON against the 3GPP-facing message contract."
    )
    parser.add_argument("--repo-root", default=".", help="Repository root, default: current directory")
    parser.add_argument(
        "--notification-json",
        action="append",
        default=[],
        help="Path to an EnergyEeNotif JSON file to validate. Can be used multiple times.",
    )
    parser.add_argument(
        "--skip-static",
        action="store_true",
        help="Only validate JSON payloads, skip repository static checks.",
    )
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()

    try:
        if not args.skip_static:
            check_repo_static(root)

        validate_notification(DEFAULT_NOTIFICATION)

        for notification_path in args.notification_json:
            validate_notification(load_json(Path(notification_path)))

    except (AssertionError, json.JSONDecodeError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print("OK: EIF 3GPP JSON checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
