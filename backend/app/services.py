"""Shared helpers used by the API routers.

Kept outside ``main.py`` and the routers so any module can use them without
creating circular imports. This module only depends on the database layer and
the AIS parser.
"""

from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt

from backend.app import ais_parser
from backend.app.db import get_nmea_rows

MAX_ROWS = 5000


def utc_now():
    return datetime.now(timezone.utc)


def to_utc_naive(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def parse_dt(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def haversine(lat1, lon1, lat2, lon2):
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def decode_item(item):
    line = item["nmea"]
    if not line or not line.startswith("!AIVDM"):
        return None

    parts = line.split(",")
    if len(parts) < 6:
        return None

    try:
        bitstring = ais_parser.nmea_payload_to_bitstring(parts[5])
        decoded = ais_parser.decode_ais(bitstring)
    except Exception:
        return None

    if "message_type" in decoded and "type" not in decoded:
        decoded["type"] = decoded["message_type"]
    if "country" not in decoded and decoded.get("mmsi") is not None:
        decoded["country"] = ais_parser.get_country_from_mmsi(decoded["mmsi"])
    decoded.setdefault("channel", "A")

    decoded["raw"] = line

    ts = item.get("timestamp")
    if ts:
        try:
            dt = to_utc_naive(ts)
            decoded["unix_timestamp"] = int(dt.timestamp())
            decoded["datetime"] = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    return decoded


def fetch_nmea_rows(from_time=None, to_time=None, limit=MAX_ROWS):
    return get_nmea_rows(from_time=from_time, to_time=to_time, limit=limit)


def decode_rows(rows, mmsi=None, lat=None, lon=None, radius_km=None):
    decoded_messages = []
    skipped_messages = 0

    for item in rows:
        decoded = decode_item(item)
        if decoded is None:
            skipped_messages += 1
            continue

        if mmsi is not None and str(decoded.get("mmsi", "")) != str(mmsi):
            skipped_messages += 1
            continue

        if lat is not None and lon is not None and radius_km is not None:
            dlat = decoded.get("lat")
            dlon = decoded.get("lon")
            if dlat is None or dlon is None:
                skipped_messages += 1
                continue
            if haversine(float(lat), float(lon), float(dlat), float(dlon)) > radius_km:
                skipped_messages += 1
                continue

        decoded_messages.append(decoded)

    return decoded_messages, skipped_messages
