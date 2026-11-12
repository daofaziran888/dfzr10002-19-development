"""分析维度管理接口：维护分析维度，覆盖提交审批、发布维度、废弃维度等动作。

维度层级树与定义包接口也挂在这里：
- GET  /api/dimension/levels            维度层级树（默认当前版本，可按版本查历史）
- GET  /api/dimension/packages          定义包列表
- POST /api/dimension/packages/upload   上传定义包（上传即校验，同文件重复上传幂等）
- GET  /api/dimension/packages/{id}     定义包详情与回执
- POST /api/dimension/packages/{id}/import  接入定义包（可按 stop_after 演练中断）
- POST /api/dimension/packages/{id}/resume  从断点继续接入
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.dimension import DimensionService, list_levels
from app.services.dimension_package import DimensionPackageService

router = APIRouter(prefix="/api/dimension", tags=["分析维度管理"])

service = DimensionService()
package_service = DimensionPackageService()

LIST_FIELDS = ["维度编号", "维度名称", "维度表名", "维度字段", "层级关系", "筛选条件", "排序规则", "维度版本"]
STATUSES = ["草稿", "待审批", "已发布", "已废弃"]


@router.get("/levels", response_model=PageResult[dict])
def list_dimension_levels(
    版本: str | None = Query(default=None, description="指定版本时返回该版本的历史层级"),
    全部版本: bool = Query(default=False, description="为 true 时返回全部版本行，含历史"),
) -> PageResult[dict]:
    """维度层级树：默认只给当前生效版本；需要核对历史时按版本或全量读取。"""
    items = list_levels(version=版本, only_current=not 全部版本)
    return PageResult(items=items, total=len(items), page=1, size=len(items) or 1)


@router.get("/packages", response_model=PageResult[dict])
def list_packages(page: int = 1, size: int = 20) -> PageResult[dict]:
    """定义包列表：按上传时间倒序，含校验状态、接入状态与接入进度。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = package_service.list_packages(page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/packages/upload", response_model=ActionResult)
def upload_package(payload: EntryPayload) -> ActionResult:
    """上传定义包并立即校验；同一文件重复上传返回原包回执，不生成重复包。"""
    filename = str(payload.values.get("文件名") or payload.values.get("filename") or "").strip()
    content = payload.values.get("内容", payload.values.get("content"))
    if content is None or (isinstance(content, str) and not content.strip()):
        return ActionResult(ok=False, message="缺少文件内容：请上传定义包文件")
    pkg, duplicated, errors = package_service.upload(filename=filename, content=content)
    if pkg is None:
        return ActionResult(ok=False, message=f"上传失败：{'；'.join(errors)}")
    if duplicated:
        return ActionResult(ok=True, message="同一文件已上传过，未生成重复定义包", entry=pkg)
    if errors:
        return ActionResult(
            ok=False,
            message=f"校验未通过，整包已拒绝：{'；'.join(errors)}",
            entry=pkg,
        )
    return ActionResult(ok=True, message=f"校验通过，共 {pkg['层级数']} 层，请核对回执后执行接入", entry=pkg)


@router.get("/packages/{pkg_id}", response_model=dict)
def get_package(pkg_id: int) -> dict:
    """定义包详情：回执里可核对层级编号、父级与先后关系，以及每层裁决与接入状态。"""
    pkg = package_service.get_package(pkg_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail=f"定义包 {pkg_id} 不存在")
    return pkg


@router.post("/packages/{pkg_id}/import", response_model=ActionResult)
def import_package(pkg_id: int, payload: EntryPayload | None = None) -> ActionResult:
    """接入已校验的定义包；校验未通过的包整包拒绝，不会留下半套层级。"""
    stop_after = None
    if payload is not None:
        raw = payload.values.get("stop_after", payload.values.get("中断于"))
        if raw is not None:
            try:
                stop_after = max(int(raw), 0)
            except (TypeError, ValueError):
                return ActionResult(ok=False, message="stop_after 必须是整数")
    pkg, ok, message = package_service.import_package(pkg_id, stop_after=stop_after)
    if pkg is None:
        raise HTTPException(status_code=404, detail=message)
    return ActionResult(ok=ok, message=message, entry=pkg)


@router.post("/packages/{pkg_id}/resume", response_model=ActionResult)
def resume_package(pkg_id: int) -> ActionResult:
    """接入中断后，从已校验的最后一个层级继续接入。"""
    pkg, ok, message = package_service.resume_package(pkg_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail=message)
    return ActionResult(ok=ok, message=message, entry=pkg)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出分析维度管理清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "dimension", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按维度编号检索"),
    status: str | None = Query(default=None, description="草稿、待审批、已发布、已废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按维度编号与状态过滤分析维度管理列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条分析维度明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"分析维度 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条分析维度，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="分析维度已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条分析维度执行提交审批、发布维度、废弃维度；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
