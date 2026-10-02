"""The vendor-side change the drift scenario simulates (`make drift`).

The head-end vendor ships a release that renames ``kwh_delivered`` to ``energy_kwh_del`` and sends it
as a string. The same batch restates reads the head-end had estimated during the scripted S-04 outage
on 14 Aug (estimates replaced by actual zero reads) — that restatement is what the recall job finds.
"""

from __future__ import annotations

from tessera.seed import domain as D
from tessera.warehouse.base import Warehouse

FEED = "raw.head_end_vendor_feed"
OLD, NEW = "kwh_delivered", "energy_kwh_del"


def apply_drift(wh: Warehouse) -> dict[str, object]:
    cols = {c.name for c in wh.table_schema(FEED)}
    if NEW in cols:
        return {"applied": False, "reason": "already drifted"}
    so = D.SCRIPTED_OUTAGE
    wh.execute(
        f"""UPDATE {FEED} SET {OLD} = 0.0, kwh_received = 0.0, quality_flag = 'ACT'
            WHERE quality_flag = 'EST' AND read_ts >= TIMESTAMP '{so["estimated_from"]}'
              AND read_ts < TIMESTAMP '{so["end_ts"]}'
              AND meter_id IN (SELECT meter_id FROM raw.meters WHERE feeder_id = '{so["feeder_id"]}')"""
    )
    restated = int(
        wh.scalar(
            f"SELECT count(*) FROM {FEED} WHERE read_ts >= TIMESTAMP '{so['estimated_from']}' "
            f"AND read_ts < TIMESTAMP '{so['end_ts']}' AND meter_id IN "
            f"(SELECT meter_id FROM raw.meters WHERE feeder_id = '{so['feeder_id']}')"
        )
        or 0
    )
    wh.execute(f"ALTER TABLE {FEED} RENAME COLUMN {OLD} TO {NEW}")
    wh.execute(f"ALTER TABLE {FEED} ALTER {NEW} TYPE VARCHAR")
    return {"applied": True, "renamed": [OLD, NEW], "new_type": "VARCHAR", "restated_reads": restated}
