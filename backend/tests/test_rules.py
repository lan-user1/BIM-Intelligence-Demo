# -*- coding: utf-8 -*-
"""规则引擎基准测试(pytest):22 道题必须全对,证据合规 19/19。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.model_service import model_service  # noqa: E402
from app.rules import RuleEngine  # noqa: E402

# (题目, 标准答案, 是否应当引用 GlobalId 证据)
CASES = [
    ("这栋楼一共有几扇门？", 16, True),
    ("一共有几扇窗？", 17, True),
    ("一共有多少面墙？", 47, True),
    ("一共有多少块楼板？", 12, True),
    ("模型里有多少种材料？", 104, False),
    ("家具有多少件？", 35, True),
    ("屋顶有几个？", 2, True),
    ("楼梯有几部？", 3, True),
    ("栏杆有几处？", 10, True),
    ("柱子有几根？", 3, True),
    ("一共有几个楼层？", 6, True),
    ("Level 2 有几扇门？", 10, True),
    ("Level 1 有几扇窗？", 0, False),
    ("Level 1 Living Rm. 层有几扇窗？", 8, True),
    ("哪一层的墙最多？", "Level 2", True),
    ("最高的楼层叫什么？", "Roof Line", True),
    ("最低的楼层标高是多少？", "Foundation", True),
    ("Level 2 比 Level 1 高多少？", 3000, True),
    ("Entrance door 在哪一层？", "Level 1", True),
    ("Entrance door 的 GlobalId 是什么？", "1PDnLIM013wvkZO9Lb4$i6", True),
    ("最常用的门类型是什么？", "Single-Flush:800 x 2100", True),
    ("房间 101 是什么房间？", "Kitchen & Dining", False),
]


def _rac_model():
    """取 rac 示例模型;没有则直接失败(基准题以该模型为准)。"""
    for info in model_service.list_models():
        if info["file_name"] == "rac_basic_sample_project.ifc":
            return model_service.get_model(info["id"])
    raise AssertionError("MySource 中缺少 rac_basic_sample_project.ifc")


def _engine():
    return RuleEngine(_rac_model())


def test_answers_match_ground_truth():
    engine = _engine()
    for q, expected, _needs_ev in CASES:
        result = engine.ask(q)
        assert result is not None, f"规则未命中: {q}"
        assert result["value"] == expected, (
            f"题目「{q}」期望 {expected!r},实际 {result['value']!r}"
        )


def test_evidence_compliance():
    engine = _engine()
    for q, _expected, needs_ev in CASES:
        if not needs_ev:
            continue
        result = engine.ask(q)
        assert result is not None, f"规则未命中: {q}"
        assert len(result["evidence"]) > 0, f"题目「{q}」缺少 GlobalId 证据"


def test_evidence_globalids_exist_in_model():
    """证据 GlobalId 必须真实存在于模型构件/楼层中。"""
    engine = _engine()
    model = _rac_model()
    known_ids = {el["global_id"] for el in model["elements"] if el.get("global_id")}
    known_ids |= {s["global_id"] for s in model["storeys"] if s.get("global_id")}
    for q, _expected, needs_ev in CASES:
        if not needs_ev:
            continue
        result = engine.ask(q)
        for gid in result["evidence"]:
            assert gid in known_ids, f"题目「{q}」引用了不存在的 GlobalId: {gid}"
