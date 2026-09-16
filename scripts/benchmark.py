# -*- coding: utf-8 -*-
"""基准测试:用 22 道人工核对过标准答案的问题考规则引擎。

输出:
  1) 答案正确率(引擎返回值 vs 标准答案)
  2) 证据引用合规率(应当引用 GlobalId 的答案是否真的附上了证据)
并生成项目根目录的 测试报告.md。

用法(在项目根目录):
    backend/.venv/Scripts/python.exe scripts/benchmark.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.model_service import model_service  # noqa: E402
from app.rules import RuleEngine  # noqa: E402


def get_model(file_name: str):
    """按文件名找到注册模型并返回解析结果。"""
    for info in model_service.list_models():
        if info["file_name"] == file_name and info["parseable"]:
            return model_service.get_model(info["id"])
    raise SystemExit(f"未在 MySource 中找到可解析的模型: {file_name}")


# (题目, 标准答案, 是否应当引用 GlobalId 证据)
CASES = [
    ("这栋楼一共有几扇门？", 16, True),
    ("一共有几扇窗？", 17, True),
    ("一共有多少面墙？", 47, True),
    ("一共有多少块楼板？", 12, True),
    ("模型里有多少种材料？", 104, False),  # 材料无 GlobalId，不计入证据合规率
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


def run_benchmark(model) -> tuple[list, int, int, int, int]:
    """在指定模型上跑全部基准题,返回 (明细行, 题数, 答对数, 证据合规数, 证据应引数)。"""
    engine = RuleEngine(model)
    rows = []
    correct = cite_ok = cite_total = 0
    for q, exp, needs_ev in CASES:
        r = engine.ask(q)
        got = None if r is None else r["value"]
        ok = got == exp
        if ok:
            correct += 1
        ev_ok = bool(r and len(r["evidence"]) > 0)
        if needs_ev:
            cite_total += 1
            if ev_ok:
                cite_ok += 1
        rows.append((q, r["answer"] if r else "(未命中规则)", str(exp), str(got),
                     "OK" if ok else "FAIL", ev_ok, len(r["evidence"]) if r else 0))
    return rows, len(CASES), correct, cite_ok, cite_total


def main() -> None:
    model = get_model("rac_basic_sample_project.ifc")
    rows, n, correct, cite_ok, cite_total = run_benchmark(model)
    acc = correct / n * 100
    cite = cite_ok / cite_total * 100 if cite_total else 100

    print("== 明细 ==")
    for i, (q, a, e, g, mark, ev, evn) in enumerate(rows, 1):
        print(f"{i:2d}. [{mark}] {q}")
        print(f"    答: {a}")
        if mark == "FAIL":
            print(f"    期望: {e}  实际返回: {g}")
        print(f"    证据数: {evn}")

    print(f"\n== 汇总 ==")
    print(f"答案正确率: {correct}/{n} = {acc:.1f}%")
    print(f"证据引用合规率: {cite_ok}/{cite_total} = {cite:.1f}%")

    report_path = PROJECT_ROOT / "测试报告.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 基准测试报告\n\n")
        f.write(f"> 模型: {model['file_name']}({model['schema']})\n\n")
        f.write("## 汇总\n\n")
        f.write(f"- 测试题目数:{n}\n")
        f.write(f"- 答案正确率:**{correct}/{n} = {acc:.1f}%**\n")
        f.write(f"- 证据引用合规率:**{cite_ok}/{cite_total} = {cite:.1f}%**\n")
        f.write("\n## 明细\n\n")
        f.write("| # | 问题 | 引擎答案 | 标准答案 | 结果 | 证据数 |\n")
        f.write("|---|---|---|---|---|---|\n")
        for i, (q, a, e, g, mark, ev, evn) in enumerate(rows, 1):
            a_ = a.replace("|", "\\|")
            f.write(f"| {i} | {q} | {a_} | {e} | {mark} | {evn} |\n")
        f.write("\n## 指标口径\n\n")
        f.write("- 正确率 = 引擎返回值与人工核对的标准答案一致的比例\n")
        f.write("- 证据引用合规率 = 应当引用 GlobalId 的答案中,实际附上证据的比例\n")
        f.write("- 材料(#5)不是构件类型、房间(#22)无 IFC 实体,均无 GlobalId 可引,不计入合规率分母\n")
        f.write("- 所有答案由规则引擎运行时从 IFC 现算,程序中无写死答案\n")
    print(f"报告已写入 {report_path}")
    if correct != n or cite_ok != cite_total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
