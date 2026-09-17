# -*- coding: utf-8 -*-
"""规则引擎基准测试(pytest):22 道题必须全对,证据合规 19/19。"""
import copy
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
    ("Basic Wall:Wall - Timber Clad:234869 在哪一层？", "Level 2", True),
    ("Basic Wall:Wall - Timber Clad:234869 的 GlobalId 是什么？", "28i3i5WDD8Ju0YHnzXOzdu", True),
    ("最常用的门类型是什么？", "Single-Flush:800 x 2100", True),
    ("房间 101 是什么房间？", "Kitchen & Dining", False),
]

# 规则引擎没有可靠口径回答这些问题时必须返回 None，由调用方交给 LLM。
FALLBACK_CASES = [
    "第一层比第二层大多少？",
    "第一层的面积是多少？",
    "二层比一层小多少？",
    "每层有多少构件？",
    "最高的墙有多高？",
    "门和窗各有多少？",
    "Entrance door 在哪一层？",
    "Entrance door 的 GlobalId 是什么？",
    "房间 999 是什么房间？",
    "Level 2 和 Level 1 的标高是多少？",
    "Level 1 有多少种材料？",
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


def test_unsupported_area_questions_fall_back_to_llm():
    """面积比较等未覆盖意图不能误命中楼层计数规则。"""
    engine = _engine()
    for q in FALLBACK_CASES:
        assert engine.ask(q) is None, f"未覆盖问题被规则引擎错误拦截: {q}"


def test_incomplete_model_data_falls_back_to_llm():
    """缺少标高或楼层实体时不能生成不完整答案。"""
    model = copy.deepcopy(_rac_model())
    for storey in model["storeys"]:
        storey["elevation"] = None
    engine = RuleEngine(model)

    for q in (
        "最高的楼层叫什么？",
        "最低的楼层叫什么？",
        "Level 2 的标高是多少？",
        "Level 2 比 Level 1 高多少？",
    ):
        assert engine.ask(q) is None, f"缺少标高时仍返回了不完整答案: {q}"

    model = copy.deepcopy(_rac_model())
    model["storeys"] = []
    model["occupied_floors"] = None
    assert RuleEngine(model).ask("一共有几个楼层？") is None


def test_empty_target_sets_fall_back_to_llm():
    """目标构件不存在时不能返回任一楼层的零值作为最终结论。"""
    model = copy.deepcopy(_rac_model())
    model["elements"] = [
        element for element in model["elements"]
        if element.get("ifc_type") != "IFCDOOR"
    ]
    engine = RuleEngine(model)

    assert engine.ask("哪一层的门最多？") is None
    assert engine.ask("最常用的门类型是什么？") is None


def test_model_specific_pdf_facts_do_not_leak_to_other_models():
    """rac 的图纸事实不能用于其他模型。"""
    model = copy.deepcopy(_rac_model())
    model["file_name"] = "another_project.ifc"
    engine = RuleEngine(model)

    assert engine.ask("房间 101 是什么房间？") is None
    assert engine.ask("图纸标注和 IFC 模型标高一致吗？") is None
