import os
import json
import math
from datetime import datetime, timezone
from urllib import parse, request
from urllib.error import URLError

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
except ImportError:  # pragma: no cover - local fallback if pymongo is missing.
    MongoClient = None
    PyMongoError = Exception


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
energy_source_samples: list[dict] = []
energy_attributions: list[dict] = []
mongo_client = None
mongo_db = None
mongo_connect_failed = False


def model_to_dict(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()

    return model.dict()


def mongo_uri() -> str:
    return os.getenv(
        "ENERGY_COLLECTOR_MONGO_URI",
        f"mongodb://{os.getenv('MONGO_IP', 'mongo')}:27017",
    )


def mongo_db_name() -> str:
    return os.getenv("ENERGY_COLLECTOR_MONGO_DB", "energy_collector")


def mongo_enabled() -> bool:
    return os.getenv("ENERGY_COLLECTOR_STORAGE", "mongo").lower() != "memory"


def get_mongo_db():
    global mongo_client, mongo_db, mongo_connect_failed

    if not mongo_enabled() or MongoClient is None:
        return None

    if mongo_db is not None:
        return mongo_db

    try:
        mongo_client = MongoClient(mongo_uri(), serverSelectionTimeoutMS=1000)
        mongo_client.admin.command("ping")
        mongo_db = mongo_client[mongo_db_name()]
        mongo_db.ue_mappings.create_index("supi", unique=True)
        mongo_db.ue_mappings.create_index("ue_ip")
        mongo_db.traffic_samples.create_index([("supi", 1), ("timestamp", 1)])
        mongo_db.android_samples.create_index([("supi", 1), ("timestamp", 1)])
        mongo_db.energy_source_samples.create_index([("source", 1), ("window_start", 1), ("window_end", 1)])
        mongo_db.energy_attributions.create_index([("supi", 1), ("event", 1), ("timestamp", 1)])
        mongo_db.energy_attributions.create_index([("source", 1), ("timestamp", 1)])
        mongo_connect_failed = False
        print(f"Energy Collector storage: MongoDB {mongo_uri()}/{mongo_db_name()}")
    except PyMongoError as exc:
        mongo_client = None
        mongo_db = None
        if not mongo_connect_failed:
            print(f"Energy Collector storage: memory fallback ({exc})")
            mongo_connect_failed = True

    return mongo_db


def storage_backend() -> str:
    return "mongodb" if get_mongo_db() is not None else "memory"


def energy_source_mode() -> str:
    return os.getenv("ENERGY_SOURCE", "traffic").lower()


def prometheus_url() -> str | None:
    value = os.getenv("PROMETHEUS_URL")
    return value.rstrip("/") if value else None


def scaphandre_promql_template() -> str:
    return os.getenv(
        "SCAPHANDRE_PROMQL_TEMPLATE",
        "increase(scaph_host_energy_microjoules[{window}]) / 1000000",
    )


def prometheus_timeout_s() -> float:
    return env_float("PROMETHEUS_TIMEOUT_S", 2.0)


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


def promql_window(duration_s: float) -> str:
    seconds = max(1, int(math.ceil(duration_s)))
    return f"{seconds}s"


def extract_prometheus_value(payload: dict) -> float | None:
    if payload.get("status") != "success":
        return None

    result = payload.get("data", {}).get("result", [])
    if not result:
        return None

    total = 0.0
    found = False

    for item in result:
        value = item.get("value")
        if not value or len(value) < 2:
            continue

        try:
            total += float(value[1])
            found = True
        except (TypeError, ValueError):
            continue

    return total if found else None


def query_prometheus_energy(start: datetime, end: datetime) -> dict | None:
    if energy_source_mode() not in {"prometheus", "scaphandre", "scaphandre_prometheus"}:
        return None

    base_url = prometheus_url()
    if not base_url:
        return None

    duration_s = (end - start).total_seconds()
    query = scaphandre_promql_template().replace("{window}", promql_window(duration_s))
    params = parse.urlencode({
        "query": query,
        "time": str(end.timestamp()),
    })
    url = f"{base_url}/api/v1/query?{params}"

    try:
        with request.urlopen(url, timeout=prometheus_timeout_s()) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"Energy source query failed: {exc}")
        return None

    value = extract_prometheus_value(payload)
    if value is None or value < 0:
        return None

    return {
        "source": "scaphandre_prometheus",
        "metric": "host_rapl_energy",
        "unit": "joules",
        "window_start": start.isoformat().replace("+00:00", "Z"),
        "window_end": end.isoformat().replace("+00:00", "Z"),
        "value": round(value, 6),
        "metadata": {
            "prometheus_url": base_url,
            "promql": query,
        },
    }


def store_energy_source_sample(sample: dict) -> None:
    db = get_mongo_db()
    if db is not None:
        db.energy_source_samples.insert_one(sample.copy())
    else:
        energy_source_samples.append(sample)


def store_energy_attribution(response: dict) -> None:
    attribution = response.get("attribution")
    energy_source = response.get("energySource")

    if attribution is None or energy_source is None:
        return

    document = {
        "timestamp": utc_now(),
        "supi": response.get("supi"),
        "event": response.get("event"),
        "start": response.get("start"),
        "end": response.get("end"),
        "source": response.get("source"),
        "energy": response.get("energyInfo", {}).get("energy"),
        "txBytes": response.get("txBytes", 0),
        "rxBytes": response.get("rxBytes", 0),
        "attribution": attribution,
        "energySource": energy_source,
    }

    for key in ("pduSessionId", "dnn", "snssai", "appId", "flowDescs"):
        if key in response:
            document[key] = response[key]

    db = get_mongo_db()
    if db is not None:
        db.energy_attributions.insert_one(document)
    else:
        energy_attributions.append(document)


def query_and_store_energy_source(start: datetime, end: datetime) -> dict | None:
    sample = query_prometheus_energy(start, end)

    if sample is not None:
        store_energy_source_sample(sample)

    return sample


def traffic_bytes(samples: list[dict]) -> tuple[int, int, int]:
    tx_total = sum(sample.get("tx_bytes", 0) for sample in samples)
    rx_total = sum(sample.get("rx_bytes", 0) for sample in samples)
    return tx_total, rx_total, tx_total + rx_total


def attributed_energy_response(
    response: dict,
    energy_source_sample: dict | None,
    selected_bytes: int,
    total_bytes: int,
) -> dict:
    if energy_source_sample is None or selected_bytes <= 0 or total_bytes <= 0:
        return response

    ratio = min(1.0, selected_bytes / total_bytes)
    measured_energy = energy_source_sample["value"]
    traffic_estimate_energy = response["energyInfo"]["energy"]
    response["source"] = energy_source_sample["source"]
    response["energyInfo"]["energy"] = round(measured_energy * ratio, 6)
    response["trafficEstimateEnergy"] = traffic_estimate_energy
    response["energySource"] = energy_source_sample
    response["attribution"] = {
        "method": "traffic_share",
        "selectedBytes": selected_bytes,
        "totalTrackedBytes": total_bytes,
        "ratio": round(ratio, 6),
        "measuredWindowEnergy": measured_energy,
        "trafficEstimateEnergy": traffic_estimate_energy,
    }
    store_energy_attribution(response)
    return response


def find_supi_by_ue_ip(ue_ip: str) -> str | None:
    db = get_mongo_db()
    if db is not None:
        mapping = db.ue_mappings.find_one({"ue_ip": ue_ip}, {"_id": 0})
        return mapping["supi"] if mapping else None

    for supi, mapping in ue_mappings.items():
        if mapping.ue_ip == ue_ip:
            return supi

    return None


def find_ue_ip_by_supi(supi: str) -> str | None:
    db = get_mongo_db()
    if db is not None:
        mapping = db.ue_mappings.find_one({"supi": supi}, {"_id": 0})
        return mapping["ue_ip"] if mapping else None

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
    return {"status": "ok", "storage": storage_backend()}


@app.get("/energy-sources/status")
def get_energy_source_status():
    mode = energy_source_mode()
    url = prometheus_url()

    return {
        "mode": mode,
        "enabled": mode in {"prometheus", "scaphandre", "scaphandre_prometheus"} and url is not None,
        "prometheusUrl": url,
        "promqlTemplate": scaphandre_promql_template(),
        "timeoutSec": prometheus_timeout_s(),
        "storage": storage_backend(),
    }


@app.get("/energy-sources/window")
def get_energy_source_window(start: str, end: str):
    start_dt = parse_time(start)
    end_dt = parse_time(end)

    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="end must be after start")

    sample = query_and_store_energy_source(start_dt, end_dt)

    if sample is None:
        return {
            "status": "unavailable",
            "reason": "energy source disabled or no Prometheus value returned",
            "energySource": get_energy_source_status(),
        }

    return {
        "status": "ok",
        "sample": sample,
        "storage": storage_backend(),
    }


@app.get("/energy-sources/attributions")
def get_energy_attributions(limit: int = Query(default=10, ge=1, le=100)):
    db = get_mongo_db()
    if db is not None:
        return list(
            db.energy_attributions.find({}, {"_id": 0})
            .sort("timestamp", -1)
            .limit(limit)
        )

    return list(reversed(energy_attributions[-limit:]))


@app.post("/ue-mappings", status_code=201)
def upsert_ue_mapping(mapping: UeMapping):
    validate_source(mapping.source)

    if not mapping.supi:
        raise HTTPException(status_code=400, detail="supi is required")

    if not mapping.ue_ip:
        raise HTTPException(status_code=400, detail="ue_ip is required")

    if mapping.timestamp is None:
        mapping.timestamp = utc_now()

    db = get_mongo_db()
    if db is not None:
        document = model_to_dict(mapping)
        db.ue_mappings.replace_one({"supi": mapping.supi}, document, upsert=True)
        total_mappings = db.ue_mappings.count_documents({})
    else:
        ue_mappings[mapping.supi] = mapping
        total_mappings = len(ue_mappings)

    return {
        "status": "stored",
        "mapping": mapping,
        "storage": storage_backend(),
        "total_mappings": total_mappings,
    }


@app.get("/ue-mappings")
def get_ue_mappings():
    db = get_mongo_db()
    if db is not None:
        return list(db.ue_mappings.find({}, {"_id": 0}))

    return list(ue_mappings.values())


@app.get("/ue-mappings/{supi}")
def get_ue_mapping(supi: str):
    db = get_mongo_db()
    if db is not None:
        mapping = db.ue_mappings.find_one({"supi": supi}, {"_id": 0})
        if mapping is None:
            raise HTTPException(status_code=404, detail="mapping not found")

        return mapping

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

    db = get_mongo_db()
    if db is not None:
        db.traffic_samples.insert_one(model_to_dict(sample))
        total_samples = db.traffic_samples.count_documents({})
    else:
        traffic_samples.append(sample)
        total_samples = len(traffic_samples)

    return {
        "status": "stored",
        "source": sample.source,
        "sample": sample,
        "storage": storage_backend(),
        "total_samples": total_samples,
    }


@app.post("/samples/android")
def add_android_sample(sample: AndroidEnergySample):
    validate_source(sample.source)

    if sample.timestamp is None:
        sample.timestamp = utc_now()

    if sample.energy_joules is not None and sample.energy_joules < 0:
        raise HTTPException(status_code=400, detail="energy_joules must be >= 0")

    db = get_mongo_db()
    if db is not None:
        db.android_samples.insert_one(model_to_dict(sample))
        total_samples = db.android_samples.count_documents({})
    else:
        android_samples.append(sample)
        total_samples = len(android_samples)

    return {
        "status": "stored",
        "source": "android",
        "sample": sample,
        "storage": storage_backend(),
        "total_samples": total_samples,
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

    db = get_mongo_db()
    if db is not None:
        if not has_scope_filter:
            android_query = {
                "supi": supi,
                "timestamp": {"$gte": start, "$lte": end},
                "energy_joules": {"$ne": None},
            }
            selected_android = list(db.android_samples.find(android_query, {"_id": 0}))

            if selected_android:
                energy = sum(sample["energy_joules"] for sample in selected_android)

                return add_optional_filters({
                    "supi": supi,
                    "event": event,
                    "start": start,
                    "end": end,
                    "source": "android",
                    "storage": "mongodb",
                    "energyInfo": {
                        "energy": round(energy, 6)
                    },
                }, pduSessionId, dnn, snssai, appId, flowDescs)

        traffic_query = {
            "supi": supi,
            "timestamp": {"$gte": start, "$lte": end},
        }

        if pduSessionId is not None:
            traffic_query["pduSessionId"] = pduSessionId

        if dnn is not None:
            traffic_query["dnn"] = dnn

        if snssai is not None:
            traffic_query["snssai"] = snssai

        if appId is not None:
            traffic_query["appId"] = appId

        if flowDescs:
            traffic_query["flowDescs"] = {"$all": flowDescs}

        selected_traffic = list(db.traffic_samples.find(traffic_query, {"_id": 0}))
        tx_total, rx_total, selected_total_bytes = traffic_bytes(selected_traffic)
        duration_s = (end_dt - start_dt).total_seconds()

        energy = estimate_energy_joules(tx_total, rx_total, duration_s)
        response = add_optional_filters({
            "supi": supi,
            "event": event,
            "start": start,
            "end": end,
            "source": "traffic-estimator",
            "storage": "mongodb",
            "txBytes": tx_total,
            "rxBytes": rx_total,
            "durationSec": duration_s,
            "energyInfo": {
                "energy": round(energy, 6)
            },
        }, pduSessionId, dnn, snssai, appId, flowDescs)

        total_traffic_query = {
            "timestamp": {"$gte": start, "$lte": end},
        }
        all_traffic = list(db.traffic_samples.find(total_traffic_query, {"_id": 0}))
        _, _, total_tracked_bytes = traffic_bytes(all_traffic)
        energy_source_sample = query_and_store_energy_source(start_dt, end_dt)

        return attributed_energy_response(
            response,
            energy_source_sample,
            selected_total_bytes,
            total_tracked_bytes,
        )

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
    selected_total_bytes = tx_total + rx_total
    duration_s = (end_dt - start_dt).total_seconds()

    energy = estimate_energy_joules(tx_total, rx_total, duration_s)
    response = add_optional_filters({
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

    all_traffic_in_window = []
    for sample in traffic_samples:
        sample_time = parse_time(sample.timestamp)

        if start_dt <= sample_time <= end_dt:
            all_traffic_in_window.append(sample)

    total_tracked_bytes = sum(
        sample.tx_bytes + sample.rx_bytes for sample in all_traffic_in_window
    )
    energy_source_sample = query_and_store_energy_source(start_dt, end_dt)

    return attributed_energy_response(
        response,
        energy_source_sample,
        selected_total_bytes,
        total_tracked_bytes,
    )
