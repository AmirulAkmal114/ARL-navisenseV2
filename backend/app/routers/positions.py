"""Time- and location-based message filtering endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from backend.app.security import require_auth
from backend.app.services import decode_rows, fetch_nmea_rows, parse_dt

router = APIRouter(prefix="/api", tags=["positions"], dependencies=[Depends(require_auth)])


@router.get("/time")
async def filter_by_time(from_time: str = None, to_time: str = None):
    try:
        from_dt = parse_dt(from_time) if from_time else None
        to_dt = parse_dt(to_time) if to_time else None
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid timestamp format")

    rows = fetch_nmea_rows(from_time=from_dt, to_time=to_dt)
    decoded_messages, skipped_messages = decode_rows(rows)

    return {
        "From": from_time,
        "To": to_time,
        "Decoded Messages": len(decoded_messages),
        "Skipped Messages": skipped_messages,
        "Decoded": decoded_messages
    }


@router.get("/coordinate")
async def get_by_coordinate(lat: float, lon: float, radius_km: float = 100):
    rows = fetch_nmea_rows(limit=3000)
    decoded_messages, _ = decode_rows(rows, lat=lat, lon=lon, radius_km=radius_km)
    return {"Number of Messages": len(decoded_messages), "Decoded": decoded_messages}


@router.get("/get-by-coordinate/duration")
async def get_by_coordinate_duration(lat: float, lon: float, from_time: str = None, to_time: str = None, radius_km: float = 100):
    try:
        from_dt = parse_dt(from_time) if from_time else None
        to_dt = parse_dt(to_time) if to_time else None
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid timestamp format")

    rows = fetch_nmea_rows(from_time=from_dt, to_time=to_dt)
    decoded_messages, _ = decode_rows(rows, lat=lat, lon=lon, radius_km=radius_km)
    return {"Number of Messages": len(decoded_messages), "Decoded": decoded_messages}
