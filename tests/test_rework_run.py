"""回炉开跑败例：先写期望，再改实现。

选定工程形态：**切流账册**（唯一读轨），双写两轨与半修脚手架整体移除。
验收口径：开跑芯片 / 回炉中余数 / 开跑落点 三分展示；一次落定、指纹防拧、
双人争抢串行、芯片与计数不齐挡入库、清洗入库拒吞回炉件。
"""
from __future__ import annotations

import re
import threading

import pytest

from app.db import _meta_set, get_db
from app.services import captures, quarantine, reconcile, review, rework
from app.services import rework_run
from app.services.errors import ServiceError

_FP_RE = re.compile(r"^[0-9a-f]{64}$")


def _make_rework(db_path: str) -> dict:
    """把种子里唯一的待决复核单驳回回炉，返回排队中的回炉单。"""
    tickets = review.list_tickets(db_path)
    ticket = next(t for t in tickets if t["decision"] == "待决")
    review.decide(ticket["id"], "驳回回炉", "tester", "回炉", db_path)
    jobs = rework.list_jobs(db_path)
    return next(j for j in jobs if j["row_id"] == ticket["row_id"] and j["status"] == "排队")


def _make_second_rework(db_path: str) -> dict:
    """再造一张回炉单，用于计数不齐 / 争抢场景。"""
    batches = captures.list_batches(db_path)
    batch = next(b for b in batches if b["status"] != "已关闭")
    detail = captures.get_batch(batch["id"], db_path)
    bad = next(i for i in detail["items"] if i["quality"] != "合格" and i["status"] == "待检")
    open_bin = next(b for b in quarantine.list_bins(db_path) if b["status"] == "开放")
    row = quarantine.enter_item(bad["id"], open_bin["id"], "第二张回炉单", db_path)
    ticket = next(
        t for t in review.list_tickets(db_path)
        if t["row_id"] == row["id"] and t["decision"] == "待决"
    )
    review.decide(ticket["id"], "驳回回炉", "tester", "回炉", db_path)
    jobs = rework.list_jobs(db_path)
    return next(j for j in jobs if j["row_id"] == row["id"] and j["status"] == "排队")


def test_before_start_chip_tells_truth(db_path):
    """未开跑时芯片不得假冒执行中；开跑落点为空。"""
    _make_rework(db_path)
    dash = rework_run.dashboard(db_path)
    assert dash["chip"] == rework_run.CHIP_WAITING
    assert dash["chip"] != rework_run.CHIP_RUNNING
    assert dash["remainder"] == 1
    assert dash["settled"] is False
    assert dash["landing"] == []
    assert dash["active_read"] == rework_run.READ_OLD
    assert dash["gate_allows"] is False


def test_start_settles_cutover_once(db_path):
    """开跑一次落定：读轨切到 cutover_book、指纹 64 位、拒回刷、落点非空。"""
    job = _make_rework(db_path)
    out = rework.run_job(job["id"], succeed=True, db_path=db_path)
    assert out["status"] == "成功"

    dash = rework_run.dashboard(db_path)
    assert dash["settled"] is True
    assert dash["active_read"] == rework_run.READ_CUTOVER
    assert dash["active_read"] != rework_run.READ_OLD
    assert dash["reject_refresh"] is True
    assert _FP_RE.match(dash["cutover_fp"]), dash["cutover_fp"]
    assert dash["fingerprint_ok"] is True

    # 开跑落点行必须有值，不能空白
    assert len(dash["landing"]) == 1
    landing = dash["landing"][0]
    assert landing["job_code"]
    assert landing["item_key"]
    assert _FP_RE.match(landing["fingerprint"])
    assert landing["settled_at"]

    # 回炉中余数清零，芯片与计数对齐，闸门打开
    assert dash["remainder"] == 0
    assert dash["count_ok"] is True
    assert dash["chip"] == rework_run.CHIP_CLEARED
    assert dash["gate_allows"] is True
    assert rework_run.gate_allows(db_path) is True


def test_same_job_double_start_rejected(db_path):
    """同一回炉单只许落定一次；直接再开跑被拒。"""
    job = _make_rework(db_path)
    rework.run_job(job["id"], succeed=False, db_path=db_path)
    with pytest.raises(ServiceError) as exc:
        rework_run.start_run(job["id"], db_path)
    assert "已开跑" in exc.value.message or "重复" in exc.value.message
    # 失败重试是二次执行，不是二次开跑：复用原落点，不新增行。
    retried = rework.run_job(job["id"], succeed=True, db_path=db_path)
    assert retried["status"] == "成功"
    assert len(rework_run.dashboard(db_path)["landing"]) == 1


def test_concurrent_start_only_one_wins(db_path):
    """两人同抢同一回炉单，只能一人成。"""
    job = _make_rework(db_path)
    barrier = threading.Barrier(2)
    results: list[tuple[str, str]] = []

    def worker():
        barrier.wait()
        try:
            rework_run.start_run(job["id"], db_path)
            results.append(("ok", ""))
        except ServiceError as exc:
            results.append(("err", exc.message))
        except Exception as exc:  # pragma: no cover - 便于定位
            results.append(("boom", str(exc)))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(kind for kind, _ in results) == ["err", "ok"], results
    assert all(msg for kind, msg in results if kind == "err")
    with get_db(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM rework_run_ledger WHERE row_id=?", (job["row_id"],)
        ).fetchone()["c"]
    assert count == 1


def test_tampered_fingerprint_blocks_ingest(db_path):
    """指纹被拧成随机值后，闸门必须挡住。"""
    job = _make_rework(db_path)
    rework.run_job(job["id"], succeed=True, db_path=db_path)
    with get_db(db_path) as conn:
        _meta_set(conn, rework_run.K_FP, "f" * 64)
    dash = rework_run.dashboard(db_path)
    assert dash["fingerprint_ok"] is False
    assert dash["gate_allows"] is False
    with pytest.raises(ServiceError) as exc:
        rework_run.require_gate(db_path)
    assert "指纹" in exc.value.message or "对不齐" in exc.value.message


def test_partial_start_blocks_until_all_cleared(db_path):
    """只钉一张、另一张仍裸露时芯片与余数对不齐，闸门必须挡。"""
    first = _make_rework(db_path)
    second = _make_second_rework(db_path)
    rework_run.start_run(first["id"], db_path)

    dash = rework_run.dashboard(db_path)
    assert dash["remainder"] == 2  # 两张都还在回炉中
    assert dash["pinned"] == 1
    assert dash["chip"] == rework_run.CHIP_RUNNING
    assert dash["count_ok"] is False
    assert dash["gate_allows"] is False

    rework_run.start_run(second["id"], db_path)
    dash = rework_run.dashboard(db_path)
    assert dash["pinned"] == 2
    assert dash["count_ok"] is False  # 虽已全钉，但仍未清空
    assert dash["gate_allows"] is False

    # 两张都成功出回炉，余数清零后闸门才开
    for job in (first, second):
        rework.run_job(job["id"], succeed=True, db_path=db_path)
    dash = rework_run.dashboard(db_path)
    assert dash["remainder"] == 0
    assert dash["chip"] == rework_run.CHIP_CLEARED
    assert dash["count_ok"] is True
    assert dash["gate_allows"] is True


def test_ingest_refuses_item_still_in_rework(db_path):
    """清洗入库不得吞掉仍在回炉中的条目。"""
    job = _make_rework(db_path)
    with get_db(db_path) as conn:
        item_id = conn.execute(
            "SELECT item_id FROM quarantine_rows WHERE id=?", (job["row_id"],)
        ).fetchone()["item_id"]
    with pytest.raises(ServiceError) as exc:
        captures.ingest_item(item_id, db_path)
    assert "回炉中" in exc.value.message
    with get_db(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM ingest_ledger WHERE item_id=?", (item_id,)
        ).fetchone()["c"] == 0


def test_ingest_blocks_before_settlement_then_allows(db_path):
    """未开跑时闸门挡合格件入库；开跑清空后才放行。"""
    batch = captures.create_batch("站内目录甲", "入库闸门批", db_path)
    item = captures.add_item(batch["id"], "sku-gate-1", "{}", "合格", db_path)
    with pytest.raises(ServiceError) as exc:
        captures.ingest_item(item["id"], db_path)
    assert "未落定" in exc.value.message

    job = _make_rework(db_path)
    rework.run_job(job["id"], succeed=True, db_path=db_path)
    landed = captures.ingest_item(item["id"], db_path)
    assert landed["item_id"] == item["id"]


def test_run_job_refuses_row_no_longer_in_rework(db_path):
    """隔离行已离开回炉中时，执行不得在无落点的情况下吞件。"""
    job = _make_rework(db_path)
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE quarantine_rows SET status='已废弃' WHERE id=?", (job["row_id"],)
        )
    with pytest.raises(ServiceError) as exc:
        rework.run_job(job["id"], succeed=True, db_path=db_path)
    assert "回炉中" in exc.value.message
    assert rework_run.dashboard(db_path)["landing"] == []


def test_reconcile_remainder_matches_dashboard(db_path):
    """对账回炉中余数与开跑芯片同源。"""
    job = _make_rework(db_path)
    rework.run_job(job["id"], succeed=False, db_path=db_path)
    dash = rework_run.dashboard(db_path)
    report = reconcile.run_reconcile(db_path)
    assert report["reworking"] == dash["remainder"] == 1
    assert isinstance(reconcile.dashboard(db_path)["rework_remainder"], int)


def test_scaffolding_removed(db_path):
    """两套读轨勿并存，脚手架须清。"""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    assert not (root / "app/services/rework_run_dualwrite.py").exists()
    assert not (root / "app/services/rework_run_cutover.py").exists()
    assert not hasattr(rework_run, "seed_skew")
    assert not hasattr(rework_run, "paint_ok")
    with get_db(db_path) as conn:
        keys = {
            r["key"]
            for r in conn.execute("SELECT key FROM meta WHERE key LIKE 'rework_run_%'").fetchall()
        }
    assert f"{rework_run.SLUG}_gate_bypass" not in keys
    assert not any(k.endswith("_decoy") for k in keys)
