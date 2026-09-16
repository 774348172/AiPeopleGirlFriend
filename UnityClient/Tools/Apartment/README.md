# 用户房屋模型接入与全屋通行（2026-09-14）

已完成全屋通行、第一批六处门窗交互和住宅工作尺度/固定客厅机位校准；纸箱入口视线已复核，M1.5 的实际性能验收仍未完成。当前结果见 [房屋比例与壁纸镜头](CALIBRATION.md)，交互用法见 [门窗交互与验证](DOORS.md)。

## 当前结果

- 来源：`GirlModel/房屋框架模型.zip`，包含 Tripo FBX 和 JPEG basecolor；原始 ZIP、FBX 与贴图保持不变。来源哈希见 `source-manifest.json`。
- 游戏资源：`AiGirlFriendUnity/Assets/Art/Apartment/ImportedShell/ApartmentShell.prefab`；`Apartment.unity` 显式引用该 Prefab。高面数原始 FBX 不被游戏 Prefab 引用。
- 原始模型：1,900,184 三角面、1,780,607 顶点。先减面至 15,000，再修复开口并消除切割增加的面数。
- 当前渲染：墙体 13,507，厨房门 199，主卧门 234，次卧门 202，浴室门 224，公共过道门 391，阳台滑动面板 183，门槛/门框封边 60，合计 **15,000 三角面**。UV 与 16 位索引保留。
- 碰撞：7 个 MeshCollider 合计 **8,680 三角面**，另有 5 个门槛/封边 BoxCollider、3 个阳台边界 BoxCollider。碰撞面不参与渲染。没有用整个房屋的凸包或实心盒子取代室内碰撞。
- 材质：持久化 URP Lit 与原始颜色图集（4096 上限）；门槛使用独立低光泽材质。整屋图集不接受窗户氛围染色。
- 平面保留约 12m 宽、绕 Y 轴 -90°、客厅地面约 Y=0 的工作坐标；后续竖向校准将门顶设为 2.10m、最高墙体约 2.715m，保留地面和约 1.1m 的阳台栏杆。详见 CALIBRATION.md；仍是剖切模型，不是完整建筑测绘重建。
- 家具仍为占位；纸箱仍位于客厅靠窗角落，次卧没有分配给女主。

## 修复了什么

1. 厨房门保留上轮修复。主卧、次卧、浴室门连同把手拆分为独立部件并固定打开；主卧残余把手碰撞一并移走，并将过窄门口加宽、补上门框切口封边。
2. 公共过道封闭面板拆开，向客厅侧打开，避免打开后反过来堵住通往次卧的窄道。
3. 阳台中间滑动面板平移，形成可见的真实开口；不是仅关闭碰撞让角色穿过玻璃。
4. 原模型在封闭门板下缺少连续地面。新增公共过道、阳台、主卧、浴室门槛，同时提供可见几何和碰撞，避免 NavMesh 有路线但角色脚下无地面。
5. 阳台正面及两侧栏杆有连续隐藏碰撞，覆盖栅条缝与转角，保留原有可见栏杆。
6. 重新离线烘焙当前房屋与家具的导航：半径 0.30m、高 1.7m、台阶 0.25m、体素 0.06m。玩家运行时和测试统一使用 `PlayerController.ConfigureCapsule`：半径 0.28m、高 1.7m、skin 0.02m、台阶 0.25m。
7. `ImportedApartmentLayout.TraversalPoints` 仅描述本地室内位置，不创建新的后端 Waypoint。保留 `apartment_table`、`apartment_kitchen`、`apartment_door`。`HeroineDirector.TryWalkToInteriorPoint` 复用实际行走路径，仅驱动表现，不提交动作、地点或房间归属事实。

## 验证

`output/apartment-traversal/playmode-results.xml`：**6 passed / 0 failed**。

- 资源与预算、原有三个后端锚点、杯子/纸箱交互仍通过原接入测试。
- 原厨房玩家进出及女主实际行走测试继续通过。
- 玩家使用真实 CharacterController，从玄关到客厅、厨房、主卧、次卧、浴室、阳台、公共过道，逐一返回；再走主卧→次卧→浴室→阳台。沿导航角点连续移动，不靠传送跨过门洞。
- 实际 HeroineDirector 以默认行走速度完成 7 条往返路径，逐帧及段内采样检查胶囊净空、脚下地面、终点；测试以 4 倍游戏时间加速，未提高 walkSpeed；未写入权威地点、动作或物品状态。
- 14 个外边界测点覆盖外墙、窗、阳台正面、两侧和两个转角；持续向外推动角色后仍有地面、未越界，并正常走回室内。4 个隔墙测点验证不能直穿厨房、卧室与浴室隔墙，但可绕门返回。
- 以上为通行子项的全开姿态验证；新增动态门状态、挡人暂停和女主等待恢复测试见 DOORS.md。不能将结果扩展到任意角色尺寸或跳跃。

诊断文件：`output/apartment-import/validation.txt`、`output/apartment-traversal/survey.txt` 和 `grid.csv`。修复前扫描与预览保留在 `output/apartment-traversal/baseline/`；原主卧、次卧、浴室、阳台和右侧过道均不可达。

## 重现

Unity **6000.1.1f1**。正常打开当前场景无需 Python 或重复减面。

1. 初次导入：保留标准 FBX/贴图文件名，执行 `AiPeople.EditorTools.ApartmentShellImporter.Inspect`，设置材质并导出 `output/apartment-import/source-mesh.bin`。该命令不覆盖优化 Prefab。
2. 安装离线依赖 `numpy`、`pymeshlab==2025.7.post1`；运行 `python Tools/Apartment/optimize_shell.py`，生成未切割的 15,000 面渲染缓冲和 8,000 面碰撞缓冲。只重做渲染时使用 `--skip-collision`。
3. `python Tools/Apartment/repair_kitchen_door.py`：从上步缓冲重新拆分厨房门，输出 `repaired-*-mesh.bin` 和 `kitchen-door-repair.json`。
4. **新增** `python Tools/Apartment/repair_room_passages.py`：从上步输出拆分其余挡路门、滑动面板并修正主卧门口；渲染/碰撞采用相同切割及姿态。仅对墙体渲染网格做少量 UV 减面，保留切割边界，将门槛/封边一并纳入 15k 预算。输出 `walkable-*-mesh.bin`、各门缓冲和 `room-passages.json`。
5. 执行 `AiPeople.EditorTools.ApartmentShellImporter.Install`：保存 Mesh、材质、默认开启的交互门窗、真实门槛、阳台碰撞、Prefab 和基础导航；门叶采用沿宽度/法向的局部坐标并按门洞调整宽度，更新场景引用及机位；校验原锚点及全部室内往返路径，生成预览。
6. 执行 `AiPeople.EditorTools.ApartmentTraversalAudit.Survey` 可导出详细碰撞/可达性网格和卧室 CharacterController 直穿门洞诊断。它不改场景或资产。
7. PlayMode：`-runTests -testPlatform PlayMode -testFilter AiPeople.Tests.Apartment -testResults <绝对XML路径>`；不要同时使用 `-quit`。
8. 构建方法：`AiPeople.EditorTools.ApartmentShellImporter.BuildPreviewPlayer`。当前目标 `Builds/CatGirlfriend-Furnished-20260914/CatGirlfriend.exe`；采用轻量启动场景选择主/小窗进程，见 [性能施工记录](../Performance/README.md)。后续版本应换新目录，避免覆盖运行中的旧包。

Unity 批处理通用参数：`-batchmode -projectPath <绝对项目路径> -executeMethod <方法> -quit -logFile <绝对日志>`。PowerShell 使用 `Start-Process -PassThru` 并等待进程结束，不能将 GUI 启动器的即时退出当成构建/测试完成。

切割脚本每次从前一阶段的未切割输入重新生成，不会累计切割。这些测量只适用于本次模型和坐标校准；更换模型必须重测。只改家具时执行 `AiPeople.EditorTools.ApartmentFurnishingRefresh.Refresh` 重新烘焙导航和生成预览，无需重装或减面外壳；改碰撞、门姿态后也必须重烘焙并重跑通行测试。

## 仍需完成

- 六处已拆分门窗支持交互与局部门洞导航更新；其余外窗仍保留合并结构。门窗状态是当前主进程的本地场景状态，重启恢复初始开启姿态，不写入后端动作或房间归属事实。
- 门高、墙体工作比例、固定客厅机位及纸箱视线已完成本轮校准；正式美术阶段仍需整理剖切墙体、窗框和完整天花板。
- 原图生模型存在几厘米地面起伏、低处门底残片、局部贴图拉伸、粗糙墙角；本轮目标是通行，不是最终美术重建。
- 未测 GPU 帧时间、功耗或显存，不能由面数推断帧率提升。
- `output/apartment-import/game-house.png` / `game-house-top.png` 是 Unity 网格渲染预览，有编辑器补光，不是 Windows 桌面截图。游戏仍保留昼夜氛围。

旧版厨房测试包、24 万面/1.5 万面比较图和 `baseline-15k-before-door` 均保留。使用新版前先退出旧播放器，否则单实例保护可能唤出旧版。

## Windows 构建复核

`Builds/CatGirlfriend-House-Walkable-20260914/CatGirlfriend.exe` 构建成功（`output/apartment-traversal-build.log`）。对该产物运行：

```powershell
python Tools/Shell/verify_wallpaper.py Builds/CatGirlfriend-House-Walkable-20260914/CatGirlfriend.exe --output output/apartment-traversal/windows-smoke
```

两次启动、每次三轮托盘原生左键回调/小窗显隐/恢复壁纸请求均通过；主窗口保持同一桌面父级、WS_CHILD、无标题栏，并位于桌面图标视图下方。结果为 `output/apartment-traversal/windows-smoke/native-results.json`，对应播放器日志也保留在该目录。测试通过程序原生回调和本地 HTTP 接口进行，没有注入物理鼠标点击；测试创建的进程已退出。

## 用户指定家具布局

2026-09-14 已按参考落实全屋可替换低模家具与九种家电，重新烘焙导航，29 项相关回归通过。纸箱右侧候选遮挡出口，调整至阳台门左侧室内窗边。见 [家具布局与重现](FURNISHING_LAYOUT.md)、[结果与预览](../../output/apartment-furnishing-layout/RESULTS.md)。下一步替换正式美术资产及补装饰细节。
