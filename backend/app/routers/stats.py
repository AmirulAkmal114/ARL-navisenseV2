"""Reference data endpoints (ports)."""

from fastapi import APIRouter, Depends, Query

from backend.app.db import count_ports, list_ports
from backend.app.security import require_auth

router = APIRouter(prefix="/api", tags=["ports"], dependencies=[Depends(require_auth)])


@router.get("/port/pagination")
async def port_pagination(page: int = Query(1, ge=1), page_size: int = Query(15, ge=1, le=100)):
    total_items = count_ports()

    offset = (page - 1) * page_size
    rows = list_ports(page_size, offset)

    data = []
    for row in rows:
        data.append({
            "country": row["country"],
            "locode": row["locode"],
            "port": row["port"],
            "latitude": float(row["latitude"]) if row["latitude"] is not None else None,
            "longitude": float(row["longitude"]) if row["longitude"] is not None else None,
        })

    return {"page": page, "page_size": page_size, "total_items": total_items, "data": data}
