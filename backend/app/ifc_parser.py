"""解析 IFC STEP 文本并提取模型、楼层、构件、属性、材料和数量信息。

该模块不依赖几何内核，目标是提供问答、构件表格和统计概览所需的轻量结构数据。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


# IFC STEP 每行通常形如 `#123=IFCWALL(...);`，复杂实体可能跨多行。
ENTITY_PATTERN = re.compile(
    r"^#(?P<id>\d+)\s*=\s*(?P<type>[A-Z0-9_]+)\s*\((?P<params>.*)\)\s*;$",
    re.DOTALL,
)

# 只解析与空间层级、属性关系、单位和材料相关的实体，降低大型 IFC 的内存占用。
SPATIAL_TYPES = {
    "IFCPROJECT",
    "IFCSITE",
    "IFCBUILDING",
    "IFCBUILDINGSTOREY",
    "IFCSPACE",
}

# 属性和数量实体最终会合并到所属构件。
PROPERTY_TYPES = {
    "IFCPROPERTYSET",
    "IFCELEMENTQUANTITY",
    "IFCPROPERTYSINGLEVALUE",
    "IFCPROPERTYENUMERATEDVALUE",
    "IFCPROPERTYBOUNDEDVALUE",
    "IFCPROPERTYLISTVALUE",
    "IFCPROPERTYREFERENCEVALUE",
    "IFCQUANTITYAREA",
    "IFCQUANTITYCOUNT",
    "IFCQUANTITYLENGTH",
    "IFCQUANTITYNUMBER",
    "IFCQUANTITYTIME",
    "IFCQUANTITYVOLUME",
    "IFCQUANTITYWEIGHT",
}

# 关系实体用于把构件连接到楼层、类型、属性和材料。
RELATION_TYPES = {
    "IFCRELDEFINESBYPROPERTIES",
    "IFCRELDEFINESBYTYPE",
    "IFCRELAGGREGATES",
    "IFCRELCONTAINEDINSPATIALSTRUCTURE",
    "IFCRELASSOCIATESMATERIAL",
    "IFCRELASSOCIATESCLASSIFICATION",
    # 门/窗填充洞口、洞口被墙包围:用于把这类构件回溯到墙所在的楼层。
    "IFCRELFILLSELEMENT",
    "IFCRELVOIDSELEMENT",
}

# 单位实体用于把 IFC 原始数值转换为米。
UNIT_TYPES = {
    "IFCSIUNIT",
    "IFCCONVERSIONBASEDUNIT",
    "IFCUNITASSIGNMENT",
}

# 材料实体用于生成构件的材料名称列表。
MATERIAL_TYPES = {
    "IFCMATERIAL",
    "IFCMATERIALLAYER",
    "IFCMATERIALLAYERSET",
    "IFCMATERIALLAYERSETUSAGE",
    "IFCMATERIALPROFILE",
    "IFCMATERIALPROFILESETUSAGE",
}

# 推算视觉层数时要排除的楼层实体名模式:基础、吊顶、屋面等非居住层。
OCCUPIED_EXCLUDE_PATTERNS = (
    "foundation", "ceiling", "roof", "parapet", "top of",
    "基础", "吊顶", "天花板", "屋面", "屋顶", "天面",
)

# 常见构件类型前缀，支持 IFC4 的多种子类型。
PRODUCT_PREFIXES = (
    "IFCBEAM",
    "IFCBUILDINGELEMENT",
    "IFCCHIMNEY",
    "IFCCOLUMN",
    "IFCCOVERING",
    "IFCCURTAINWALL",
    "IFCDOOR",
    "IFCFOOTING",
    "IFCFURNISHINGELEMENT",
    "IFCFURNITURE",
    "IFCMEMBER",
    "IFCOPENINGELEMENT",
    "IFCPILE",
    "IFCPLATE",
    "IFCRAILING",
    "IFCRAMP",
    "IFCRAMPFLIGHT",
    "IFCROOF",
    "IFCSLAB",
    "IFCSTAIR",
    "IFCSTAIRFLIGHT",
    "IFCWALL",
    "IFCWINDOW",
    "IFCCABLECARRIER",
    "IFCCABLECARRIERFITTING",
    "IFCCABLECARRIERSEGMENT",
    "IFCDUCTFITTING",
    "IFCDUCTSEGMENT",
    "IFCFLOWCONTROLLER",
    "IFCFLOWFITTING",
    "IFCFLOWSEGMENT",
    "IFCFLOWTERMINAL",
    "IFCPIPEFITTING",
    "IFCPIPESEGMENT",
    "IFCAIRTERMINAL",
    "IFCBOILER",
    "IFCCHILLER",
    "IFCELECTRICAPPLIANCE",
    "IFCFAN",
    "IFCLIGHTFIXTURE",
    "IFCOUTLET",
    "IFCPROTECTIVEDEVICE",
    "IFCSANITARYTERMINAL",
    "IFCSPACEHEATER",
    "IFCSWITCHINGDEVICE",
    "IFCSYSTEMFURNITUREELEMENT",
    "IFCUNITARYEQUIPMENT",
    "IFCREINFORCINGBAR",
    "IFCREINFORCINGMESH",
)

# 几何实体不参与属性解析，但对识别 IFC 文件完整性仍有帮助。
GEOMETRY_TYPES = {
    "IFCCARTESIANPOINT",
    "IFCFACEOUTERBOUND",
    "IFCPOLYLOOP",
    "IFCFACE",
    "IFCORIENTEDEDGE",
    "IFCTRIMMEDCURVE",
    "IFCEDGECURVE",
    "IFCAXIS2PLACEMENT3D",
    "IFCLINE",
    "IFCVECTOR",
    "IFCVERTEXPOINT",
    "IFCEDGELOOP",
    "IFCADVANCEDFACE",
    "IFCPLANE",
    "IFCCIRCLE",
    "IFCSHAPEREPRESENTATION",
    "IFCCYLINDRICALSURFACE",
}

# 将常见 IFC 类型名转换为面向用户的中文标签。
TYPE_LABELS = {
    "IFCWALL": "墙",
    "IFCWALLSTANDARDCASE": "墙",
    "IFCDOOR": "门",
    "IFCWINDOW": "窗",
    "IFCSLAB": "楼板",
    "IFCBEAM": "梁",
    "IFCCOLUMN": "柱",
    "IFCMEMBER": "构件",
    "IFCPLATE": "板",
    "IFCCOVERING": "饰面",
    "IFCRAILING": "栏杆",
    "IFCSTAIR": "楼梯",
    "IFCSTAIRFLIGHT": "梯段",
    "IFCROOF": "屋顶",
    "IFCFOOTING": "基础",
    "IFCPILE": "桩",
    "IFCSPACE": "空间",
    "IFCFURNISHINGELEMENT": "家具",
    "IFCPIPESEGMENT": "管道",
    "IFCPIPEFITTING": "管件",
    "IFCDUCTSEGMENT": "风管",
    "IFCDUCTFITTING": "风管管件",
    "IFCCABLECARRIERSEGMENT": "桥架",
    "IFCFLOWTERMINAL": "末端设备",
    "IFCFLOWSEGMENT": "管线",
    "IFCREINFORCINGBAR": "钢筋",
}


@dataclass(frozen=True)
class IfcReference:
    """IFC 实体引用 `#123` 的结构化表示。"""

    id: int


@dataclass
class IfcEntity:
    """从 STEP 行解析出的 IFC 实体。"""

    id: int
    type_name: str
    params: list[Any]


# 解码 IFC 字符串中的转义、Unicode 和十六进制字符。
def _decode_ifc_string(value: str) -> str:
    value = value.replace("''", "'")

    def replace_hex(match: re.Match[str]) -> str:
        digits = match.group(1)
        try:
            return bytes.fromhex(digits).decode("utf-16-be")
        except (ValueError, UnicodeDecodeError):
            return ""

    def replace_unicode(match: re.Match[str]) -> str:
        digits = match.group(1)
        try:
            return chr(int(digits, 16))
        except ValueError:
            return ""

    value = re.sub(r"\\X2\\([0-9A-Fa-f]+)\\X0\\", replace_hex, value)
    value = re.sub(r"\\X\\([0-9A-Fa-f]{2,6})", replace_unicode, value)
    value = value.replace("\\S\\", "")
    return value.strip()


# 按顶层逗号切分参数，同时忽略字符串和嵌套括号内部的逗号。
def _split_top_level(value: str) -> list[str]:
    if not value.strip():
        return []

    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_string = False
    index = 0
    while index < len(value):
        char = value[index]
        if char == "'":
            current.append(char)
            if in_string and index + 1 < len(value) and value[index + 1] == "'":
                current.append("'")
                index += 2
                continue
            in_string = not in_string
        elif not in_string and char == "(":
            depth += 1
            current.append(char)
        elif not in_string and char == ")":
            depth -= 1
            current.append(char)
        elif not in_string and char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    parts.append("".join(current).strip())
    return parts


# 将 STEP 标量、引用、枚举、列表和内联类型转换成 Python 对象。
def _parse_value(value: str) -> Any:
    value = value.strip()
    if value == "$":
        return None
    if value == "*":
        return "*"
    if value.startswith("#") and value[1:].isdigit():
        return IfcReference(int(value[1:]))
    if value.startswith("'") and value.endswith("'"):
        return _decode_ifc_string(value[1:-1])
    if value.startswith(".") and value.endswith("."):
        return value[1:-1]
    if value.startswith("(") and value.endswith(")"):
        return [_parse_value(item) for item in _split_top_level(value[1:-1])]
    if value.startswith("IFC") and "(" in value and value.endswith(")"):
        type_name, inner = value.split("(", 1)
        parsed = _split_top_level(inner[:-1])
        if len(parsed) == 1:
            return _parse_value(parsed[0])
        return {"type": type_name, "value": [_parse_value(item) for item in parsed]}
    try:
        number = float(value)
    except ValueError:
        return value
    if math.isfinite(number) and number.is_integer():
        return int(number)
    return number


# 解析实体的完整参数列表。
def _parse_parameters(raw: str) -> list[Any]:
    return [_parse_value(item) for item in _split_top_level(raw)]


# 读取 IFC 文本并保留后续分析需要的实体，同时统计所有实体类型。
def _load_entities(path: Path) -> tuple[dict[int, IfcEntity], Counter[str], str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    entities: dict[int, IfcEntity] = {}
    type_counts: Counter[str] = Counter()
    buffer: list[str] = []

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not buffer and not stripped.startswith("#"):
            continue
        # STEP 实体可能跨行，只有遇到分号才认为一条语句结束。
        buffer.append(stripped)
        if not stripped.endswith(";"):
            continue

        statement = "".join(buffer)
        buffer = []
        match = ENTITY_PATTERN.match(statement)
        if not match:
            continue

        entity_id = int(match.group("id"))
        type_name = match.group("type").upper()
        type_counts[type_name] += 1

        # 只保留分析所需实体，避免把完整的几何定义树加载进内存。
        should_parse = (
            type_name in SPATIAL_TYPES
            or type_name in PROPERTY_TYPES
            or type_name in RELATION_TYPES
            or type_name in UNIT_TYPES
            or type_name in MATERIAL_TYPES
            or type_name.startswith(PRODUCT_PREFIXES)
        )
        if should_parse:
            entities[entity_id] = IfcEntity(
                id=entity_id,
                type_name=type_name,
                params=_parse_parameters(match.group("params")),
            )

    schema_match = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", text, re.IGNORECASE)
    schema = schema_match.group(1) if schema_match else "IFC"
    return entities, type_counts, schema


# 提取引用 ID，供属性、楼层和材料关系展开使用。
def _ref_id(value: Any) -> int | None:
    return value.id if isinstance(value, IfcReference) else None


# 提取引用列表并过滤非引用参数;单个引用也按一个元素的列表处理,
# 因为 IFC 导出的关系实体中 RelatedObjects 可能是单引用也可能是一对括号的列表。
def _ref_ids(value: Any) -> list[int]:
    if isinstance(value, IfcReference):
        return [value.id]
    if not isinstance(value, list):
        return []
    return [item.id for item in value if isinstance(item, IfcReference)]


# 读取 IFC 实体的 Name 字段，常见位置为第三个参数。
def _entity_name(entity: IfcEntity | None) -> str | None:
    if entity is None or len(entity.params) < 3:
        return None
    value = entity.params[2]
    return value if isinstance(value, str) and value else None


# 读取属性或数量的名称字段。
def _property_name(entity: IfcEntity) -> str | None:
    if not entity.params:
        return None
    value = entity.params[0]
    return value if isinstance(value, str) and value else None


# 尽量从不同材料实体结构中提取可读名称。
def _material_name(entity: IfcEntity | None) -> str | None:
    if entity is None:
        return None
    if entity.type_name in {
        "IFCMATERIAL",
        "IFCMATERIALLAYERSET",
        "IFCMATERIALPROFILESETUSAGE",
    }:
        return _property_name(entity)
    return _entity_name(entity) or _property_name(entity)


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


# 判断实体是否属于需要进入构件清单的 IFC 产品类型。
# 排除 TYPE 与 PROPERTIES 后缀:如 IFCDOORTYPE、IFCDOORLININGPROPERTIES
# 是类型/属性定义实体,不是建筑构件。
def _is_product(type_name: str) -> bool:
    if type_name.endswith(("TYPE", "PROPERTIES")):
        return False
    return type_name.startswith(PRODUCT_PREFIXES)


# 根据 IFC 类型归入建筑、结构、机电或其他类别。
def _category(type_name: str) -> str:
    lowered = type_name.lower()
    if any(token in lowered for token in ("wall", "curtainwall")):
        return "建筑-墙体"
    if "door" in lowered or "window" in lowered or "opening" in lowered:
        return "建筑-门窗洞口"
    if any(token in lowered for token in ("slab", "plate", "covering", "roof")):
        return "建筑-楼板屋面"
    if any(token in lowered for token in ("column", "beam", "member", "railing")):
        return "结构-框架构件"
    if any(token in lowered for token in ("footing", "pile", "reinforc")):
        return "结构-基础钢筋"
    if any(token in lowered for token in ("stair", "ramp")):
        return "建筑-交通构件"
    if any(token in lowered for token in ("pipe", "duct", "cablecarrier", "flow")):
        return "机电-管线"
    if any(
        token in lowered
        for token in ("airterminal", "boiler", "chiller", "light", "outlet", "fan")
    ):
        return "机电-设备"
    if "furnit" in lowered:
        return "建筑-家具"
    if "space" in lowered:
        return "空间"
    return "其他构件"


# 为未知 IFC 类型生成可读标签。
def _ifc_type_label(type_name: str) -> str:
    if type_name in TYPE_LABELS:
        return TYPE_LABELS[type_name]
    normalized = type_name.removeprefix("IFC")
    aliases = {
        "WALLSTANDARDCASE": "墙",
        "BUILDINGELEMENTPROXY": "代理构件",
        "FLOWTERMINAL": "末端设备",
        "FLOWSEGMENT": "管线",
        "FLOWFITTING": "管件",
        "FURNITURE": "家具",
        "AIRTERMINAL": "风口",
        "LIGHTFIXTURE": "灯具",
        "ELECTRICAPPLIANCE": "电气设备",
    }
    return aliases.get(normalized, normalized.replace("_", " ").title())


# 读取属性值或数量值，不同实体的字段位置略有差异。
def _extract_value(entity: IfcEntity) -> Any:
    if entity.type_name == "IFCPROPERTYSINGLEVALUE" and len(entity.params) > 2:
        return entity.params[2]
    if entity.type_name.startswith("IFCQUANTITY") and len(entity.params) > 3:
        return entity.params[3]
    if entity.type_name == "IFCPROPERTYENUMERATEDVALUE" and len(entity.params) > 2:
        return entity.params[2]
    return None


# 从楼层实体名称推算视觉上的可居住层数:排除基础/吊顶/屋面等,
# 并把名称包含其他楼层名的附属层(如 "Level 1 Living Rm." 并入 "Level 1")。
def _occupied_floor_count(storeys: list[dict[str, Any]]) -> int:
    names = [storey["name"] for storey in storeys]
    candidates = [
        name for name in names
        if not any(pattern in name.lower() for pattern in OCCUPIED_EXCLUDE_PATTERNS)
    ]
    main_floors = [
        name for name in candidates
        if not any(other != name and other in name for other in names)
    ]
    return len(main_floors)


# 模型没有楼层实体时,从配套 PDF 图纸的标高标注取视觉层数(局部导入避免循环)。
# 上传副本文件名带随机前缀(如 1cca82a6a1-rst_basic_sample_project),按后缀匹配。
def _occupied_floors_from_drawing(path: Path) -> int | None:
    from .pdf_facts import OCCUPIED_FLOORS_FROM_DRAWING

    stem = path.stem
    for key, value in OCCUPIED_FLOORS_FROM_DRAWING.items():
        if stem == key or stem.endswith("-" + key):
            return value
    return None


def _build_model_summary(
    model_id: str,
    path: Path,
    entities: dict[int, IfcEntity],
    type_counts: Counter[str],
    schema: str,
) -> dict[str, Any]:
    """把解析后的实体关系汇总为完整模型数据。"""
    project = next(
        (entity for entity in entities.values() if entity.type_name == "IFCPROJECT"),
        None,
    )
    products = {
        entity_id: entity
        for entity_id, entity in entities.items()
        if _is_product(entity.type_name)
    }

    element_to_storey: dict[int, int] = {}
    parent_by_child: dict[int, int] = {}
    type_by_element: dict[int, int] = {}
    type_names: dict[int, str] = {}
    property_sets: dict[int, dict[str, Any]] = {}
    property_owner: dict[int, list[int]] = {}
    quantities: dict[int, dict[str, float]] = {}
    material_sets: dict[int, list[str]] = defaultdict(list)
    openings_by_filler: dict[int, int] = {}
    walls_by_opening: dict[int, list[int]] = defaultdict(list)

    # 第一轮：展开构件与空间、类型、属性和材料之间的关系。
    for entity in entities.values():
        if entity.type_name == "IFCRELCONTAINEDINSPATIALSTRUCTURE":
            if len(entity.params) < 6:
                continue
            storey_id = _ref_id(entity.params[5])
            if storey_id is None:
                continue
            for object_id in _ref_ids(entity.params[4]):
                element_to_storey[object_id] = storey_id
        elif entity.type_name == "IFCRELAGGREGATES":
            if len(entity.params) < 6:
                continue
            parent_id = _ref_id(entity.params[4])
            if parent_id is None:
                continue
            for child_id in _ref_ids(entity.params[5]):
                parent_by_child[child_id] = parent_id
        elif entity.type_name == "IFCRELDEFINESBYPROPERTIES":
            if len(entity.params) < 6:
                continue
            definition_id = _ref_id(entity.params[5])
            if definition_id is None:
                continue
            for object_id in _ref_ids(entity.params[4]):
                property_owner.setdefault(definition_id, []).append(object_id)
        elif entity.type_name == "IFCRELDEFINESBYTYPE":
            if len(entity.params) < 6:
                continue
            type_id = _ref_id(entity.params[4])
            if type_id is None:
                continue
            for object_id in _ref_ids(entity.params[5]):
                type_by_element[object_id] = type_id
        elif entity.type_name == "IFCRELASSOCIATESMATERIAL":
            if len(entity.params) < 6:
                continue
            material_id = _ref_id(entity.params[5])
            if material_id is None:
                continue
            material = entities.get(material_id)
            material_name = _material_name(material) or (
                material.type_name if material else f"#{material_id}"
            )
            for object_id in _ref_ids(entity.params[4]):
                material_sets[object_id].append(material_name)
        elif entity.type_name == "IFCRELFILLSELEMENT":
            # 记录"门/窗填充了哪个洞口",楼层回溯时经洞口找到宿主墙。
            if len(entity.params) < 6:
                continue
            opening_id = _ref_id(entity.params[4])
            if opening_id is None:
                continue
            for filler_id in _ref_ids(entity.params[5]):
                openings_by_filler[filler_id] = opening_id
        elif entity.type_name == "IFCRELVOIDSELEMENT":
            # 记录"哪个洞口挖在哪堵墙上",与 FillsVoids 配合完成三级回溯。
            if len(entity.params) < 6:
                continue
            wall_id = _ref_id(entity.params[4])
            if wall_id is None:
                continue
            for opening_id in _ref_ids(entity.params[5]):
                walls_by_opening[opening_id].append(wall_id)

    # 第二轮：读取属性集和数量集，并挂到对应构件。
    for entity in entities.values():
        if entity.type_name in {"IFCPROPERTYSET", "IFCELEMENTQUANTITY"}:
            props: dict[str, Any] = {}
            quantity_props: dict[str, float] = {}
            properties_index = 4 if entity.type_name == "IFCPROPERTYSET" else 5
            properties_value = (
                entity.params[properties_index]
                if len(entity.params) > properties_index
                else None
            )
            for prop_id in _ref_ids(properties_value):
                prop = entities.get(prop_id)
                if prop is None:
                    continue
                prop_name = _property_name(prop)
                if not prop_name:
                    continue
                value = _extract_value(prop)
                if prop.type_name.startswith("IFCQUANTITY"):
                    number = _number(value)
                    if number is not None:
                        quantity_props[prop_name] = round(number, 4)
                else:
                    props[prop_name] = value
            for owner_id in property_owner.get(entity.id, []):
                if quantity_props:
                    quantities.setdefault(owner_id, {}).update(quantity_props)
                if props:
                    property_sets.setdefault(owner_id, {}).update(props)
        elif entity.type_name.endswith("TYPE") and len(entity.params) > 2:
            name = entity.params[2]
            if isinstance(name, str):
                type_names[entity.id] = name

    storey_entities = {
        entity.id: entity
        for entity in entities.values()
        if entity.type_name == "IFCBUILDINGSTOREY"
    }
    storey_counts: Counter[int] = Counter()

    def resolve_storey(element_id: int, _seen: set[int] | None = None) -> int | None:
        """三级回溯构件楼层:直接空间包含 → 聚合父链 → 门/窗所填充洞口所在的墙。"""
        seen = _seen if _seen is not None else set()
        if element_id in seen:
            return None
        seen.add(element_id)

        entity = entities.get(element_id)
        if entity is not None and entity.type_name == "IFCBUILDINGSTOREY":
            return element_id
        direct = element_to_storey.get(element_id)
        if direct is not None:
            return direct

        # 挂在墙洞口的门/窗(或洞口本身):先找宿主墙,再解析墙的楼层。
        opening_id = openings_by_filler.get(element_id)
        if opening_id is None and entity is not None and entity.type_name == "IFCOPENINGELEMENT":
            opening_id = element_id
        if opening_id is not None:
            for wall_id in walls_by_opening.get(opening_id, []):
                storey_id = resolve_storey(wall_id, seen)
                if storey_id is not None:
                    return storey_id

        # 幕墙门等聚合在父构件(如 IfcCurtainWall)下的情况,沿父链上溯。
        parent_id = parent_by_child.get(element_id)
        if parent_id is not None:
            return resolve_storey(parent_id, seen)
        return None

    for element_id in products:
        storey_id = resolve_storey(element_id)
        if storey_id is not None:
            storey_counts[storey_id] += 1

    storeys: list[dict[str, Any]] = []
    for storey_id, storey in storey_entities.items():
        elevation = None
        if len(storey.params) > 9:
            elevation = _number(storey.params[9])
        storeys.append(
            {
                "id": storey_id,
                "global_id": storey.params[0] if storey.params else None,
                "name": _entity_name(storey) or f"楼层 #{storey_id}",
                "elevation": elevation,
                "element_count": storey_counts.get(storey_id, 0),
            }
        )
    storeys.sort(
        key=lambda item: (
            item["elevation"] if item["elevation"] is not None else float("-inf"),
            item["name"],
        )
    )

    element_count_by_type = Counter(
        entity.type_name for entity in products.values()
    )
    category_counts = Counter(
        _category(entity.type_name) for entity in products.values()
    )
    element_counts = [
        {
            "type": type_name,
            "label": _ifc_type_label(type_name),
            "category": _category(type_name),
            "count": count,
        }
        for type_name, count in element_count_by_type.most_common()
    ]

    property_name_counts: Counter[str] = Counter()
    numeric_quantity_totals: dict[str, float] = defaultdict(float)
    # 汇总属性出现频率和可求和的数值型数量。
    for values in property_sets.values():
        property_name_counts.update(values.keys())
    for values in quantities.values():
        for name, value in values.items():
            numeric_quantity_totals[name] += value

    unit_assignment = next(
        (
            entity
            for entity in entities.values()
            if entity.type_name == "IFCUNITASSIGNMENT"
        ),
        None,
    )
    unit_ids = _ref_ids(unit_assignment.params[0]) if unit_assignment else []
    units: dict[str, str] = {}
    length_scale_to_metre = 1.0
    for unit_id in unit_ids:
        unit = entities.get(unit_id)
        if unit is None or unit.type_name != "IFCSIUNIT" or len(unit.params) < 4:
            continue
        unit_type = unit.params[1]
        prefix = unit.params[2]
        name = unit.params[3]
        if isinstance(unit_type, str) and isinstance(name, str):
            units[unit_type] = f"{prefix or ''} {name}".strip().title()
            if unit_type == "LENGTHUNIT":
                # IFC 可以声明毫米、厘米、米等长度单位，统一换算为米。
                prefix_scales = {
                    "EXA": 1e18,
                    "PETA": 1e15,
                    "TERA": 1e12,
                    "GIGA": 1e9,
                    "MEGA": 1e6,
                    "KILO": 1e3,
                    "HECTO": 1e2,
                    "DECA": 1e1,
                    "DECI": 1e-1,
                    "CENTI": 1e-2,
                    "MILLI": 1e-3,
                    "MICRO": 1e-6,
                    "NANO": 1e-9,
                }
                length_scale_to_metre = prefix_scales.get(prefix, 1.0)

    for storey in storeys:
        elevation = storey.get("elevation")
        storey["elevation_m"] = (
            round(elevation * length_scale_to_metre, 5)
            if elevation is not None
            else None
        )

    common_elements: list[dict[str, Any]] = []
    # 输出前端表格和问答检索使用的扁平构件结构。
    for element_id, entity in products.items():
        storey_id = resolve_storey(element_id)
        type_id = type_by_element.get(element_id)
        common_elements.append(
            {
                "id": element_id,
                "global_id": entity.params[0] if entity.params else None,
                "ifc_type": entity.type_name,
                "label": _ifc_type_label(entity.type_name),
                "category": _category(entity.type_name),
                "name": (
                    entity.params[2]
                    if len(entity.params) > 2 and isinstance(entity.params[2], str)
                    else ""
                ),
                "object_type": (
                    entity.params[4]
                    if len(entity.params) > 4 and isinstance(entity.params[4], str)
                    else ""
                ),
                "type_name": type_names.get(type_id, "") if type_id else "",
                "storey_id": storey_id,
                # 直接空间包含的楼层(未做洞口/聚合回溯),问答规则按此口径统计。
                "direct_storey_id": element_to_storey.get(element_id),
                "storey": (
                    _entity_name(storey_entities.get(storey_id))
                    if storey_id in storey_entities
                    else ""
                ),
                "materials": material_sets.get(element_id, []),
                "properties": property_sets.get(element_id, {}),
                "quantities": quantities.get(element_id, {}),
            }
        )
    common_elements.sort(key=lambda item: (item["category"], item["ifc_type"], item["id"]))

    # 汇总所有 IfcMaterial 实体,供问答"有多少材料"等规则使用。
    materials: list[dict[str, Any]] = []
    for entity in entities.values():
        if entity.type_name == "IFCMATERIAL":
            materials.append(
                {"id": entity.id, "name": _property_name(entity) or f"材料 #{entity.id}"}
            )
    materials.sort(key=lambda item: item["id"])

    file_stat = path.stat()
    return {
        "id": model_id,
        "file_name": path.name,
        "file_size": file_stat.st_size,
        "source_mtime_ns": file_stat.st_mtime_ns,
        "modified_at": datetime.fromtimestamp(
            file_stat.st_mtime, tz=timezone.utc
        ).isoformat(),
        "format": "IFC",
        "schema": schema,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "checksum": _file_checksum(path),
        "project": {
            "global_id": project.params[0] if project and project.params else None,
            "name": _entity_name(project) or path.stem,
            "description": (
                project.params[3]
                if project and len(project.params) > 3
                else None
            ),
            "long_name": (
                project.params[5]
                if project and len(project.params) > 5
                else None
            ),
        },
        "statistics": {
            "entity_count": sum(type_counts.values()),
            "element_count": len(products),
            "storey_count": len(storey_entities),
            "property_set_count": sum(
                1 for entity in entities.values() if entity.type_name == "IFCPROPERTYSET"
            ),
            "quantity_count": sum(
                1
                for entity in entities.values()
                if entity.type_name.startswith("IFCQUANTITY")
            ),
        },
        "storeys": storeys,
        "element_counts": element_counts,
        "category_counts": [
            {"name": name, "count": count}
            for name, count in category_counts.most_common()
        ],
        "top_entity_types": [
            {"type": name, "count": count}
            for name, count in type_counts.most_common(25)
        ],
        "units": units,
        "length_scale_to_metre": length_scale_to_metre,
        "materials": materials,
        # 视觉层数:有楼层实体时按名称推算;没有时查图纸数据线(见 pdf_facts)。
        "occupied_floors": (
            _occupied_floor_count(storeys) if storeys
            else _occupied_floors_from_drawing(path)
        ),
        "top_properties": [
            {"name": name, "count": count}
            for name, count in property_name_counts.most_common(30)
        ],
        "quantity_totals": {
            name: round(value, 3)
            for name, value in sorted(numeric_quantity_totals.items())[:40]
        },
        "elements": common_elements,
        # 解析格式版本:结构变更后递增,旧的磁盘缓存自动失效重建。
        "cache_format": 5,
    }


# 计算文件 SHA-256，用于判断磁盘缓存是否仍然有效。
def _file_checksum(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


# 解析 IFC 文件并返回可直接缓存和序列化的模型字典。
def parse_ifc(path: Path, model_id: str) -> dict[str, Any]:
    if path.suffix.lower() != ".ifc":
        raise ValueError("Only IFC files can be parsed directly.")

    entities, type_counts, schema = _load_entities(path)
    if not type_counts:
        raise ValueError("The file does not contain readable IFC STEP entities.")
    return _build_model_summary(model_id, path, entities, type_counts, schema)


# 把问题切成检索词元:中文按单字+双字 n-gram,英文按单词,兼顾中英混合问题。
# 纯按空格分词时中文整句是一个 token,永远匹配不上构件。
def _query_tokens(question: str) -> list[str]:
    tokens: list[str] = []
    for segment in re.findall(r"[一-鿿]+", question):
        if len(segment) == 1:
            tokens.append(segment)
        else:
            tokens.extend(segment[i : i + 2] for i in range(len(segment) - 1))
    tokens.extend(word for word in re.findall(r"[a-z0-9_]+", question.lower()))
    return list(dict.fromkeys(tokens))


# 判断构件是否满足名称搜索、类型和楼层过滤条件。
def element_matches(
    element: dict[str, Any],
    query: str,
    element_type: str | None = None,
    storey_id: int | None = None,
) -> bool:
    if element_type and element["ifc_type"] != element_type:
        return False
    if storey_id is not None and element.get("storey_id") != storey_id:
        return False
    if not query:
        return True
    haystack = " ".join(
        str(value)
        for value in (
            element.get("name"),
            element.get("global_id"),
            element.get("ifc_type"),
            element.get("label"),
            element.get("category"),
            element.get("object_type"),
            element.get("type_name"),
            element.get("storey"),
            " ".join(element.get("materials", [])),
        )
    ).lower()
    return all(token in haystack for token in _query_tokens(query))


# 根据问题中的词元对构件进行简单相关性排序，供 AI 上下文引用。
def search_elements(
    elements: Iterable[dict[str, Any]],
    question: str,
    limit: int = 24,
) -> list[dict[str, Any]]:
    tokens = _query_tokens(question)
    if not tokens:
        return []

    scored: list[tuple[int, dict[str, Any]]] = []
    for element in elements:
        searchable = " ".join(
            str(element.get(key, ""))
            for key in ("name", "global_id", "ifc_type", "label", "category", "storey")
        ).lower()
        score = sum(3 if token in (element.get("name") or "").lower() else 1 for token in tokens if token in searchable)
        if score:
            scored.append((score, element))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    return [element for _, element in scored[:limit]]


# 使用紧凑 JSON 保存模型缓存，减少磁盘占用。
def model_to_cache_json(model: dict[str, Any]) -> str:
    return json.dumps(model, ensure_ascii=False, separators=(",", ":"))
