# -*- coding: utf-8 -*-
"""PDF 图纸数据线:从 rac_basic_sample_project.pdf 人工提取的事实数据。

与 IFC 不同:图纸数据没有 GlobalId。
使用规则:图纸数据只做两件事——①补充 IFC 里没有的数据(如房间);②与 IFC 交叉验证。
答案必须标注来源;证据(GlobalId)永远引用 IFC 侧。

注意:这些数字只对 rac 数据包的图纸有效,换模型后图纸类规则不适用。
"""

# 楼层标高标注:提取自 PDF 第 4-6 页(立面图/剖面图标高标注)
PDF_ELEVATIONS = {
    "Foundation": {"elevation": -800, "source": "PDF p4-6 立面/剖面标高标注"},
    "Level 1 Living Rm.": {"elevation": -550, "source": "PDF p4-6 立面/剖面标高标注"},
    "Level 1": {"elevation": 0, "source": "PDF p4-6 立面/剖面标高标注"},
    "Ceiling": {"elevation": 2700, "source": "PDF p4-6 立面/剖面标高标注"},
    "Level 2": {"elevation": 3000, "source": "PDF p4-6 立面/剖面标高标注"},
    "Roof Line": {"elevation": 6000, "source": "PDF p4-6 立面/剖面标高标注"},
}

# 房间表:提取自 PDF 第 3 页(A102 平面图房间标签);IFC 里没有房间实体
ROOMS = {
    "101": {"name": "Kitchen & Dining", "area": "23 m²"},
    "105": {"name": "Hall", "area": "70 m²"},
    "106": {"name": "Living", "area": None},
    "201": {"name": "Entry Hall", "area": None},
    "202": {"name": "Bedroom", "area": "30 m²"},
    "206": {"name": "Master Bedroom", "area": "27 m²"},
}

# 无楼层实体的模型的视觉层数:提取自 rst_basic_sample_project.pdf 立面图标高标注
# (Level 1 = 0、Level 2 = 3000、Roof Level = 9000,可居住楼层 2 层)。
# 键为 IFC 文件名去掉 .ifc 后缀。
OCCUPIED_FLOORS_FROM_DRAWING = {
    "rst_basic_sample_project": 2,
}
