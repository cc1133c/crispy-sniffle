# Editor MCP 接入指南

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

### 7.8 UMG 布局与颜色：文档没写的正确写法

`widget_edit` 那 9 个方法只覆盖「增、删、查、换」，**布局和颜色得绕到对象属性上写**。实测对照：

| 写法 | 结果 |
|---|---|
| `ue.widget_slot(wbp, name, 'Position', '0,0')` | ❌ `failed to set 'Position'` |
| `ue.widget_slot(wbp, name, 'Size', '40,40')` | ❌ `failed to set 'Size'` |
| `ue.widget_set_property(wbp, name, 'ColorAndOpacity', '0,0,0,1')` | ❌ `failed to set`（属性是 BPReadOnly） |
| `slot.set_position(...)` / `slot.set_size(...)` | ❌ 4.18 未暴露这些方法 |
| `slot.get_property('LayoutData').get_field('Offsets').set_field(...)` | ✅ |
| `widget.ColorAndOpacity = ue.FLinearColor(r, g, b, a)` | ✅ |

可用写法：

```python
tree = wbp.get_property('WidgetTree')
root = tree.get_property('RootWidget')

for s in root.get_property('Slots'):
    c = s.get_property('Content')
    if c is not None and c.get_name() == 'Image_1':
        offs = s.get_property('LayoutData').get_field('Offsets')
        offs.set_field('Left', 0.0)
        offs.set_field('Top', 0.0)
        offs.set_field('Right', 120.0)
        offs.set_field('Bottom', 120.0)
        c.ColorAndOpacity = ue.FLinearColor(1.0, 0.0, 0.0, 1.0)
```

**最关键的一条：Offsets 的语义和原版 UE 不一样。**

| 字段 | 原版 UE | 这个编辑器 |
|---|---|---|
| `Left` / `Top` | 位置 | 位置（一致） |
| `Right` / `Bottom` | 右下边界的绝对坐标，宽 = Right − Left | **直接就是宽 / 高** |

按原版语义写 `(0, 36, 288, 38)` 想画一条 2px 横线，实际会渲染成 **288 × 38 的色块**；多条叠起来就糊成一整块。`widget_inspect` 也按这个语义显示，所以它打印的 `Size` 就是 offset 值本身，不是算出来的宽高。

### 7.9 旋转做不了

`RenderTransform.Angle` 在 4.18 上写不进去：

```python
rt = widget.get_property('RenderTransform')
rt.set_field('Angle', 45.0)
widget.set_property('RenderTransform', rt)   # 调用不报错
# 读回：{'Angle': 0.0}   值没进去
```

`FWidgetTransform` 是按值拷贝，改了字段也回写不到控件上。需要斜线时只能用**阶梯小方块**近似（例如 6×6 的块按 6px 步进排 12 个），或者直接用自带斜线的贴图。

### 7.10 禁用 API 清单（本版本实测，会崩）

下面这组来自社区仓库 `quan686/pubgld-mcp` 的实测记录（2026-05-21 事故），在本版本编辑器上会触发**原生崩溃**。官方 `ue_py` 不拦截这些调用，只能靠自己避开。

**必崩**

| API / 操作 | 症状 |
|---|---|
| `sync_browser_to_assets` | `ModuleManager.cpp:423` ContentBrowser assert |
| `open_editor_for_asset` / `find_editor_for_asset` | 同上 |
| `close_editor_for_asset` / `close_all_asset_editors` | 资产编辑器操作不安全 |
| `get_selected_assets` / `get_all_edited_assets` | 会拉起未加载的 ContentBrowser 模块 |
| `new_object` 直接构造 UMG 子树 | 致命崩溃（日志紧邻 `LogMovieScene` legacy data） |
| `delete_object` / `shutdown` | 危险，直接禁止 |

**高危**

| API | 风险 |
|---|---|
| `get_assets` / `get_assets_by_class` / `get_assets_by_filter` | 同步加载大对象图，`UObjectArray.cpp:82` assert |
| `all_classes` / `tobject_iterator` | 全局遍历，对象数组膨胀 |
| `load_package` / `unload_package` | 拉入巨大依赖图 / 使活动对象失效 |
| `run_async_task` | 执行时长无法从 MCP 侧界定 |
| 各类 `open_*_dialog` / `invoke_tab` | 阻塞模态循环 |

> 加控件一律用 `ue.widget_add`，不要用 `ue.new_object` 手搓 WidgetTree。同样的目标：前者实测正常，后者实测崩溃。

## 8. 查阅官方 Lua API（离线文档）

官方 API 站点是 JS 动态渲染，`WebFetch` 抓不到正文。用 `tools/sync_oasis_docs.py` 可以把文档同步成本地 Markdown。

### 用法

```bash
# 需要 Python 3.7+，只用标准库，无需 pip 安装
python tools/sync_oasis_docs.py --out ./official-docs --workers 8
python tools/sync_oasis_docs.py --out ./official-docs --wiki
```

产物结构：

```
official-docs/
├── INDEX.md                 全量索引（1400+ 类）
└── docs/
    ├── api/
    │   ├── class/           类：变量 / 函数签名 / 参数 / 返回值
    │   ├── cppenum/         枚举
    │   ├── cppstruct/       结构体
    │   └── globalfunc/      全局函数
    └── wiki/                Wiki 文章正文
```

### 数据源

| 用途 | 地址 |
|---|---|
| 类 / 枚举 / 结构体索引 | `https://developer.gp.qq.com/api/{family}/list/sorted_list.json` |
| 详情 | `https://developer.gp.qq.com/api/{path}.json` |
| Wiki 分类 | `https://developer.gp.qq.com/wikieditor/_api/look-Category` |
| Wiki 正文 | `https://developer.gp.qq.com/wikieditor/_api/query-articles?Id={id}` |

以上接口全部公开，不需要登录，也不需要 Cookie。

### 检索方式（重要）

**不要整文件读**，按需取片段：

```bash
grep "UGCGameSystem" INDEX.md                                       # 先定位文件
grep -A 25 "GetAllPlayerPawn" docs/api/class/.../UGCGameSystem.md    # 再取签名
```

单个函数约 300–800 token，整读一个类约 20k token，差 20 倍以上。

### 已知坑

- `list.json` 里的 `Path` 是 `detail/class/xxx.json`，直接拼会 **404**；正确顺序来自 `sorted_list.json` 的 `class/detail/xxx.json`（`class` 在前）
- 官方改版后接口路径可能变化，脚本需要跟着调整
- 本仓库**不附带**同步产物（官方原文不批量分发），请自行生成
- 中文路径需要 URL 编码，脚本已处理

---

## 9. 用 MCP 做 UMG 与「世界空间 UI」（实测补充）

### 10.1 新建控件蓝图

```python
# 1) 先确认工厂（用户界面 / 控件蓝图）
call_toolset("UGCAskQ.UGCAskQAssetToolset", "ListCreatableAssetTypes", {})

# 2) 创建（路径必须在 /<ProjectName>/Asset/ 下，/Game 和项目根 UI 目录都不行）
call_toolset("UGCAskQ.UGCAskQAssetToolset", "CreateAssetWithFactory", {
    "FolderPath": "/superman-bf/Asset/Blueprint/UI",
    "AssetName": "W_HeadTitleIcon",
    "FactoryName": "UGCWidgetBlueprintFactory",
    "FactoryPropsJson": "{}"          # 默认父类 /Script/UMG.UserWidget
})

# 3) 自动生成配套 Lua（已有则返回 created=False）
ue.bp_lua_find_or_create("/superman-bf/Asset/Blueprint/UI/W_HeadTitleIcon")
```

新建出来的控件蓝图默认只有一个根 `CanvasPanel_0`。读控件树一律用 `ue.widget_inspect(wbp)`，拿到的对象必须用 `UGCWidgetBlueprint` 类加载，`get_asset` 返回的是 Package，不能直接用：

```python
cls = ue.find_class('UGCWidgetBlueprint')
wbp = ue.load_object(cls, '/superman-bf/Asset/Blueprint/UI/W_Title')
print(ue.widget_inspect(wbp))
```

### 10.2 `widget_slot` 只有 `LayoutData` 能写

`Position` / `Size` / `Anchors` / `Offsets` 单独写全部失败，CanvasPanelSlot 只能整块写 `LayoutData`，值是 UE 的导出文本格式（`Anchors` 是嵌套的 `Minimum/Maximum`，不是平铺的）：

```python
val = ('(Offsets=(Left=0,Top=0,Right=0,Bottom=0),'
       'Anchors=(Minimum=(X=0,Y=0),Maximum=(X=1,Y=1)),'
       'Alignment=(X=0,Y=0))')
ue.widget_slot(wbp, 'Img_Icon', 'LayoutData', val)   # 让 Image 铺满父级
```

`ZOrder`、`bAutoSize` 这类真正的 UPROPERTY 可以直接写。写完记得 `ue.compile_blueprint(wbp)` + `wbp.save_package()`。

### 10.3 世界空间 WidgetComponent（头顶称号 / 世界 UI）

参照工程里 `WorldUI_1` 的做法：Actor + `WidgetComponent`（`Space=0` 即 World，`bIsTwoSided=True`，`BlendMode=2` 即 Transparent，`Pivot=(0.5,0.5)`）。

- **DrawSize 同时是渲染目标分辨率**。直接按世界尺寸设（比如 40cm 高）等于用 40px 渲染，必然糊。
- **清晰度与大小解耦**：`SetDrawSize` 用高分辨率（如 256 高），再 `SetRelativeScale3D` 缩到目标世界尺寸（如 72cm），看到的是大图、采样的是高分辨率。
- **朝向玩家（billboard）**：旋转 **Actor**，不是组件：

```lua
local Yaw = math.deg(math.atan(LocalLoc.Y - SelfLoc.Y, LocalLoc.X - SelfLoc.X))
Self:K2_SetActorRotation(UGCMathUtility.MakeRotator(0, 0, Yaw), false)   -- 已验证可用
```

不要用 `K2_SetWorldRotation` 转组件：第 3 个参数是 out 的 `FHitResult`，Lua 传 `false` 会每帧抛 LuaException。

- **只在客户端生成**：`UGCActorComponentUtility.SpawnActor` 服务器和客户端都能调；纯视觉的东西客户端本地生成即可，不占同步。
- 取组件里的控件：`UGCWidgetUtility.GetWidgetComponentUserWidget(Comp)`；组件勾了手动重绘要 `RequestWidgetComponentRedraw(Comp)`。
- **创建控件后不能立刻取子控件**：`CreateWidget` 完就 `GetSubWidget` 会崩（工程里已验证）。必须先 `AddChildWidget` / `SetWidget`，等控件进入控件树后再取；`Construct` 里也不要取子控件。

### 10.4 运行时改图片（比例 / 裁切）

| 需求 | 官方接口 |
|---|---|
| 取贴图像素尺寸 | `UTexture2D:Blueprint_GetSizeX()` / `GetSizeY()` |
| 取控件实际槽位尺寸 | `Widget:GetCachedGeometry()` + `UGCWidgetUtility.GetLocalSize(Geo)` |
| 等比显示、防拉伸 | `Widget:SetRenderScale(FVector2D)` + `SetRenderTransformPivot(FVector2D(0.5,0.5))` |
| 只显示贴图有效区域（去透明边） | `UImage:GetBrush()` → 改 `UV0/UV1` → `UImage:SetBrush()` |

注意：`UGCWidgetUtility.SetWidgetSlotSize/SetWidgetSlotPosition/GetWidgetGeometry` 的形参类型是 `UUserWidget`，只对「用户控件」子节点可靠；对 `UImage` 这类普通控件请用 `GetCachedGeometry` + 渲染缩放。

### 10.5 贴图源数据（只读可用，不能改尺寸）

```python
d = tex.texture_get_source_data()   # bytearray，长度 = W*H*4，BGRA 顺序
# 例如 1255*836*4 = 4196720；alpha = d[3::4]
```

可以做 alpha 边界分析，但 `texture_set_source_data(data, mip_id)` 的第二个参数是 **mip 索引**，不能改宽高。想真正裁切贴图只能重新导入资源。

---

## 10. 用 MCP 批量复制关卡（实测）

目标：把子关卡（如 `senlin`）复制一份到主地图旁边，作为独立打怪场景。

```python
# 1) 磁盘级复制 .umap（编辑器开着也能做）
#    Copy-Item senlin.umap senlin_battle.umap

# 2) 加进当前世界（必须用完整包路径，短名会报 package does not exist）
world = ue.get_editor_world()
ue.add_level_to_world(world, '/superman-bf/senlin_battle')

# 3) 整体平移：先对全部 Actor 做位置快照，再按「快照 + 偏移」绝对赋值
#    （父 Actor 移动时 ChildActor 会跟随，逐个相对移动会双倍偏移）

# 4) 保存：pkg.modify() + pkg.save_package()
#    这一步会产出同名 .uasset，不会写 .umap
```

四个坑：

| 坑 | 现象 | 解决 |
|---|---|---|
| 保存接口对新关卡包无效 | `project_save` / `editor_save_all` / `console_exec("SAVEALL")` 都返回成功但不写文件 | 用 `ue.get_asset("/项目/关卡名")` 取包 → `modify()` → `save_package()` |
| 包名与内部对象名不一致 | `save_package` 写出的是 `xxx.uasset` | 用 PowerShell 把 `.uasset` 复制覆盖同名 `.umap`，再删掉 `.uasset`（不用关编辑器） |
| ChildActor 双倍偏移 | 子 Actor 被移动两次 | 先快照全部 Actor 坐标，再按 `snapshot[i] + offset` 绝对赋值（幂等） |
| Landscape GUID 冲突 | 打开时提示保存 / 换新 GUID | 正常现象，按提示保存即可 |
| 放置位置判断错 | 以为复制失败、实际离得很远 | 别按包围盒判断，先用 `all_actors()` 统计坐标密集区再定偏移 |

`ue.duplicate_asset` 不可用（UGC 资产必须在 `Asset/` 下）；复制关卡走「文件复制 + `add_level_to_world`」。

---

## 相关

- 官方 Lua API 文档：<https://developer.gp.qq.com/api/#/>
- 编辑器 Python API 文档：用 `ue_read queries=["py:index"]` 在编辑器内查询（70 分类 / 863 方法）
