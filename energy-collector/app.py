import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field


app = FastAPI(title="Energy Collector / NWDAF-lite")

VALID_SOURCES = {"manual", "android", "upf"}


class UeMapping(BaseModel):
    supi: str
    ue_ip: str
    source: str = "manual"
    timestamp: str | None = None


class TrafficSample(BaseModel):
    supi: str | None = None
    ue_ip: str | None = None
    timestamp: str | None = None
    pduSessionId: str | None = None
    dnn: str | None = None
    snssai: str | None = None
    appId: str | None = None
    flowDescs: list[str] | None = None
    tx_bytes: int = Field(ge=0)
    rx_bytes: int = Field(ge=0)
    source: str = "manual"


class AndroidEnergySample(BaseModel):
    supi: str
    ue_ip: str | None = None
    timestamp: str | None = None
    energy_joules: float | None = None
    current_now_ua: int | None = None
    voltage_now_uv: int | None = None
    source: str = "android"


ue_mappings: dict[str, UeMapping] = {}
traffic_samples: list[TrafficSample] = []
android_samples: list[AndroidEnergySample] = []


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)

    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        return default


def validate_source(source: str) -> None:
    if source not in VALID_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid source '{source}', expected one of {sorted(VALID_SOURCES)}",
        )


def estimate_energy_joules(tx_bytes: int, rx_bytes: int, duration_s: float) -> float:
    idle_power_w = env_float("ENERGY_IDLE_POWER_W", 0.05)
    alpha_tx = env_float("ENERGY_ALPHA_TX_J_PER_BYTE", 0.0000008)
    alpha_rx = env_float("ENERGY_ALPHA_RX_J_PER_BYTE", 0.0000004)

    return idle_power_w * duration_s + alpha_tx * tx_bytes + alpha_rx * rx_bytes


def find_supi_by_ue_ip(ue_ip: str) -> str | None:
    for supi, mapping in ue_mappings.items():
        if mapping.ue_ip == ue_ip:
            return supi

    return None


def find_ue_ip_by_supi(supi: str) -> str | None:
    mapping = ue_mappings.get(supi)

    if mapping is None:
        return None

    return mapping.ue_ip


def matches_optional_filter(sample_value: str | None, query_value: str | None) -> bool:
    if query_value is None:
        return True

    return sample_value == query_value


def matches_optional_list_filter(sample_values: list[str] | None, query_values: list[str] | None) -> bool:
    if not query_values:
        return True

    if not sample_values:
        return False

    return all(value in sample_values for value in query_values)


def add_optional_filters(
    response: dict,
    pdu_session_id: str | None,
    dnn: str | None,
    snssai: str | None,
    app_id: str | None,
    flow_descs: list[str] | None,
) -> dict:
    if pdu_session_id is not None:
        response["pduSessionId"] = pdu_session_id

    if dnn is not None:
        response["dnn"] = dnn

    if snssai is not None:
        response["snssai"] = snssai

    if app_id is not None:
        response["appId"] = app_id

    if flow_descs:
        response["flowDescs"] = flow_descs

    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ue-mappings", status_code=201)
def upsert_ue_mapping(mapping: UeMapping):
    validate_source(mapping.source)

    if not mapping.supi:
        raise HTTPException(status_code=400, detail="supi is required")

    if not mapping.ue_ip:
        raise HTTPException(status_code=400, detail="ue_ip is required")

    if mapping.timestamp is None:
        mapping.timestamp = utc_now()

    ue_mappings[mapping.supi] = mapping

    return {
        "status": "stored",
        "mapping": mapping,
        "total_mappings": len(ue_mappings),
    }


@app.get("/ue-mappings")
def get_ue_mappings():
    return list(ue_mappings.values())


@app.get("/ue-mappings/{supi}")
def get_ue_mapping(supi: str):
    mapping = ue_mappings.get(supi)

    if mapping is None:
        raise HTTPException(status_code=404, detail="mapping not found")

    return mapping


@app.post("/samples/traffic")
def add_traffic_sample(sample: TrafficSample):
    validate_source(sample.source)

    if sample.timestamp is None:
        sample.timestamp = utc_now()

    if sample.supi is None and sample.ue_ip is not None:
        sample.supi = find_supi_by_ue_ip(sample.ue_ip)

    if sample.ue_ip is None and sample.supi is not None:
        sample.ue_ip = find_ue_ip_by_supi(sample.supi)

    if sample.supi is None:
        raise HTTPException(
            status_code=400,
            detail="supi is required, or ue_ip must be mapped first via /ue-mappings",
        )

    traffic_samples.append(sample)

    return {
        "status": "stored",
        "source": sample.source,
        "sample": sample,
        "total_samples": len(traffic_samples),
    }


@app.post("/samples/android")
def add_android_sample(sample: AndroidEnergySample):
    validate_source(sample.source)

    if sample.timestamp is None:
        sample.timestamp = utc_now()

    if sample.energy_joules is not None and sample.energy_joules < 0:
        raise HTTPException(status_code=400, detail="energy_joules must be >= 0")

    android_samples.append(sample)

    return {
        "status": "stored",
        "source": "android",
        "sample": sample,
        "total_samples": len(android_samples),
    }


@app.get("/energy/v1/report")
def get_energy_report(
    supi: str,
    event: str,
    start: str,
    end: str,
    pduSessionId: str | None = None,
    dnn: str | None = None,
    snssai: str | None = None,
    appId: str | None = None,
    flowDescs: list[str] | None = Query(default=None),
):
    start_dt = parse_time(start)
    end_dt = parse_time(end)
    has_scope_filter = (
        pduSessionId is not None or dnn is not None or snssai is not None or
        appId is not None or bool(flowDescs)
    )

    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="end must be after start")

    selected_android = []

    if not has_scope_filter:
        for sample in android_samples:
            if sample.supi != supi:
                continue

            sample_time = parse_time(sample.timestamp)

            if start_dt <= sample_time <= end_dt and sample.energy_joules is not None:
                selected_android.append(sample)

    if selected_android:
        energy = sum(sample.energy_joules for sample in selected_android)

        return add_optional_filters({
            "supi": supi,
            "event": event,
            "start": start,
            "end": end,
            "source": "android",
            "energyInfo": {
                "energy": round(energy, 6)
            },
        }, pduSessionId, dnn, snssai, appId, flowDescs)

    selected_traffic = []

    for sample in traffic_samples:
        if sample.supi != supi:
            continue

        if not matches_optional_filter(sample.pduSessionId, pduSessionId):
            continue

        if not matches_optional_filter(sample.dnn, dnn):
            continue

        if not matches_optional_filter(sample.snssai, snssai):
            continue

        if not matches_optional_filter(sample.appId, appId):
            continue

        if not matches_optional_list_filter(sample.flowDescs, flowDescs):
            continue

        sample_time = parse_time(sample.timestamp)

        if start_dt <= sample_time <= end_dt:
            selected_traffic.append(sample)

    tx_total = sum(sample.tx_bytes for sample in selected_traffic)
    rx_total = sum(sample.rx_bytes for sample in selected_traffic)
    duration_s = (end_dt - start_dt).total_seconds()

    energy = estimate_energy_joules(tx_total, rx_total, duration_s)

    return add_optional_filters({
        "supi": supi,
        "event": event,
        "start": start,
        "end": end,
        "source": "traffic-estimator",
        "txBytes": tx_total,
        "rxBytes": rx_total,
        "durationSec": duration_s,
        "energyInfo": {
            "energy": round(energy, 6)
        },
    }, pduSessionId, dnn, snssai, appId, flowDescs)
