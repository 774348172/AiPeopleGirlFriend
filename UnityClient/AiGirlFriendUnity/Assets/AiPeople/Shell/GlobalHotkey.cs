using System;
using System.Threading;

namespace AiPeople.Shell
{
    /// <summary>
    /// 全局热键（Win32 RegisterHotKey，独立线程消息循环）。
    /// 只注册系统热键，不注入、不挂钩任何其它进程。
    /// </summary>
    public sealed class GlobalHotkey : IDisposable
    {
        private const int DefaultHotkeyId = 0x4170;

        private int _hotkeyId = DefaultHotkeyId;

        private Thread _thread;
        private volatile bool _running;
        private volatile bool _pressed;
        private volatile bool _registered;
        private volatile string _error = string.Empty;
        private uint _threadId;

        public bool Registered => _registered;
        public string LastError => _error;

        public static bool TryParse(string text, out uint modifiers, out uint virtualKey, out string error)
        {
            modifiers = 0;
            virtualKey = 0;
            error = string.Empty;
            if (string.IsNullOrWhiteSpace(text))
            {
                error = "热键为空";
                return false;
            }

            foreach (string rawPart in text.Split('+'))
            {
                string part = rawPart.Trim();
                if (part.Length == 0)
                {
                    continue;
                }

                switch (part.ToLowerInvariant())
                {
                    case "ctrl":
                    case "control":
                        modifiers |= NativeMethods.MOD_CONTROL;
                        break;
                    case "alt":
                        modifiers |= NativeMethods.MOD_ALT;
                        break;
                    case "shift":
                        modifiers |= NativeMethods.MOD_SHIFT;
                        break;
                    case "win":
                        modifiers |= NativeMethods.MOD_WIN;
                        break;
                    default:
                        if (part.Length == 1 && char.IsLetterOrDigit(part[0]))
                        {
                            virtualKey = char.ToUpperInvariant(part[0]);
                        }
                        else if (part.Length >= 2
                            && (part[0] == 'F' || part[0] == 'f')
                            && int.TryParse(part.Substring(1), out int functionKey)
                            && functionKey >= 1
                            && functionKey <= 24)
                        {
                            virtualKey = (uint)(0x70 + functionKey - 1);
                        }
                        else
                        {
                            error = "无法识别的按键：" + part;
                            return false;
                        }

                        break;
                }
            }

            if (virtualKey == 0)
            {
                error = "热键缺少主键（例如 Ctrl+Alt+B）";
                return false;
            }

            return true;
        }

        public bool Start(string hotkey, int hotkeyId = DefaultHotkeyId)
        {
            _hotkeyId = hotkeyId;
            if (_running)
            {
                return _registered;
            }

            if (!TryParse(hotkey, out uint modifiers, out uint virtualKey, out string parseError))
            {
                _error = parseError;
                return false;
            }

            _running = true;
            _error = string.Empty;
            _thread = new Thread(() => Loop(modifiers, virtualKey))
            {
                IsBackground = true,
                Name = "AiPeopleHotkey",
            };
            _thread.Start();

            for (int i = 0; i < 50 && !_registered && _error.Length == 0; i++)
            {
                Thread.Sleep(10);
            }

            return _registered;
        }

        private void Loop(uint modifiers, uint virtualKey)
        {
            _threadId = NativeMethods.GetCurrentThreadId();
            if (!NativeMethods.RegisterHotKey(IntPtr.Zero, _hotkeyId, modifiers | NativeMethods.MOD_NOREPEAT, virtualKey))
            {
                _error = "热键注册失败（可能已被占用）";
                _running = false;
                return;
            }

            _registered = true;
            while (_running)
            {
                if (NativeMethods.GetMessage(out NativeMethods.MSG message, IntPtr.Zero, 0, 0))
                {
                    if (message.message == NativeMethods.WM_HOTKEY)
                    {
                        UiLatencyTrace.Mark("hotkey_received");
                        _pressed = true;
                    }
                }
            }

            NativeMethods.UnregisterHotKey(IntPtr.Zero, _hotkeyId);
            _registered = false;
        }

        /// <summary>主线程轮询：本帧是否有热键按下（消费一次）。</summary>
        public bool ConsumePressed()
        {
            if (!_pressed)
            {
                return false;
            }

            _pressed = false;
            return true;
        }

        public void Stop()
        {
            if (!_running)
            {
                return;
            }

            _running = false;
            if (_threadId != 0)
            {
                NativeMethods.PostThreadMessage(_threadId, NativeMethods.WM_QUIT, IntPtr.Zero, IntPtr.Zero);
            }

            _thread?.Join(500);
            _thread = null;
            _threadId = 0;
        }

        public void Dispose()
        {
            Stop();
        }
    }
}
