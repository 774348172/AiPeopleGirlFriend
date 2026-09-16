using System;
using System.Runtime.InteropServices;
using UnityEngine;

namespace AiPeople.Shell
{
    /// <summary>Conservative native occlusion: one opaque foreground window covers the wallpaper monitor.</summary>
    public static class DesktopOcclusion
    {
        [StructLayout(LayoutKind.Sequential)]
        private struct MonitorInfo { public int size; public NativeMethods.RECT monitor, work; public uint flags; }
        [DllImport("user32.dll")] private static extern IntPtr MonitorFromWindow(IntPtr h, uint flags);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern bool GetMonitorInfo(IntPtr h, ref MonitorInfo info);
        [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr h);
        [DllImport("dwmapi.dll", EntryPoint = "DwmGetWindowAttribute")]
        private static extern int GetFrame(IntPtr h, int attribute, out NativeMethods.RECT value, int size);
        [DllImport("dwmapi.dll", EntryPoint = "DwmGetWindowAttribute")]
        private static extern int GetCloaked(IntPtr h, int attribute, out int value, int size);
        [DllImport("wtsapi32.dll", CharSet = CharSet.Unicode)]
        private static extern bool WTSQuerySessionInformation(IntPtr server, uint session, int kind,
            out IntPtr buffer, out int bytes);
        [DllImport("wtsapi32.dll")] private static extern void WTSFreeMemory(IntPtr buffer);

        private static bool SessionIsLocked()
        {
            // WTSINFOEX level 1: DWORD Level, 4-byte alignment padding, SessionId,
            // SessionState, SessionFlags. Windows 10/11 flags: 0 locked, 1 unlocked.
            if (!WTSQuerySessionInformation(IntPtr.Zero, uint.MaxValue, 25, out var buffer, out int bytes)) return false;
            try { return bytes >= 20 && Marshal.ReadInt32(buffer) == 1 && Marshal.ReadInt32(buffer, 16) == 0; }
            finally { WTSFreeMemory(buffer); }
        }

        public static bool CoversWorkArea(RectInt window, RectInt work) => work.width > 0 && work.height > 0
            && window.xMin <= work.xMin + 2 && window.yMin <= work.yMin + 2
            && window.xMax >= work.xMax - 2 && window.yMax >= work.yMax - 2;

        private static RectInt Rect(NativeMethods.RECT r) => new RectInt(r.Left, r.Top, r.Right-r.Left, r.Bottom-r.Top);

        public static bool IsCovered(IntPtr wallpaper, int chatProcessId = 0)
        {
            if (wallpaper == IntPtr.Zero) return false;
            // LockApp can be foreground AND DWM-cloaked; query the session before excluding cloaked apps.
            if (SessionIsLocked()) return true;
            var info = new MonitorInfo { size = Marshal.SizeOf<MonitorInfo>() };
            if (!GetMonitorInfo(MonitorFromWindow(wallpaper, 2), ref info)) return false;
            IntPtr h = NativeMethods.GetForegroundWindow();
            if (h == IntPtr.Zero || h == wallpaper || DesktopWindowNative.IsDesktopForeground(h)) return false;
            NativeMethods.GetWindowThreadProcessId(h, out int pid);
            // A small chat window must not wake rendering beneath the app it was opened over.
            if (chatProcessId != 0 && pid == chatProcessId)
            {
                h = NativeMethods.GetWindow(h, NativeMethods.GW_HWNDNEXT);
                while (h != IntPtr.Zero && (!NativeMethods.IsWindowVisible(h) || IsIconic(h)))
                    h = NativeMethods.GetWindow(h, NativeMethods.GW_HWNDNEXT);
            }
            if (h == IntPtr.Zero || h == wallpaper || DesktopWindowNative.IsDesktopForeground(h)
                || !NativeMethods.IsWindowVisible(h) || IsIconic(h)) return false;
            // Layered/transparent overlays are not proof that the wallpaper is hidden.
            if ((NativeMethods.GetWindowLong(h, NativeMethods.GWL_EXSTYLE) & 0x00080020) != 0) return false;
            if (GetCloaked(h, 14, out int cloaked, sizeof(int)) == 0 && cloaked != 0) return false;
            if (GetFrame(h, 9, out var frame, Marshal.SizeOf<NativeMethods.RECT>()) != 0
                && !NativeMethods.GetWindowRect(h, out frame)) return false;
            return CoversWorkArea(Rect(frame), Rect(info.work));
        }
    }
}
