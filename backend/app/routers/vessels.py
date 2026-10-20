"""Vessel message listing and lookup endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.security import require_auth
from backend.app.services import decode_item, decode_rows, fetch_nmea_rows, parse_dt

router = APIRouter(prefix="/api", tags=["vessels"], dependencies=[Depends(require_auth)])


@router.get("/decode")
async def get_raw_nmea():
    rows = fetch_nmea_rows()
    nmea_list = [item["nmea"] for item in rows]
    return {
        "Number of NMEA messages": len(nmea_list),
        "NMEA": nmea_list
    }


@router.get("/all")
async def get_decoded_all():
    rows = fetch_nmea_rows()
    decoded_messages, skipped_messages = decode_rows(rows)
    return {
        "Decoded Messages": len(decoded_messages),
        "Skipped Messages": skipped_messages,
        "Decoded": decoded_messages
    }


@router.get("/class")
async def get_by_class(vessel_class: str = Query(...)):
    vclass = vessel_class.upper()
    if vclass not in ("A", "B"):
        raise HTTPException(status_code=422, detail="vessel_class must be A or B")

    allowed_types = [1, 2, 3] if vclass == "A" else [18, 19, 24]

    rows = fetch_nmea_rows()
    decoded_messages = []
    skipped_messages = 0

    for item in rows:
        decoded = decode_item(item)
        if decoded is None:
            skipped_messages += 1
            continue
        if decoded.get("type") not in allowed_types:
            skipped_messages += 1
            continue
        decoded_messages.append(decoded)

    return {
        "Vessel Class": vclass,
        "Decoded Messages": len(decoded_messages),
        "Skipped Messages": skipped_messages,
        "Decoded": decoded_messages
    }


@router.get("/mmsi")
async def get_by_mmsi(mmsi: str):
    rows = fetch_nmea_rows()
    decoded_messages, _ = decode_rows(rows, mmsi=mmsi)
    if not decoded_messages:
        raise HTTPException(status_code=404, detail="MMSI not found")
    return {"Number of Messages": len(decoded_messages), "Decoded": decoded_messages}


@router.get("/get-by-mmsi/duration")
async def get_by_mmsi_duration(mmsi: str, from_time: str = None, to_time: str = None):
    try:
        from_dt = parse_dt(from_time) if from_time else None
        to_dt = parse_dt(to_time) if to_time else None
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid timestamp format")

    rows = fetch_nmea_rows(from_time=from_dt, to_time=to_dt)
    decoded_messages, _ = decode_rows(rows, mmsi=mmsi)
    return {"Number of Messages": len(decoded_messages), "Decoded": decoded_messages}
