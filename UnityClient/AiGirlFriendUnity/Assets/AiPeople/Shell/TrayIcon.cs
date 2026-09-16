using System;
using System.Collections.Concurrent;
using System.Runtime.InteropServices;
using System.Threading;
using UnityEngine;

namespace AiPeople.Shell
{
    public enum TrayCommand
    {
        ShowChat,
        HideChat,
        RestoreWallpaper,
        Quit,
    }

    /// <summary>
    /// 系统托盘图标（原生 Shell_NotifyIcon，消息专用窗口 + 子类化 WndProc）：
    /// 左键唤出对话小窗；右键菜单（显示/隐藏小窗、恢复桌面壁纸、退出）。
    /// 消息循环在独立线程；命令投递到队列，由主线程（App.Update）取出执行。
    /// </summary>
    public sealed class TrayIcon : IDisposable
    {
        private const int IconId = 1;
        private const int CallbackMessage = NativeMethods.WM_APP + 1;
        private const int CommandShow = 1001;
        private const int CommandHide = 1002;
        private const int CommandWallpaper = 1003;
        private const int CommandQuit = 1004;

        public readonly ConcurrentQueue<TrayCommand> Commands = new ConcurrentQueue<TrayCommand>();

        private Thread _thread;
        private volatile bool _running;
        private IntPtr _hwnd = IntPtr.Zero;
        private IntPtr _icon = IntPtr.Zero;
        private IntPtr _previousProc = IntPtr.Zero;
        private NativeMethods.WndProcDelegate _wndProc;
        private uint _threadId;

        public bool IsRunning { get; private set; }
        public string LastError { get; private set; } = string.Empty;

        public void Start(string tooltip)
        {
            if (_running)
            {
                return;
            }

            _running = true;
            _thread = new Thread(() => Loop(tooltip)) { IsBackground = true, Name = "AiPeopleTray" };
            _thread.Start();

            for (int i = 0; i < 60 && !IsRunning && _thread.IsAlive && LastError.Length == 0; i++)
            {
                Thread.Sleep(10);
            }
        }

        private void Loop(string tooltip)
        {
            var data = new NativeMethods.NOTIFYICONDATA();
            try
            {
                _threadId = NativeMethods.GetCurrentThreadId();
                _wndProc = WndProc;

                _hwnd = NativeMethods.CreateWindowEx(
                    0, "STATIC", "AiPeopleTray", 0, 0, 0, 0, 0,
                    NativeMethods.HWND_MESSAGE, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
                if (_hwnd == IntPtr.Zero)
                {
                    LastError = "创建托盘消息窗口失败";
                    _running = false;
                    return;
                }

                _previousProc = NativeMethods.SetWindowLongPtr(
                    _hwnd, NativeMethods.GWLP_WNDPROC, Marshal.GetFunctionPointerForDelegate(_wndProc));

                _icon = LoadAppIcon();
                data = new NativeMethods.NOTIFYICONDATA
                {
                    cbSize = Marshal.SizeOf<NativeMethods.NOTIFYICONDATA>(),
                    hWnd = _hwnd,
                    uID = IconId,
                    uFlags = NativeMethods.NIF_MESSAGE | NativeMethods.NIF_ICON | NativeMethods.NIF_TIP,
                    uCallbackMessage = CallbackMessage,
                    hIcon = _icon,
                    szTip = tooltip,
                    szInfo = string.Empty,
                    szInfoTitle = string.Empty,
                };

                if (!NativeMethods.Shell_NotifyIcon(NativeMethods.NIM_ADD, ref data))
                {
                    LastError = "托盘图标添加失败";
                    _running = false;
                    return;
                }

                IsRunning = true;
                Debug.Log("[AiPeople] 系统托盘图标已创建");

                while (_running && NativeMethods.GetMessage(out NativeMethods.MSG message, IntPtr.Zero, 0, 0))
                {
                    NativeMethods.TranslateMessage(ref message);
                    NativeMethods.DispatchMessage(ref message);
                }
            }
            catch (Exception exception)
            {
                LastError = exception.Message;
            }
            finally
            {
                try
                {
                    if (data.cbSize != 0)
                    {
                        NativeMethods.Shell_NotifyIcon(NativeMethods.NIM_DELETE, ref data);
                    }

                    if (_icon != IntPtr.Zero)
                    {
                        NativeMethods.DestroyIcon(_icon);
                    }

                    if (_hwnd != IntPtr.Zero)
                    {
                        NativeMethods.DestroyWindow(_hwnd);
                    }
                }
                catch (Exception)
                {
                    // 退出期忽略
                }

                IsRunning = false;
                _running = false;
            }
        }

        private IntPtr WndProc(IntPtr hwnd, uint msg, IntPtr wParam, IntPtr lParam)
        {
            if (msg == CallbackMessage)
            {
                int evt = lParam.ToInt32() & 0xFFFF;
                if (evt == NativeMethods.WM_LBUTTONUP)
                {
                    Commands.Enqueue(TrayCommand.ShowChat);
                }
                else if (evt == NativeMethods.WM_RBUTTONUP)
                {
                    ShowMenu();
                }

                return IntPtr.Zero;
            }

            return _previousProc != IntPtr.Zero
                ? NativeMethods.CallWindowProc(_previousProc, hwnd, msg, wParam, lParam)
                : NativeMethods.DefWindowProc(hwnd, msg, wParam, lParam);
        }

        private void ShowMenu()
        {
            IntPtr menu = NativeMethods.CreatePopupMenu();
            NativeMethods.AppendMenu(menu, NativeMethods.MF_STRING, CommandShow, "显示对话小窗");
            NativeMethods.AppendMenu(menu, NativeMethods.MF_STRING, CommandHide, "隐藏对话小窗");
            NativeMethods.AppendMenu(menu, NativeMethods.MF_STRING, CommandWallpaper, "恢复桌面壁纸");
            NativeMethods.AppendMenu(menu, NativeMethods.MF_STRING, CommandQuit, "退出程序");

            NativeMethods.GetCursorPos(out NativeMethods.POINT point);
            NativeMethods.SetForegroundWindow(_hwnd);
            int command = NativeMethods.TrackPopupMenu(
                menu,
                NativeMethods.TPM_RETURNCMD | NativeMethods.TPM_RIGHTBUTTON,
                point.X, point.Y, 0, _hwnd, IntPtr.Zero);
            NativeMethods.PostMessage(_hwnd, NativeMethods.WM_NULL, IntPtr.Zero, IntPtr.Zero);
            NativeMethods.DestroyMenu(menu);

            EnqueueMenuCommand(command);
        }

        private void EnqueueMenuCommand(int command)
        {
            switch (command)
            {
                case CommandShow: Commands.Enqueue(TrayCommand.ShowChat); break;
                case CommandHide: Commands.Enqueue(TrayCommand.HideChat); break;
                case CommandWallpaper: Commands.Enqueue(TrayCommand.RestoreWallpaper); break;
                case CommandQuit: Commands.Enqueue(TrayCommand.Quit); break;
            }
        }

        private static IntPtr LoadAppIcon()
        {
            try
            {
                string exePath = Environment.GetCommandLineArgs()[0];
                IntPtr icon = NativeMethods.ExtractIcon(IntPtr.Zero, exePath, 0);
                if (icon != IntPtr.Zero && icon != new IntPtr(1))
                {
                    return icon;
                }
            }
            catch (Exception)
            {
                // 退回系统默认图标
            }

            return NativeMethods.LoadIcon(IntPtr.Zero, new IntPtr(NativeMethods.IDI_APPLICATION));
        }

        public void Dispose()
        {
            _running = false;
            if (_threadId != 0)
            {
                NativeMethods.PostThreadMessage(_threadId, NativeMethods.WM_QUIT, IntPtr.Zero, IntPtr.Zero);
            }

            _thread?.Join(500);
            _thread = null;
            IsRunning = false;
        }
    }
}
