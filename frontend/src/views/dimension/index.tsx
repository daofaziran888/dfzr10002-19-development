import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/dimension'
const columns = [
  "维度编号",
  "维度名称",
  "维度表名",
  "维度字段",
  "层级关系",
  "筛选条件",
  "排序规则",
  "维度版本"
]
const actions = [
  "提交审批",
  "发布维度",
  "废弃维度"
]
const packageColumns = ["包编号", "文件名", "包版本", "层级数", "校验状态", "接入状态", "接入进度", "受影响图表"]
const receiptColumns = ["层级编号", "层级名称", "父级编号", "顺序", "版本", "裁决", "接入"]
const levelColumns = ["层级编号", "层级名称", "父级编号", "顺序", "版本", "是否当前", "来源包"]
const stats = [
  {
    "label": "分析维度总数",
    "value": 0
  },
  {
    "label": "已发布维度",
    "value": 0
  },
  {
    "label": "待审批维度",
    "value": 0
  }
]

type Row = Record<string, any>

export default function DimensionView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [infoMessage, setInfoMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const [packages, setPackages] = useState<Row[]>([])
  const [levels, setLevels] = useState<Row[]>([])
  const [showAllVersions, setShowAllVersions] = useState(false)
  const [receipt, setReceipt] = useState<Row | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('分析维度列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分析维度管理列表读取失败')
    }
  }, [filters])

  const reloadPackages = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}/packages?size=100`)
      if (!response.ok) return
      const payload = await response.json()
      setPackages(payload.items ?? [])
    } catch {
      /* 列表读取失败时保留旧数据，主列表错误统一在下方展示 */
    }
  }, [])

  const reloadLevels = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}/levels?全部版本=${showAllVersions}`)
      if (!response.ok) return
      const payload = await response.json()
      setLevels(payload.items ?? [])
    } catch {
      /* 同上 */
    }
  }, [showAllVersions])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    void reloadPackages()
  }, [reloadPackages])

  useEffect(() => {
    void reloadLevels()
  }, [reloadLevels])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      if (!response.ok) {
        throw new Error('分析维度管理动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分析维度管理操作失败')
    }
  }

  async function uploadPackage(file: File) {
    setErrorMessage('')
    setInfoMessage('')
    try {
      const text = await file.text()
      const response = await request(`${ENDPOINT}/packages/upload`, {
        method: 'POST',
        body: JSON.stringify({ 文件名: file.name, 内容: text }),
      })
      const payload = await response.json()
      if (payload.ok) {
        setInfoMessage(payload.message)
      } else {
        setErrorMessage(payload.message)
      }
      if (payload.entry) {
        setReceipt(payload.entry)
      }
      await reloadPackages()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '定义包上传失败')
    }
  }

  async function importPackage(pkg: Row) {
    setErrorMessage('')
    setInfoMessage('')
    try {
      const response = await request(`${ENDPOINT}/packages/${pkg.id}/import`, {
        method: 'POST',
        body: JSON.stringify({ values: {} }),
      })
      const payload = await response.json()
      if (payload.ok) {
        setInfoMessage(payload.message)
      } else {
        setErrorMessage(payload.message)
      }
      if (payload.entry) {
        setReceipt(payload.entry)
      }
      await Promise.all([reloadPackages(), reloadLevels()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '定义包接入失败')
    }
  }

  async function resumePackage(pkg: Row) {
    setErrorMessage('')
    setInfoMessage('')
    try {
      const response = await request(`${ENDPOINT}/packages/${pkg.id}/resume`, { method: 'POST' })
      const payload = await response.json()
      if (payload.ok) {
        setInfoMessage(payload.message)
      } else {
        setErrorMessage(payload.message)
      }
      if (payload.entry) {
        setReceipt(payload.entry)
      }
      await Promise.all([reloadPackages(), reloadLevels()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '定义包续接失败')
    }
  }

  async function showReceipt(pkg: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/packages/${pkg.id}`)
      if (!response.ok) {
        throw new Error('回执读取失败')
      }
      setReceipt(await response.json())
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '回执读取失败')
    }
  }

  return (
    <section className="page" data-module="dimension">
      <header className="page-head">
        <div>
          <h2>分析维度管理管理</h2>
          <p className="page-desc">维护分析维度的层级结构、筛选条件与版本管理，支撑多维度下钻分析。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('分析维度登记入口尚未接入审批流')}>
            登记分析维度
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出分析维度管理清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <section className="panel" data-block="dimension-package">
        <h3 className="panel-title">分析维度定义包接入</h3>
        <p className="page-desc">
          上游治理系统交付的定义包在此上传：上传即校验，校验通过并核对回执后再接入维度树。
          版本冲突按「包版本高于现有以包为准、否则保留现有」裁决；校验不通过整包拒绝，重复上传与重复接入均幂等。
        </p>
        <div className="upload-bar">
          <input
            ref={fileRef}
            type="file"
            accept=".json,application/json"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) {
                void uploadPackage(file)
              }
              event.target.value = ''
            }}
          />
          <span className="page-desc">示例文件见仓库 examples/ 目录</span>
        </div>

        <table className="data-table">
          <thead>
            <tr>
              {packageColumns.map((column) => <th key={column}>{column}</th>)}
              <th>可执行动作</th>
            </tr>
          </thead>
          <tbody>
            {packages.map((pkg) => (
              <tr key={String(pkg.id)}>
                <td>{pkg.包编号}</td>
                <td>{pkg.文件名}</td>
                <td>{pkg.包版本}</td>
                <td>{pkg.层级数}</td>
                <td>
                  <span className={`badge ${pkg.校验状态 === '校验通过' ? 'ok' : 'err'}`}>{pkg.校验状态}</span>
                </td>
                <td>
                  <span className={`badge ${pkg.接入状态 === '已接入' ? 'ok' : pkg.接入状态 === '接入中断' ? 'warn' : ''}`}>
                    {pkg.接入状态}
                  </span>
                </td>
                <td>{pkg.已接入层级数}/{pkg.层级数}</td>
                <td>{(pkg.受影响图表 ?? []).join('、') || '—'}</td>
                <td className="row-actions">
                  {pkg.校验状态 === '校验通过' && (pkg.接入状态 === '未接入') && (
                    <button className="link" type="button" onClick={() => void importPackage(pkg)}>接入</button>
                  )}
                  {pkg.校验状态 === '校验通过' && (pkg.接入状态 === '接入中断' || pkg.接入状态 === '接入中') && (
                    <button className="link" type="button" onClick={() => void resumePackage(pkg)}>继续接入</button>
                  )}
                  <button className="link" type="button" onClick={() => void showReceipt(pkg)}>回执</button>
                </td>
              </tr>
            ))}
            {packages.length === 0 && (
              <tr>
                <td className="empty-state" colSpan={packageColumns.length + 1}>尚未上传定义包</td>
              </tr>
            )}
          </tbody>
        </table>

        {receipt && (
          <div className="receipt">
            <h4 className="panel-title">
              回执：{receipt.包编号}（{receipt.校验状态} / {receipt.接入状态}）
              <button className="link receipt-close" type="button" onClick={() => setReceipt(null)}>收起</button>
            </h4>
            {(receipt.校验信息 ?? []).length > 0 && (
              <ul className="error-list">
                {(receipt.校验信息 ?? []).map((item: string) => <li key={item} className="error-text">{item}</li>)}
              </ul>
            )}
            <table className="data-table">
              <thead>
                <tr>
                  {receiptColumns.map((column) => <th key={column}>{column}</th>)}
                </tr>
              </thead>
              <tbody>
                {(receipt.回执 ?? []).map((item: Row) => (
                  <tr key={String(item.层级编号)}>
                    {receiptColumns.map((column) => (
                      <td key={column}>{item[column] === '' || item[column] == null ? '—' : String(item[column])}</td>
                    ))}
                  </tr>
                ))}
                {(receipt.回执 ?? []).length === 0 && (
                  <tr>
                    <td className="empty-state" colSpan={receiptColumns.length}>包内没有可展示的层级</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel" data-block="dimension-levels">
        <h3 className="panel-title">
          维度层级树
          <label className="level-toggle">
            <input
              type="checkbox"
              checked={showAllVersions}
              onChange={(event) => setShowAllVersions(event.target.checked)}
            />
            显示历史版本
          </label>
        </h3>
        <table className="data-table">
          <thead>
            <tr>
              {levelColumns.map((column) => <th key={column}>{column}</th>)}
            </tr>
          </thead>
          <tbody>
            {levels.map((level) => (
              <tr key={`${level.层级编号}@${level.版本}`}>
                {levelColumns.map((column) => (
                  <td key={column}>
                    {column === '是否当前'
                      ? (level.是否当前 ? '当前' : '历史')
                      : (level[column] === '' || level[column] == null ? '—' : String(level[column]))}
                  </td>
                ))}
              </tr>
            ))}
            {levels.length === 0 && (
              <tr>
                <td className="empty-state" colSpan={levelColumns.length}>维度层级树为空</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无分析维度管理数据，可先登记分析维度</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条分析维度管理记录</span>
        {infoMessage && <span className="ok-text">{infoMessage}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
