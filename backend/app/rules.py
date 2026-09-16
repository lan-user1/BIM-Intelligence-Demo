# -*- coding: utf-8 -*-
"""规则式问答引擎:在小组成果的模型解析结果上现场计算答案。

设计思路:问题 → 意图识别(关键词规则)→ 查当前模型数据 → 组装答案 + 证据(GlobalId)。
所有答案都是运行时从当前加载的模型现算出来的,没有任何写死的答案;
换一个模型,同一套规则自动给出该模型自己的答案。

已知局限(诚实声明,写入误差分析):
    1. IFC 里没有房间实体 → 房间类问题走 PDF 数据线,无法引用 GlobalId
    2. 按楼层计数使用"直接空间包含"口径,挂在墙洞口/幕墙下的构件不在此计数内
    3. 只覆盖规则式问题;未命中的问题返回 None,由调用方交给 LLM 兜底
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .pdf_facts import PDF_ELEVATIONS, ROOMS

# 日常说法 → (IFC 类型键, 中文名, 量词);顺序敏感:"层/楼层"必须放最后,
# 避免在 "Level 1 Living Rm. 层有几扇窗" 这类问题里抢在 "窗" 之前匹配。
ENTITY_ALIASES = [
    ("门", ("IfcDoor", "门", "扇")),
    ("窗", ("IfcWindow", "窗", "扇")),
    ("窗户", ("IfcWindow", "窗", "扇")),
    ("墙", ("IfcWall", "墙", "面")),
    ("楼板", ("IfcSlab", "楼板", "块")),
    ("柱", ("IfcColumn", "柱", "根")),
    ("屋顶", ("IfcRoof", "屋顶", "个")),
    ("楼梯", ("IfcStair", "楼梯", "部")),
    ("栏杆", ("IfcRailing", "栏杆", "处")),
    ("家具", ("IfcFurnishingElement", "家具", "件")),
    ("材料", ("IfcMaterial", "材料", "种")),
    ("door", ("IfcDoor", "门", "扇")),
    ("window", ("IfcWindow", "窗", "扇")),
    ("wall", ("IfcWall", "墙", "面")),
    ("slab", ("IfcSlab", "楼板", "块")),
    ("column", ("IfcColumn", "柱", "根")),
    ("roof", ("IfcRoof", "屋顶", "个")),
    ("stair", ("IfcStair", "楼梯", "部")),
    ("railing", ("IfcRailing", "栏杆", "处")),
    ("furniture", ("IfcFurnishingElement", "家具", "件")),
    ("material", ("IfcMaterial", "材料", "种")),
    ("楼层", ("IfcBuildingStorey", "楼层", "个")),
    ("storey", ("IfcBuildingStorey", "楼层", "个")),
    ("层", ("IfcBuildingStorey", "楼层", "个")),
]

# 类型键 → 解析器元素里的 ifc_type 集合。
# IfcFurnishingElement 对应 IFCFURNITURE + IFCSYSTEMFURNITUREELEMENT(ifcopenshell 口径);
# IfcStair 不含 IFCSTAIRFLIGHT(梯段是独立实体,不计入"楼梯几部")。
TYPE_MATCH = {
    "IfcDoor": ("IFCDOOR",),
    "IfcWindow": ("IFCWINDOW",),
    "IfcWall": ("IFCWALL", "IFCWALLSTANDARDCASE"),
    "IfcSlab": ("IFCSLAB",),
    "IfcColumn": ("IFCCOLUMN",),
    "IfcRoof": ("IFCROOF",),
    "IfcStair": ("IFCSTAIR",),
    "IfcRailing": ("IFCRAILING",),
    "IfcFurnishingElement": ("IFCFURNITURE", "IFCSYSTEMFURNITUREELEMENT"),
    "IfcTransportElement": ("IFCTRANSPORTELEMENT",),
    "IfcCurtainWall": ("IFCCURTAINWALL",),
    "IfcBeam": ("IFCBEAM",),
    "IfcSpace": ("IFCSPACE",),
}

# 楼层叫法 → 标准楼层名;顺序敏感:"level 1 living" 必须在 "level 1" 之前
STOREY_KEYWORDS = [
    ("level 1 living", "Level 1 Living Rm."),
    ("living rm", "Level 1 Living Rm."),
    ("level 2", "Level 2"),
    ("二层", "Level 2"),
    ("2层", "Level 2"),
    ("level 1", "Level 1"),
    ("一层", "Level 1"),
    ("1层", "Level 1"),
    ("roof line", "Roof Line"),
    ("屋顶层", "Roof Line"),
    ("ceiling", "Ceiling"),
    ("天花板", "Ceiling"),
    ("foundation", "Foundation"),
    ("基础", "Foundation"),
]


class RuleEngine:
    """规则式问答引擎:每次 ask() 现场查当前模型,答案自带 GlobalId 证据。"""

    def __init__(self, model: dict[str, Any]):
        self.model = model
        self.elements: list[dict[str, Any]] = model.get("elements", [])
        self.storeys: list[dict[str, Any]] = model.get("storeys", [])
        self.materials: list[dict[str, Any]] = model.get("materials", [])

    # ---------- 基础工具 ----------

    def _find_storey(self, name: str) -> dict[str, Any] | None:
        for storey in self.storeys:
            if storey["name"] == name:
                return storey
        return None

    def _elements_of(self, typ: str) -> list[dict[str, Any]]:
        matched = TYPE_MATCH.get(typ, (typ.upper(),))
        return [el for el in self.elements if el.get("ifc_type") in matched]

    def _detect_storey(self, q: str) -> dict[str, Any] | None:
        """从问题里识别提到的楼层。"""
        ql = q.lower()
        if re.search(r"哪一|which", ql):
            return None  # "哪一层"是提问,不是点名楼层
        for kw, name in STOREY_KEYWORDS:
            if kw in ql:
                return self._find_storey(name)
        return None

    def _detect_entity(self, q: str) -> tuple[str | None, str, str]:
        """从问题里识别提到的构件类型,返回 (IFC类型键, 中文名, 量词)。"""
        ql = q.lower()
        for kw, (typ, cn, mw) in ENTITY_ALIASES:
            if kw in ql:
                return typ, cn, mw
        return None, "", ""

    def _extract_name(self, q: str) -> str | None:
        """从问题里提取要查找的构件名(引号里的名字,或"在哪/GlobalId"前的名字)。"""
        m = re.search(r'[“"]([^”"]+)[”"]', q)
        if m:
            return m.group(1).strip()
        m = re.search(r'(.+?)\s*(?:在哪|在什么层|位于|的\s*globalid|where is )', q, re.I)
        if m:
            return m.group(1).strip()
        return None

    def _find_elements(self, name: str) -> list[dict[str, Any]]:
        """按名称(不区分大小写的包含匹配)在所有构件里查找。
        结果按 GlobalId 排序,保证输出稳定可复现。"""
        nl = name.lower().strip()
        matches = [
            el for el in self.elements
            if el.get("name") and nl in el["name"].lower()
        ]
        return sorted(matches, key=lambda el: el.get("global_id") or "")

    # ---------- 各类意图的回答 ----------

    def _answer_count(self, typ: str, cn: str, mw: str, storey: dict[str, Any] | None) -> dict[str, Any]:
        """意图:某类构件有多少个(可选限定楼层)。"""
        if typ == "IfcMaterial":
            names = [m.get("name") or f"材料 #{m.get('id')}" for m in self.materials]
            return {"answer": f"整栋楼共有 {len(names)} {mw}{cn}。",
                    "value": len(names), "evidence": names,
                    "note": "材料不是 IFC 构件类型,没有 GlobalId,证据改引名称"}
        if typ == "IfcBuildingStorey":
            if not self.storeys:
                drawing_floors = self.model.get("occupied_floors")
                if drawing_floors:
                    return {"answer": f"当前模型没有楼层实体（IfcBuildingStorey），但配套图纸标注该建筑有 {drawing_floors} 层。",
                            "value": None, "evidence": [],
                            "note": "楼层数来自图纸数据线；IFC 未声明楼层空间结构"}
                return {"answer": "当前模型没有楼层实体（IfcBuildingStorey），无法回答楼层数问题。",
                        "value": None, "evidence": [],
                        "note": "模型未声明楼层空间结构，构件统计等功能不受影响"}
            ids = [s["global_id"] for s in self.storeys if s.get("global_id")]
            names = "、".join(s["name"] for s in self.storeys)
            return {"answer": f"整栋楼共有 {len(self.storeys)} 个楼层实体（IFC BuildingStorey）：{names}。",
                    "value": len(self.storeys), "evidence": ids,
                    "note": "IFC 把基础、吊顶、屋面等也声明为楼层实体；可居住楼层为 Level 1 与 Level 2"}
        if storey:
            # 与构件清单一致:按解析后楼层归属统计(含通过幕墙/墙洞口间接归属的构件)。
            els = [el for el in self._elements_of(typ)
                   if el.get("storey") == storey["name"]]
        else:
            els = self._elements_of(typ)
        n = len(els)
        scope = storey["name"] if storey else "整栋楼"
        if n == 0:
            return {"answer": f"{scope}没有{cn}（查询结果为 0）。",
                    "value": 0, "evidence": [],
                    "note": "查询结果为 0，无证据可引"}
        ids = [el["global_id"] for el in els if el.get("global_id")]
        note = ""
        if storey:
            direct_n = sum(
                1 for el in self._elements_of(typ)
                if el.get("direct_storey_id") == storey["id"]
            )
            if direct_n != n:
                note = (f"其中 {direct_n} 个由楼层直接空间包含,"
                        f"{n - direct_n} 个通过幕墙/洞口间接归属")
        return {"answer": f"{scope}共有 {n} {mw}{cn}。",
                "value": n, "evidence": ids, "note": note}

    def _answer_highest(self) -> dict[str, Any]:
        if not self.storeys:
            return {"answer": "当前模型中没有楼层实体（IfcBuildingStorey），无法回答最高楼层。",
                    "value": None, "evidence": [], "note": "模型无空间结构数据"}
        s = max(self.storeys, key=lambda x: x["elevation"] if x.get("elevation") is not None else float("-inf"))
        return {"answer": f"最高的楼层是 {s['name']}，标高 {s['elevation']:g}。",
                "value": s["name"], "evidence": [s["global_id"]], "note": ""}

    def _answer_lowest(self) -> dict[str, Any]:
        if not self.storeys:
            return {"answer": "当前模型中没有楼层实体（IfcBuildingStorey），无法回答最低楼层。",
                    "value": None, "evidence": [], "note": "模型无空间结构数据"}
        s = min(self.storeys, key=lambda x: x["elevation"] if x.get("elevation") is not None else float("inf"))
        return {"answer": f"最低的楼层是 {s['name']}，标高 {s['elevation']:g}。",
                "value": s["name"], "evidence": [s["global_id"]], "note": ""}

    def _answer_elevation(self, storey: dict[str, Any]) -> dict[str, Any]:
        return {"answer": f"{storey['name']} 的标高是 {storey['elevation']:g}。",
                "value": round(storey["elevation"]), "evidence": [storey["global_id"]], "note": ""}

    def _answer_diff(self, q: str) -> dict[str, Any]:
        found = []
        for kw, name in STOREY_KEYWORDS:
            if kw in q.lower() and name not in found:
                found.append(name)
        if len(found) >= 2:
            a, b = self._find_storey(found[0]), self._find_storey(found[1])
            diff = a["elevation"] - b["elevation"]
            return {"answer": f"{a['name']} 比 {b['name']} 高 {diff:g}。",
                    "value": round(diff),
                    "evidence": [a["global_id"], b["global_id"]], "note": ""}
        return {"answer": "请给出两个楼层名，例如：Level 2 比 Level 1 高多少？",
                "value": None, "evidence": [], "note": "未识别出两个楼层"}

    def _answer_which_storey_most(self, typ: str, cn: str) -> dict[str, Any]:
        """意图:哪一层的某类构件最多。"""
        best, bn, best_ids = None, -1, []
        for s in self.storeys:
            els = [el for el in self._elements_of(typ)
                   if el.get("storey") == s["name"]]
            if len(els) > bn:
                best, bn, best_ids = s, len(els), [el["global_id"] for el in els if el.get("global_id")]
        if best is None:
            return {"answer": "模型里没有楼层数据。", "value": None, "evidence": [], "note": ""}
        return {"answer": f"{best['name']} 的{cn}最多，共 {bn} 个。",
                "value": best["name"], "evidence": best_ids,
                "note": "按解析后楼层归属统计"}

    def _answer_most_common(self, typ: str, cn: str) -> dict[str, Any]:
        """意图:最常见的构件族类型是什么。"""
        els = self._elements_of(typ)

        def fam(el: dict[str, Any]) -> str:
            # 名称格式常为 "族类型:尺寸:元素Id"，去掉末尾 Id 再统计
            name = el.get("name") or "(未命名)"
            return re.sub(r":\d+$", "", name)

        top, n = Counter(fam(el) for el in els).most_common(1)[0]
        ids = [el["global_id"] for el in els if fam(el) == top and el.get("global_id")]
        note = "" if ids else f"{cn}不是 IFC 构件类型，没有 GlobalId"
        return {"answer": f"最常见的{cn}类型是「{top}」，共 {n} 个（占 {n}/{len(els)}）。",
                "value": top, "evidence": ids, "note": note}

    def _answer_where(self, q: str) -> dict[str, Any]:
        """意图:某个构件在哪一层。"""
        name = self._extract_name(q)
        if not name:
            return {"answer": "请告诉我你要找的构件名，例如：Entrance door 在哪一层？",
                    "value": None, "evidence": [], "note": ""}
        matches = self._find_elements(name)
        if not matches:
            return {"answer": f"在当前模型里没找到名称包含「{name}」的构件。",
                    "value": None, "evidence": [], "note": ""}
        el = matches[0]
        storey = el.get("storey") or None
        extra = f"（另有 {len(matches)-1} 个同名构件）" if len(matches) > 1 else ""
        if storey is None:
            return {"answer": f"「{el['name']}」未直接关联到任何楼层{extra}。",
                    "value": None, "evidence": [el["global_id"]],
                    "note": "该构件不在任何楼层的包含关系中"}
        return {"answer": f"「{el['name']}」在 {storey}{extra}。",
                "value": storey, "evidence": [el["global_id"]], "note": ""}

    def _answer_find_globalid(self, q: str) -> dict[str, Any]:
        """意图:某个构件的 GlobalId 是什么。"""
        name = self._extract_name(q)
        if not name:
            return {"answer": "请给出构件名，例如：Entrance door 的 GlobalId 是什么？",
                    "value": None, "evidence": [], "note": ""}
        matches = self._find_elements(name)
        if not matches:
            return {"answer": f"在当前模型里没找到名称包含「{name}」的构件。",
                    "value": None, "evidence": [], "note": ""}
        el = matches[0]
        extra = f"（另有 {len(matches)-1} 个同名构件，只展示第一个）" if len(matches) > 1 else ""
        return {"answer": f"「{el['name']}」的 GlobalId 是 {el['global_id']}。{extra}",
                "value": el["global_id"], "evidence": [el["global_id"]], "note": ""}

    def _answer_elevation_check(self) -> dict[str, Any]:
        """意图:交叉验证——图纸标注的楼层标高与 IFC 模型是否一致。"""
        if not self.storeys:
            return {"answer": "当前模型中没有楼层实体，无法与图纸标高对比。",
                    "value": None, "evidence": [], "note": "模型无空间结构数据"}
        rows = []
        for s in self.storeys:
            pdf = PDF_ELEVATIONS.get(s["name"])
            if not pdf:
                rows.append((s["name"], None, round(s["elevation"]), "图纸未标注"))
                continue
            same = (round(pdf["elevation"]) == round(s["elevation"]))
            rows.append((s["name"], pdf["elevation"], round(s["elevation"]),
                         "一致" if same else "不一致"))
        ok = sum(1 for r in rows if r[3] == "一致")
        total = sum(1 for r in rows if r[1] is not None)
        if ok == total:
            text = f"图纸标注与 IFC 模型标高完全一致（{ok}/{total} 层）。"
        else:
            diffs = "；".join(f"{r[0]}(图纸 {r[1]} / 模型 {r[2]})"
                              for r in rows if r[3] == "不一致")
            text = f"{ok}/{total} 层一致；不一致的层：{diffs}。"
        return {"answer": text, "value": (ok == total),
                "evidence": [s["global_id"] for s in self.storeys if s.get("global_id")],
                "note": "图纸数据来源:PDF p4-6 立面/剖面标注(图纸无 GlobalId,证据引用 IFC 侧)"}

    def _answer_room(self, q: str) -> dict[str, Any]:
        """意图:房间类问题(走 PDF 数据线,IFC 无房间实体)。"""
        m = re.search(r"房间\s*([0-9]+[A-Za-z]*)", q) or \
            re.search(r"room\s*([0-9]+[A-Za-z]*)", q, re.I)
        if not m:
            return {"answer": "请给出房间编号，例如：房间 101 是什么房间？",
                    "value": None, "evidence": [], "note": ""}
        num = m.group(1)
        if num in ROOMS:
            r = ROOMS[num]
            area = f"，面积 {r['area']}" if r.get("area") else ""
            return {"answer": f"房间 {num} 是 {r['name']}{area}。",
                    "value": r["name"], "evidence": [],
                    "note": "数据来源:PDF 图纸 A102 平面图标签;IFC 中无房间实体,无法引用 GlobalId"}
        return {"answer": f"房间 {num} 的标签未能可靠配对（图纸标签提取待完善）。",
                "value": None, "evidence": [], "note": "已知局限:部分房间标签未录入"}

    # ---------- 主入口 ----------

    def ask(self, question: str) -> dict[str, Any] | None:
        """输入自然语言问题。命中规则返回 {answer, value, evidence, note};
        未命中返回 None(由调用方交给 LLM 兜底)。"""
        q = question.strip()
        ql = q.lower()
        storey = self._detect_storey(q)
        etype, cname, mw = self._detect_entity(q)

        # —— 意图分派(顺序重要,从上到下)——
        if "最高" in q or "highest" in ql:
            return self._answer_highest()
        if "最低" in q or "lowest" in ql:
            return self._answer_lowest()
        if "比" in q and "高" in q and "标" not in q:
            return self._answer_diff(q)
        if ("标高" in q or "elevation" in ql) and storey:
            return self._answer_elevation(storey)
        if ("一致" in q or "agree" in ql or "match" in ql) and \
                ("标高" in q or "图纸" in q or "drawing" in ql):
            return self._answer_elevation_check()
        if ("哪一" in q or "which" in ql) and ("最多" in q or "most" in ql) and etype:
            return self._answer_which_storey_most(etype, cname)
        if ("在哪" in q or "where" in ql) and not re.search(r"多少|几", ql):
            return self._answer_where(q)
        if ("最常用" in q or "最常见" in q or "most common" in ql) and etype:
            return self._answer_most_common(etype, cname)
        if re.search(r"多少|几|how many|count", ql) and etype:
            return self._answer_count(etype, cname, mw, storey)
        if "globalid" in ql and ("什么" in q or "what" in ql):
            return self._answer_find_globalid(q)
        if re.search(r"房间|room", ql):
            return self._answer_room(q)
        return None
