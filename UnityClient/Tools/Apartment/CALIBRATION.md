# 房屋比例与壁纸镜头校准（2026-09-14）

本轮继续 M1.5 / 步骤 10A，在已经走通的平面结构上校正竖向尺度，并将常驻壁纸从远距离全屋施工鸟瞰改为客厅靠窗的固定机位。全部使用实时 3D 网格。

## 米制工作尺度

这些尺寸是本次模型的工程工作值，不是世界正典新增的建筑测绘数据。平面仍沿用约 12m 宽的既有坐标；人物总渲染包围盒高 1.62m（含耳朵等附件），玩家通行胶囊高 1.70m。

| 部位 | 校准前 | 校准后 |
|---|---|---|
| 五扇室内门的门叶高度 | 约 1.31～1.44m | 约 2.01m，门顶 Y=2.10m |
| 阳台滑动面板高度 | 约 1.09m | 约 2.015m，门顶 Y=2.10m |
| 外墙最高处 | 约 1.84m | 约 2.715m |
| 室内较低的剖切墙顶 | 约 1.39m 起 | 约 2.40m 起，保留原模型高低变化 |
| 窗台尺度参考 | 原始 Y=.60m | Y=.90m；原模型窗框起伏仍保留 |
| 阳台正面栏杆 | 相对阳台地面约 1.1m | 保持约 1.1m；隐藏保护碰撞顶降至 Y=.96m，与可见栏杆接近 |

`ApartmentMetricCalibration.ShellHeight` 对原始缓冲的世界 Y 分段校正；Y≤.12m 的地面、基础、门槛不动，X/Z 不动，阳台栏杆不跟随室内墙体拉高。栏杆与房屋连接的短回边平滑过渡。门叶以原渲染网格最高点为共同参照处理视觉和碰撞，保持原门底与所有门宽/铰链位置，门洞保护区域和主卧封边同步增高。

校准不增加三角面，房屋仍为 **15,000 渲染面、8,680 网格碰撞面**。原 FBX、ZIP、图集、离线切割输入不修改；每次 Install 都从原缓冲重算，不会累计拉伸。MeshCollider 与渲染使用相同的高度规则，导航重新烘焙。

这里校正了实际使用比例，没有把图生模型重建成精确建筑 CAD：内部墙体仍是剖切结构，没有新增完整天花板，窗框/墙角不平整和局部纹理拉伸仍需正式美术资产阶段处理。不能把墙体最高点当成全屋实测净层高。

## 常驻镜头

- 位置 `(-4.85, 2.60, -3.90)`，注视点 `(-1.50, .15, -1.55)`；16:9 垂直 FOV=54°。
- 人物站位保持 `(-2, 0, -3.2)`，默认朝向改为 315°，让人物正面进入镜头；行走时仍由原来的朝向逻辑控制。
- 16:10 保留横向构图；宽屏先增加两侧室内内容，到测量过的墙边界后调整 FOV。覆盖 16:9、16:10、21:9 的模型包围盒检查，人物避开画面边缘与底部任务栏区域。
- `ApartmentWallpaperCamera` 订阅 URP 的 `beginCameraRendering`，分辨率/宽高比变化后更新投影，不添加 RenderTexture 或额外相机渲染。内置管线另有 `OnPreCull` 兼容回调。
- Editor 的第一人称和全屋俯视检查仍保留。常驻镜头不跟踪角色跨房间；角色离开客厅后可能被真实墙体遮住，这是固定机位的范围限制。
- 主窗口仍作为桌面壁纸，不接收操作；房间控制仍在独立小窗。

纸箱保留客厅靠窗原位。分别从 Y=.95m（坐姿检查）和 Y=1.50m（站姿检查）向狭窄玄关尽头的真实入口表面进行射线检查，目标约 `(-1.1, eye, 5.99)`。检查全部六处门窗开启、关闭两种状态；没有用近处导航点替代实际入口，也没有移动纸箱或写入后端位置事实。

## 重现和证据

沿用 [README](README.md) 中的原模型减面/拆门缓冲，执行 `AiPeople.EditorTools.ApartmentShellImporter.Install` 即可生成校准后的网格、Prefab、导航、场景参数、测量报告与预览。

- `output/apartment-calibration/baseline/`：校准前预览、门尺寸、输入缓冲 SHA-256。
- `output/apartment-calibration/measurements.txt`：真实加载网格的门顶、碰撞顶、墙体包围盒、人物、栏杆、纸箱视线、三种比例投影测量。
- `output/apartment-calibration/wallpaper-16x9.png`、`wallpaper-16x10.png`、`wallpaper-21x9.png`：三种屏幕比例的编辑器补光预览，**不是 Windows 桌面截图，也不代表实时昼夜亮度**。
- `game-house-overview.png`、`game-house-top.png`：同一结构的全屋检查机位。

新增 `ApartmentCalibrationTests` 覆盖门高与头部高度的真实门叶阻挡、可见栏杆与保护碰撞一致、坐/站姿入口视线、门开/关状态、真实人物网格的构图与 URP 宽高比更新。既有全屋通行、门窗、防夹、小窗、壁纸恢复回归也需通过。

本轮构建使用独立目录 `Builds/CatGirlfriend-Calibrated-20260914/`，旧版保留。M1.5 的 GPU 帧时间和显存仍需独立实测，不能从本次面数或功能测试推断性能验收。

## 本轮验证结果

- Unity PlayMode：**21 passed / 0 failed**，结果 `output/apartment-calibration/playmode-results.xml`。包含新校准检查和上一轮 18 项相关回归；生成、测试、构建日志均在同一目录。
- Windows 构建成功：`Builds/CatGirlfriend-Calibrated-20260914/CatGirlfriend.exe`。
- 执行 `python Tools/Shell/verify_wallpaper.py Builds/CatGirlfriend-Calibrated-20260914/CatGirlfriend.exe --doors --output output/apartment-calibration/windows-smoke`：两次启动、6 轮托盘/小窗/壁纸恢复、六处门窗共 12 次状态转换均通过，始终保持原主窗口和桌面父级、位于图标下方。测试使用自己的原生回调及本地接口，没有注入物理鼠标。测试创建的进程均已退出。
- Windows 原始结果：`windows-smoke/native-results.json`；汇总及构建文件哈希：`acceptance.json`。预览仍为编辑器渲染，未冒充桌面实拍。
