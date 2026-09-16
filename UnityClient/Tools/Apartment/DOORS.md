# 六处门窗交互（2026-09-14）

在全屋通行基础上，厨房门、主卧门、次卧门、浴室门、公共过道门、阳台移窗已可开关。房屋总渲染预算仍为 **15,000 三角面**，碰撞网格为 8,680 面，另有门槛/边界盒碰撞。

## 使用

- Windows：打开对话小窗，点击顶部 **房间**，逐项打开/关闭。点击 **对话** 返回聊天，输入草稿保留。壁纸主窗口仍不接收鼠标键盘，也不切回普通窗口。
- Editor 第一人称：靠近门叶并注视它，按 **E**。提示随开关状态刷新。
- 通道有人时，关门请求会拒绝并说明原因。运动途中有人进入扫过区域时暂停，人离开后继续；可请求反向移动，但同样执行防夹检查。
- 门窗初始为打开，重启恢复该初始姿态。其他未拆分的外窗本轮不提供交互。

## 实现边界

`ApartmentDoor` 仅修改真实门叶的姿态和碰撞。开关约 0.65 秒，滑动面板与转轴门共享控制接口；重复指定同一目标状态不会反向或重启动画。针对主卧/次卧/浴室门洞校准门叶宽度，保持厚度、UV 和面数；视觉与碰撞采用同一局部坐标变换。

导航保留预烘焙的全开基础网格，关闭或运动中的门洞由局部 `NavMeshObstacle` carving 阻断，不在每次操作时重烘焙整套房屋。额外的同步门洞检查填补 carving 延迟的一帧，阻止旧路径被继续使用。原全开门叶占据区域仍保守地保留在基础网格中。

`HeroineDirector` 发现通路版本改变后重新计算路线；不可达时原地等待，开门后继续。移动前另做门叶胶囊扫掠检查。门叶运动同时检查真实玩家 CharacterController 和已注册女主的身体范围。没有为了穿门而传送角色、停用墙体碰撞或改变后端动作计时。

门窗状态在主进程内维护，不调用 `GameWorldState.Execute`，不产生女主动作完成或房间归属事实。原房屋整体图集不用于氛围染色；导入房屋也不再用旧占位窗布尔值向场景描述拼接笼统的“窗关着”。

## 小窗接口

- `GET /ui/room`：读取主线程预先发布的 JSON 快照，包含 id、label、open、targetOpen、moving、blocked。
- `POST /ui/door`：例如 `{"id":"KitchenDoor","state":"closed"}`，state 只接受 `open` 或 `closed`；返回 accepted 和 reason。
- HTTP 工作线程只处理文本及投递命令。实际场景访问在 Unity 主线程执行，响应表示主线程已接受或拒绝该请求，不表示动画已经结束。
- 无效参数返回 400；未就绪或未在两秒内确认返回 503。尚未执行的超时命令被取消，防止晚到的点击改变门窗。继续使用既有请求大小与并发限制。
- 房间面板打开且小窗可见时约每 0.3 秒查询一次状态；不增加隐藏小窗的轮询负担。小窗 Canvas 单独按 900×420 布局，按钮字号和命中区域与实际窗口一致；标题拖动区域避开功能按钮。

## 验证与重现

`output/apartment-doors-all-tests.xml`：**18 passed / 0 failed**，包含：

- 六处门窗各两次关闭/打开；关闭后的实际玩家阻挡、导航立即不可达、再次开启后的可达性、重复请求幂等、姿态无漂移。
- 玩家站在门口时拒绝关门；中途进入时暂停，离开后完成；女主无 CharacterController 时也能防夹。
- 女主行走途中关门后等待，开门后继续抵达，不生成权威地点、物品或动作事实。
- 小窗实际按钮经 HTTP 及主线程队列控制场景门叶；输入草稿区域切换；错误状态拒绝；超时命令不延后执行。
- 既有全屋通行、14 处外边界、4 处隔墙、UI 接口、小窗显隐延迟与壁纸恢复回归。

测试过滤器：`AiPeople.Tests.Apartment;AiPeople.Tests.RoomControlsTests;AiPeople.Tests.ChatUiServerTests;AiPeople.Tests.ChatWindowLatencyTests;AiPeople.Tests.ShellTests`。Unity PlayMode 使用 `-runTests -testPlatform PlayMode -testFilter "<过滤器>" -testResults <绝对XML>`，不加 `-quit`。

重新生成 Prefab 与基础导航：按 README 中的离线缓冲步骤运行 `ApartmentShellImporter.Install`。`ApartmentShellImporter.PreviewDoorStates` 输出开/关状态的真实网格预览；不保存临时预览姿态。UI 预览：`output/apartment-doors/room-controls.png`；关门全屋：`output/apartment-import/doors-closed.png`。这些是编辑器渲染，不是桌面截图。

本轮门窗交互存档版本为 `Builds/CatGirlfriend-Doors-20260914/CatGirlfriend.exe`。后续房屋比例、居住机位和纸箱视线校准见 [CALIBRATION.md](CALIBRATION.md)；GPU/显存实测仍未完成，不能标记为 M1.5 整体验收。

## Windows 产物验证

上述路径已构建成功，日志 `output/apartment-doors-build.log`。运行：

```powershell
python Tools/Shell/verify_wallpaper.py Builds/CatGirlfriend-Doors-20260914/CatGirlfriend.exe --doors --output output/apartment-doors/windows-smoke
```

六处门窗逐一关闭并重新打开，共 12 次状态转换，重复请求同一状态均被接受；整个过程主窗口保持原 HWND、桌面父级及图标下方挂载关系。另验证两次启动、6 轮托盘原生回调/小窗显隐/恢复壁纸。结果 `output/apartment-doors/windows-smoke/native-results.json`，验收汇总 `output/apartment-doors/acceptance.json`。测试通过本程序原生回调和本地接口进行，没有注入物理鼠标；测试启动的主/小窗进程已退出。
