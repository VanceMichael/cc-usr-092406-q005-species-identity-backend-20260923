"""品种身份链路端到端测试。

覆盖：
* 简称/旧称/带空格写法的归一化与自动采信；
* 真正不同品系只提示候选、不自动合并，支持事后拆分；
* 旧文本扫描 → 未决队列 → 合并/驳回/撤回，全部留审计依据；
* 标准名更正只改后续展示，已签署周期分析按原口径重现；
* 塘口轮养、批次与投苗品种版本冻结；
* 并发别名裁定只有一个版本生效（部分唯一索引）；
* 列表、周期分析、追溯三端身份一致；
* 旧库轻量迁移补列。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# 在导入应用（模块级建表）之前，把默认数据库指向临时文件，避免污染仓库目录
_TMP_ENV = tempfile.TemporaryDirectory()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_TMP_ENV.name) / 'module_default.db'}")

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from fastapi.testclient import TestClient


class SpeciesIdentityTestBase(unittest.TestCase):
    def setUp(self):
        # 每个用例独立 SQLite 文件，避免相互污染
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False},
        )
        from app import models  # noqa: F401  确保所有表注册到 Base.metadata
        from app.database import Base, get_db
        from app.migrations import run_lightweight_migrations
        Base.metadata.create_all(bind=self.engine)
        run_lightweight_migrations(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        from app.main import app
        self.app = app

        def _override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        from app.database import get_db
        self.app.dependency_overrides.pop(get_db, None)
        self.engine.dispose()
        self._tmp.cleanup()

    # -- 辅助 ----------------------------------------------------------

    def create_species(self, name="罗氏沼虾", code=None, **extra):
        payload = {"name": name}
        if code:
            payload["code"] = code
        payload.update(extra)
        r = self.client.post("/api/species/", json=payload)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def create_pond(self, name, species):
        r = self.client.post("/api/ponds/", json={
            "name": name, "area": 10.0, "water_depth": 1.5, "species": species,
        })
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def create_batch(self, number, pond_id, species, date="2026-03-01"):
        r = self.client.post("/api/batches/", json={
            "batch_number": number, "pond_id": pond_id,
            "species": species, "stocking_date": date,
        })
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def create_stocking(self, batch_id, species, quantity=1000):
        r = self.client.post("/api/stocking-records/", json={
            "batch_id": batch_id, "species": species, "quantity": quantity,
        })
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def pending_mappings(self):
        r = self.client.get("/api/species/mappings/", params={"status": "pending"})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


class NormalizationTests(SpeciesIdentityTestBase):
    def test_whitespace_punctuation_variants_auto_resolve_to_same_identity(self):
        sp = self.create_species(code="MACRO")

        for variant in [" 罗氏 沼虾 ", "罗氏沼虾", "罗氏·沼虾", "罗氏,沼虾"]:
            pond = self.create_pond(f"P-{variant!r}", variant)
            self.assertEqual(pond["species_code"], "MACRO", variant)
            self.assertEqual(pond["species_identity"]["current_name"], "罗氏沼虾", variant)

        # 自动采信留下依据，但不产生未决映射
        self.assertEqual(self.pending_mappings(), [])
        mappings = self.client.get("/api/species/mappings/",
                                   params={"status": "resolved_auto"}).json()
        self.assertTrue(any(m["raw_text"] == "罗氏 沼虾" for m in mappings))

    def test_suggest_only_hints_does_not_create_identity(self):
        self.create_species(code="MACRO")
        r = self.client.get("/api/species/suggest/", params={"text": "罗氏虾"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIsNone(body["match"])
        self.assertTrue(body["candidates"], "缺字写法应给出模糊候选提示")
        self.assertEqual(body["candidates"][0]["code"], "MACRO")
        # 提示不落库
        self.assertEqual(self.pending_mappings(), [])


class DistinctStrainTests(SpeciesIdentityTestBase):
    def test_distinct_strain_is_queued_not_auto_merged(self):
        self.create_species(code="MACRO")
        self.create_pond("P0", "罗氏沼虾")
        # “南太湖2号”是罗氏沼虾的选育品系，属于真正不同的品系
        batch = self.create_batch("B-STRAIN", 1, "南太湖2号罗氏沼虾")
        self.assertIsNone(batch["species_code"])
        self.assertTrue(batch["species_identity"]["pending"])
        self.assertIsNotNone(batch["species_identity"]["mapping_id"])

        pending = self.pending_mappings()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["raw_text"], "南太湖2号罗氏沼虾")
        # 系统可以提示它与罗氏沼虾相似，但置信度只能是 low，绝不自动合并
        self.assertEqual(pending[0]["confidence"], "low")

    def test_split_moves_mismerged_alias_to_new_strain(self):
        sp = self.create_species(code="MACRO")
        pond_a = self.create_pond("PA", "罗氏沼虾")
        pond_b = self.create_pond("PB", "南太湖2号")

        # 人工误把“南太湖2号”合并进罗氏沼虾
        scan = self.client.post("/api/species/scan/").json()
        mapping_id = next(q["mapping_id"] for q in scan["queued"] if q["raw_text"] == "南太湖2号")
        r = self.client.post(f"/api/species/mappings/{mapping_id}/approve",
                             json={"target_species_id": sp["id"], "reason": "误并", "actor": "甲"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.get("/api/ponds/2/").json()["species_code"], "MACRO")

        # 发现错误后拆分
        r = self.client.post(f"/api/species/{sp['id']}/split", json={
            "new_species_name": "南太湖2号",
            "move_aliases": ["南太湖2号"],
            "reason": "选育品系应独立",
            "actor": "乙",
        })
        self.assertEqual(r.status_code, 200, r.text)
        new_code = r.json()["code"]
        self.assertNotEqual(new_code, "MACRO")

        # 按原文重新归属：B 改挂新品种，A 保持原品种
        self.assertEqual(self.client.get("/api/ponds/2/").json()["species_code"], new_code)
        self.assertEqual(self.client.get("/api/ponds/1/").json()["species_code"], "MACRO")

        # 双方都有 split/created 依据
        events_old = self.client.get(f"/api/species/{sp['id']}/events/").json()
        self.assertIn("split", [e["event_type"] for e in events_old])
        new_id = self.client.get("/api/species/").json()[-1]["id"]
        events_new = self.client.get(f"/api/species/{new_id}/events/").json()
        self.assertTrue(any(e["event_type"] == "created" for e in events_new))

        # 跨场区比较时两个品系各占一行
        overview = self.client.get("/api/species/overview/").json()
        codes = {o["code"] for o in overview}
        self.assertIn("MACRO", codes)
        self.assertIn(new_code, codes)


class LegacyMappingTests(SpeciesIdentityTestBase):
    def _seed_legacy_rows(self):
        """直接写库模拟没有品种身份列时代的旧数据。"""
        db = self.Session()
        try:
            db.execute(text("INSERT INTO ponds (name, area, water_depth, species, species_code, status, created_at, updated_at) "
                            "VALUES ('老塘', 8.0, 1.2, '马来西亚大虾', NULL, 'active', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"))
            db.execute(text("INSERT INTO batches (batch_number, pond_id, species, species_code, "
                            "stocking_date, status, created_at, updated_at) "
                            "VALUES ('老批', 1, '淡水长臂大虾', NULL, '2026-02-01', 'active', '2026-02-01 00:00:00', '2026-02-01 00:00:00')"))
            db.execute(text("INSERT INTO stocking_records (batch_id, species, species_code, quantity, created_at) "
                            "VALUES (1, '淡水長臂大蝦', NULL, 500, '2026-02-02 00:00:00')"))
            db.commit()
        finally:
            db.close()

    def test_scan_queues_then_manual_merge_backfills_and_unifies_views(self):
        sp = self.create_species(code="MACRO")
        self._seed_legacy_rows()

        r = self.client.post("/api/species/scan/")
        self.assertEqual(r.status_code, 200, r.text)
        result = r.json()
        queued = {q["raw_text"]: q for q in result["queued"]}
        self.assertIn("马来西亚大虾", queued)
        self.assertIn("淡水长臂大虾", queued)

        # 合并“淡水长臂大虾”
        mid = queued["淡水长臂大虾"]["mapping_id"]
        r = self.client.post(f"/api/species/mappings/{mid}/approve", json={
            "target_species_id": sp["id"], "reason": "罗氏沼虾旧称", "actor": "张三",
        })
        self.assertEqual(r.status_code, 200, r.text)
        approved = r.json()
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["target_code"], "MACRO")

        # 批次被回填
        batch = self.client.get("/api/batches/1/").json()
        self.assertEqual(batch["species_code"], "MACRO")
        self.assertEqual(batch["species_identity"]["current_name"], "罗氏沼虾")
        self.assertIn("淡水长臂大虾", batch["species_identity"]["aliases"])

        # 合并审计依据
        events = self.client.get(f"/api/species/{sp['id']}/events/").json()
        approve_events = [e for e in events if e["event_type"] == "mapping_approved"]
        self.assertEqual(len(approve_events), 1)
        self.assertEqual(approve_events[0]["reason"], "罗氏沼虾旧称；回填 1 条")

        # 追溯接口与批次列表身份一致，且保留原文
        trace = self.client.get("/api/analysis/traceability/1/").json()
        self.assertEqual(trace["batch"]["species_code"], "MACRO")
        self.assertEqual(trace["batch"]["species"], "罗氏沼虾")
        self.assertEqual(trace["batch"]["recorded_species_name"], "淡水长臂大虾")

        # 驳回“马来西亚大虾”：保持未裁定，不回填
        other = self.pending_mappings()
        other_id = next(m["id"] for m in other if m["raw_text"] == "马来西亚大虾")
        r = self.client.post(f"/api/species/mappings/{other_id}/reject",
                             json={"reason": "并非同物", "actor": "李四"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "rejected")
        pond = self.client.get("/api/ponds/1/").json()
        self.assertIsNone(pond["species_code"])
        self.assertNotIn("MACRO", {o["code"] for o in
                                   self.client.get("/api/species/overview/").json()
                                   if o["current_name"] == "马来西亚大虾"})

    def test_withdraw_approved_mapping_keeps_historical_snapshots(self):
        sp = self.create_species(code="MACRO")
        pond = self.create_pond("PW", "旧称写法X")
        scan = self.client.post("/api/species/scan/").json()
        mid = scan["queued"][0]["mapping_id"]
        self.client.post(f"/api/species/mappings/{mid}/approve",
                         json={"target_species_id": sp["id"], "reason": "先合并"})
        self.assertEqual(self.client.get("/api/ponds/1/").json()["species_code"], "MACRO")

        r = self.client.post(f"/api/species/mappings/{mid}/withdraw",
                             json={"reason": "证据不足，撤回", "actor": "复核员"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "withdrawn")

        # 别名已下线
        sp_after = self.client.get(f"/api/species/{sp['id']}/").json()
        self.assertIn("旧称写法X", sp_after["historical_names"])
        self.assertNotIn("旧称写法X", sp_after["aliases"])

        # 已采用的历史快照保留（投苗/批次当时采用的版本不可被追溯抹掉）
        self.assertEqual(self.client.get("/api/ponds/1/").json()["species_code"], "MACRO")
        events = self.client.get(f"/api/species/{sp['id']}/events/").json()
        self.assertIn("mapping_withdrawn", [e["event_type"] for e in events])

    def test_rejected_text_re_enters_queue_when_seen_again(self):
        self.create_species(code="MACRO")
        self.create_pond("PR", "青壳罗氏沼虾")
        mid = self.pending_mappings()[0]["id"]
        self.client.post(f"/api/species/mappings/{mid}/reject", json={"reason": "不同品系"})
        self.assertEqual(self.pending_mappings(), [])

        # 旧文本再次出现（新录入）→ 重新入队，允许翻案
        self.create_pond("PR2", "青壳罗氏沼虾")
        pending = self.pending_mappings()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["raw_text"], "青壳罗氏沼虾")

    def test_scan_is_idempotent(self):
        self.create_species(code="MACRO")
        self.create_pond("PI", "罗氏沼虾")
        first = self.client.post("/api/species/scan/").json()
        second = self.client.post("/api/species/scan/").json()
        self.assertEqual(second["scanned_keys"], first["scanned_keys"])
        self.assertEqual(second["auto_resolved"], [], "已回填且有依据时不应重复造映射")


class RenameAndSignatureTests(SpeciesIdentityTestBase):
    def test_rename_changes_future_display_but_signed_analysis_replays(self):
        sp = self.create_species(code="MACRO")
        pond = self.create_pond("P1", "罗氏沼虾")
        batch = self.create_batch("B1", pond["id"], "淡水长臂大虾")
        # 先把旧称合并，使批次归入 MACRO
        mid = self.pending_mappings()[0]["id"]
        self.client.post(f"/api/species/mappings/{mid}/approve",
                         json={"target_species_id": sp["id"], "reason": "旧称"})
        self.create_stocking(batch["id"], "罗氏沼虾", 2000)

        # 按旧口径签署
        signed = self.client.post("/api/analysis/cycle/1/sign", json={"signer": "周分析"}).json()
        self.assertEqual(signed["species"], "罗氏沼虾")

        # 名称更正
        r = self.client.post(f"/api/species/{sp['id']}/rename", json={
            "new_name": "罗氏沼虾（长臂虾科）", "reason": "与名录对齐", "actor": "管理员",
        })
        self.assertEqual(r.status_code, 200, r.text)

        # 后续展示用新标准名，历史名明确返回
        cycle = self.client.get("/api/analysis/cycle/1/").json()
        self.assertEqual(cycle["species"], "罗氏沼虾（长臂虾科）")
        self.assertIn("罗氏沼虾", cycle["species_identity"]["historical_names"])
        self.assertEqual(cycle["species_code"], "MACRO")

        trace = self.client.get("/api/analysis/traceability/1/").json()
        self.assertEqual(trace["batch"]["species"], "罗氏沼虾（长臂虾科）")
        self.assertEqual(trace["batch"]["recorded_species_name"], "淡水长臂大虾")

        # 已签署分析按原口径重现
        replay = self.client.get("/api/analysis/cycle/1/signed").json()
        self.assertEqual(replay["species"], "罗氏沼虾")
        self.assertEqual(replay["signed_species_name"], "罗氏沼虾")
        self.assertEqual(replay["signer"], "周分析")

        # 重复签署返回既有快照
        again = self.client.post("/api/analysis/cycle/1/sign", json={"signer": "别人"}).json()
        self.assertEqual(again["signer"], "周分析")

    def test_signed_endpoint_404_without_signature(self):
        self.create_species(code="MACRO")
        pond = self.create_pond("P1", "罗氏沼虾")
        self.create_batch("B1", pond["id"], "罗氏沼虾")
        r = self.client.get("/api/analysis/cycle/1/signed")
        self.assertEqual(r.status_code, 404)


class FreezeAndRotationTests(SpeciesIdentityTestBase):
    def test_batch_and_stocking_keep_their_species_version(self):
        sp = self.create_species(code="MACRO")
        pond = self.create_pond("P1", "罗氏沼虾")
        batch = self.create_batch("B1", pond["id"], "罗氏沼虾")
        stocking = self.create_stocking(batch["id"], "罗氏沼虾")

        r = self.client.put(f"/api/batches/{batch['id']}/", json={"species": "南美白对虾"})
        self.assertEqual(r.status_code, 409)
        r = self.client.put(f"/api/stocking-records/{stocking['id']}/",
                            json={"species": "南美白对虾"})
        self.assertEqual(r.status_code, 409)

        # 同文本不影响正常更新
        r = self.client.put(f"/api/batches/{batch['id']}/",
                            json={"species": "罗氏沼虾", "status": "closed"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_pond_allows_rotation_between_species(self):
        a = self.create_species("罗氏沼虾", code="MACRO")
        b = self.create_species("南美白对虾", code="VANNAMEI")
        pond = self.create_pond("P-ROT", "罗氏沼虾")
        self.assertEqual(pond["species_code"], "MACRO")

        r = self.client.put(f"/api/ponds/{pond['id']}/", json={"species": "南美白对虾"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["species_code"], "VANNAMEI")

        # 两个轮养批次各自保留创建时版本
        b1 = self.create_batch("ROT-1", pond["id"], "罗氏沼虾")
        b2 = self.create_batch("ROT-2", pond["id"], "南美白对虾")
        self.assertEqual(b1["species_code"], "MACRO")
        self.assertEqual(b2["species_code"], "VANNAMEI")

    def test_mixed_stocking_keeps_own_species_version_in_trace_and_overview(self):
        self.create_species("罗氏沼虾", code="MACRO")
        self.create_species("青虾", code="PALAE")
        pond = self.create_pond("P-MIX", "罗氏沼虾")
        batch = self.create_batch("MIX-1", pond["id"], "罗氏沼虾")
        main = self.create_stocking(batch["id"], "罗氏沼虾", 2000)
        extra = self.create_stocking(batch["id"], "青虾", 300)

        # 追溯接口：批次引用批次身份，投苗引用各自投苗身份，不再互相串名
        trace = self.client.get(f"/api/analysis/traceability/{batch['id']}/").json()
        self.assertEqual(trace["batch"]["species_code"], "MACRO")
        codes = {r["species_code"] for r in trace["stocking_records"]}
        self.assertEqual(codes, {"MACRO", "PALAE"})
        recorded = {r["recorded_species_name"] for r in trace["stocking_records"]}
        self.assertEqual(recorded, {"罗氏沼虾", "青虾"})

        # 汇总按投苗自身冻结身份归属，不被批次身份吞掉
        overview = {o["code"]: o for o in self.client.get("/api/species/overview/").json()}
        self.assertEqual(overview["MACRO"]["stocking_quantity"], 2000)
        self.assertEqual(overview["PALAE"]["stocking_quantity"], 300)


class ConcurrentAliasTests(SpeciesIdentityTestBase):
    def test_concurrent_alias_approval_only_one_version_active(self):
        from app.services import species as svc
        db1, db2 = self.Session(), self.Session()
        try:
            s1 = svc.create_species(db1, "品种甲", code="SP-A")
            s2 = svc.create_species(db2, "品种乙", code="SP-B")
            a1 = svc.propose_alias(db1, s1.id, "同写法", actor="甲")
            a2 = svc.propose_alias(db2, s2.id, "同写法 ", actor="乙")

            svc.approve_alias(db1, a1.id, actor="甲")
            with self.assertRaises(svc.SpeciesConflict) as ctx:
                svc.approve_alias(db2, a2.id, actor="乙")
            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            db1.close()
            db2.close()

        # 库里该归一化键只有一个 active
        db = self.Session()
        try:
            rows = db.execute(text(
                "SELECT species_id FROM species_names WHERE normalized_key = :k AND status='active'"
            ), {"k": "同写法"}).fetchall()
            self.assertEqual(len(rows), 1)
        finally:
            db.close()

    def test_cannot_approve_alias_owned_by_another_species(self):
        from app.services import species as svc
        from app.models import SpeciesName
        db = self.Session()
        try:
            s1 = svc.create_species(db, "品种甲", code="SP-A")
            s2 = svc.create_species(db, "品种乙", code="SP-B")
            # 绕过提议校验，直接构造一条待裁定别名（部分唯一索引只管 active，允许共存）
            sneaky = SpeciesName(species_id=s2.id, name="品种甲",
                                  normalized_key="品种甲", name_type="alias",
                                  status="proposed")
            db.add(sneaky)
            db.commit()
            # 键已被 s1 的 standard name 占用：批准必失败
            with self.assertRaises(svc.SpeciesConflict):
                svc.approve_alias(db, sneaky.id)
        finally:
            db.close()


class OverviewTests(SpeciesIdentityTestBase):
    def test_overview_groups_by_identity_and_lists_unresolved_separately(self):
        sp = self.create_species(code="MACRO")
        self.create_pond("P1", "罗氏沼虾")
        self.create_pond("P2", " 罗氏 沼虾 ")
        b = self.create_batch("B1", 1, "淡水长臂大虾")
        mid = self.pending_mappings()[0]["id"]
        self.client.post(f"/api/species/mappings/{mid}/approve",
                         json={"target_species_id": sp["id"], "reason": "旧称"})
        self.create_stocking(b["id"], "罗氏沼虾", 3000)
        # 未裁定文本
        self.create_pond("P3", "某种未知虾")

        overview = self.client.get("/api/species/overview/").json()
        macro = next(o for o in overview if o["code"] == "MACRO")
        self.assertEqual(macro["pond_count"], 2)
        self.assertEqual(macro["batch_count"], 1)
        self.assertEqual(macro["stocking_quantity"], 3000)
        pending_rows = [o for o in overview if o["status"] == "pending"]
        self.assertEqual([o["current_name"] for o in pending_rows], ["某种未知虾"])


class MigrationTests(SpeciesIdentityTestBase):
    def test_legacy_database_gets_identity_columns(self):
        # 关闭当前引擎，构造一个只有旧表结构的库
        self.engine.dispose()
        legacy_engine = create_engine(f"sqlite:///{self.db_path}")
        with legacy_engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS ponds"))
            conn.execute(text(
                "CREATE TABLE ponds (id INTEGER PRIMARY KEY, name VARCHAR(100), "
                "area FLOAT, water_depth FLOAT, species VARCHAR(100), status VARCHAR(20), "
                "created_at DATETIME, updated_at DATETIME)"
            ))
            conn.execute(text("INSERT INTO ponds (name, area, water_depth, species, status, created_at, updated_at) "
                              "VALUES ('迁移塘', 5, 1, '罗氏沼虾', 'active', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"))
        legacy_engine.dispose()

        # 模拟应用重启：建表 + 轻量迁移
        from app.database import Base
        from app.migrations import run_lightweight_migrations
        self.engine = create_engine(f"sqlite:///{self.db_path}",
                                    connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        applied = run_lightweight_migrations(self.engine)
        self.assertIn("ponds.species_code", applied)

        cols = {c["name"] for c in inspect(self.engine).get_columns("ponds")}
        self.assertIn("species_code", cols)

        self.Session.configure(bind=self.engine)
        self.create_species(code="MACRO")
        scan = self.client.post("/api/species/scan/").json()
        self.assertTrue(any(x["code"] == "MACRO" for x in scan["auto_resolved"]))
        pond = self.client.get("/api/ponds/1/").json()
        self.assertEqual(pond["species_code"], "MACRO")


class ExplicitMergeTests(SpeciesIdentityTestBase):
    def test_explicit_merge_migrates_two_catalog_species_with_audit(self):
        a = self.create_species("罗氏沼虾", code="MACRO")
        b = self.create_species("马来西亚大虾", code="MALAY")
        pa = self.create_pond("PA", "罗氏沼虾")
        pb = self.create_pond("PB", "马来西亚大虾")

        r = self.client.post(f"/api/species/{b['id']}/merge/{a['id']}",
                             json={"reason": "查证为同一物种", "actor": "专家"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["source_code"], "MALAY")
        self.assertEqual(body["target_code"], "MACRO")
        self.assertEqual(body["migrated_records"], 1)  # 只有 PB 归属于 MALAY

        self.assertEqual(self.client.get("/api/ponds/2/").json()["species_code"], "MACRO")
        merged = self.client.get(f"/api/species/{b['id']}/").json()
        self.assertEqual(merged["status"], "inactive")
        target = self.client.get(f"/api/species/{a['id']}/").json()
        self.assertIn("马来西亚大虾", target["aliases"])

        events = self.client.get(f"/api/species/{a['id']}/events/").json()
        self.assertIn("species_merged", [e["event_type"] for e in events])

    def test_mapping_approval_cannot_silently_merge_another_species(self):
        # 竞态防御：待裁定文本的写法在裁定前已成为另一品种的生效名称，
        # 指向别的品种批准必须被拒，不能静默强合。
        a = self.create_species("品种甲", code="SP-A")
        b = self.create_species("品种乙", code="SP-B")
        from app.models import SpeciesMapping
        db = self.Session()
        try:
            stale = SpeciesMapping(raw_text="品种乙", normalized_key="品种乙",
                                   candidate_species_id=a["id"], confidence="low",
                                   status="pending", source="entry", occurrence_count=1)
            db.add(stale)
            db.commit()
            stale_id = stale.id
        finally:
            db.close()

        r = self.client.post(f"/api/species/mappings/{stale_id}/approve",
                             json={"target_species_id": a["id"], "reason": "想当然合并"})
        self.assertEqual(r.status_code, 409, r.text)


class VariantEvidenceTests(SpeciesIdentityTestBase):
    def test_repeated_whitespace_variants_reuse_one_evidence_record(self):
        self.create_species(code="MACRO")
        self.create_pond("P1", "罗氏 沼虾")
        self.create_pond("P2", "罗氏  沼虾")
        self.create_pond("P3", "罗氏·沼虾")
        rows = self.client.get("/api/species/mappings/",
                               params={"status": "resolved_auto"}).json()
        macro_rows = [m for m in rows if m["normalized_key"] == "罗氏沼虾"]
        self.assertEqual(len(macro_rows), 1, "同一归一化键只应有一条采信依据")
        self.assertEqual(macro_rows[0]["occurrence_count"], 3)


if __name__ == "__main__":
    unittest.main()
