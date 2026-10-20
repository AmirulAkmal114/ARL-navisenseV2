"""Data ingestion endpoints (API-key protected)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.app import ais_parser
from backend.app.db import (
    distinct_mmsi,
    get_nmea_since,
    insert_ais,
    insert_nmea,
    insert_sensor,
    latest_ais_timestamp,
)
from backend.app.security import require_ingest_key
from backend.app.services import parse_dt, utc_now

router = APIRouter(prefix="/api", tags=["ingest"], dependencies=[Depends(require_ingest_key)])


@router.post("/ingest")
async def ingest_nmea(request: Request):
    body = await request.json()

    ts = None
    if body.get("timestamp"):
        try:
            ts = parse_dt(body["timestamp"])
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid timestamp format")

    nmea = body.get("nmea")
    raw_data = body.get("data") or []
    device_id = body.get("device_id")
    voltage = body.get("voltage")
    temperature = body.get("temperature")

    messages = []
    if nmea:
        messages.append(nmea)
    if isinstance(raw_data, list):
        for msg in raw_data:
            msg = (msg or "").strip()
            if msg:
                messages.append(msg)

    if messages:
        rows = [(msg, ts or utc_now()) for msg in messages]
        count = insert_nmea(rows)
        return {"status": "success", "saved": count, "table": "nmea_data"}

    if voltage is not None or temperature is not None:
        insert_sensor(device_id, voltage, temperature, ts or utc_now())
        return {"status": "success", "saved": 1, "table": "sensor_data"}

    raise HTTPException(status_code=400, detail="No NMEA messages or sensor values provided")


@router.post("/insert_ais_data")
async def insert_ais_data(limit: int = Query(1000, ge=1, le=5000)):
    since = latest_ais_timestamp() or datetime(1970, 1, 1, tzinfo=timezone.utc)

    rows = get_nmea_since(since, limit)

    if not rows:
        return {
            "message": "No new AIS data to archive",
            "inserted": 0,
            "inserted_mmsi": [],
            "all_mmsi_in_table": distinct_mmsi(),
        }

    archive_rows = []
    inserted_mmsi = []
    for row in rows:
        line = row["nmea"]
        mmsi = None
        try:
            if line and line.startswith("!AIVDM"):
                parts = line.split(",")
                if len(parts) >= 6:
                    bitstring = ais_parser.nmea_payload_to_bitstring(parts[5])
                    decoded = ais_parser.decode_ais(bitstring)
                    if decoded.get("mmsi") is not None:
                        mmsi = str(decoded["mmsi"])
        except Exception:
            mmsi = None

        archive_rows.append((row["timestamp"], 1, 2, 101, line, mmsi))
        if mmsi and mmsi not in inserted_mmsi:
            inserted_mmsi.append(mmsi)

    inserted = insert_ais(archive_rows)

    return {
        "message": "AIS data inserted successfully",
        "inserted": inserted,
        "inserted_mmsi": inserted_mmsi,
        "all_mmsi_in_table": distinct_mmsi(),
    }
