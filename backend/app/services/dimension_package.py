"""分析维度定义包：上传校验、版本裁决、断点续接与回执。

冲突裁决规则（本模块的设计约定，全局唯一依据）：
- 包内层级版本高于维度树同编号当前版本 → 以包为准，覆盖接入（旧版本行保留供历史查询）；
- 包内层级版本低于或等于当前版本 → 以现有维度树为准，保留现有层级；
- 同版本重复接入 → 幂等跳过，不产生重复层级。

其他约定：
- 校验不通过的包整包拒绝，不写入任何层级，也不允许进入接入流程；
- 接入按拓扑序逐层推进，回执记录每层的接入状态，中断后从最后一个已接入层级继续；
- 同一文件（内容指纹相同）重复上传不生成新包，重复接入不产生重复层级。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from app.services.dimension import current_version_map
from app.store import store

MODULE = "dimension_package"
LEVEL_MODULE = "dimension_level"
CHART_MODULE = "chart"

PACKAGE_ALIASES = {
    "包编号": ["包编号", "package_code", "packageCode", "pkgCode"],
    "包版本": ["包版本", "package_version", "packageVersion", "pkgVersion"],
    "层级": ["层级", "levels", "hierarchies", "items"],
}
LEVEL_ALIASES = {
    "层级编号": ["层级编号", "code", "level_code", "levelCode", "dim_code"],
    "层级名称": ["层级名称", "name", "level_name", "levelName"],
    "父级编号": ["父级编号", "parent", "parent_code", "parentCode"],
    "顺序": ["顺序", "order", "seq", "sort"],
    "版本": ["版本", "version"],
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _pick(mapping: dict[str, Any], aliases: list[str], default: Any = "") -> Any:
    for key in aliases:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return default


def parse_version(value: Any) -> tuple[int, ...] | None:
    """把 1.0.0 / v2.1 这类版本串解析成可比较的元组；解析不了返回 None。"""
    text = str(value or "").strip().lstrip("vV")
    if not text:
        return None
    parts = text.split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def compare_versions(left: Any, right: Any) -> int:
    """比较两个版本串：>0 表示 left 更新，0 表示相同，<0 表示 left 更旧。"""
    va, vb = parse_version(left), parse_version(right)
    if va is None or vb is None:
        return 0
    length = max(len(va), len(vb))
    va += (0,) * (length - len(va))
    vb += (0,) * (length - len(vb))
    return (va > vb) - (va < vb)


def parse_dimension_codes(text: Any) -> list[str]:
    """把图表上的关联维度串（逗号/顿号/分号/空白分隔）拆成编号列表。"""
    return [part for part in re.split(r"[,，、;；\s]+", str(text or "").strip()) if part]


class DimensionPackageService:
    # ---------- 读取 ----------

    def list_packages(self, *, page: int = 1, size: int = 20) -> tuple[list[dict[str, Any]], int]:
        rows = sorted(store.rows(MODULE), key=lambda row: int(row.get("id", 0)), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_package(self, pkg_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, pkg_id)

    # ---------- 上传与校验 ----------

    def upload(self, *, filename: str, content: Any) -> tuple[dict[str, Any] | None, bool, list[str]]:
        """上传即校验。返回 (包记录, 是否重复上传, 校验错误列表)。"""
        if isinstance(content, (dict, list)):
            text = json.dumps(content, ensure_ascii=False, sort_keys=True)
        else:
            text = str(content or "")
        if not text.strip():
            return None, False, ["文件内容为空"]
        fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
        for pkg in store.rows(MODULE):
            if pkg.get("内容指纹") == fingerprint:
                return pkg, True, []

        try:
            raw = json.loads(text)
            if not isinstance(raw, dict):
                raise ValueError("定义包必须是 JSON 对象")
        except (json.JSONDecodeError, ValueError) as exc:
            meta, levels, errors = {"包编号": "", "包版本": ""}, [], [f"文件解析失败：{exc}"]
        else:
            meta, levels, errors = self._normalize(raw)
            errors.extend(self._validate(meta, levels))

        rows = store.rows(MODULE)
        pkg: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "status": "已记录",
            "pending": not errors,
            "abnormal": bool(errors),
            "包编号": meta["包编号"] or "（未识别）",
            "文件名": filename or "未命名文件",
            "包版本": meta["包版本"] or "—",
            "内容指纹": fingerprint,
            "层级数": len(levels),
            "校验状态": "校验失败" if errors else "校验通过",
            "校验信息": errors,
            "接入状态": "未接入",
            "已接入层级数": 0,
            "受影响图表": [],
            "上传时间": _now(),
            "接入时间": "",
            "回执": [self._receipt_item(level) for level in (levels if errors else self._topo_order(levels))],
        }
        rows.append(pkg)
        return pkg, False, errors

    def _normalize(self, raw: dict[str, Any]) -> tuple[dict[str, str], list[dict[str, Any]], list[str]]:
        errors: list[str] = []
        meta = {
            "包编号": str(_pick(raw, PACKAGE_ALIASES["包编号"])).strip(),
            "包版本": str(_pick(raw, PACKAGE_ALIASES["包版本"])).strip(),
        }
        raw_levels = _pick(raw, PACKAGE_ALIASES["层级"], default=[])
        if not isinstance(raw_levels, list):
            errors.append("层级清单必须是数组")
            raw_levels = []
        levels: list[dict[str, Any]] = []
        for index, item in enumerate(raw_levels):
            if not isinstance(item, dict):
                errors.append(f"第 {index + 1} 个层级不是对象")
                continue
            levels.append({
                "层级编号": str(_pick(item, LEVEL_ALIASES["层级编号"])).strip(),
                "层级名称": str(_pick(item, LEVEL_ALIASES["层级名称"])).strip(),
                "父级编号": str(_pick(item, LEVEL_ALIASES["父级编号"])).strip(),
                "顺序": _pick(item, LEVEL_ALIASES["顺序"], default=index + 1),
                "版本": str(_pick(item, LEVEL_ALIASES["版本"]) or meta["包版本"]).strip(),
            })
        return meta, levels, errors

    def _validate(self, meta: dict[str, str], levels: list[dict[str, Any]]) -> list[str]:
        errors: list[str] = []
        if not meta["包编号"]:
            errors.append("缺少包编号")
        if parse_version(meta["包版本"]) is None:
            errors.append(f"包版本「{meta['包版本'] or '空'}」格式不正确，应为 数字.数字[.数字]")
        if not levels:
            errors.append("层级清单为空")

        by_code: dict[str, dict[str, Any]] = {}
        for index, level in enumerate(levels):
            code = level["层级编号"]
            label = code or f"第 {index + 1} 个层级"
            if not code:
                errors.append(f"{label}缺少层级编号")
            elif code in by_code:
                errors.append(f"层级编号 {code} 在包内重复")
            else:
                by_code[code] = level
            if not level["层级名称"]:
                errors.append(f"{label}缺少层级名称")
            if parse_version(level["版本"]) is None:
                errors.append(f"层级 {label} 的版本「{level['版本'] or '空'}」格式不正确")
            try:
                order = int(level["顺序"])
                if order < 1:
                    raise ValueError
                level["顺序"] = order
            except (TypeError, ValueError):
                errors.append(f"层级 {label} 的顺序「{level['顺序']}」不是正整数")

        tree_codes = set(current_version_map())
        for level in levels:
            code, parent = level["层级编号"], level["父级编号"]
            if not parent:
                continue
            if parent == code:
                errors.append(f"层级 {code} 的父级不能是自身")
            elif parent not in by_code and parent not in tree_codes:
                errors.append(f"层级 {code} 的父级 {parent} 缺失（既不在包内，也不在现有维度树）")

        sibling_order: dict[str, dict[int, str]] = {}
        for level in levels:
            if not isinstance(level["顺序"], int):
                continue
            siblings = sibling_order.setdefault(level["父级编号"], {})
            if level["顺序"] in siblings:
                errors.append(
                    f"父级「{level['父级编号'] or '根'}」下顺序 {level['顺序']} 被 "
                    f"{siblings[level['顺序']]} 与 {level['层级编号']} 同时占用（先后关系冲突）"
                )
            else:
                siblings[level["顺序"]] = level["层级编号"]

        for level in levels:
            chain: set[str] = set()
            node: dict[str, Any] | None = level
            while node and node["父级编号"]:
                parent = node["父级编号"]
                if parent in chain:
                    errors.append(f"层级 {level['层级编号']} 的父子关系存在循环")
                    break
                chain.add(parent)
                node = by_code.get(parent)
        return errors

    @staticmethod
    def _receipt_item(level: dict[str, Any]) -> dict[str, Any]:
        return {
            "层级编号": level["层级编号"],
            "层级名称": level["层级名称"],
            "父级编号": level["父级编号"],
            "顺序": level["顺序"],
            "版本": level["版本"],
            "裁决": "待裁决",
            "接入": "待接入",
        }

    @staticmethod
    def _topo_order(levels: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """父级先于子级、同级按顺序号排列；校验通过后调用，保证无环。"""
        by_code = {level["层级编号"]: level for level in levels}
        ordered: list[dict[str, Any]] = []
        state: dict[str, bool] = {}

        def visit(code: str) -> None:
            if state.get(code):
                return
            state[code] = True
            parent = by_code[code]["父级编号"]
            if parent in by_code:
                visit(parent)
            ordered.append(by_code[code])

        for level in sorted(levels, key=lambda item: (item["父级编号"] or "", item["顺序"], item["层级编号"])):
            visit(level["层级编号"])
        return ordered

    # ---------- 接入与断点续接 ----------

    def import_package(self, pkg_id: int, *, stop_after: int | None = None) -> tuple[dict[str, Any] | None, bool, str]:
        """按回执顺序逐层接入；stop_after 用于演练接入中断，中断后可调 resume 续接。"""
        pkg = store.find(MODULE, pkg_id)
        if pkg is None:
            return None, False, f"定义包 {pkg_id} 不存在"
        if pkg.get("校验状态") != "校验通过":
            return pkg, False, "定义包校验未通过，不能接入；请修正包内容后重新上传"
        if pkg.get("接入状态") == "已接入":
            return pkg, True, "该定义包已完成接入，重复接入不会产生重复层级"

        receipt: list[dict[str, Any]] = pkg.get("回执", [])
        stats = {"新增": 0, "覆盖": 0, "保留": 0, "跳过": 0}
        applied = 0
        for item in receipt:
            if item["接入"] == "已接入":
                continue
            verdict = self._apply_level(item, pkg)
            item["裁决"] = verdict
            item["接入"] = "已接入"
            applied += 1
            stats[verdict.split("（")[0]] += 1
            pkg["已接入层级数"] = sum(1 for entry in receipt if entry["接入"] == "已接入")
            pkg["接入状态"] = "接入中"
            self._refresh_affected_charts(pkg)
            if stop_after is not None and applied >= stop_after:
                pkg["接入状态"] = "接入中断"
                return pkg, False, (
                    f"接入中断：已完成 {pkg['已接入层级数']}/{len(receipt)} 层，"
                    "已校验的层级保留生效，可从断点继续接入"
                )

        pkg["接入状态"] = "已接入"
        pkg["接入时间"] = _now()
        pkg["pending"] = False
        return pkg, True, (
            f"接入完成：新增 {stats['新增']} 层、覆盖 {stats['覆盖']} 层、"
            f"保留现有 {stats['保留']} 层、跳过 {stats['跳过']} 层；"
            f"受影响图表 {len(pkg['受影响图表'])} 个已标记待重新绑定"
        )

    def resume_package(self, pkg_id: int) -> tuple[dict[str, Any] | None, bool, str]:
        """从已校验的最后一个层级继续接入；已接入的包重复调用是幂等的。"""
        pkg = store.find(MODULE, pkg_id)
        if pkg is None:
            return None, False, f"定义包 {pkg_id} 不存在"
        status = pkg.get("接入状态")
        if status == "已接入":
            return pkg, True, "该定义包已完成接入，无需继续"
        if status == "未接入":
            return pkg, False, "该定义包尚未开始接入，请先执行接入"
        return self.import_package(pkg_id)

    def _apply_level(self, item: dict[str, Any], pkg: dict[str, Any]) -> str:
        """按版本裁决规则应用单个层级；旧版本行保留，保证历史查询可用。"""
        rows = store.rows(LEVEL_MODULE)
        code = item["层级编号"]
        current = next((row for row in rows if row["层级编号"] == code and row.get("是否当前")), None)
        if current is None:
            rows.append(self._new_level_row(item, pkg))
            return "新增"
        diff = compare_versions(item["版本"], current["版本"])
        if diff > 0:
            current["是否当前"] = False
            rows.append(self._new_level_row(item, pkg))
            return "覆盖（包优先）"
        if diff == 0:
            return "跳过（同版本）"
        return "保留（现有优先）"

    @staticmethod
    def _new_level_row(item: dict[str, Any], pkg: dict[str, Any]) -> dict[str, Any]:
        rows = store.rows(LEVEL_MODULE)
        return {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "status": "生效中",
            "pending": False,
            "abnormal": False,
            "层级编号": item["层级编号"],
            "层级名称": item["层级名称"],
            "父级编号": item["父级编号"],
            "顺序": item["顺序"],
            "版本": item["版本"],
            "是否当前": True,
            "来源包": pkg["包编号"],
        }

    @staticmethod
    def _refresh_affected_charts(pkg: dict[str, Any]) -> None:
        """汇总已接入层级影响到的图表，供回执与图表清单核对。"""
        imported = {item["层级编号"] for item in pkg.get("回执", []) if item["接入"] == "已接入"}
        affected = []
        for chart in store.rows(CHART_MODULE):
            if imported & set(parse_dimension_codes(chart.get("关联维度"))):
                affected.append(str(chart.get("图表编号")))
        pkg["受影响图表"] = sorted(affected)
