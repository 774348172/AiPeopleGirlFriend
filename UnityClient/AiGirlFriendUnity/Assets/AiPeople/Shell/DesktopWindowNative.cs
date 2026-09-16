using System;
using System.Text;
using UnityEngine;

namespace AiPeople.Shell
{
    /// <summary>
    /// 桌面窗口原生操作：无边框/不激活样式、壁纸层挂载与卸载、置底/置前、桌面窗口识别。
    /// 仅 Windows 播放器使用；任何失败都返回 false，由上层降级。
    /// </summary>
    internal static class DesktopWindowNative
    {
        /// <summary>
        /// 找到本进程的主窗口。注意：窗口挂到桌面（壁纸层）后不再是顶层窗口，无法再被枚举到，
        /// 因此第一次找到后**缓存句柄**（SetParent 不改变 HWND，句柄始终有效）。
        /// </summary>
        internal static IntPtr FindUnityWindow()
        {
            if (_cachedWindow != IntPtr.Zero && NativeMethods.IsWindow(_cachedWindow))
            {
                return _cachedWindow;
            }

            int processId = System.Diagnostics.Process.GetCurrentProcess().Id;
            IntPtr bestVisible = IntPtr.Zero;
            int bestVisibleArea = 0;
            IntPtr bestAny = IntPtr.Zero;
            int bestAnyArea = 0;

            NativeMethods.EnumWindows((hwnd, _) =>
            {
                NativeMethods.GetWindowThreadProcessId(hwnd, out int ownerProcessId);
                if (ownerProcessId != processId)
                {
                    return true;
                }

                if (!NativeMethods.GetWindowRect(hwnd, out NativeMethods.RECT rect))
                {
                    return true;
                }

                int area = (rect.Right - rect.Left) * (rect.Bottom - rect.Top);
                if (area > bestAnyArea)
                {
                    bestAnyArea = area;
                    bestAny = hwnd;
                }

                if (NativeMethods.IsWindowVisible(hwnd) && area > bestVisibleArea)
                {
                    bestVisibleArea = area;
                    bestVisible = hwnd;
                }

                return true;
            }, IntPtr.Zero);

            IntPtr found = bestVisible != IntPtr.Zero ? bestVisible : bestAny;
            if (found != IntPtr.Zero)
            {
                _cachedWindow = found;
            }

            return found;
        }

        private static IntPtr _cachedWindow = IntPtr.Zero;

        internal static string DescribeWindow(IntPtr hwnd)
        {
            if (hwnd == IntPtr.Zero)
            {
                return "0x0";
            }

            string className = GetClassName(hwnd);
            NativeMethods.GetWindowRect(hwnd, out NativeMethods.RECT rect);
            return "0x" + hwnd.ToInt64().ToString("X")
                + " class=" + className
                + " rect=(" + rect.Left + "," + rect.Top + "," + rect.Right + "," + rect.Bottom + ")"
                + " parent=0x" + NativeMethods.GetParent(hwnd).ToInt64().ToString("X");
        }

        /// <summary>
        /// 壁纸/桌面层样式：无边框、不激活、不进 Alt-Tab、置底。
        /// 注意：跨进程 SetParent 到 Progman/WorkerW 时，窗口应带 WS_CHILD（去掉 WS_POPUP），否则 Windows 会静默不生效。
        /// </summary>
        internal static void ApplyOverlayStyles(IntPtr hwnd)
        {
            int style = unchecked((int)(NativeMethods.WS_CHILD | NativeMethods.WS_VISIBLE));
            NativeMethods.SetWindowLong(hwnd, NativeMethods.GWL_STYLE, style);

            int ex = NativeMethods.GetWindowLong(hwnd, NativeMethods.GWL_EXSTYLE);
            ex |= unchecked((int)(NativeMethods.WS_EX_NOACTIVATE | NativeMethods.WS_EX_TOOLWINDOW));
            ex &= ~unchecked((int)NativeMethods.WS_EX_APPWINDOW);
            ex &= ~unchecked((int)NativeMethods.WS_EX_TOPMOST);
            NativeMethods.SetWindowLong(hwnd, NativeMethods.GWL_EXSTYLE, ex);
            ApplyFrameChange(hwnd);
            SendToBottom(hwnd);
        }

        /// <summary>样式修改后必须走一次 SWP_FRAMECHANGED 才会真正生效。</summary>
        private static void ApplyFrameChange(IntPtr hwnd)
        {
            NativeMethods.SetWindowPos(
                hwnd, IntPtr.Zero, 0, 0, 0, 0,
                NativeMethods.SWP_NOMOVE | NativeMethods.SWP_NOSIZE | NativeMethods.SWP_NOZORDER
                | NativeMethods.SWP_NOACTIVATE | NativeMethods.SWP_FRAMECHANGED);
        }

        /// <summary>
        /// 会话态样式：从壁纸层脱离后必须恢复为**可激活的顶层窗口**。
        /// 关键：清掉 WS_CHILD（带 WS_CHILD 的窗口在系统层面无法激活，SetForegroundWindow 必然失败）。
        /// </summary>
        internal static void ApplySessionStyles(IntPtr hwnd)
        {
            int style = unchecked((int)(NativeMethods.WS_POPUP | NativeMethods.WS_VISIBLE));
            NativeMethods.SetWindowLong(hwnd, NativeMethods.GWL_STYLE, style);

            int ex = NativeMethods.GetWindowLong(hwnd, NativeMethods.GWL_EXSTYLE);
            ex &= ~unchecked((int)(NativeMethods.WS_EX_NOACTIVATE | NativeMethods.WS_EX_TOOLWINDOW));
            ex &= ~unchecked((int)NativeMethods.WS_EX_TOPMOST);
            ex |= unchecked((int)NativeMethods.WS_EX_APPWINDOW);
            NativeMethods.SetWindowLong(hwnd, NativeMethods.GWL_EXSTYLE, ex);

            ApplyFrameChange(hwnd);
            ResizeToPrimaryMonitor(hwnd);
        }

        /// <summary>会话态样式：允许激活、进入任务栏与 Alt-Tab。</summary>
        internal static void ApplyInteractiveStyles(IntPtr hwnd)
        {
            int ex = NativeMethods.GetWindowLong(hwnd, NativeMethods.GWL_EXSTYLE);
            ex &= ~unchecked((int)NativeMethods.WS_EX_NOACTIVATE);
            ex &= ~unchecked((int)NativeMethods.WS_EX_TOOLWINDOW);
            ex |= unchecked((int)NativeMethods.WS_EX_APPWINDOW);
            NativeMethods.SetWindowLong(hwnd, NativeMethods.GWL_EXSTYLE, ex);
        }

        internal static void SendToBottom(IntPtr hwnd)
        {
            NativeMethods.SetWindowPos(
                hwnd, NativeMethods.HWND_BOTTOM, 0, 0, 0, 0,
                NativeMethods.SWP_NOMOVE | NativeMethods.SWP_NOSIZE | NativeMethods.SWP_NOACTIVATE);
        }

        /// <summary>把窗口精确摆放到主显示器整屏（壁纸模式用）。</summary>
        internal static void ResizeToPrimaryMonitor(IntPtr hwnd)
        {
            int width = NativeMethods.GetSystemMetrics(NativeMethods.SM_CXSCREEN);
            int height = NativeMethods.GetSystemMetrics(NativeMethods.SM_CYSCREEN);
            NativeMethods.SetWindowPos(
                hwnd, IntPtr.Zero, 0, 0, width, height,
                NativeMethods.SWP_NOZORDER | NativeMethods.SWP_NOACTIVATE | NativeMethods.SWP_SHOWWINDOW);
        }

        /// <summary>恢复成"正常窗口"：标题栏、可缩放、可最小化、进任务栏，并居中到一个合理尺寸。</summary>
        internal static void ApplyNormalWindowStyles(IntPtr hwnd)
        {
            int style = NativeMethods.GetWindowLong(hwnd, NativeMethods.GWL_STYLE);
            style &= ~unchecked((int)NativeMethods.WS_POPUP);
            style &= ~unchecked((int)NativeMethods.WS_CHILD);
            style |= unchecked((int)(
                NativeMethods.WS_CAPTION | NativeMethods.WS_SYSMENU | NativeMethods.WS_THICKFRAME
                | NativeMethods.WS_MINIMIZEBOX | NativeMethods.WS_MAXIMIZEBOX | NativeMethods.WS_VISIBLE));
            NativeMethods.SetWindowLong(hwnd, NativeMethods.GWL_STYLE, style);

            int ex = NativeMethods.GetWindowLong(hwnd, NativeMethods.GWL_EXSTYLE);
            ex &= ~unchecked((int)(NativeMethods.WS_EX_NOACTIVATE | NativeMethods.WS_EX_TOOLWINDOW));
            ex &= ~unchecked((int)NativeMethods.WS_EX_TOPMOST);
            ex |= unchecked((int)NativeMethods.WS_EX_APPWINDOW);
            NativeMethods.SetWindowLong(hwnd, NativeMethods.GWL_EXSTYLE, ex);

            int screenWidth = NativeMethods.GetSystemMetrics(NativeMethods.SM_CXSCREEN);
            int screenHeight = NativeMethods.GetSystemMetrics(NativeMethods.SM_CYSCREEN);
            const int windowWidth = 1600;
            const int windowHeight = 900;
            int x = Mathf.Max(0, (screenWidth - windowWidth) / 2);
            int y = Mathf.Max(0, (screenHeight - windowHeight) / 2);
            NativeMethods.SetWindowPos(
                hwnd, IntPtr.Zero, x, y,
                Mathf.Min(windowWidth, screenWidth), Mathf.Min(windowHeight, screenHeight),
                NativeMethods.SWP_NOZORDER | NativeMethods.SWP_SHOWWINDOW | NativeMethods.SWP_FRAMECHANGED);
        }

        /// <summary>
        /// 校验桌面层挂载是否正确：窗口必须与 SHELLDLL_DefView 同级，且紧贴在其下方，
        /// 这样桌面图标始终位于 Unity 窗口之上。
        /// 返回 "ok" 或失败原因。
        /// </summary>
        internal static string VerifyDesktopLayer(IntPtr hwnd)
        {
            IntPtr parent = NativeMethods.GetParent(hwnd);
            if (parent == IntPtr.Zero)
            {
                return "未挂载（窗口无父级）";
            }

            if (TryFindIconView(out IntPtr iconHost, out IntPtr iconView))
            {
                if (parent != iconHost)
                {
                    return "挂载宿主与图标宿主不一致（parent=0x" + parent.ToInt64().ToString("X")
                        + "，图标宿主=0x" + iconHost.ToInt64().ToString("X") + "）";
                }

                IntPtr belowIcon = NativeMethods.GetWindow(iconView, NativeMethods.GW_HWNDNEXT);
                if (belowIcon == hwnd)
                {
                    return "ok";
                }

                return "未紧贴图标视图下方（图标下方窗口=0x" + belowIcon.ToInt64().ToString("X") + "）";
            }

            // 找不到图标视图（桌面图标可能被隐藏）→ 退化为"同级最底层"判定
            IntPtr below = NativeMethods.GetWindow(hwnd, NativeMethods.GW_HWNDNEXT);
            if (below != IntPtr.Zero)
            {
                return "不是同级最底层（下方还有窗口 0x" + below.ToInt64().ToString("X")
                    + " class=" + GetClassName(below) + "）";
            }

            return "ok（未找到图标视图，按最底层判定）";
        }

        private static bool HasIconView(IntPtr window)
        {
            if (NativeMethods.FindWindowEx(window, IntPtr.Zero, "SHELLDLL_DefView", null) != IntPtr.Zero)
            {
                return true;
            }

            IntPtr worker = NativeMethods.FindWindowEx(window, IntPtr.Zero, "WorkerW", null);
            while (worker != IntPtr.Zero)
            {
                if (NativeMethods.FindWindowEx(worker, IntPtr.Zero, "SHELLDLL_DefView", null) != IntPtr.Zero)
                {
                    return true;
                }

                worker = NativeMethods.FindWindowEx(window, worker, "WorkerW", null);
            }

            return false;
        }

        internal static IntPtr FindIconHost()
        {
            IntPtr found = IntPtr.Zero;
            NativeMethods.EnumWindows((window, _) =>
            {
                if (HasIconView(window))
                {
                    found = window;
                }

                return true;
            }, IntPtr.Zero);
            return found;
        }

        private static IntPtr TopLevelAncestor(IntPtr hwnd)
        {
            IntPtr current = hwnd;
            while (true)
            {
                IntPtr parent = NativeMethods.GetParent(current);
                if (parent == IntPtr.Zero)
                {
                    return current;
                }

                current = parent;
            }
        }

        /// <summary>
        /// 取得前台与键盘焦点。依次尝试四种 Windows 认可的做法（前台锁定策略会拒绝单纯 SetForegroundWindow）：
        /// 直接设置 → AttachThreadInput 线程接入 → 置顶/取消置顶切换 → 模拟 Alt 解锁。
        /// </summary>
        internal static bool BringToFrontAndFocus(IntPtr hwnd)
        {
            // 1) 直接
            NativeMethods.BringWindowToTop(hwnd);
            NativeMethods.SetForegroundWindow(hwnd);
            NativeMethods.SetActiveWindow(hwnd);
            if (NativeMethods.GetForegroundWindow() == hwnd)
            {
                Debug.Log("[AiPeople] 取得前台焦点：成功（直接）");
                return true;
            }

            // 2) AttachThreadInput
            IntPtr foreground = NativeMethods.GetForegroundWindow();
            uint targetThread = (uint)NativeMethods.GetWindowThreadProcessId(hwnd, out _);
            uint foregroundThread = foreground != IntPtr.Zero
                ? (uint)NativeMethods.GetWindowThreadProcessId(foreground, out _)
                : 0;
            uint currentThread = NativeMethods.GetCurrentThreadId();
            bool attached = false;
            try
            {
                if (foregroundThread != 0 && foregroundThread != currentThread)
                {
                    attached = NativeMethods.AttachThreadInput(currentThread, foregroundThread, true);
                }

                NativeMethods.BringWindowToTop(hwnd);
                NativeMethods.SetForegroundWindow(hwnd);
                NativeMethods.SetActiveWindow(hwnd);
            }
            finally
            {
                if (attached)
                {
                    NativeMethods.AttachThreadInput(currentThread, foregroundThread, false);
                }
            }

            if (NativeMethods.GetForegroundWindow() == hwnd)
            {
                Debug.Log("[AiPeople] 取得前台焦点：成功（线程接入）");
                return true;
            }

            // 3) 置顶/取消置顶切换
            NativeMethods.SetWindowPos(
                hwnd, NativeMethods.HWND_TOPMOST, 0, 0, 0, 0,
                NativeMethods.SWP_NOMOVE | NativeMethods.SWP_NOSIZE | NativeMethods.SWP_SHOWWINDOW);
            NativeMethods.SetWindowPos(
                hwnd, NativeMethods.HWND_NOTOPMOST, 0, 0, 0, 0,
                NativeMethods.SWP_NOMOVE | NativeMethods.SWP_NOSIZE | NativeMethods.SWP_SHOWWINDOW);
            NativeMethods.SetForegroundWindow(hwnd);
            if (NativeMethods.GetForegroundWindow() == hwnd)
            {
                Debug.Log("[AiPeople] 取得前台焦点：成功（置顶切换）");
                return true;
            }

            // 4) 模拟 Alt 解锁（让系统认为存在用户输入意图）
            NativeMethods.keybd_event(0x12, 0, 0, UIntPtr.Zero);
            NativeMethods.keybd_event(0x12, 0, 2, UIntPtr.Zero);
            NativeMethods.SetForegroundWindow(hwnd);
            bool ok = NativeMethods.GetForegroundWindow() == hwnd;
            Debug.Log("[AiPeople] 取得前台焦点：" + (ok ? "成功（Alt 解锁）" : "失败（点一下输入框即可输入）"));
            return ok;
        }

        /// <summary>找到图标视图 SHELLDLL_DefView 及其宿主（Progman 或 Progman 下的 WorkerW 子窗口）。</summary>
        internal static bool TryFindIconView(out IntPtr iconHost, out IntPtr iconView)
        {
            iconHost = IntPtr.Zero;
            iconView = IntPtr.Zero;
            IntPtr progman = NativeMethods.FindWindow("Progman", null);
            if (progman == IntPtr.Zero)
            {
                return false;
            }

            iconView = NativeMethods.FindWindowEx(progman, IntPtr.Zero, "SHELLDLL_DefView", null);
            if (iconView != IntPtr.Zero)
            {
                iconHost = progman;
                return true;
            }

            IntPtr worker = NativeMethods.FindWindowEx(progman, IntPtr.Zero, "WorkerW", null);
            while (worker != IntPtr.Zero)
            {
                IntPtr view = NativeMethods.FindWindowEx(worker, IntPtr.Zero, "SHELLDLL_DefView", null);
                if (view != IntPtr.Zero)
                {
                    iconHost = worker;
                    iconView = view;
                    return true;
                }

                worker = NativeMethods.FindWindowEx(progman, worker, "WorkerW", null);
            }

            return false;
        }

        /// <summary>
        /// 精确分层挂载：作为图标视图的同级窗口，并**紧贴在图标视图下方**
        /// （即：系统壁纸之上、桌面图标之下）。这是 Win11 25H2 上唯一能同时看到房间和图标的层级。
        /// </summary>
        internal static bool TryAttachUnderIcons(IntPtr hwnd, out string detail)
        {
            if (!TryFindIconView(out IntPtr iconHost, out IntPtr iconView))
            {
                detail = "未找到图标视图 SHELLDLL_DefView";
                return false;
            }

            if (!SetParentAndVerify(hwnd, iconHost, "挂到图标宿主", out detail))
            {
                return false;
            }

            NativeMethods.SetWindowPos(
                hwnd, iconView, 0, 0, 0, 0,
                NativeMethods.SWP_NOMOVE | NativeMethods.SWP_NOSIZE | NativeMethods.SWP_NOACTIVATE);
            detail += "，并置于图标视图正下方";
            return true;
        }

        /// <summary>挂载到桌面壁纸层（WorkerW，图标之后）；失败时兜底直挂 Progman。</summary>
        internal static bool TryAttachToDesktopWallpaper(IntPtr hwnd, out string detail)
        {
            detail = string.Empty;
            IntPtr progman = NativeMethods.FindWindow("Progman", null);
            if (progman == IntPtr.Zero)
            {
                detail = "未找到 Progman";
                return false;
            }

            IntPtr unused;
            NativeMethods.SendMessageTimeout(
                progman, NativeMethods.WM_SPAWN_WORKER_PROC, IntPtr.Zero, IntPtr.Zero,
                NativeMethods.SMTO_NORMAL, 1000, out unused);

            IntPtr workerW = FindWallpaperWorkerW();
            IntPtr parent = workerW != IntPtr.Zero ? workerW : progman;
            return SetParentAndVerify(hwnd, parent, workerW != IntPtr.Zero ? "WorkerW" : "Progman(无WorkerW)", out detail);
        }

        /// <summary>备选挂载：直接挂到 Progman 并置底（旧系统/特殊桌面环境）。</summary>
        internal static bool TryAttachToProgman(IntPtr hwnd, out string detail)
        {
            IntPtr progman = NativeMethods.FindWindow("Progman", null);
            if (progman == IntPtr.Zero)
            {
                detail = "未找到 Progman";
                return false;
            }

            if (!SetParentAndVerify(hwnd, progman, "Progman直挂", out detail))
            {
                return false;
            }

            SendToBottom(hwnd);
            detail += " + 置底";
            return true;
        }

        /// <summary>
        /// SetParent 并复验：注意 SetParent 返回的是"旧父级"（顶层窗口为 0，不能当作失败），
        /// 因此以调用后的 GetParent 实际结果为准。
        /// </summary>
        private static bool SetParentAndVerify(IntPtr hwnd, IntPtr parent, string label, out string detail)
        {
            IntPtr previous = NativeMethods.SetParent(hwnd, parent);
            int error = System.Runtime.InteropServices.Marshal.GetLastWin32Error();
            IntPtr actual = NativeMethods.GetParent(hwnd);
            if (actual != parent)
            {
                detail = label + " 失败：GetParent=0x" + actual.ToInt64().ToString("X")
                    + "（期望 0x" + parent.ToInt64().ToString("X") + "，旧父级=0x"
                    + previous.ToInt64().ToString("X") + "，错误码=" + error + "）";
                return false;
            }

            detail = label + " 挂载成功（旧父级=0x" + previous.ToInt64().ToString("X") + "）";
            return true;
        }

        internal static void DetachFromDesktop(IntPtr hwnd)
        {
            NativeMethods.SetParent(hwnd, IntPtr.Zero);
        }

        /// <summary>诊断：打印桌面窗口树（顶层窗口 + 其可视子窗口），用于适配不同 Windows 版本的桌面结构。</summary>
        internal static void LogDesktopTree()
        {
            var builder = new StringBuilder(2048);
            builder.Append("[AiPeople] 桌面窗口树：\n");
            int index = 0;
            NativeMethods.EnumWindows((hwnd, _) =>
            {
                if (index++ > 40)
                {
                    return true;
                }

                if (!NativeMethods.IsWindowVisible(hwnd))
                {
                    return true;
                }

                string className = GetClassName(hwnd);
                NativeMethods.GetWindowRect(hwnd, out NativeMethods.RECT rect);
                bool shellView = NativeMethods.FindWindowEx(hwnd, IntPtr.Zero, "SHELLDLL_DefView", null) != IntPtr.Zero;
                builder.Append("  #").Append(index - 1)
                    .Append(" 0x").Append(hwnd.ToInt64().ToString("X"))
                    .Append(' ').Append(className)
                    .Append(" rect=(").Append(rect.Left).Append(',').Append(rect.Top)
                    .Append(',').Append(rect.Right).Append(',').Append(rect.Bottom).Append(')')
                    .Append(shellView ? " [含SHELLDLL_DefView]" : string.Empty)
                    .Append('\n');

                // 一层子窗口
                IntPtr child = NativeMethods.FindWindowEx(hwnd, IntPtr.Zero, null, null);
                int childIndex = 0;
                while (child != IntPtr.Zero && childIndex < 6)
                {
                    string childClass = GetClassName(child);
                    if (childClass == "SHELLDLL_DefView" || childClass == "WorkerW" || childClass == "SysListView32")
                    {
                        builder.Append("      子 0x").Append(child.ToInt64().ToString("X"))
                            .Append(' ').Append(childClass).Append('\n');
                    }

                    child = NativeMethods.FindWindowEx(hwnd, child, null, null);
                    childIndex++;
                }

                return true;
            }, IntPtr.Zero);

            Debug.Log(builder.ToString());
        }

        /// <summary>当前前台窗口是否是桌面（用于壁纸模式下的点击轮询判定）。</summary>
        internal static bool IsDesktopForeground(IntPtr hwnd)
        {
            if (hwnd == IntPtr.Zero)
            {
                return false;
            }

            string className = GetClassName(hwnd);
            return className == "Progman" || className == "WorkerW" || className == "Shell_TrayWnd"
                || className == "SHELLDLL_DefView";
        }

        internal static string GetClassName(IntPtr hwnd)
        {
            var buffer = new StringBuilder(256);
            int length = NativeMethods.GetClassName(hwnd, buffer, buffer.Capacity);
            return length > 0 ? buffer.ToString() : string.Empty;
        }

        private static IntPtr FindWallpaperWorkerW()
        {
            IntPtr found = IntPtr.Zero;
            NativeMethods.EnumWindows((hwnd, _) =>
            {
                IntPtr shellView = NativeMethods.FindWindowEx(hwnd, IntPtr.Zero, "SHELLDLL_DefView", null);
                if (shellView != IntPtr.Zero)
                {
                    found = NativeMethods.FindWindowEx(IntPtr.Zero, hwnd, "WorkerW", null);
                }

                return true;
            }, IntPtr.Zero);
            return found;
        }
    }
}
