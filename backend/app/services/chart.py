"""可视化图表配置业务规则：状态流转、字段校验与筛选口径都收在这里。

维度绑定相关约定：
- 图表通过 关联维度 引用维度层级编号，绑定维度版本 记录各编号绑定时的版本；
- 绑定状态 不落库，读取时对照维度树当前版本实时推导（与维度树同源）：
  任一关联维度的当前版本与绑定版本不一致 → 待重新绑定；
- 待重新绑定的图表仍按绑定的历史版本取数，重新绑定后切到当前版本。
"""
from __future__ import annotations

import zlib
from typing import Any

from app.services.dimension import current_version_map, find_level
from app.services.dimension_package import parse_dimension_codes
from app.store import store

MODULE = "chart"
REQUIRED_FIELDS = ["图表编号", "图表名称", "图表类型"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]
EXTRA_ACTIONS = ["重新绑定"]


def normalize_binding(binding: Any, codes: list[str]) -> dict[str, str]:
    """绑定维度版本统一成 {层级编号: 版本}；兼容历史遗留的单版本字符串写法。"""
    if isinstance(binding, dict):
        return {str(code): str(version) for code, version in binding.items()}
    text = str(binding or "").strip()
    return {code: text for code in codes} if text else {}


def bind_status(row: dict[str, Any]) -> tuple[str, str]:
    """对照维度树当前版本推导绑定状态，返回 (状态, 绑定版本说明)。"""
    codes = parse_dimension_codes(row.get("关联维度"))
    if not codes:
        return "未涉及维度", "—"
    binding = normalize_binding(row.get("绑定维度版本"), codes)
    current = current_version_map()
    stale = any(current.get(code) != binding.get(code) for code in codes)
    display = "、".join(f"{code}@{binding.get(code) or '未绑定'}" for code in codes)
    return ("待重新绑定" if stale else "已绑定"), display


def with_bind_info(row: dict[str, Any]) -> dict[str, Any]:
    status, display = bind_status(row)
    return {**row, "绑定状态": status, "绑定版本说明": display}


class ChartService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("图表编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [with_bind_info(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return with_bind_info(row) if row else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["关联维度"] = str(values.get("关联维度") or "")
        entry["绑定维度版本"] = values.get("绑定维度版本") or {}
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"图表配置 {entry_id} 不存在或已归档"
        if action == "重新绑定":
            return self._rebind(entry)
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于可视化图表配置可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"图表配置已{action}"

    def _rebind(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """把绑定版本对齐到维度树当前版本，对齐后绑定状态回到 已绑定。"""
        codes = parse_dimension_codes(entry.get("关联维度"))
        if not codes:
            return None, "该图表未关联分析维度，无需重新绑定"
        current = current_version_map()
        missing = [code for code in codes if code not in current]
        if missing:
            return None, f"维度 {'、'.join(missing)} 在维度树中不存在，无法重新绑定"
        entry["绑定维度版本"] = {code: current[code] for code in codes}
        return with_bind_info(entry), "图表配置已重新绑定到当前维度树版本"

    def query_entry(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """按图表绑定的维度版本取数：待重新绑定期间仍返回历史层级，保证可取数。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"图表配置 {entry_id} 不存在或已归档"
        codes = parse_dimension_codes(entry.get("关联维度"))
        binding = normalize_binding(entry.get("绑定维度版本"), codes)
        status, display = bind_status(entry)
        levels: list[dict[str, Any]] = []
        for code in codes:
            level = find_level(code, binding.get(code))
            if level:
                levels.append({
                    "层级编号": level["层级编号"],
                    "层级名称": level["层级名称"],
                    "父级编号": level["父级编号"],
                    "顺序": level["顺序"],
                    "版本": level["版本"],
                })
            else:
                levels.append({"层级编号": code, "版本": binding.get(code) or "", "缺失": True})
        data_rows = []
        for level in levels:
            if level.get("缺失"):
                continue
            for seq in (1, 2):
                digest = zlib.crc32(f"{level['层级编号']}#{level['版本']}#{seq}".encode("utf-8"))
                data_rows.append({
                    "层级编号": level["层级编号"],
                    "维度值": f"{level['层级名称']}样例{seq}",
                    "指标值": digest % 900 + 100,
                })
        result = {
            "ok": True,
            "图表编号": entry.get("图表编号"),
            "绑定状态": status,
            "查询版本": display,
            "层级": levels,
            "数据行": data_rows,
            "message": f"按绑定版本（{display}）取数成功，返回 {len(data_rows)} 行",
        }
        return result, ""
