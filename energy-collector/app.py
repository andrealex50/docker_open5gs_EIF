from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Energy Collector / NWDAF-lite")


class TrafficSample(BaseModel):
    supi: str
    ue_ip: str | None = None
    timestamp: str | None = None
    tx_bytes: int
    rx_bytes: int


samples: list[TrafficSample] = []


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def estimate_energy_joules(tx_bytes: int, rx_bytes: int, duration_s: float) -> float:
    idle_power_w = 0.05
    alpha_tx = 0.0000008
    alpha_rx = 0.0000004

    return idle_power_w * duration_s + alpha_tx * tx_bytes + alpha_rx * rx_bytes


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/samples/traffic")
def add_traffic_sample(sample: TrafficSample):
    if sample.timestamp is None:
        sample.timestamp = datetime.now(timezone.utc).isoformat()

    samples.append(sample)
    return {"status": "stored", "total_samples": len(samples)}


@app.get("/energy/v1/report")
def get_energy_report(
    supi: str,
    event: str,
    start: str,
    end: str,
    pduSessionId: str | None = None,
    dnn: str | None = None,
    snssai: str | None = None,
):
    start_dt = parse_time(start)
    end_dt = parse_time(end)

    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="end must be after start")

    selected = []

    for sample in samples:
        if sample.supi != supi:
            continue

        sample_time = parse_time(sample.timestamp)

        if start_dt <= sample_time <= end_dt:
            selected.append(sample)

    tx_total = sum(s.tx_bytes for s in selected)
    rx_total = sum(s.rx_bytes for s in selected)
    duration_s = (end_dt - start_dt).total_seconds()

    energy = estimate_energy_joules(tx_total, rx_total, duration_s)

    return {
        "supi": supi,
        "event": event,
        "start": start,
        "end": end,
        "txBytes": tx_total,
        "rxBytes": rx_total,
        "energyInfo": {
            "energy": round(energy, 6)
        }
    }