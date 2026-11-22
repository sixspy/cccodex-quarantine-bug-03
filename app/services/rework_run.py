"""回炉开跑：切流账册一次落定。

口径（唯一读轨，不允许与双写两轨并存）：

- **开跑芯片** `chip`：执行中 / 待开跑 / 已清空，由账册与余数推出，不靠手写。
- **回炉中余数** `remainder`：仍未落定（status='回炉中'）的隔离行数。
- **开跑落点** `landing`：账册逐行，记条目键、指纹、落定时刻。

开跑动作在一个事务里锁定当刻条目键与指纹并写入账册；`INSERT ... ON CONFLICT
DO NOTHING` 保证同一回炉单只落定一次，双人争抢只有一人成。落定后读轨改为
`cutover_book` 且拒绝回刷；闸门要求读轨、指纹、芯片与余数四者对齐，否则挡入库。
"""
from __future__ import annotations

import hashlib

from app.db import get_db
from app.services.errors import ServiceError

SLUG = "rework_run"
TITLE = "回炉开跑"

CHIP_RUNNING = "执行中"
CHIP_WAITING = "待开跑"
CHIP_CLEARED = "已清空"

READ_OLD = "old_track"
READ_CUTOVER = "cutover_book"

K_READ = f"{SLUG}_active_read"
K_FP = f"{SLUG}_cutover_fp"
K_REJECT = f"{SLUG}_reject_refresh"


def _meta_get(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _meta_set(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (key, value),
    )


def fingerprint(row: dict) -> str:
    """条目键 + 回炉单身份 的稳定指纹；拧随机值即无法复算。"""
    raw = f"{row.get('item_key', '')}|{row.get('job_code', '')}|{row.get('row_id', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def remainder(db_path: str | None = None) -> int:
    with get_db(db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM quarantine_rows WHERE status='回炉中'"
        ).fetchone()["c"]


def _state(db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        active_read = _meta_get(conn, K_READ, READ_OLD)
        cutover_fp = _meta_get(conn, K_FP, "")
        reject_refresh = _meta_get(conn, K_REJECT, "0") == "1"
        remaining = conn.execute(
            "SELECT COUNT(*) AS c FROM quarantine_rows WHERE status='回炉中'"
        ).fetchone()["c"]
        rows = conn.execute(
            """
            SELECT l.row_id, l.job_id, l.job_code, l.item_key, l.fingerprint,
                   l.settled_at, i.item_key AS live_item_key, j.job_code AS live_job_code
            FROM rework_run_ledger l
            JOIN quarantine_rows r ON r.id=l.row_id
            JOIN capture_items i ON i.id=r.item_id
            LEFT JOIN rework_jobs j ON j.id=l.job_id
            ORDER BY l.id
            """
        ).fetchall()
        unpinned_open = conn.execute(
            """
            SELECT COUNT(*) AS c FROM quarantine_rows r
            WHERE r.status='回炉中'
              AND NOT EXISTS (SELECT 1 FROM rework_run_ledger l WHERE l.row_id=r.id)
            """
        ).fetchone()["c"]
    landing = [
        {k: r[k] for k in ("row_id", "job_id", "job_code", "item_key", "fingerprint", "settled_at")}
        for r in rows
    ]
    settled = bool(landing)
    # 指纹防回刷：账册落定的指纹必须能由当前库内条目键 / 回炉单复算一致；
    # 切流指纹必须是账册指纹的摘要，拧成随机值即对不齐。
    fp_ok = (
        settled
        and _is_fp(cutover_fp)
        and all(
            r["fingerprint"]
            == fingerprint(
                {
                    "row_id": r["row_id"],
                    "item_key": r["live_item_key"],
                    "job_code": r["live_job_code"] or r["job_code"],
                }
            )
            for r in rows
        )
        and cutover_fp == _ledger_digest([r["fingerprint"] for r in rows])
    )
    chip = _derive_chip(settled=settled, remaining=remaining)
    count_ok = settled and remaining == 0 and unpinned_open == 0
    gate_allows = (
        settled
        and active_read == READ_CUTOVER
        and reject_refresh
        and fp_ok
        and count_ok
        and chip == CHIP_CLEARED
    )
    return {
        "title": TITLE,
        "chip": chip,
        "active_read": active_read,
        "cutover_fp": cutover_fp,
        "reject_refresh": reject_refresh,
        "remainder": remaining,
        "pinned": len(landing),
        "unpinned_open": unpinned_open,
        "landing": landing,
        "settled": settled,
        "fingerprint_ok": fp_ok,
        "count_ok": count_ok,
        "gate_allows": gate_allows,
    }


def _ledger_digest(fingerprints: list[str]) -> str:
    return hashlib.sha256("\n".join(fingerprints).encode("utf-8")).hexdigest()


def _derive_chip(*, settled: bool, remaining: int) -> str:
    if not settled:
        return CHIP_WAITING
    if remaining > 0:
        return CHIP_RUNNING
    return CHIP_CLEARED


def _is_fp(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def dashboard(db_path: str | None = None) -> dict:
    state = _state(db_path)
    state["ok_chip"] = CHIP_CLEARED
    state["waiting_chip"] = CHIP_WAITING
    state["running_chip"] = CHIP_RUNNING
    state["label"] = "开跑钉定"
    state["read_tracks"] = {"old_track": READ_OLD, "cutover_book": READ_CUTOVER}
    return state


def start_run(job_id: int, db_path: str | None = None) -> dict:
    """开跑一次落定：按回炉单锁条目键与指纹写账册，切读轨，拒回刷。

    同一回炉单只能落定一次。并发争抢时 `BEGIN IMMEDIATE` 把写锁串行化，
    先到者写入账册，后到者撞唯一键得到 409。任何一步失败整体回滚并抛中文错误。
    """
    try:
        with get_db(db_path) as conn:
            # 先拿写锁再读状态：并发争抢被串行化，后来者一定看到先到者的落点。
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute(
                """
                SELECT j.id AS job_id, j.job_code, j.row_id,
                       r.status AS row_status, i.item_key
                FROM rework_jobs j
                JOIN quarantine_rows r ON r.id=j.row_id
                JOIN capture_items i ON i.id=r.item_id
                WHERE j.id=?
                """,
                (int(job_id or 0),),
            ).fetchone()
            if not job:
                raise ServiceError("回炉单不存在，无法开跑", 404)
            if job["row_status"] != "回炉中":
                raise ServiceError("该回炉单不在回炉中，无法开跑", 409)

            payload = {
                "row_id": job["row_id"],
                "item_key": job["item_key"],
                "job_code": job["job_code"],
            }
            fp = fingerprint(payload)
            cur = conn.execute(
                """
                INSERT INTO rework_run_ledger(row_id, job_id, job_code, item_key, fingerprint)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(row_id) DO NOTHING
                """,
                (job["row_id"], job["job_id"], job["job_code"], job["item_key"], fp),
            )
            if cur.rowcount != 1:
                raise ServiceError("该回炉单已开跑，禁止重复落定", 409)

            ledger_fps = [
                r["fingerprint"]
                for r in conn.execute(
                    "SELECT fingerprint FROM rework_run_ledger ORDER BY id"
                ).fetchall()
            ]
            _meta_set(conn, K_READ, READ_CUTOVER)
            _meta_set(conn, K_FP, _ledger_digest(ledger_fps))
            _meta_set(conn, K_REJECT, "1")
    except ServiceError:
        raise
    except Exception as exc:  # noqa: BLE001 - 对外统一中文
        raise ServiceError(f"{TITLE}落定失败：{exc}", 500) from exc
    return dashboard(db_path)


def record_landing(job_id: int, db_path: str | None = None) -> dict | None:
    """回炉单执行时按 job 自动开跑；已落定的回炉单原样返回其落点，不重复落定。

    无法落定（回炉单不存在 / 隔离行已不在回炉中）时向上抛中文错误，
    不得让执行路径在无落点的情况下继续吞件。
    """
    with get_db(db_path) as conn:
        job = conn.execute(
            "SELECT id, row_id FROM rework_jobs WHERE id=?", (int(job_id or 0),)
        ).fetchone()
        if not job:
            raise ServiceError("回炉单不存在，无法开跑", 404)
        already = conn.execute(
            "SELECT fingerprint FROM rework_run_ledger WHERE row_id=?",
            (job["row_id"],),
        ).fetchone()
    if already:
        return dict(already)
    return start_run(job["id"], db_path)


def require_gate(db_path: str | None = None) -> None:
    """清洗入库前的硬闸：读轨/指纹/芯片/余数四者对齐才放行。"""
    state = _state(db_path)
    if not state["settled"]:
        raise ServiceError(f"{TITLE}未落定，禁止入库", 409)
    if state["active_read"] != READ_CUTOVER or not state["reject_refresh"]:
        raise ServiceError(f"{TITLE}读轨未切到切流账册，禁止入库", 409)
    if not state["fingerprint_ok"]:
        raise ServiceError(f"{TITLE}指纹与账册对不齐，禁止入库", 409)
    if not state["count_ok"] or state["chip"] != CHIP_CLEARED:
        raise ServiceError(
            f"{TITLE}芯片与回炉中余数对不齐（余数{state['remainder']}），禁止入库", 409
        )


def gate_allows(db_path: str | None = None) -> bool:
    try:
        require_gate(db_path)
    except ServiceError:
        return False
    return True
