# 点击后变成普通窗口：2026-09-14 修复

本机复现日志备份在 `output/wallpaper-click-fix/Player.log`。启动日志先显示“壁纸层已挂载并通过校验”，随后出现“托盘切换窗口模式 → Normal”，且本地 `aipeople_shell.json` 保存为 `windowMode: 2`。该次退出壁纸来自托盘的双向模式切换命令，而非挂载失败。

## 最终行为

- 托盘菜单的旧“切换窗口模式”替换为单向“恢复桌面壁纸”，连续选择都请求 Wallpaper。
- 小窗标题栏只保留“恢复桌面壁纸”，普通窗口选项放在原设置面板，避免日常聊天时误触切出壁纸。
- 已正确挂载时，重复恢复请求仅保存意图，不再改分辨率、重新挂载或重启热键。
- 新模式请求取消旧的窗口等待/挂载协程，避免旧尝试在新模式建立后执行过时的降级。
- 主动选择普通窗口时先解除桌面父子关系，再恢复普通窗口样式。
- ShellTests 每项测试前后保留并恢复用户的原始设置文件，避免测试改变下次启动模式。
- 本机原 `windowMode: 2` 已备份并修正为 Wallpaper；其他设置保持原值。此修正针对本机，不对所有用户的显式普通窗口偏好做强制迁移。

## 验证

1. `AiPeople.Tests.ShellTests`：4 项通过，0 失败；包括菜单取消不投递模式命令、重复恢复不保存 Normal、设置持久化和原有会话状态测试。结果：`output/wallpaper-click-fix/shell-tests.xml`。
2. Windows 构建成功：`Builds/CatGirlfriend-WallpaperFix-20260914/CatGirlfriend.exe`。
3. `python Tools/Shell/verify_wallpaper.py Builds/CatGirlfriend-WallpaperFix-20260914/CatGirlfriend.exe`：连续两次启动，每次三轮托盘左键原生回调、HTTP 小窗显隐及恢复壁纸请求；检查主窗口句柄、桌面父级、WS_CHILD/无标题栏样式及图标视图前后关系，均通过。结果：`output/wallpaper-click-fix/native-results.json`。

构建版测试使用本程序的原生回调及本地接口，没有注入鼠标点击。脚本在 UI 端口已被占用时拒绝运行，并在测试后退出自己启动的播放器。运行前应先退出旧版。
