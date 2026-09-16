using System;
using System.Collections;
using UnityEngine;

namespace AiPeople.Shell
{
    public enum ShellStatus
    {
        /// <summary>普通窗口 / Editor：完整窗口交互（开发与兜底）。</summary>
        Normal,

        /// <summary>壁纸层：铺满桌面、图标之后、不接收输入。</summary>
        Wallpaper,

        /// <summary>会话态：窗口临时置前并聚焦输入；退出后回归原状态。</summary>
        SessionActive,
    }

    /// <summary>
    /// 桌面外壳控制器（Windows）：
    /// - 窗口形态决策（壁纸层 / 普通窗口）与降级；
    /// - 全局热键与会话态切换（点击常驻对话框 / 热键进入，退出干净回归）；
    /// - 壁纸模式下的桌面点击轮询（仅当点击落在常驻对话框区域且前台是桌面时进入会话态；无鼠标钩子）；
    /// - 性能纪律：帧率上限、全屏应用前台时暂停渲染（世界服务心跳在独立线程继续）。
    /// Editor 与非 Windows 平台自动为普通窗口模式。
    /// </summary>
    public sealed class DesktopShellController : MonoBehaviour
    {
        public event Action<bool> SessionStateChanged;
        public event Action<string> Notify;

        /// <summary>会话态下检测到回车（Win32 轮询，兜底：部分环境 UI 输入框收不到回车事件）。</summary>
        public event Action SubmitKeyPressed;

        /// <summary>热键拦截：返回 true 表示调用方已处理（桌面形态用它唤出/收起对话小窗）。</summary>
        public Func<bool> InterceptHotkey;

        /// <summary>壁纸模式下点击常驻对话条（主进程据此唤出对话小窗，而不是把主窗口抬成全屏）。</summary>
        public event Action ChatBarClicked;

        public ShellSettingsData Settings { get; private set; }
        public ShellStatus Status { get; private set; } = ShellStatus.Normal;

        /// <summary>构建版是否启用桌面表现（常驻对话框 + 会话态 + 固定机位）；Editor 内为 false。</summary>
        public bool DesktopPresentation { get; private set; }

        public bool IsSessionActive => Status == ShellStatus.SessionActive;
        public bool IsDesktopShell => Status == ShellStatus.Wallpaper || Status == ShellStatus.SessionActive;
        public bool IsWallpaperMode { get; private set; }
        public bool RenderPaused { get; private set; }
        public string LastDetail { get; private set; } = string.Empty;

        /// <summary>常驻对话框的屏幕像素区域（原点左下，UI 每帧写入；用于壁纸模式点击判定）。</summary>
        public Rect ChatBarPixelRect { get; set; }

        private GlobalHotkey _hotkey;
        private Camera _viewCamera;
        private Canvas _presentationCanvas;
        private bool _cameraWasEnabled, _canvasWasEnabled;
        private double _lastActivity;
        public Func<bool> HasVisualActivity;
        public Func<int> ChatProcessId;
        public const float IdleDelaySeconds = 15f;
        private IntPtr _hwnd = IntPtr.Zero;
        private bool _mouseWasDown;
        private bool _submitKeyWasDown;
        private int _focusRetries;
        private float _focusRetryTimer;
        private int _normalFps = 30;
        private float _fullscreenTimer;
        private Coroutine _windowModeRetry;
        private Coroutine _wallpaperAttach;

        public void Initialize(ShellSettingsData settings, Camera viewCamera, Canvas presentationCanvas = null)
        {
            Settings = settings ?? new ShellSettingsData();
            _viewCamera = viewCamera;
            _presentationCanvas = presentationCanvas;
            _lastActivity = Time.realtimeSinceStartupAsDouble;
            _normalFps = Mathf.Clamp(Settings.targetFps, 5, 240);
            Application.targetFrameRate = _normalFps;
            // 桌面常驻程序：失去焦点后必须继续运行（否则切到别的窗口她就冻住）
            Application.runInBackground = true;
            ApplyMode((ShellWindowMode)Settings.windowMode);
        }

        /// <summary>应用窗口形态（并持久化）。可在设置界面随时切换。</summary>
        public void ApplyMode(ShellWindowMode mode)
        {
            Settings.windowMode = (int)mode;
            ShellSettingsStore.Save(Settings);

            // A recovery request is one-way and idempotent. Do not resize/reparent a healthy wallpaper.
            if (mode != ShellWindowMode.Normal && IsWallpaperMode && _hwnd != IntPtr.Zero
                && DesktopWindowNative.VerifyDesktopLayer(_hwnd).StartsWith("ok"))
            {
                return;
            }

            // Superseded startup/recovery attempts must never apply an old fallback afterwards.
            SetRenderPaused(false);
            if (_windowModeRetry != null)
            {
                StopCoroutine(_windowModeRetry);
                _windowModeRetry = null;
            }
            if (_wallpaperAttach != null)
            {
                StopCoroutine(_wallpaperAttach);
                _wallpaperAttach = null;
            }

            StopHotkey();
            IsWallpaperMode = false;
            Status = ShellStatus.Normal;
            _hwnd = IntPtr.Zero;

            if (Application.isEditor || Application.platform != RuntimePlatform.WindowsPlayer)
            {
                DesktopPresentation = false;
                LastDetail = Application.isEditor
                    ? "Editor：开发模式（第一人称 + 完整对话面板；壁纸层需构建版验证）"
                    : "非 Windows 播放器：普通窗口模式";
                StartHotkey();
                Notify?.Invoke(LastDetail);
                return;
            }

            // 构建版一律先强制窗口化，杜绝"全屏盖住桌面"
            DesktopPresentation = true;
            Screen.fullScreenMode = FullScreenMode.Windowed;

            _hwnd = DesktopWindowNative.FindUnityWindow();
            if (_hwnd == IntPtr.Zero)
            {
                LastDetail = "播放器窗口尚未就绪，正在等待窗口句柄后进入壁纸层";
                Notify?.Invoke(LastDetail);
                if (_windowModeRetry != null) StopCoroutine(_windowModeRetry);
                _windowModeRetry = StartCoroutine(RetryInitialWindowMode(mode));
                return;
            }

            Debug.Log("[AiPeople] 主窗口识别：" + DesktopWindowNative.DescribeWindow(_hwnd));
            DesktopWindowNative.LogDesktopTree();

            if (mode == ShellWindowMode.Normal)
            {
                ApplyNormalWindow();
                LastDetail = "普通窗口模式（1600×900，可最小化）";
                StartHotkey();
                Notify?.Invoke(LastDetail);
                return;
            }

            // 壁纸挂载完成前隐藏启动窗口，避免用户看到短暂的普通窗口/全屏覆盖。
            NativeMethods.ShowWindow(_hwnd, NativeMethods.SW_HIDE);

            Screen.SetResolution(
                NativeMethods.GetSystemMetrics(NativeMethods.SM_CXSCREEN),
                NativeMethods.GetSystemMetrics(NativeMethods.SM_CYSCREEN),
                FullScreenMode.Windowed);
            DesktopWindowNative.ApplyOverlayStyles(_hwnd);
            DesktopWindowNative.ResizeToPrimaryMonitor(_hwnd);

            // 分辨率切换可能让 Unity 重建窗口，挂载放到协程里重试（每轮重新识别窗口句柄）
            _wallpaperAttach = StartCoroutine(AttachWallpaperWithRetries());
        }

        private IEnumerator RetryInitialWindowMode(ShellWindowMode mode)
        {
            for (int attempt = 0; attempt < 20; attempt++)
            {
                yield return new WaitForSecondsRealtime(0.25f);
                if (DesktopWindowNative.FindUnityWindow() != IntPtr.Zero)
                {
                    _windowModeRetry = null;
                    ApplyMode(mode);
                    yield break;
                }
            }

            _windowModeRetry = null;
            LastDetail = "等待播放器窗口超时，保持普通窗口模式";
            StartHotkey();
            Notify?.Invoke(LastDetail);
        }

        private IEnumerator AttachWallpaperWithRetries()
        {
            const int maxAttempts = 4;
            string lastDetail = string.Empty;
            string lastVerify = "未校验";

            for (int attemptIndex = 1; attemptIndex <= maxAttempts; attemptIndex++)
            {
                yield return new WaitForSecondsRealtime(0.6f);

                IntPtr hwnd = DesktopWindowNative.FindUnityWindow();
                if (hwnd == IntPtr.Zero)
                {
                    lastDetail = "未找到窗口句柄";
                    continue;
                }

                _hwnd = hwnd;
                DesktopWindowNative.ApplyOverlayStyles(hwnd);
                DesktopWindowNative.ResizeToPrimaryMonitor(hwnd);

                string detail = string.Empty;
                string verify = "未尝试";

                // 尝试 A（优先级最高）：紧贴图标视图下方 —— 房间在系统壁纸之上、桌面图标之下
                if (DesktopWindowNative.TryAttachUnderIcons(hwnd, out detail))
                {
                    verify = DesktopWindowNative.VerifyDesktopLayer(hwnd);
                    Debug.Log($"[AiPeople] 壁纸挂载#{attemptIndex}A(图标下方)：{detail} → 校验={verify}");
                    if (verify.StartsWith("ok"))
                    {
                        _wallpaperAttach = null;
                        FinishWallpaperSuccess(detail);
                        yield break;
                    }
                }
                else
                {
                    Debug.Log($"[AiPeople] 壁纸挂载#{attemptIndex}A 失败：{detail}");
                }

                lastDetail = detail;
                lastVerify = verify;
            }

            _wallpaperAttach = null;
            if (_hwnd != IntPtr.Zero)
            {
                DesktopWindowNative.DetachFromDesktop(_hwnd);
            }

            ApplyNormalWindow();
            LastDetail = "壁纸层不可用（" + lastVerify + "），已降级普通窗口；最后尝试：" + lastDetail;
            Notify?.Invoke(LastDetail);
            Debug.LogWarning("[AiPeople] " + LastDetail);
        }

        private void FinishWallpaperSuccess(string detail)
        {
            StartHotkey();
            if (_hotkey == null || !_hotkey.Registered)
            {
                // 安全兜底：壁纸层没有任务栏入口，热键不可用就等于无法交互/无法退出
                DesktopWindowNative.DetachFromDesktop(_hwnd);
                ApplyNormalWindow();
                LastDetail = "热键不可用，已回到普通窗口模式（避免壁纸层无法交互与退出）";
                Notify?.Invoke(LastDetail);
                return;
            }

            IsWallpaperMode = true;
            Status = ShellStatus.Wallpaper;
            NativeMethods.ShowWindow(_hwnd, NativeMethods.SW_SHOWNOACTIVATE);
            LastDetail = "壁纸层已挂载并通过校验（" + detail + "）";
            Notify?.Invoke(LastDetail);
        }

        private void ApplyNormalWindow()
        {
            if (_hwnd == IntPtr.Zero)
            {
                return;
            }

            Screen.fullScreenMode = FullScreenMode.Windowed;
            Screen.SetResolution(1600, 900, FullScreenMode.Windowed);
            DesktopWindowNative.DetachFromDesktop(_hwnd);
            DesktopWindowNative.ApplyNormalWindowStyles(_hwnd);
            NativeMethods.ShowWindow(_hwnd, NativeMethods.SW_SHOWNOACTIVATE);
        }

        public void SetTargetFps(int fps)
        {
            _normalFps = Mathf.Clamp(fps, 5, 240);
            Settings.targetFps = _normalFps;
            ShellSettingsStore.Save(Settings);
            if (!RenderPaused)
            {
                Application.targetFrameRate = _normalFps;
            }
        }

        public void ToggleSession()
        {
            if (IsSessionActive)
            {
                DisengageSession();
            }
            else
            {
                EngageSession();
            }
        }

        /// <summary>进入会话态：壁纸模式下临时脱离壁纸层并置前聚焦。</summary>
        public void EngageSession()
        {
            if (IsSessionActive)
            {
                return;
            }

            if (_hwnd != IntPtr.Zero)
            {
                if (IsWallpaperMode)
                {
                    DesktopWindowNative.DetachFromDesktop(_hwnd);
                    DesktopWindowNative.ApplySessionStyles(_hwnd);
                }

                _focusRetries = 0;
                DesktopWindowNative.BringToFrontAndFocus(_hwnd);
            }

            Status = ShellStatus.SessionActive;
            SetRenderPaused(false);
            Cursor.lockState = CursorLockMode.None;
            Cursor.visible = true;
            Debug.Log("[AiPeople] 进入会话态：前景窗口=0x"
                + NativeMethods.GetForegroundWindow().ToInt64().ToString("X")
                + "，本窗口=0x" + _hwnd.ToInt64().ToString("X"));
            SessionStateChanged?.Invoke(true);
        }

        /// <summary>退出会话态：立即回归壁纸层/普通窗口并把焦点交还。</summary>
        public void DisengageSession()
        {
            if (!IsSessionActive)
            {
                return;
            }

            Status = IsWallpaperMode ? ShellStatus.Wallpaper : ShellStatus.Normal;
            if (IsWallpaperMode && _hwnd != IntPtr.Zero)
            {
                DesktopWindowNative.ApplyOverlayStyles(_hwnd);
                DesktopWindowNative.TryAttachToDesktopWallpaper(_hwnd, out _);
                DesktopWindowNative.SendToBottom(_hwnd);
            }

            SessionStateChanged?.Invoke(false);
        }

        public void QuitApplication()
        {
            Application.Quit();
        }

        private void StartHotkey()
        {
            _hotkey = new GlobalHotkey();
            if (!_hotkey.Start(Settings.hotkey))
            {
                Notify?.Invoke("全局热键不可用：" + _hotkey.LastError + "（可在设置面板更换）");
            }
        }

        private void StopHotkey()
        {
            _hotkey?.Dispose();
            _hotkey = null;
        }

        private void Update()
        {
            if (_hotkey != null && _hotkey.ConsumePressed())
            {
                if (InterceptHotkey == null || !InterceptHotkey())
                {
                    ToggleSession();
                }
            }

            if (Status == ShellStatus.Wallpaper)
            {
                PollDesktopClick();
            }

            if (IsSessionActive)
            {
                PollSubmitKey();
                RetryFocusIfNeeded();
            }

            _fullscreenTimer -= Time.unscaledDeltaTime;
            if (_fullscreenTimer <= 0f)
            {
                _fullscreenTimer = 0.5f;
                EvaluateFullscreenPause();
            }
            if (HasVisualActivity?.Invoke() == true) NotifyActivity();
            UpdateFrameBudget();
        }

        public void NotifyActivity()
        {
            _lastActivity = Time.realtimeSinceStartupAsDouble;
            UpdateFrameBudget();
        }

        private void UpdateFrameBudget()
        {
            int fps = RenderPaused || (IsWallpaperMode && !IsSessionActive
                && Time.realtimeSinceStartupAsDouble - _lastActivity >= IdleDelaySeconds)
                ? Mathf.Min(15, _normalFps) : _normalFps;
            if (Application.targetFrameRate != fps) Application.targetFrameRate = fps;
        }

        /// <summary>会话态回车轮询（Win32 GetAsyncKeyState，边沿触发）。</summary>
        private void PollSubmitKey()
        {
            bool down = IsSubmitKeyDown;
            bool pressedNow = down && !_submitKeyWasDown;
            _submitKeyWasDown = down;
            if (pressedNow)
            {
                Debug.Log("[AiPeople] 回车通道=Win32轮询");
                SubmitKeyPressed?.Invoke();
            }
        }

        /// <summary>诊断/轮询用：回车键当前是否按下（Win32 原始状态）。</summary>
        public bool IsSubmitKeyDown => (NativeMethods.GetAsyncKeyState(NativeMethods.VK_RETURN) & 0x8000) != 0;

        /// <summary>当前是否已拿到 OS 层键盘焦点（前台窗口就是我们）。</summary>
        public bool HasKeyboardFocus => _hwnd != IntPtr.Zero && NativeMethods.GetForegroundWindow() == _hwnd;

        /// <summary>会话态内若未取得前台焦点，周期性重试（最多 8 次）。</summary>
        private void RetryFocusIfNeeded()
        {
            if (_hwnd == IntPtr.Zero || HasKeyboardFocus || _focusRetries >= 8)
            {
                return;
            }

            _focusRetryTimer -= Time.unscaledDeltaTime;
            if (_focusRetryTimer > 0f)
            {
                return;
            }

            _focusRetryTimer = 0.5f;
            _focusRetries++;
            DesktopWindowNative.BringToFrontAndFocus(_hwnd);
        }

        /// <summary>
        /// 壁纸模式点击轮询：无鼠标钩子，仅当"左键刚按下 + 光标在常驻对话框区域 + 前台是桌面"时进入会话态。
        /// </summary>
        private void PollDesktopClick()
        {
            bool down = (NativeMethods.GetAsyncKeyState(NativeMethods.VK_LBUTTON) & 0x8000) != 0;
            bool pressedNow = down && !_mouseWasDown;
            _mouseWasDown = down;
            if (!pressedNow || ChatBarPixelRect.width < 2f)
            {
                return;
            }

            if (!NativeMethods.GetCursorPos(out NativeMethods.POINT point))
            {
                return;
            }

            var cursor = new Vector2(point.X, Screen.height - point.Y);
            if (!ChatBarPixelRect.Contains(cursor))
            {
                return;
            }

            if (!DesktopWindowNative.IsDesktopForeground(NativeMethods.GetForegroundWindow()))
            {
                return;
            }

            // v2.3：点击常驻条只请求唤出对话小窗；不再把主窗口抬成全屏（旧会话态已作废）
            Debug.Log("[AiPeople] 点击常驻对话条");
            ChatBarClicked?.Invoke();
        }

        /// <summary>全屏应用前台 → 暂停渲染（保留世界服务线程心跳）；否则恢复。</summary>
        private void EvaluateFullscreenPause()
        {
            if (IsSessionActive || !IsWallpaperMode || _hwnd == IntPtr.Zero)
            {
                SetRenderPaused(false);
                return;
            }

            SetRenderPaused(DesktopOcclusion.IsCovered(_hwnd, ChatProcessId?.Invoke() ?? 0));
        }

        private void SetRenderPaused(bool paused)
        {
            if (RenderPaused == paused)
            {
                return;
            }

            RenderPaused = paused;
            // 相机停绘时仍要及时处理热键和 /ui 命令；1 FPS 会使小窗开关额外延迟一秒。
            if (!paused) _lastActivity = Time.realtimeSinceStartupAsDouble;
            UpdateFrameBudget();
            if (_viewCamera != null)
            {
                if (paused) _cameraWasEnabled = _viewCamera.enabled;
                _viewCamera.enabled = paused ? false : _cameraWasEnabled;
            }
            if (_presentationCanvas != null)
            {
                if (paused) _canvasWasEnabled = _presentationCanvas.enabled;
                _presentationCanvas.enabled = paused ? false : _canvasWasEnabled;
            }

            Notify?.Invoke(paused ? "桌面被完整遮挡：已暂停场景与 UI 绘制" : "已恢复渲染");
        }

        private void OnApplicationQuit()
        {
            RestoreWindow();
        }

        private void OnDestroy()
        {
            RestoreWindow();
        }

        private void RestoreWindow()
        {
            if (IsWallpaperMode && _hwnd != IntPtr.Zero)
            {
                DesktopWindowNative.DetachFromDesktop(_hwnd);
                DesktopWindowNative.ApplyInteractiveStyles(_hwnd);
            }

            StopHotkey();
        }
    }
}
