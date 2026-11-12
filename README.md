# 数据分析工作台

面向企业数据分析师与数据产品经理的一站式数据分析工作台。提供指标口径管理、聚合查询、分析实验设计、数据服务发布与应用集成的完整链路。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标口径管理 | `indicator` | 业务指标 | 指标编号、指标名称、计算公式 |
| 分析数据源 | `datasource` | 数据源连接 | 连接编号、连接名称、数据库类型 |
| 聚合查询配置 | `query` | 查询模板 | 模板编号、模板名称、查询语句 |
| 分析实验管理 | `experiment` | 分析实验 | 实验编号、实验名称、分析数据集 |
| 分析数据集 | `dataset` | 数据集 | 数据集编号、数据集名称、来源表名 |
| 可视化图表配置 | `chart` | 图表配置 | 图表编号、图表名称、图表类型 |
| 分析报告管理 | `report` | 分析报告 | 报告编号、报告标题、关联数据集 |
| 数据服务发布 | `service` | 数据服务 | 服务编号、服务名称、输出数据集 |
| 数据看板 | `dashboard` | 看板面板 | 面板编号、面板名称、布局配置 |
| 分析维度管理 | `dimension` | 分析维度 | 维度编号、维度名称、维度表名 |
| 全局筛选器 | `filter` | 筛选器 | 筛选器编号、筛选器名称、关联字段 |
| 定时分析任务 | `schedule` | 定时任务 | 任务编号、任务名称、分析类型 |
| 数据导出配置 | `export` | 导出模板 | 模板编号、模板名称、数据源绑定 |
| 查询缓存管理 | `cache` | 缓存规则 | 规则编号、缓存目标、缓存时间 |
| 分析模型管理 | `model` | 分析模型 | 模型编号、模型名称、模型类型 |
| 分析操作日志 | `log` | 操作日志 | 日志编号、操作类型、操作对象 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 分析维度定义包接入

分析维度层级除了逐条维护，还支持接入上游治理系统交付的定义包（JSON 文件，
示例见 `examples/`）。页面入口在「分析维度管理」：上传即校验，核对回执后再接入。

- **校验**：层级编号唯一、父级必须存在于包内或现有维度树、无循环、同级先后
  顺序不冲突、版本格式合法。校验不通过整包拒绝，不写入任何层级。
- **裁决规则**：包内层级版本高于维度树同编号当前版本 → 以包为准覆盖（旧版本
  保留供历史查询）；低于或等于 → 以现有维度树为准保留；同版本重复接入幂等跳过。
- **断点续接**：接入按拓扑序逐层推进并记录进度，中断后调
  `POST /api/dimension/packages/{id}/resume` 从最后一个已接入层级继续。
- **幂等**：同一文件（内容指纹相同）重复上传返回原包回执；重复接入不产生重复层级。
- **图表联动**：接入后引用这些维度的图表自动标记「待重新绑定」（读取时对照维度树
  当前版本推导，与维度树同源），期间仍按绑定的历史版本取数
  （`GET /api/chart/{id}/query`），在图表页执行「重新绑定」后切到当前版本。

主要接口：

| 接口 | 说明 |
| --- | --- |
| `POST /api/dimension/packages/upload` | 上传定义包并校验，返回回执 |
| `GET  /api/dimension/packages` / `/{id}` | 定义包列表 / 详情与回执 |
| `POST /api/dimension/packages/{id}/import` | 接入（`stop_after` 可演练中断） |
| `POST /api/dimension/packages/{id}/resume` | 断点续接 |
| `GET  /api/dimension/levels` | 维度层级树（`版本=` 查历史版本） |
| `GET  /api/chart/{id}/query` | 按图表绑定版本取数验证 |

端到端验证：起后端后执行 `python3 -I scripts/verify_dimension_package.py`。

领域：数据工程
子方向：数据分析与数据应用
