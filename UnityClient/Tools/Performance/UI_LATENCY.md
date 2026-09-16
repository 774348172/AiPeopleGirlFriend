# 小窗真实输入与首帧检查

本检查使用 Windows Computer Use 的实际鼠标/热键输入，测试自己的程序，不发送聊天消息。

## 开启诊断

在新的输出目录启动新构建，设置进程环境变量：

- `AIPEOPLE_UI_LATENCY_OUTPUT`：绝对输出目录。主进程记录热键收到时间，小窗记录显示、状态就绪、真实按钮 pointer down/up、门窗确认以及 Unity 帧末。
- `AIPEOPLE_UI_CAPTURE=1`：可选，为上述关键帧增加 GPU 截图和读回完成标记。PNG 保存在同一目录；截图可能包含已有本地聊天内容，不要公开上传。
- `AIPEOPLE_UI_AUTOMATION=1`：仅在诊断开启时生效。把小窗临时列入任务栏，让忽略工具窗口的 UI 自动操作工具能够找到它；没有更换 UI、HTTP、事件处理或渲染路径。正常启动仍为不进任务栏的工具窗口。

未设置诊断目录时，不创建探针组件、按钮探针、截图和时序文件。不得把诊断环境变量写入用户全局设置。

使用 UI 工具选择返回的应用窗口，观察实际截图后点击。步骤：首次热键打开，切换房间，开关门，返回对话，点击收起，再重复打开/切换/关闭。保持默认主壁纸挂载，只让独立小窗接收输入。保留一次正常工具窗口样式的首开记录，区分自动操作识别开关对窗口样式的影响。

## 汇总

```powershell
python Tools/Performance/summarize_ui.py output/ui-latency/<新的记录目录>
```

`ui-summary.json` 给出每次耗时、样本数、中位数、最大值。主/小窗的 Unity Mono `Stopwatch` 起点不同，跨进程只用 UTC 毫秒；小窗内部用 Stopwatch，不能跨进程相减。

测量分层：

1. 热键被主程序收到→窗口显示→首个 Unity UI 帧末→对话状态内容帧末。
2. 真实按钮松开事件→面板内容帧末、门窗确认、或原生隐藏。
3. GPU 读回成功与 PNG：证明该帧有可验证的渲染结果。

这里不把 Unity 帧末称为显示器扫描完成；GPU 读回也包含诊断复制开销。按钮计时始于 Unity 收到真实 pointer-up，未包含鼠标硬件/驱动到 Unity 的输入队列延迟。截图、编码、日志均可能增加诊断开销。关闭按钮走本地立即隐藏路径，不可拿此前 HTTP `/ui/hide` 的约 0.23 秒代替点击关闭延迟。

门窗 `settled_ui` 必须匹配被操作门的目标状态且运动已结束；通常包含约 0.65 秒动画和房间状态查询延迟。不能把这段时间全部算作卡顿。

功能回归继续使用现有 ChatWindowLatencyTests、RoomControlsTests、ChatUiServerTests、ShellTests 以及 Apartment 系列。验收结论和实际包路径见 `output/ui-latency/RESULTS.md`。
