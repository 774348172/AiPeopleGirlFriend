# AiGirlFriendUnity —《猫咪女友》Unity 客户端

《猫咪女友》3D 箱庭客户端（方案 A：同居小屋）。M1 阶段目标：出租屋场景可走动 + 与后端 8767 服务连续对话 + 读取已提交状态展示。

## 运行前置

1. 后端服务（Python，仓库 `D:\AIPeopleGit\ai-girlfriend\AiPeople`）启动：

   ```bat
   cd D:\AIPeopleGit\ai-girlfriend\AiPeople
   python tools\baiweixi_chat_app.py --port 8767
   ```

   就绪检查：浏览器打开 `http://127.0.0.1:8767/api/health` 应返回 `"status":"ready"`。

2. Unity：6000.1.1f1（URP）。打开工程目录 `AiGirlFriendUnity/`。

### 无模型开发（契约桩）

没有模型资产、或只想调前端时，用开发用契约桩代替后端（**不产生真实模型回复，禁止用于验收**）：

```bat
cd D:\AIPeopleGit\UnityClient
python Tools\dev_stub\aipeople_stub.py --port 8767
```

桩复刻 `/api/health`、`/api/status`、`/api/chat/stream` 的线协议（含分片流式与 1:1 游戏时钟）。

## 运行（M1）

- 打开场景 `Assets/Scenes/Apartment.unity`（如不存在，用菜单 `AiPeople/重建 Apartment 场景` 生成），点击 Play。
- 操作：
  - `WASD` 移动、鼠标转向（第一人称）；靠近餐桌/厨房/门口会自动切换"当前所在位置"。
  - `E` 与注视的物体交互：灯开关（门边墙）、窗户把手、桌上的水杯、她的纸箱。
  - `回车` 或 `T` 聚焦聊天输入框（光标解锁、暂停移动）；`Esc` 退出输入。
  - 中文输入建议用系统输入法；若编辑器内回车与输入法冲突，用"发送"按钮提交。
- 交互产生的男主侧事实（灯/窗/手持水杯）会随下一轮对话并入场景描述上报；不改写任何女主状态。
- 对话显示规则：流式输出期间显示"正在说…"；只有后端 `done` 提交成功才定稿为白未晞说过的话；失败会标记错误行。

## 配置

`AiGirlFriendUnity/Assets/StreamingAssets/aipeople_config.json`（改后重启播放即可，不需要改代码）：

| 字段 | 说明 | 默认 |
|---|---|---|
| `endpoint` | 后端地址 | `http://127.0.0.1:8767` |
| `saveId` | 存档 ID，须匹配后端规则 `^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$` | `m1check0001` |
| `characterId` | 女主角角色 ID（P0 固定 `baiweixi`） | `baiweixi` |
| `requestTimeoutSeconds` | 单轮请求超时 | `120` |

也可在 Unity 内使用 `Assets/AiPeople/Resources/AiPeopleConfig.asset`（右键 Create → AiPeople → Config）作为非明文配置；StreamingAssets JSON 优先级更高。

## 目录结构

```
AiGirlFriendUnity/Assets/
  AiPeople/
    Core/       通信（NDJSON 流式）、状态模型、本地显示历史、配置、字体/材质工具
    World/      出租屋 blockout、点位锚点（对齐后端 location_id）、第一人称控制、场景氛围（昼夜/天气）、交互物
    Character/  白未晞占位表现（M2 按资产路线替换）
    UI/         对话面板、HUD（uGUI）
    App/        运行时装配入口 AiPeopleApp
    Editor/     场景构建器（菜单 AiPeople/重建 Apartment 场景）
    Tests/      PlayMode 自动化测试
  Scenes/Apartment.unity
Tools/dev_stub/ 开发用契约桩（无模型）
```

## 契约来源（不要臆造字段）

- 线协议与字段以后端 `tools/baiweixi_chat_app.py` 为唯一来源：`/api/health`、`/api/status`、`/api/chat/stream`（NDJSON：`delta` / `done` / `error`）。
- 位置词汇（`location_id`）：`apartment_table` / `apartment_kitchen` / `apartment_door` / `lane_corner` / `coffee_shop`（M1 只做出租屋三项）。
- 规则：表现层只读已提交状态；不伪造动作来源；动作闭环（M3）等待模型侧 `JUDGE_TURN` 交付。

## 游戏世界服务（M3）

Unity 是世界的唯一事实源与执行者，本地监听 `127.0.0.1:8770`（端口与开关在 `AiPeopleApp` Inspector：`gameServerPort` / `gameServerEnabled`）：

| 端点 | 说明 |
|---|---|
| `GET /game/health` | 就绪检查 |
| `GET /game/actions` | 动作清单（**游戏导出**，后端消费）：`cook_meal` / `move_to` / `pick_up_item` |
| `GET /game/project` | 世界投影：进行中动作+剩余秒数、物品实体、女主活动、拒绝反馈 |
| `POST /game/execute_action` | 执行动作（白名单/参数校验在游戏侧；拒绝返回 `accepted:false` + `reason`） |
| `POST /game/player_fact_report` | 男主侧事实上报（保持 `pending_confirmation`） |

动作表现与结果：女主会**真的走过去**（播 `walk`、转向、到位回 `idle`）；`cook_meal` 到期后投影出现 `桌上: 一碗热汤面`，并在桌面生成面碗实体；被拒动作的原因进入 `recent_feedback` 供下一轮回注。做饭等工作动画待补（当前以 idle 占位）。

后端接入（可选，默认不改变现状）：`AiPeople/runtime/world_mind/remote_game_world.py` 的 `RemoteGameWorld("http://127.0.0.1:8770")` 实现同一 `GameWorldInterface`，通过 `WorldMindRuntime(..., game_world=...)` 注入启用；`baiweixi_chat_app` 的接线开关为 M3 剩余项。

## 桌面形态（M5）

构建版为 **Windows 桌面背景程序**：主进程的 3D 房间位于桌面图标下方；独立轻量小窗负责对话和房间控制。

2026-09-16 最新本地验收包：`Builds/CatGirlfriend-FurnitureBatchFix-20260916/CatGirlfriend.exe`。
家具合批修复与 21/21 回归结果见 [家具验收记录](output/furniture-batching-20260916/RESULTS.md)。构建包不随源码上传。
菜单构建与房屋预览共用 `AiPeoplePlayerBuilder.BuildWindowsPlayerAtPath`，固定包含
`PlayerBootstrap → Apartment → ChatWindow` 三个场景，由启动参数选择进程角色。
构建前校验轻量入口的资源依赖，采用 D3D11 优先，构建后恢复编辑器图形设置。
`AIPEOPLE_BUILD_PATH` 可指定新输出目录；未设置时菜单构建仍输出 `Builds/CatGirlfriend`。
本轮结果与验证边界见 [发布入口验收](output/release-entry-20260916/RESULTS.md)。

```bat
:: 最新已验证构建（旧包保留）
D:\AIPeopleGit\UnityClient\Builds\CatGirlfriend-EntryFix-20260916\CatGirlfriend.exe
:: 重新构建：Unity 菜单 AiPeople/构建 Windows 播放器，或
:: Unity.exe -batchmode -quit -projectPath AiGirlFriendUnity -executeMethod AiPeople.EditorTools.AiPeoplePlayerBuilder.BuildWindowsPlayer
```

- **运行**：双击 `CatGirlfriend.exe` → 自动尝试壁纸层挂载（失败自动降级普通窗口，启动日志有说明）。
- **对话**：使用托盘或 **`Ctrl+Alt+B`** 唤出独立小窗；`Esc` 或“收起”隐藏小窗，壁纸主窗口始终不接收输入。
- **房间/退出**：小窗顶部提供房间控制、恢复桌面壁纸和退出入口；托盘退出与 `Ctrl+Alt+Q` 同样联动关闭双进程。
- **设置文件**：`%USERPROFILE%\AppData\LocalLow\AiPeople\CatGirlfriend\aipeople_shell.json`（可直接编辑热键等）。
- **性能纪律**：30 FPS 上限；前台存在全屏应用时自动暂停渲染（游戏世界服务心跳在独立线程继续）；不注入、不挂钩任何进程。
- **安全兜底**：热键注册失败时**不进入壁纸层**（否则没有交互与退出入口）；异常情况可用任务管理器结束 `CatGirlfriend.exe`。
- Editor 内恒为普通窗口模式（第一人称 + 完整对话面板）；**壁纸层只能在构建版验证**。

## 自动化测试

2026-09-14 点击后变回普通窗口的修复及构建版验证见 [桌面模式修复说明](Tools/Shell/README.md)。托盘与小窗提供单向“恢复桌面壁纸”，重复操作保持已挂载窗口。

小窗显隐回归（无需模型服务）：`-runTests -testPlatform PlayMode -testFilter "AiPeople.Tests.ChatWindowLatencyTests;AiPeople.Tests.ChatUiServerTests"`。
覆盖隐藏时唤出不读取历史、慢回应期间立即收起、旧回应不重新开窗、轮询不重叠和 HTTP 收起确认。

小窗隐藏时关闭 Canvas 绘制，消息循环保持 30 FPS，通过 `/ui/visibility` 每 50ms 查询显隐标志（受帧调度影响）；显示后为 60 FPS，完整历史仍每 200ms 查询。同一时刻只允许一个状态查询，网络故障时退避到 1s。收起先在本地隐藏，再等待主进程确认；显隐时间戳记录在 `chatwindow.log`。这是响应机制调整，实际端到端 <300ms 指标仍需构建版实测。
主程序在检测到前台全屏应用时停用房间相机，但继续以 30 FPS 处理热键与命令，避免暂停绘制同时阻塞小窗响应。

PlayMode 测试（`Assets/AiPeople/Tests/PlayMode`）：

1. 启动契约桩：`python Tools\dev_stub\aipeople_stub.py --port 8767`
2. 命令行运行：

```bat
"D:\unityeditor\6000.1.1f1\Editor\Unity.exe" -batchmode -nographics ^
  -projectPath "D:\AIPeopleGit\UnityClient\AiGirlFriendUnity" ^
  -runTests -testPlatform PlayMode ^
  -testResults "Logs\playmode_results.xml" -logFile "Logs\batch_tests.log"
```

覆盖（5 项）：运行时装配冒烟；状态与流式对话契约（`delta` 累计 == 已提交正文）；30 轮循环零协议错误；**游戏世界动作状态机**（清单校验/时间推进/煮面出实体）；**游戏世界 HTTP 服务往返**（actions/execute/project/fact report）。契约桩未运行时端到端用例自动跳过（`Assert.Ignore`）。**以上均为契约级验证，真实模型验收以正式后端与人工验收为准。**

后端侧对应测试（用 Unity「导出游戏世界契约样例」的真实 JSON 作夹具）：

```bat
cd D:\AIPeopleGit\ai-girlfriend\AiPeople
python -m pytest tests\world_mind\test_remote_game_world.py -q
```

## 权威文档（后端仓库）

- 设计：`AiPeople/设计文档/AI设计/当前权威设计/Unity客户端游戏形态与集成设计_20260910.md`
- 施工计划：`AiPeople/设计文档/AI设计/当前权威设计/Unity客户端施工计划_阶段A_20260910.md`

## 女主模型

房屋模型已接入，渲染保持 15,000 三角面：见 [房屋模型接入、重现步骤和已知限制](Tools/Apartment/README.md)。已通过入口至全屋的玩家/女主往返、14 处外边界和 4 处隔墙阻挡测试。六处门窗可通过小窗“房间”面板控制，详见 [门窗交互](Tools/Apartment/DOORS.md)。已完成 [房屋比例与壁纸镜头校准](Tools/Apartment/CALIBRATION.md)，并继续实施 [性能优化与实测](Tools/Performance/README.md)：小窗独立轻量场景、空闲降频、完整遮挡停绘及小步进行走修复，25 项相关回归通过。本机解锁桌面短时渲染/节流和原生开关验证已通过；[真实 UI 输入与内容帧](Tools/Performance/UI_LATENCY.md)已补验，并修复历史文字越界；目标硬件与后端同跑、8 小时稳定性仍待完成，不能视为 M1.5 整体验收通过。

- 资源位置：`AiGirlFriendUnity/Assets/Art/Characters/baiweixi/`（Tripo 导出，Git LFS 管理）：
  - `tripo_convert_f38d68cf-….fbx` + 同名 `.fbm` 贴图目录 —— **绑骨 + 动画版**（Unity 内 65 节点、21 个蒙皮网格、21 张 basecolor；**4 段动画：idle / walk / afraid / sing_02**）；
  - `BaiWeixiAnimator.controller` —— 由 `AiPeople/生成女主 Animator Controller` 菜单生成，默认状态为 idle；
  - 历史版本（静态版、无动画绑骨版）保留在 `GirlModel/` 归档 zip 与 git 历史中。
- 导入规范：`AiPeopleCharacterModelPostprocessor` 对 `Assets/Art/Characters/**` 自动设置 **Rig=Humanoid**、导入动画/BlendShape，并把 idle/walk/run 类片段设为循环。已验证 Avatar `isValid=True, isHuman=True`。
- 接入方式：场景中的 `AiPeopleApp` 组件通过 `heroineModelPrefab` + `heroineAnimatorController` 引用；运行时按身高 **1.62 m** 自动缩放并落地，把内置管线材质转换为 URP Lit（保留原 basecolor 贴图），并挂载 Animator 播放 idle。模型缺失时自动降级为胶囊占位 + 名牌。
- 调整：位置 / 朝向 / 身高在 `AiPeopleApp` 的 Inspector（`heroinePosition` / `heroineYaw` / `heroineHeight`）直接改，不需要改代码。
- 更换模型：新 FBX 与 `.fbm` 放入同一目录 → 更新 `Assets/AiPeople/Editor/AiPeopleModelProbe.cs` 的 `BaiweixiModelPath` → 删除旧 `BaiWeixiAnimator.controller` → 菜单 `AiPeople/重建 Apartment 场景`（会先探测并输出节点/蒙皮/贴图/Avatar/动画片段统计，再生成 Animator）。
- 补充动作：优先在 Tripo 网页版继续生成；或用 Mixamo 免费动作库（**无需上传本模型**，选内置角色下载 "Without Skin" 的 FBX，导入后由 Humanoid 自动重定向）。外观（猫耳女仆）与角色正典（宽大上衣 + 长裤）的差异仍属 D3。

## M1 已知占位与限制

- 白未晞为绑骨 + 动画模型（Humanoid Avatar 有效、4 段动作，场景内默认播放 idle）；缺失的日常动作（煮面/坐下/躺下等）待按需补充；外观与正典服装的差异待 D3。
- 房屋已使用导入的实时 3D 模型；按用户参考落实全屋家具与九种家电，当前为可替换低模外形，正式漫画材质与细节后续细化。见 [家具布局与重现](Tools/Apartment/FURNISHING_LAYOUT.md)。
- 对话显示历史保存在本地（`Application.persistentDataPath`，按 saveId 分文件）；它是显示缓存，事实以后端提交事务为准；后端未提供历史接口，重启后场景内显示由本地缓存恢复。
- 开发期字体使用 Windows 系统字体（Microsoft YaHei 等）动态生成；**发布前**必须替换为随包授权的 CJK 字体资产。
- blockout 材质在运行时用 `Shader.Find("Universal Render Pipeline/Lit")` 创建；**打包发布**时需把 URP Lit 加入 Graphics 设置的 Always Included Shaders（或改为材质资产），否则独立构建中可能渲染为粉色。

最新家具布局包：`Builds/CatGirlfriend-Furnished-20260914/CatGirlfriend.exe`，结果见 [家具施工验证](output/apartment-furnishing-layout/RESULTS.md)。保留前版 `CatGirlfriend-UiVerified-20260914` 及其 [小窗实测记录](output/ui-latency/RESULTS.md)。使用新版前先退出旧播放器，避免单实例保护唤出旧版。

家具调整后的相关回归 29 项通过。用户已明确跳过 8 小时稳定性测试，记为未测试并继续施工；目标 12GB 显卡与实际后端同跑仍未验证。该决定覆盖上文历史待验描述。


## 从 GitHub 获取本次 Unity 基线

本目录位于 `774348172/AiPeopleGirlFriend` 仓库的 `UnityClient/` 下；仓库根目录既有后端保持原样。
本次上传分支为 `codex/unity-client-20260916`，包含 Unity 6000.1.1f1 工程、Assets 与 .meta、包锁定文件、项目设置、工具和源模型 ZIP。

```powershell
git lfs install
$env:GIT_LFS_SKIP_SMUDGE = '1'
git clone --branch codex/unity-client-20260916 https://github.com/774348172/AiPeopleGirlFriend.git
Remove-Item Env:GIT_LFS_SKIP_SMUDGE
cd AiPeopleGirlFriend
git lfs pull --include='UnityClient/**'
```

用 Unity Hub 打开 `UnityClient/AiGirlFriendUnity`。先安装 Git LFS 并下载完整资源，不能直接把 GitHub Download ZIP 中的 LFS 指针当作模型使用。

后台服务代码位于仓库根目录的 `AiPeople/`；上文机器绝对路径是原开发环境示例，其他机器请替换为本机克隆目录。此次仅添加 Unity 工程，没有同步开发机上后端的其他未发布修改，也不代表远端旧后端与当前客户端已经做过端到端验收。

上传检查还发现远端原有 `AiPeople/training_packages/models/Qwen3.5-4B/tokenizer.json` 的 LFS 对象返回 404。这是后端原有缺失，未在本次 Unity 上传中修改；上面的下载命令限定为 UnityClient，避免被该后端缺失项阻断。

可在 UnityClient 目录用 PowerShell 构建（编辑器路径按安装位置调整）：

```powershell
$projectPath = (Resolve-Path AiGirlFriendUnity).Path
$env:AIPEOPLE_BUILD_PATH = Join-Path (Get-Location) 'Builds/CatGirlfriend/CatGirlfriend.exe'
$buildLog = Join-Path (Get-Location) 'build.log'
& 'D:/unityeditor/6000.1.1f1/Editor/Unity.exe' -batchmode -quit -projectPath $projectPath -executeMethod AiPeople.EditorTools.AiPeoplePlayerBuilder.BuildWindowsPlayer -logFile $buildLog
```

已有 PlayMode 结果保留在 `output/` 的指定 XML/RESULTS 文件中。其余日志、历史截图、生成中间产物仅在开发机保留；历史文档中的这些本地证据链接不一定随源码提供。Library、Temp、Builds、工具临时依赖和聊天存档未纳入本次上传。
