from __future__ import annotations

import hashlib

from app.db import get_db
from app.services import audit as audit_svc
from app.services.errors import ServiceError


def list_batches(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, batch_code, source_name, status, item_count, bad_count, note, created_at
            FROM capture_batches ORDER BY id DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_batch(batch_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM capture_batches WHERE id=?", (batch_id,)
        ).fetchone()
        if not row:
            raise ServiceError("批次不存在", 404)
        items = conn.execute(
            """
            SELECT id, item_key, quality, status, payload, created_at
            FROM capture_items WHERE batch_id=? ORDER BY id
            """,
            (batch_id,),
        ).fetchall()
        data = dict(row)
        data["items"] = [dict(i) for i in items]
        return data


def create_batch(source_name: str, note: str = "", db_path: str | None = None) -> dict:
    source_name = (source_name or "").strip()
    if not source_name:
        raise ServiceError("来源名不能空")
    with get_db(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM capture_batches").fetchone()["c"] + 1
        code = f"CAP-NEW-{n:04d}"
        cur = conn.execute(
            """
            INSERT INTO capture_batches(batch_code, source_name, status, note)
            VALUES (?, ?, '采集中', ?)
            """,
            (code, source_name, note or ""),
        )
        batch_id = cur.lastrowid
    audit_svc.write_event("新建批次", "capture_batch", batch_id, code, db_path=db_path)
    return get_batch(batch_id, db_path)


def add_item(
    batch_id: int,
    item_key: str,
    payload: str = "",
    quality: str = "合格",
    db_path: str | None = None,
) -> dict:
    item_key = (item_key or "").strip()
    if not item_key:
        raise ServiceError("条目键不能空")
    if quality not in ("合格", "缺字段", "编码空", "重复键"):
        raise ServiceError("质量标记无效")
    with get_db(db_path) as conn:
        batch = conn.execute(
            "SELECT * FROM capture_batches WHERE id=?", (batch_id,)
        ).fetchone()
        if not batch:
            raise ServiceError("批次不存在", 404)
        if batch["status"] == "已关闭":
            raise ServiceError("已关闭批次不能再加条目")
        exists = conn.execute(
            "SELECT id FROM capture_items WHERE batch_id=? AND item_key=?",
            (batch_id, item_key),
        ).fetchone()
        if exists:
            raise ServiceError("同批键已存在")
        cur = conn.execute(
            """
            INSERT INTO capture_items(batch_id, item_key, payload, quality, status)
            VALUES (?, ?, ?, ?, '待检')
            """,
            (batch_id, item_key, payload or "", quality),
        )
        _refresh_batch_counts(conn, batch_id)
        item_id = cur.lastrowid
        row = conn.execute("SELECT * FROM capture_items WHERE id=?", (item_id,)).fetchone()
    audit_svc.write_event("追加条目", "capture_item", item_id, item_key, db_path=db_path)
    return dict(row)


def _ingest_fingerprint(item: dict) -> str:
    raw = f"{item['id']}|{item['item_key']}|{item['batch_id']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ingest_item(item_id: int, db_path: str | None = None) -> dict:
    """清洗入库：回炉件在清空前一律拒绝吞入，开跑闸门不齐同样拒收。"""
    from app.services import rework_run as rework_run_svc

    with get_db(db_path) as conn:
        item = conn.execute(
            "SELECT * FROM capture_items WHERE id=?", (item_id,)
        ).fetchone()
        if not item:
            raise ServiceError("条目不存在", 404)
        rework_row = conn.execute(
            "SELECT id FROM quarantine_rows WHERE item_id=? AND status='回炉中'",
            (item_id,),
        ).fetchone()
    if rework_row or item["status"] == "回炉中":
        raise ServiceError("条目仍在回炉中，清洗入库拒吞", 409)

    # 闸门不齐不得先写台账：先判定，再落账。
    rework_run_svc.require_gate(db_path)

    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO ingest_ledger(item_id, item_key, fingerprint)
            VALUES (?, ?, ?)
            ON CONFLICT(item_id) DO NOTHING
            """,
            (item_id, item["item_key"], _ingest_fingerprint(item)),
        )
        out = conn.execute(
            "SELECT * FROM ingest_ledger WHERE item_id=?", (item_id,)
        ).fetchone()
    audit_svc.write_event("清洗入库", "capture_item", item_id, item["item_key"], db_path=db_path)
    return dict(out)


def close_batch(batch_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        batch = conn.execute(
            "SELECT * FROM capture_batches WHERE id=?", (batch_id,)
        ).fetchone()
        if not batch:
            raise ServiceError("批次不存在", 404)
        conn.execute(
            "UPDATE capture_batches SET status='已关闭' WHERE id=?", (batch_id,)
        )
        conn.execute(
            "UPDATE capture_items SET status='已关闭' WHERE batch_id=? AND status='待检' AND quality='合格'",
            (batch_id,),
        )
    audit_svc.write_event("关闭批次", "capture_batch", batch_id, "", db_path=db_path)
    return get_batch(batch_id, db_path)


def _refresh_batch_counts(conn, batch_id: int) -> None:
    cnt = conn.execute(
        "SELECT COUNT(*) AS c FROM capture_items WHERE batch_id=?", (batch_id,)
    ).fetchone()["c"]
    bad = conn.execute(
        "SELECT COUNT(*) AS c FROM capture_items WHERE batch_id=? AND quality!='合格'",
        (batch_id,),
    ).fetchone()["c"]
    conn.execute(
        "UPDATE capture_batches SET item_count=?, bad_count=? WHERE id=?",
        (cnt, bad, batch_id),
    )
