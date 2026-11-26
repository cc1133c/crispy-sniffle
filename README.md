# 绿洲启元（和平精英 UGC）编辑器内置 MCP 接入指南

把 `OasisEraEditor` 自带的 MCP 服务接进任意支持 **Streamable HTTP** 的 MCP 客户端（Trae / Cursor / Claude Code 等），让 AI 直接操作编辑器：跑 Python、读写蓝图与 UMG、控制 PIE、调用官方 Toolset。

> 本仓库只是**接入文档**。MCP 服务本身是编辑器内置的，不需要安装任何东西，也没有第三方依赖。

---

## 1. 这是什么

从编辑器启动日志可以看到：

```
LogUGCMCPBridge: [UGCMCPBridge] StartupModule: registering MCP tools.
LogUGCAskQEditor: FastMCP bridge version: 3.4.7-bridge.2
LogUGCAskQEditor: Auto-starting MCP Server on 127.0.0.1:12463 (restored from last session).
```

也就是说，**只要编辑器开着，MCP 服务就已经在跑**，你需要的只是把客户端连上去。

已实测环境：

| 项目 | 值 |
|---|---|
| 编辑器 | `OasisEraEditor(2001776)` |
| 引擎 | UE 4.18.1 定制版（`WindowsUGCEditor`） |
| 内置 Python | 3.11.1 |
| MCP 服务 | `UGCEditor-PIE` v2.0.0 |
| 协议版本 | `2024-11-05` |
| 传输方式 | Streamable HTTP（POST） |

## 2. 端点

```
http://127.0.0.1:12463/mcp
```

- **只监听本机** `127.0.0.1`，不需要 token，不需要 headers。
- **只接受 POST**。`GET /mcp` 返回 `405`，`/sse` 返回 `404`——**它不是 SSE 服务**。
- 前置条件：编辑器已打开，且 UGC 账号已登录（未登录时 `status.ready=false`，大部分功能不可用）。

## 3. 接入

### Trae

配置文件：`%APPDATA%\Trae CN\User\mcp.json`

```json
{
  "mcpServers": {
    "ugc-editor": {
      "type": "http",
      "url": "http://127.0.0.1:12463/mcp"
    }
  }
}
```

改完**重启 TRAE**（或在 MCP 面板重新连接）。

### 其他客户端

任何支持 Streamable HTTP 的 MCP 客户端都填同一个 URL，不需要 headers、不需要环境变量、不需要启动命令。

### 验证是否连通

不依赖客户端，直接用 curl 打通一次：

```bash
curl -s -X POST "http://127.0.0.1:12463/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"probe\",\"version\":\"1.0\"}}}"
```

期望返回：

```json
{"id":1,"jsonrpc":"2.0","result":{
  "capabilities":{"resources":{},"tools":{}},
  "protocolVersion":"2024-11-05",
  "serverInfo":{"name":"UGCEditor-PIE","version":"2.0.0"}
}}
```

响应头里会带 `Mcp-Session-Id`，后续请求需要把它回传。

## 4. 工具清单（10 个）

| 工具 | 作用 | 关键约束 |
|---|---|---|
| `ue_py` | 在编辑器里执行任意 Python | 必须 `import unreal_engine as ue`（不是 `import unreal`）；写操作需 `plan` |
| `ue_read` | 查文档 / Schema / 枚举 / 类层级 / 编辑器上下文 | `py:` `schema:` `enum:` `subclasses:` `ctx:` `status:`，单次最多 10 条 |
| `ue_pie` | 控制 PIE：启动、停止、查询、跑 Lua、重载 Lua、开关 player、弱网模拟 | 一次调用一个 action |
| `call_toolset` | 调官方 Toolset 函数（结构化写入通道） | 复杂类型必须走这里 |
| `list_toolsets` | 列出已注册 Toolset | — |
| `describe_toolset` | 取某个 Toolset 的 JSON Schema | 调 `call_toolset` 前先看 |
| `ue_skill` | 读取项目内置任务技能（如特效生成） | — |
| `ue_alert_dialog` | 感知/操作编辑器交互弹窗 | 弹窗会阻塞后续工具，必须先处理 |
| `ue_import_dialog` | 操作 FBX 导入选项弹窗 | 无需屏幕点击 |
| `ue_plan_submit` | 提交写操作计划，拿 `plan_id` | 多步写同一资产时复用 |

## 5. 能力覆盖

编辑器内置 Python API 规模（`ue_read queries=["py:index"]`）：

```
Python API Category Index (70 categories, 863 methods)
```

`unreal_engine` 模块对外导出 564 个名字。

**结论：不存在功能缺失。** 因为：

- `ue_py` 能跑任意 Python，直接触达全部 863 个方法；
- `ue_read` 提供全部方法的签名与示例；
- 复杂属性（Array / Struct / InstancedObject）通过 `call_toolset` 写入；
- 运行时能力（跑游戏内 Lua、PIE）通过 `ue_pie` 提供。

所以接入时**不需要额外封装 SDK**，只需把 URL 连上。

## 6. 使用规则

1. **先查状态**：`ue_read queries=["status:"]`，确认 `ready=true`。
2. **不要猜 API**：先 `ue_read queries=["py:guide <类别>"]` 或 `py:<方法名>`。
3. **资产路径**是 `/<ProjectName>/Asset/...`（例如 `/superman/Asset/Blueprint/UI/W_TitlePlate`），**不是** `/Game/`。
4. **写操作要带 plan**（PRV 校验）：纯查询可省略；省略计划的写入在 observe 模式只告警，在 enforce 模式会被拒。
5. **长任务不要阻塞**：用 `ugc_asyncio.schedule(coro)`，否则会卡住编辑器主线程和其他调用。
6. **弹窗优先处理**：返回根节点的 `alert.blocking=true` 时，先解决弹窗再继续。

## 7. 踩坑记录

### 7.1 绝对不要用 `ue_site.py` 做启动钩子（会把编辑器搞崩）

有些第三方方案（例如 `pubgld-mcp`）会把脚本放到：

```
<编辑器>/Content/pyScripts/ue_site.py
```

编辑器确实会在 **Python 虚拟机初始化的第 0 帧** 自动 `import ue_site`（日志里是 `Warning: Importing ue_site`）。但此时引擎尚未就绪，任何引擎调用（`import unreal_engine` 之后的 `add_ticker` 之类）都会触发**原生崩溃**——`try/except` 抓不到，日志写完那一行就断，崩溃转储 `CallStack` 为空。

实测两次崩溃日志都停在：

```
LogPython: Warning: Importing ue_site
```

修复方式：删掉该目录下的 `ue_site.py` 和 `__pycache__`，恢复正常。

正常启动时的对照日志是：

```
LogPython: Warning: ue_site Python module not found
```

### 7.2 `/sse` 是 404，别配成 SSE

这个桥是 Streamable HTTP：

| 请求 | 结果 |
|---|---|
| `GET /sse` | 404 |
| `GET /mcp` | 405（说明端点存在，只是不接受 GET） |
| `POST /mcp` | 200 |

客户端配置写成 `"type": "sse"` + `http://127.0.0.1:12463/sse` 会一直连不上。

### 7.3 端口可能漂移

日志写的是 `restored from last session`，默认 `12463`。如果该端口被占用，编辑器会换一个端口，此时客户端的 URL 要跟着改。连不上先确认端口：

```powershell
Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -ge 12000 -and $_.LocalPort -le 13000 }
```

### 7.4 `dir(obj)` 不可靠

在 `ue_python` 里对 UObject 调 `dir()`，会回落到模块级函数列表，看不到真实成员。例如 `root.add_child` 会报 `AttributeError`。

**查 API 一律用 `ue_read queries=["py:<关键词>"]`**，不要靠反射猜。

### 7.5 没有 `add_child`，加 UMG 控件要用 `widget_add`

`UPanelWidget::AddChild` 未暴露。正确做法：

```python
ue.widget_add(wbp, 'Image', 'Image_1', 'CanvasPanel_0')
```

配套 API（属于 `widget_edit` 分类，共 9 个）：

| 方法 | 作用 |
|---|---|
| `ue.widget_add` | 添加控件 |
| `ue.widget_inspect` | 查询控件树（返回树形字符串） |
| `ue.widget_remove` | 删除控件 |
| `ue.widget_move` | 移动控件 |
| `ue.widget_swap` | 替换控件类型 |
| `ue.widget_set_property` | 设置控件属性 |
| `ue.widget_slot` | 设置 Slot 布局 |
| `ue.widget_wrap` | 容器包裹控件 |
| `ue.widget_unwrap` | 解除容器包裹 |

### 7.6 复杂属性不能直接赋值

Array / Struct / InstancedObject 直接赋值会被阻止并报错，必须走：

```python
from toolset_proxy import ToolsetProxy
obj_ts = ToolsetProxy("ObjectToolset")
obj_ts.SetProperties(InstancePath='/superman/Asset/...', ValuesJson='{"Health":100}')
```

标量（int/float/bool/string/enum）仍然可以直接赋值。

### 7.7 编辑器必须开着

MCP 服务跑在编辑器进程里，编辑器关闭即断开。这不是常驻后台服务。

## 8. 一次真实调用记录

给 `W_TitlePlate` 加 5 个 Image 控件（`ue_py`，带计划与事务）：

```python
import unreal_engine as ue

wbp = ue.find_asset('/superman/Asset/Blueprint/UI/W_TitlePlate.W_TitlePlate')
print(ue.widget_inspect(wbp))          # 操作前

for i in range(1, 6):
    ue.widget_add(wbp, 'Image', 'Image_%d' % i, 'CanvasPanel_0')

print(ue.widget_inspect(wbp))          # 操作后
ue.compile_blueprint(wbp)
wbp.save_package()
```

返回：

```
BEFORE
[CanvasPanel] CanvasPanel_0

AFTER
[CanvasPanel] CanvasPanel_0
  [Image] Image_1  {CanvasPanel (Pos: 0,0, Size: 100,30)}
  [Image] Image_2  {CanvasPanel (Pos: 0,0, Size: 100,30)}
  [Image] Image_3  {CanvasPanel (Pos: 0,0, Size: 100,30)}
  [Image] Image_4  {CanvasPanel (Pos: 0,0, Size: 100,30)}
  [Image] Image_5  {CanvasPanel (Pos: 0,0, Size: 100,30)}

SAVED_OK
```

PRV 校验结果：`decision=pass`、`plan_valid=true`。

---

## 相关

- 官方 Lua API 文档：<https://developer.gp.qq.com/api/#/>
- 编辑器 Python API 文档：用 `ue_read queries=["py:index"]` 在编辑器内查询（70 分类 / 863 方法）
