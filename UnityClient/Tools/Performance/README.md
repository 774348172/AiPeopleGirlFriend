# 壁纸性能施工与测量

本轮继续 M1.5：常驻渲染、空闲与遮挡节流、小窗和门窗操作的开销。性能结果必须注明硬件、渲染 API、分辨率和真实渲染状态。

## 已实施的优化

1. **小窗独立场景**：发行版先进入 `PlayerBootstrap.unity`，根据 `--chat-window` 选择 `ChatWindow.unity` 或 `Apartment.unity`。小窗不再反序列化房屋、人物和人物动画资源；Editor 直接打开 Apartment 的开发方式仍可用。
2. **D3D11 优先**：Windows 发行构建使用 D3D11，保留 D3D12 备选。子进程跟随主进程实际选中的 API。构建结束后恢复 Editor 原有 API 设置。切换依据为本机实际驻留内存差异，不能据此声称所有显卡 D3D11 都更快。
3. **遮挡停绘**：依据壁纸所在显示器的工作区和 DWM 实际窗口边界判断前台窗口是否完整覆盖桌面，支持普通最大化窗口及全屏窗口。小窗位于前台时检查其后的窗口。忽略透明、最小化和 DWM 隐藏窗口；额外使用 WTS 会话状态识别锁屏，因为 LockApp 本身可能被 DWM 标为隐藏。普通窗口模式不使用壁纸遮挡暂停。
4. **相机与 Canvas 同步停绘**：保存两者原有 enabled 状态，恢复时不会误开启原本隐藏的 UI。停绘期间 Unity 仍以最多 15 FPS 处理主线程命令和世界表现，HTTP 服务保持运行。
5. **空闲降频**：壁纸无视觉活动 15 秒后降至最多 15 FPS；小窗可见、生成对白、女主行走或门窗运动时保持用户设置的活跃帧率（默认 30）。UI 命令入队执行时立即唤醒帧率；完整遮挡时唤醒命令处理不强行开启绘制。隐藏小窗继续使用之前延迟修复后的 30 FPS、Canvas 停绘和轻量可见性轮询，不恢复会造成开窗等待的 1 FPS。
6. **行走与帧率**：地面采样仅修正人物 Y，不再用最近 NavMesh 点回写 X/Z。原算法可能在重建地面高差处抵消一个小时间步的全部位移，导致一直走不到厨房；新增极小时间步回归覆盖此问题。

遮挡检测是保守的单窗口判断，不合并多个小窗口的覆盖区域；不能把这种情况当作已经验证的完全遮挡。没有为省电停止后端动作计时、生成状态或把角色传送到目的地。

## 可复现的发行版测量

正常启动不创建性能采样组件或写性能文件。设置环境变量 `AIPEOPLE_PERF_OUTPUT` 后，`PerformanceProbe` 才在各自进程内创建 CSV：

- 帧间隔、Unity FrameTiming 的 CPU 主线程/渲染线程/GPU 时间、有效采样标记；
- 实际暂停状态、分辨率、目标帧率、绘制调用和三角面计数；
- Unity 已用内存（只是一部分进程占用）。

`BuildPreviewPlayer` 构建普通 Release 播放器并开启 Frame Timing 支持，非 Development Build。构建路径可以通过 `AIPEOPLE_BUILD_PATH` 指定；已有可执行文件不会被覆盖。

2026-09-16 起，预览构建和菜单 `BuildWindowsPlayer` 共用 `BuildWindowsPlayerAtPath`，统一三场景启动入口、Frame Timing 与 D3D11 优先策略。预览入口继续拒绝覆盖已有 exe；菜单入口保留既有输出行为并支持上述路径变量。场景只做读取及依赖校验，构建不会重新生成启动/小窗场景。验收见 `output/release-entry-20260916/RESULTS.md`。

在解锁的交互桌面运行：

```powershell
python Tools/Performance/measure_player.py <新版本exe> --output output/apartment-performance/desktop-run --seconds 20
python Tools/Performance/summarize.py output/apartment-performance/desktop-run
python Tools/Performance/check_desktop.py output/apartment-performance/desktop-run
```

工具仅启动目标程序及自己拥有的临时覆盖窗口，不移动或关闭其他程序。记录正常绘制、可见空闲、工作区覆盖、全屏覆盖、恢复、6 次小窗显隐、6 处门窗各一次关闭/打开。窗口必须实际取得前台，失败会中止；临时窗口和测试播放器会在 finally 中退出。小窗延迟通过自己的托盘原生回调到真实窗口可见性测量，没有注入物理鼠标，也没有发送聊天内容。

Windows `psutil` 记录进程工作集、专用提交、CPU 和句柄；PDH `GPU Process Memory` 记录各进程的专用/共享 GPU 内存。不要拿 `nvidia-smi` 的整卡占用当作本程序显存。未取得的计数为 null/空列，不能填成 0。

锁屏时只能运行受限驻留检查：

```powershell
python Tools/Performance/measure_player.py <新版本exe> --output output/apartment-performance/locked-run --paused-only --seconds 20
```

这个模式会验证采样时相机确实处于暂停状态，**不代表正常壁纸帧率、遮挡切换或解锁恢复通过**。`--graphics-api d3d11` / `d3d12` 可用于同一二进制的 API 比较；旧基线小窗尚不跟随主进程的强制 API，解释结果时应分别检查两个 `device.txt`。

## 预算与验收边界

- 权威预算：12GB 显存级别机器与后端共享，客户端渲染 ≤4GB；默认活跃 30 FPS；小窗唤出 <300ms。主/小窗约 400/300MB 内存目标须注明测量口径，不能混用工作集和专用提交。
- 当前机器为 i9-14900K、RTX 4090 24GB、64GB 内存、1920×1080。不能替代目标 12GB 机器与实际后端同跑。
- `cpu_frame_ms` 包含帧率限速等待，33.3ms 不等于主线程实际工作 33.3ms；查看 `cpu_main_ms` / `cpu_render_ms`。未绘制时的 GPU 时间不用于正常渲染结论。
- CPU 百分比按一个逻辑核心=100% 记录；整机占比需再除以逻辑核心数。
- 房屋 15,000 面只是房屋预算；整帧还包含人物、阴影和深度等重复绘制，不能据房屋面数推算帧率。
- 本机解锁桌面短时渲染、空闲/遮挡/恢复及原生窗口显隐已补验。8 小时内存/句柄长跑、真实 UI 点击到内容首帧、12GB 与后端同跑尚须各自提供证据，未完成前 M1.5 不标记整体验收。

## 本轮产物与结果

最终 Windows 包：`Builds/CatGirlfriend-Performance-v2-20260914/CatGirlfriend.exe`。旧版和中间测量包保留。25 项相关 PlayMode 回归通过；锁屏驻留及原生挂载/门窗接口验证已完成。详细表格与未完成项见 [RESULTS.md](../../output/apartment-performance/RESULTS.md)，机器可读汇总为同目录 `acceptance.json`。解锁后顺序完成基线/最终版短时测量，16 项桌面证据检查通过；两次启动、6 轮托盘/恢复及 12 次门窗转换复验通过。原始数据在 `baseline-unlocked-20260914`、`final-unlocked-20260914` 和 `windows-smoke-unlocked-20260914`。原生显隐不是内容首帧，仍未完成目标硬件/后端、长跑和真实 UI 点击验收。

## 后续真实 UI 补验

真实热键/按钮事件至内容帧已在本机补验，3 轮小窗与面板操作、12 次门窗操作通过；修复历史文字越界，25 项回归通过。最新包 `Builds/CatGirlfriend-UiVerified-20260914/CatGirlfriend.exe`；[重现与测量边界](UI_LATENCY.md)、[实测结果](../../output/ui-latency/RESULTS.md)。此前 v2 数据仍保留原包归属。目标硬件/真实后端与 8 小时稳定性仍待完成。
