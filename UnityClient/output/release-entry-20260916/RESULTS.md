# Windows 发布入口修复验收 — 2026-09-16

## 问题与变更

菜单/CLI 的 `AiPeoplePlayerBuilder` 仅打包 Apartment，绕过了预览构建已采用的轻量启动场景。小窗虽然能通过 Apartment 的分支启动，但会先反序列化场景依赖。

菜单/CLI 与 `ApartmentShellImporter.BuildPreviewPlayer` 现共用 `BuildWindowsPlayerAtPath`：

- 打包顺序固定为 PlayerBootstrap、Apartment、ChatWindow。启动参数决定主进程加载 Apartment、小窗加载 ChatWindow。
- 使用已保存场景；缺失场景或轻量入口引用房屋/角色/家具时构建失败，不重写场景或丢弃编辑器中未保存内容。
- Release、D3D11 优先（D3D12 备选）、Frame Timing 设置统一；临时图形设置在 finally 恢复。
- 构建失败抛出异常，使批处理不能错误地返回构建成功。
- 保留预览入口不覆盖旧包的保护，菜单入口新增 AIPEOPLE_BUILD_PATH 输出覆盖选项。

## 交付与证据

包：`Builds/CatGirlfriend-EntryFix-20260916/CatGirlfriend.exe`。旧包保留；请使用此新路径。

- `build.log`：Succeeded，221 MB，16.4 秒，三场景顺序正确。
- `playmode.xml`：11/11 通过，0 失败，覆盖 ShellTests、ChatWindowLatencyTests、RoomControlsTests。加强既有场景依赖测试：检查三场景全部启用及两处轻量入口的依赖。
- `smoke/results.json`、`smoke/attachment.jsonl`：两次独立启动均挂到图标宿主，保留 WS_CHILD、图标正下方层级、非前台；12 次门窗 API 转换通过。
- `smoke/player-1.log`、`smoke/player-2.log`：主进程从 PlayerBootstrap 加载 Apartment，使用 Direct3D 11。
- `chatwindow.log`：小窗从 PlayerBootstrap 加载 ChatWindow，使用 Direct3D 11，无房屋/家具构建日志。
- `ui/`：启用局部诊断变量后，用 Computer Use 实际点击房间面板、返回对话、收起、再次通过本地激活接口打开、点击退出。小窗历史与房间状态正常显示；退出后本轮主/小窗进程均消失。
- `ui-attachment.jsonl`：小窗操作期间壁纸层级仍通过校验。

## 验证边界和遗留

- 本机 RTX 4090；后端 8767 未运行。本轮验证本地历史和离线降级，不代表真实模型对话或 12GB 同跑验收。
- 本轮小窗唤起使用本地 `/ui/activate`，没有重测托盘点击或全局热键；收起、房间切换和退出为实际鼠标点击。诊断模式临时让小窗出现在任务栏，正常两次启动没有此开关。
- 时间记录是程序事件/Unity 帧/GPU 读回，不能视为物理点击到显示器的延迟。
- 主进程仍有既有家具网格 `Cannot combine mesh that does not allow access` 警告，本轮未修改家具合批，未宣称家具外观通过验收。
- 8 小时稳定测试按用户决定不执行。M6 自主环境行为尚未施工。

## 重现

在 UnityClient 目录设置进程级环境变量 `AIPEOPLE_BUILD_PATH` 为新输出的 exe 绝对路径，再运行：

```powershell
& 'D:/unityeditor/6000.1.1f1/Editor/Unity.exe' -batchmode -quit -projectPath D:/AIPeopleGit/UnityClient/AiGirlFriendUnity -executeMethod AiPeople.EditorTools.AiPeoplePlayerBuilder.BuildWindowsPlayer -logFile <构建日志绝对路径>
python Tools/Apartment/verify_furnished_player.py <新exe> --output <新证据目录>
```

PlayMode 使用 `-runTests -testPlatform PlayMode -testFilter "AiPeople.Tests.ShellTests;AiPeople.Tests.ChatWindowLatencyTests;AiPeople.Tests.RoomControlsTests" -testResults <XML绝对路径>`，不加 `-quit`。小窗真实输入诊断见 `Tools/Performance/UI_LATENCY.md`。
