using System;
using UnityEngine;

namespace AiPeople.Shell
{
    /// <summary>
    /// 对话小窗进程用的窗口原生操作（公共包装）：隐藏/显示/移动/取矩形/前台判定。
    /// 供 UI 层的 ChatWindowApp 调用（NativeMethods 为程序集内部）。
    /// </summary>
    public static class ChatWindowNative
    {
        public static void HideCurrentWindow()
        {
            IntPtr hwnd = DesktopWindowNative.FindUnityWindow();
            if (hwnd != IntPtr.Zero)
            {
                NativeMethods.ShowWindow(hwnd, NativeMethods.SW_HIDE);
            }
        }

        public static void ShowCurrentWindow()
        {
            IntPtr hwnd = DesktopWindowNative.FindUnityWindow();
            if (hwnd == IntPtr.Zero)
            {
                return;
            }

            NativeMethods.ShowWindow(hwnd, NativeMethods.SW_SHOW);
        }

        public static void FocusCurrentWindow()
        {
            IntPtr hwnd = DesktopWindowNative.FindUnityWindow();
            if (hwnd != IntPtr.Zero)
            {
                DesktopWindowNative.BringToFrontAndFocus(hwnd);
            }
        }

        /// <summary>小窗外观：无边框、不进任务栏（工具窗口）、可激活可聚焦；不改尺寸。</summary>
        public static void ApplyFramelessToolWindow()
        {
            IntPtr hwnd = DesktopWindowNative.FindUnityWindow();
            if (hwnd == IntPtr.Zero)
            {
                return;
            }

            int style = unchecked((int)(NativeMethods.WS_POPUP | NativeMethods.WS_VISIBLE));
            NativeMethods.SetWindowLong(hwnd, NativeMethods.GWL_STYLE, style);

            int ex = NativeMethods.GetWindowLong(hwnd, NativeMethods.GWL_EXSTYLE);
            ex &= ~unchecked((int)(NativeMethods.WS_EX_NOACTIVATE | NativeMethods.WS_EX_APPWINDOW));
            ex |= unchecked((int)NativeMethods.WS_EX_TOOLWINDOW);
            // UI automation tools omit tool windows. Explicit diagnostic launches may expose
            // the same window in the taskbar; normal launches retain the tool-window styles.
            if (UiLatencyTrace.Enabled && Environment.GetEnvironmentVariable("AIPEOPLE_UI_AUTOMATION") == "1")
            {
                ex &= ~unchecked((int)NativeMethods.WS_EX_TOOLWINDOW);
                ex |= unchecked((int)NativeMethods.WS_EX_APPWINDOW);
            }
            NativeMethods.SetWindowLong(hwnd, NativeMethods.GWL_EXSTYLE, ex);

            NativeMethods.SetWindowPos(
                hwnd, IntPtr.Zero, 0, 0, 0, 0,
                NativeMethods.SWP_NOMOVE | NativeMethods.SWP_NOSIZE | NativeMethods.SWP_NOZORDER
                | NativeMethods.SWP_NOACTIVATE | NativeMethods.SWP_FRAMECHANGED);
        }

        public static void MoveCurrentWindow(int x, int y)
        {
            IntPtr hwnd = DesktopWindowNative.FindUnityWindow();
            if (hwnd == IntPtr.Zero)
            {
                return;
            }

            NativeMethods.SetWindowPos(
                hwnd, IntPtr.Zero, x, y, 0, 0,
                NativeMethods.SWP_NOSIZE | NativeMethods.SWP_NOZORDER | NativeMethods.SWP_NOACTIVATE);
        }

        public static bool TryGetCurrentWindowRect(out int x, out int y, out int width, out int height)
        {
            x = y = width = height = 0;
            IntPtr hwnd = DesktopWindowNative.FindUnityWindow();
            if (hwnd == IntPtr.Zero || !NativeMethods.GetWindowRect(hwnd, out NativeMethods.RECT rect))
            {
                return false;
            }

            x = rect.Left;
            y = rect.Top;
            width = rect.Right - rect.Left;
            height = rect.Bottom - rect.Top;
            return true;
        }

        public static bool IsCurrentWindowForeground()
        {
            IntPtr hwnd = DesktopWindowNative.FindUnityWindow();
            return hwnd != IntPtr.Zero && NativeMethods.GetForegroundWindow() == hwnd;
        }

        public static bool TryGetCursorPosition(out int x, out int y)
        {
            x = y = 0;
            if (!NativeMethods.GetCursorPos(out NativeMethods.POINT point))
            {
                return false;
            }

            x = point.X;
            y = point.Y;
            return true;
        }

        public static bool IsMouseLeftDown()
        {
            return (NativeMethods.GetAsyncKeyState(NativeMethods.VK_LBUTTON) & 0x8000) != 0;
        }
    }
}
