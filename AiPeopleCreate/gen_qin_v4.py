"""阶段 5：秦未晞 V4 数据生成（历史兼容壳，2026-08-08 多角色接线改造）。

项目已切换：白未晞（baiweixi）为当前 P0 女主角，秦未晞（qinweixi）为历史角色
（正典迁至 历史与调研文档/历史角色/秦未晞/）。本文件保留为历史兼容壳：
  - `python gen_qin_v4.py` 等价 `python gen_v4.py --profile qinweixi`
  - 保留历史符号导出（PROFILES_ROOT / QWX_PACKAGE_SET / ROOT / FREEZE02_CONTRACT /
    qin_style_resolver），供历史工具（batch_experiment / re_export / b_phase_sample_check）
    与旧测试继续 import；qin_style_resolver 已改为通用 resolver 的秦历史包实例
    （不再保留 legacy 双实现，2026-08-08 用户拍板）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gen_v4 import (  # noqa: E402  (符号转发：常量与入口)
    DEFAULT_FREEZE02_CONTRACT,
    PROFILES_ROOT,
    ROOT,
    build_package_set,
    main,
)
from data_gen_v4.adapters.modes.style import make_style_resolver  # noqa: E402

# 秦未晞历史正典（2026-08-08 迁出现行正典：唯一副本在
# F:\AiPeople\历史与调研文档\历史角色\秦未晞\，本仓库副本供历史包/兼容壳编译对照）
QWX_SOURCES = ROOT / "历史与调研文档" / "历史角色" / "秦未晞"
# FREEZE-02 冻结合同（AI 程序侧只读引用，P0-5：未冻结 mode 在生成前被 AdmissionBlocked）
FREEZE02_CONTRACT = DEFAULT_FREEZE02_CONTRACT
# 秦未晞历史包引用（与 gen_v4.build_package_set 构造结果逐字一致，保留为历史符号）
QWX_PACKAGE_SET = build_package_set(PROFILES_ROOT, "qinweixi")

# 秦未晞 style resolver（历史兼容符号）：由通用 make_style_resolver 按秦历史包
# 构造，输出与旧 legacy 实现一致（秦式 voice 字段逐字兼容，见 style.py render_style）
_QIN_PROFILE = yaml.safe_load(
    (PROFILES_ROOT / "qinweixi" / "profile.yaml").read_text(encoding="utf-8")
)
qin_style_resolver = make_style_resolver(_QIN_PROFILE, ROOT)


if __name__ == "__main__":
    main()
