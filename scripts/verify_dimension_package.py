"""分析维度定义包端到端验证：校验、裁决、原子性、断点续接、幂等、图表同源。

运行前先起后端：cd backend && ./run.sh
用法：python3 -I scripts/verify_dimension_package.py
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
PASS, FAIL = 0, 0


def call(method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
    if params:
        path = f"{path}?{urllib.parse.urlencode(params)}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


def current_levels() -> dict[str, str]:
    items = call("GET", "/api/dimension/levels")["items"]
    return {row["层级编号"]: row["版本"] for row in items}


def chart(code: str) -> dict:
    items = call("GET", f"/api/chart?keyword={code}")["items"]
    return next(row for row in items if row["图表编号"] == code)


with open("examples/dimension-package-geo-v2.json", encoding="utf-8") as fh:
    valid_text = fh.read()
with open("examples/dimension-package-geo-broken.json", encoding="utf-8") as fh:
    broken_text = fh.read()

print("== 1. 初始状态：维度树 v1.0.0，CFG-0001 已绑定 ==")
levels = current_levels()
check("初始层级 4 条", set(levels) == {"DIM-001", "DIM-002", "DIM-003", "DIM-101"}, str(levels))
check("初始版本全部 1.0.0", set(levels.values()) == {"1.0.0"}, str(levels))
check("CFG-0001 已绑定", chart("CFG-0001")["绑定状态"] == "已绑定")

print("== 2. 父级缺失的包整包拒绝，不留半套层级 ==")
resp = call("POST", "/api/dimension/packages/upload", {"values": {"文件名": "broken.json", "内容": broken_text}})
check("上传返回校验失败", not resp["ok"] and "父级 DIM-099 缺失" in resp["message"], resp["message"])
check("维度树未被污染", current_levels() == levels, str(current_levels()))
broken_id = resp["entry"]["id"]
resp2 = call("POST", f"/api/dimension/packages/{broken_id}/import", {"values": {}})
check("校验失败的包禁止接入", not resp2["ok"], resp2["message"])
check("接入后维度树仍未被污染", current_levels() == levels)

print("== 3. 合法包上传即校验，回执可核对编号/父级/先后 ==")
resp = call("POST", "/api/dimension/packages/upload", {"values": {"文件名": "geo-v2.json", "内容": valid_text}})
check("校验通过", resp["ok"], resp["message"])
pkg_id = resp["entry"]["id"]
receipt = {item["层级编号"]: item for item in resp["entry"]["回执"]}
check("回执三层", set(receipt) == {"DIM-001", "DIM-002", "DIM-003"})
check("回执父级正确", receipt["DIM-003"]["父级编号"] == "DIM-002" and receipt["DIM-001"]["父级编号"] == "")
check("回执先后关系正确", all(item["顺序"] == 1 for item in receipt.values()))

print("== 4. 同一文件重复上传不生成重复包 ==")
resp = call("POST", "/api/dimension/packages/upload", {"values": {"文件名": "geo-v2.json", "内容": valid_text}})
check("重复上传幂等", resp["ok"] and "重复" in resp["message"] and resp["entry"]["id"] == pkg_id, resp["message"])
total = call("GET", "/api/dimension/packages?size=100")["total"]
check("包记录只有 2 条（broken + valid）", total == 2, str(total))

print("== 5. 接入中断后从最后一个已校验层级继续 ==")
resp = call("POST", f"/api/dimension/packages/{pkg_id}/import", {"values": {"stop_after": 2}})
check("演练中断", not resp["ok"] and resp["entry"]["接入状态"] == "接入中断", resp["message"])
check("已接入 2/3 层", resp["entry"]["已接入层级数"] == 2)
mid = current_levels()
check("前两层已覆盖为 2.0.0", mid["DIM-001"] == "2.0.0" and mid["DIM-002"] == "2.0.0", str(mid))
check("第三层仍是 1.0.0", mid["DIM-003"] == "1.0.0", str(mid))
resp = call("POST", f"/api/dimension/packages/{pkg_id}/resume")
check("续接完成", resp["ok"] and resp["entry"]["接入状态"] == "已接入", resp["message"])
final = current_levels()
check("三层全部 2.0.0", all(final[code] == "2.0.0" for code in ("DIM-001", "DIM-002", "DIM-003")), str(final))
check("未受影响维度保持 1.0.0", final["DIM-101"] == "1.0.0")
check("当前层级仍 4 条（无重复）", len(final) == 4, str(final))

print("== 6. 受影响图表标记待重新绑定，仍按历史层级取数 ==")
check("回执受影响图表含 CFG-0001", "CFG-0001" in resp["entry"]["受影响图表"], str(resp["entry"]["受影响图表"]))
check("CFG-0001 待重新绑定", chart("CFG-0001")["绑定状态"] == "待重新绑定")
check("CFG-0002 不受影响", chart("CFG-0002")["绑定状态"] == "已绑定")
query = call("GET", "/api/chart/1/query")
check("CFG-0001 待重新绑定仍可取数", query["ok"] and query["绑定状态"] == "待重新绑定" and len(query["数据行"]) > 0)
check("取数用的是历史版本 1.0.0", all(row["版本"] == "1.0.0" for row in query["层级"]), str(query["层级"]))
hist = call("GET", "/api/dimension/levels", params={"版本": "1.0.0"})["items"]
check("历史版本层级可查", len(hist) == 4 and all(row["版本"] == "1.0.0" for row in hist))

print("== 7. 重复接入幂等，不产生重复层级 ==")
resp = call("POST", f"/api/dimension/packages/{pkg_id}/import", {"values": {}})
check("重复接入提示幂等", resp["ok"] and "重复接入" in resp["message"], resp["message"])
check("当前层级仍 4 条", len(current_levels()) == 4)
all_levels = call("GET", "/api/dimension/levels", params={"全部版本": "true"})["items"]
check("含历史共 7 行（4 初始 + 3 覆盖）", len(all_levels) == 7, str(len(all_levels)))

print("== 8. 重新绑定后状态与维度树同源 ==")
resp = call("POST", "/api/chart/1/actions", {"values": {"action": "重新绑定"}})
check("重新绑定成功", resp["ok"], resp["message"])
check("CFG-0001 回到已绑定", chart("CFG-0001")["绑定状态"] == "已绑定")
query = call("GET", "/api/chart/1/query")
check("重新绑定后按 2.0.0 取数", all(row["版本"] == "2.0.0" for row in query["层级"]), str(query["层级"]))

print(f"\n结果：{PASS} 通过，{FAIL} 失败")
raise SystemExit(1 if FAIL else 0)
