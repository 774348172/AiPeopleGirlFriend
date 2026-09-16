# 家具合批修复验收 — 2026-09-16

## 修复

`ApartmentFurnishings.Merge` 原先把运行时导入 FBX 一并提交给 `CombineMeshes`。GPU-only 网格不可读时合并失败，随后源对象仍被隐藏/删除。现仅处理本类生成的可读 Cube 装饰，导入网格保持原始层级、材质槽和变换。没有修改模型导入 Read/Write、模型比例、房屋尺寸、碰撞阈值或导航。

## 结果

- `playmode.xml`：Apartment 相关 PlayMode **21/21 通过，0 失败**。包含四个新增用例：两件真实模型的完整保留，以及可读/不可读多子网格保留。
- 源 Prefab 与实际房屋构建结果逐项比较：沙发 12,453、凳子 24,287 三角面，网格引用、材质数组、激活状态和相对变换一致。程序化家具继续使用原 5,000 三角面预算；导入模型另行检查，不再以误删除后的低面数当作通过。
- `build.log`：Windows Succeeded，221 MB，9.3 秒；输出 `Builds/CatGirlfriend-FurnitureBatchFix-20260916/CatGirlfriend.exe`。旧包保留。
- `smoke/results.json`：两次独立启动均保持图标下方壁纸挂载；首轮六扇门共 12 次开/关操作通过。
- `smoke/player-1.log`、`smoke/player-2.log`：均加载 SofaV2 和 StoolV2，无 `Cannot combine mesh that does not allow access` 警告。
- 第三次启动 PID 16668：通过本地 `/ui/mode` 临时切到普通窗口，Computer Use 直接查看新包游戏画面，确认灰绿沙发、抱枕、木腿和前方木质凳子/茶几实际显示，没有原来的消失问题。此项为真实运行画面检查，未以独立 Prefab 预览代替。
- `visual-player.log`、`visual-attachment.jsonl`：检查后恢复 wallpaper。切换请求刚返回时的一次即时采样未完成挂载，稍后采样 passed=true，日志确认图标下方校验通过；随后通过 `/ui/quit` 退出本次测试实例。

## 范围

后端 8767 未运行，日志中的 Curl error 7 为离线连接失败；本轮未验证真实模型对话。未运行八小时测试，也没有扩展 M6。已有家具美术质量和其他家具制作不在本次警告修复范围。

## 重现

在 UnityClient 工作目录运行测试（绝对路径替换为当前工作目录；测试命令不加 `-quit`）：

```powershell
& 'D:/unityeditor/6000.1.1f1/Editor/Unity.exe' -batchmode -projectPath D:/AIPeopleGit/UnityClient/AiGirlFriendUnity -runTests -testPlatform PlayMode -testFilter AiPeople.Tests.Apartment -testResults <结果XML绝对路径> -logFile <测试日志绝对路径>
$env:AIPEOPLE_BUILD_PATH='<新输出目录>/CatGirlfriend.exe'
& 'D:/unityeditor/6000.1.1f1/Editor/Unity.exe' -batchmode -quit -projectPath D:/AIPeopleGit/UnityClient/AiGirlFriendUnity -executeMethod AiPeople.EditorTools.AiPeoplePlayerBuilder.BuildWindowsPlayer -logFile <构建日志绝对路径>
python Tools/Apartment/verify_furnished_player.py <新exe路径> --output <新证据目录>
```
