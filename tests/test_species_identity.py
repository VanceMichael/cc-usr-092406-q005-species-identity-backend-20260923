"""品种身份链路端到端测试: 目录、别名、版本更正、未决队列、轮养、签署重现、合并/拆分与统一身份。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

# 在导入应用前把数据库指向临时文件(应用导入即建表/迁移, 不能落到仓库内)
_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB.name}"
os.environ.setdefault("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, get_db
from app import models


def _make_client():
    """每个用例使用独立的内存 SQLite, 并覆盖路由的 get_db 依赖。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    return client, TestingSession


class SpeciesIdentityTest(unittest.TestCase):
    def setUp(self):
        self.client, self.SessionLocal = _make_client()
        self.c = self.client

    def _db(self):
        return self.SessionLocal()

    def _create_species(self, name, **kw):
        r = self.c.post("/api/species/identities/", json={"canonical_name": name, **kw})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _create_pond(self, name="塘口1", species=None):
        body = {"name": name, "area": 10.0, "water_depth": 1.5}
        if species is not None:
            body["species"] = species
        r = self.c.post("/api/ponds/", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _create_batch(self, number, pond_id, species, date="2026-01-01"):
        r = self.c.post("/api/batches/", json={
            "batch_number": number, "pond_id": pond_id,
            "species": species, "stocking_date": date,
        })
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # ------------------------------------------------------------------
    # 1. 规范名保存标识/有效名称; 带空格等同一写法精确命中
    # ------------------------------------------------------------------
    def test_catalog_persists_code_and_effective_name(self):
        sp = self._create_species("罗氏沼虾")
        self.assertTrue(sp["code"].startswith("SP-"))
        self.assertEqual(sp["current_standard_name"], "罗氏沼虾")
        self.assertIn("罗氏沼虾", sp["aliases"])

        versions = self.c.get(f"/api/species/identities/{sp['id']}/versions/").json()
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["status"], "effective")

    def test_whitespace_and_fullwidth_variants_match_exactly(self):
        sp = self._create_species("罗氏沼虾")
        for variant in (" 罗氏 沼虾 ", "罗氏　沼虾", "罗氏 沼虾"):
            r = self.c.post("/api/species/resolve/", json={"text": variant})
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertTrue(body["matched"])
            self.assertEqual(body["identity_code"], sp["code"])

    # ------------------------------------------------------------------
    # 2. 不认识的写法: 提示候选并入未决队列, 绝不自动建身份/自动合并
    # ------------------------------------------------------------------
    def test_unknown_text_becomes_pending_without_auto_merge(self):
        sp = self._create_species("罗氏沼虾")
        # 录入预览: 只提示不命中, 不自动建身份
        r = self.c.post("/api/species/resolve/", json={"text": "马来西亚大虾"})
        body = r.json()
        self.assertFalse(body["matched"])
        self.assertEqual(body["status"], "pending")

        # 目录里仍然只有 1 个品种身份, 没有被自动拆分或合并
        identities = self.c.get("/api/species/identities/").json()
        self.assertEqual(len(identities), 1)

        # 真实落库一条业务记录后, 该文本进入未决队列
        self._create_pond("野塘", "马来西亚大虾")
        pending = self.c.get("/api/species/legacy/").json()
        self.assertEqual([p["raw_text"] for p in pending], ["马来西亚大虾"])

    # ------------------------------------------------------------------
    # 3. 真正不同的品系不会被自动合并
    # ------------------------------------------------------------------
    def test_distinct_strains_remain_separate(self):
        luo = self._create_species("罗氏沼虾")
        nanmei = self._create_species("南美白对虾")
        self.assertNotEqual(luo["id"], nanmei["id"])

        # 把 A 品种的写法当作 B 的别名批准, 必须被拒绝(只能显式合并)
        prop = self.c.post(
            f"/api/species/identities/{nanmei['id']}/aliases/",
            json={"alias_text": "罗氏沼虾", "proposed_by": "甲"},
        )
        self.assertEqual(prop.status_code, 409)

        self._create_pond("塘A", "罗氏沼虾")
        self._create_pond("塘B", "南美白对虾")
        self._create_batch("BA", 1, "罗氏沼虾")
        self._create_batch("BB", 2, "南美白对虾")
        groups = self.c.get("/api/analysis/compare-by-species/").json()["groups"]
        codes = {g["identity_code"] for g in groups}
        self.assertEqual(codes, {luo["code"], nanmei["code"]})

    # ------------------------------------------------------------------
    # 4. 塘口允许轮养, 留下轮养历史
    # ------------------------------------------------------------------
    def test_pond_rotation_records_history(self):
        self._create_species("罗氏沼虾")
        self._create_species("南美白对虾")
        pond = self._create_pond("轮养塘", "罗氏沼虾")

        upd = self.c.put(f"/api/ponds/{pond['id']}/", json={"species": "南美白对虾"})
        self.assertEqual(upd.status_code, 200)
        self.assertEqual(
            upd.json()["species_identity"]["current_standard_name"], "南美白对虾"
        )

        history = self.c.get(f"/api/ponds/{pond['id']}/species-history/").json()
        self.assertEqual([h["species_text"] for h in history], ["罗氏沼虾", "南美白对虾"])
        self.assertIsNotNone(history[1]["species_identity_id"])

    # ------------------------------------------------------------------
    # 5. 批次/投苗冻结创建时品种版本; 名称更正只改后续展示, 已签署分析按原口径重现
    # ------------------------------------------------------------------
    def test_rename_preserves_batch_version_and_signed_analysis(self):
        sp = self._create_species("罗氏沼虾")
        self._create_pond()
        batch = self._create_batch("B-001", 1, "罗氏沼虾")
        stocking = self.c.post("/api/stocking-records/", json={
            "batch_id": batch["id"], "species": "罗氏沼虾", "quantity": 1000,
        }).json()

        batch_version = batch["species_version_id"]

        # 批次与投苗都不允许事后改品种文本
        self.assertEqual(
            self.c.put(f"/api/batches/{batch['id']}/", json={"species": "别的虾"}).status_code,
            409,
        )
        self.assertEqual(
            self.c.put(f"/api/stocking-records/{stocking['id']}/", json={"species": "别的虾"}).status_code,
            409,
        )

        # 名称更正
        corr = self.c.post(
            f"/api/species/identities/{sp['id']}/correct-name/",
            json={"new_name": "罗氏沼虾(马来西亚品系)", "reason": "规范命名", "changed_by": "主管"},
        )
        self.assertEqual(corr.status_code, 200, corr.text)

        # 批次仍引用旧版本: 视图里同时给出历史名与当前标准名
        batch_after = self.c.get(f"/api/batches/{batch['id']}/").json()
        view = batch_after["species_identity"]
        self.assertEqual(view["identity_code"], sp["code"])
        self.assertEqual(view["historical_name"], "罗氏沼虾")
        self.assertTrue(view["is_historical_name"])
        self.assertEqual(view["current_standard_name"], "罗氏沼虾(马来西亚品系)")
        self.assertEqual(batch_after["species_version_id"], batch_version)

        # 同一身份只有一个生效版本
        versions = self.c.get(f"/api/species/identities/{sp['id']}/versions/").json()
        self.assertEqual([v["status"] for v in versions], ["superseded", "effective"])

        # 签署更正前的分析
        signed = self.c.post(
            f"/api/analysis/cycle/{batch['id']}/sign/",
            json={"signed_by": "甲", "sign_reason": "周期确认"},
        ).json()
        self.assertEqual(signed["signed"]["species_name_at_signing"], "罗氏沼虾")

        db = self._db()
        snap = db.query(models.SignedAnalysis).filter_by(batch_id=batch["id"]).one()
        signed_id = snap.id
        db.close()

        # 再次更名后, 已签署分析仍按原口径重现
        self.c.post(
            f"/api/species/identities/{sp['id']}/correct-name/",
            json={"new_name": "罗氏沼虾", "reason": "恢复", "changed_by": "主管"},
        )
        reproduced = self.c.get(f"/api/analysis/cycle/signed/{signed_id}/").json()
        self.assertEqual(reproduced["signed"]["species_name_at_signing"], "罗氏沼虾")
        self.assertEqual(reproduced["batch_number"], "B-001")

    # ------------------------------------------------------------------
    # 6. 未决队列: 候选、映射回填、撤回留痕, 合并/拆分/撤回都有决策依据
    # ------------------------------------------------------------------
    def test_legacy_scan_decide_and_withdraw(self):
        sp = self._create_species("罗氏沼虾")
        self._create_pond("塘1", "罗氏沼虾(旧称)")
        batch = self._create_batch("B1", 1, "罗氏沼虾(旧称)")
        # 投苗用第三种写法 -> 同一未决文本出现 2 次(塘口+批次), 另一文本 1 次
        self.c.post("/api/stocking-records/", json={
            "batch_id": batch["id"], "species": "马来西亚大虾", "quantity": 100,
        })

        stats = self.c.post("/api/species/legacy/scan/").json()
        # 文本在业务落库时已入队; scan 负责校准计数, 不要求这里新增
        self.assertIn("new_texts", stats)

        pending = {p["raw_text"]: p for p in self.c.get("/api/species/legacy/").json()}
        self.assertEqual(pending["罗氏沼虾(旧称)"]["occurrence_count"], 2)
        self.assertIn("ponds", pending["罗氏沼虾(旧称)"]["source_table"])
        self.assertIn("batches", pending["罗氏沼虾(旧称)"]["source_table"])

        # 人工映射旧称到既有身份, 业务记录被回填
        leg = pending["罗氏沼虾(旧称)"]
        decide = self.c.post(
            f"/api/species/legacy/{leg['id']}/decide/",
            json={"action": "map", "target_identity_id": sp["id"],
                  "decided_by": "主管", "rationale": "确认为同一品种旧称"},
        )
        self.assertEqual(decide.status_code, 200, decide.text)

        batch_after = self.c.get(f"/api/batches/{batch['id']}/").json()
        self.assertEqual(batch_after["species_identity"]["identity_code"], sp["code"])
        pond_after = self.c.get("/api/ponds/1/").json()
        self.assertEqual(pond_after["species_identity"]["identity_code"], sp["code"])

        # 以后再录入该旧称直接命中, 不再进队列
        again = self.c.post("/api/species/resolve/", json={"text": "罗氏沼虾(旧称)"}).json()
        self.assertEqual(again["status"], "exact_alias")

        # 撤回映射: 记录回到未决, 回填被解除, 依据保留
        withdraw = self.c.post(
            f"/api/species/legacy/{leg['id']}/withdraw/",
            json={"decided_by": "主管", "rationale": "裁定有误, 撤回重审"},
        )
        self.assertEqual(withdraw.status_code, 200, withdraw.text)
        batch_after2 = self.c.get(f"/api/batches/{batch['id']}/").json()
        self.assertTrue(batch_after2["species_identity"]["unresolved"])

        decisions = self.c.get("/api/species/decisions/").json()
        actions = {d["action"] for d in decisions}
        self.assertIn("map", actions)
        self.assertIn("withdraw", actions)
        map_reasons = [d["rationale"] for d in decisions if d["action"] == "map"]
        self.assertIn("确认为同一品种旧称", map_reasons)

    # ------------------------------------------------------------------
    # 7. 别名并发裁定: 乐观锁保证只有一个版本生效
    # ------------------------------------------------------------------
    def test_concurrent_alias_adjudication_only_one_effective(self):
        sp = self._create_species("罗氏沼虾")
        prop = self.c.post(
            f"/api/species/identities/{sp['id']}/aliases/",
            json={"alias_text": "马来西亚大虾", "proposed_by": "甲", "reason": "别称"},
        )
        alias_id = prop.json()["id"]
        self.assertEqual(prop.status_code, 200)

        # 两个裁定者同时持有 lock_version=1
        first = self.c.post(
            f"/api/species/aliases/{alias_id}/adjudicate/",
            json={"action": "approve", "reviewed_by": "乙",
                  "reason": "确认同义", "expected_lock_version": 1},
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["status"], "approved")

        second = self.c.post(
            f"/api/species/aliases/{alias_id}/adjudicate/",
            json={"action": "reject", "reviewed_by": "丙",
                  "reason": "重复裁定", "expected_lock_version": 1},
        )
        self.assertEqual(second.status_code, 409)

        final = self.c.get("/api/species/aliases/", params={"status": "approved"}).json()
        self.assertEqual(len(final), 2)  # 规范名 + 新批准的别名
        self.assertTrue(any(a["alias_text"] == "马来西亚大虾" for a in final))

    # ------------------------------------------------------------------
    # 8. 显式合并 + 追溯/比较统一身份; 再拆分出品系
    # ------------------------------------------------------------------
    def test_explicit_merge_unifies_trace_and_comparison(self):
        luo = self._create_species("罗氏沼虾")
        # 第二个身份代表“旧目录里误建的重复品种”
        dup = self._create_species("马来西亚大虾")

        self._create_pond("塘1", "罗氏沼虾")
        b1 = self._create_batch("B1", 1, "罗氏沼虾")
        self._create_pond("塘2", "马来西亚大虾")
        b2 = self._create_batch("B2", 2, "马来西亚大虾")

        # 显式合并(系统不会自己做)
        merge = self.c.post("/api/species/merge/", json={
            "source_identity_id": dup["id"],
            "target_identity_id": luo["id"],
            "rationale": "马来西亚大虾为罗氏沼虾俗称",
            "decided_by": "主管",
        })
        self.assertEqual(merge.status_code, 200, merge.text)

        # 两条批次、追溯都解析到同一存续身份
        for bid in (b1["id"], b2["id"]):
            view = self.c.get(f"/api/batches/{bid}/").json()["species_identity"]
            self.assertEqual(view["identity_code"], luo["code"])

        trace = self.c.get(f"/api/analysis/traceability/{b2['id']}/").json()
        self.assertEqual(trace["batch"]["species_identity"]["identity_code"], luo["code"])

        groups = self.c.get("/api/analysis/compare-by-species/").json()["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["batch_count"], 2)

        # 拆分: 发现其实是不同品系, 把写法迁回新身份
        split = self.c.post("/api/species/split/", json={
            "from_identity_id": luo["id"],
            "new_canonical_name": "马来西亚野生大虾",
            "move_alias_texts": ["马来西亚大虾"],
            "rationale": "经核实为独立品系",
            "decided_by": "主管",
        })
        self.assertEqual(split.status_code, 200, split.text)
        new_id = split.json()["id"]
        self.assertNotEqual(new_id, luo["id"])

        groups2 = {g["identity_id"]: g for g in
                   self.c.get("/api/analysis/compare-by-species/").json()["groups"]}
        # 原 dup 身份已合并到 luo; 拆分只迁移写法, 历史批次身份仍稳定跟随合并链
        self.assertIn(luo["id"], groups2)

    # ------------------------------------------------------------------
    # 9. 列表 / 周期分析 / 追溯返回同一身份并显式标注三种状态
    # ------------------------------------------------------------------
    def test_unified_identity_view_across_surfaces(self):
        sp = self._create_species("罗氏沼虾")
        self._create_pond(species="罗氏沼虾")
        batch = self._create_batch("B1", 1, "罗氏沼虾")
        self.c.post("/api/stocking-records/", json={
            "batch_id": batch["id"], "species": "罗氏沼虾", "quantity": 500,
        })

        ponds = self.c.get("/api/ponds/").json()
        batches = self.c.get("/api/batches/").json()
        cycle = self.c.get(f"/api/analysis/cycle/{batch['id']}/").json()
        trace = self.c.get(f"/api/analysis/traceability/{batch['id']}/").json()

        code_surfaces = {
            ponds[0]["species_identity"]["identity_code"],
            batches[0]["species_identity"]["identity_code"],
            cycle["species_identity"]["identity_code"],
            trace["batch"]["species_identity"]["identity_code"],
            trace["stocking_records"][0]["species_identity"]["identity_code"],
        }
        self.assertEqual(code_surfaces, {sp["code"]})
        self.assertEqual(cycle["species_identity"]["resolution_status"], "current")

    def test_pending_view_marks_unresolved(self):
        self._create_pond("野塘", "某种未登记鱼")
        pond = self.c.get("/api/ponds/").json()[0]
        view = pond["species_identity"]
        self.assertTrue(view["unresolved"])
        self.assertEqual(view["resolution_status"], "pending")
        self.assertEqual(view["raw_name"], "某种未登记鱼")


if __name__ == "__main__":
    unittest.main()
