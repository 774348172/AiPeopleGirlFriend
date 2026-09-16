using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Text;
using AiPeople.Core;
using AiPeople.Shell;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.Networking;
using UnityEngine.UI;

namespace AiPeople.UI
{
    /// <summary>
    /// 对话小窗进程（--chat-window 启动）：立绘 + 对话历史 + 输入框。
    /// 只与主进程的 /ui/* 通信（不直连后端）；启动即隐藏，主进程请求显示时才出现；
    /// 支持拖动移动（位置持久化）、Esc 收起、失去前台焦点自动收起。
    /// </summary>
    public sealed class ChatWindowApp : MonoBehaviour
    {
        private const string MainEndpoint = "http://127.0.0.1:8771";
        private const float VisiblePollSeconds = 0.2f;
        private const float HiddenPollSeconds = 0.05f;
        private const int HiddenFrameRate = 30;
        private string _mainEndpoint = MainEndpoint;
        private Canvas _canvas;
        private bool _pollInFlight;
        private bool _hideInFlight;
        private bool _hideUnconfirmed;
        private int _visibilityEpoch;
        private UnityWebRequest _activePoll;
        private Coroutine _focusRetry;

        private static readonly int WindowWidth = 900;
        private static readonly int WindowHeight = 420;

        private InputField _input;
        private Text _status;
        private Text _historyText;
        private RectTransform _root;
        private Image _portrait;
        private string _lastStateJson = string.Empty;
        private bool _visible;
        private bool _dragging;
        private Vector2Int _dragOffset;
        private Vector2Int _windowPosition;
        private float _pollTimer;
        private float _visibleSince;
        private RoomControlsPanel _roomControls;

        public static bool IsChatWindowMode()
        {
            string[] args = Environment.GetCommandLineArgs();
            foreach (string arg in args)
            {
                if (string.Equals(arg, "--chat-window", StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }

            return false;
        }

        private void Start()
        {
            UiLatencyProbe.Attach(gameObject);
            UiLatencyTrace.Mark("chat_start");
            Application.runInBackground = true;
            QualitySettings.vSyncCount = 0;
            Application.targetFrameRate = HiddenFrameRate;
            Screen.SetResolution(WindowWidth, WindowHeight, FullScreenMode.Windowed);
            EnsureEventSystem();
            BuildUi();
            UiLatencyTrace.Mark("ui_built");
            LoadPosition();
            PlaceWindow();
            StartCoroutine(StartHiddenNextFrame());
        }

        private IEnumerator StartHiddenNextFrame()
        {
            yield return null;
            ChatWindowNative.ApplyFramelessToolWindow();
            HideWindowImmediately();
        }

        private void EnsureEventSystem()
        {
            if (UnityEngine.Object.FindFirstObjectByType<UnityEngine.EventSystems.EventSystem>() != null)
            {
                return;
            }

            var go = new GameObject("EventSystem");
            go.AddComponent<UnityEngine.EventSystems.EventSystem>();
            go.AddComponent<UnityEngine.InputSystem.UI.InputSystemUIInputModule>();
        }

        private void BuildUi()
        {
            GameObject canvas = UiRoot.CreateCanvas();
            _canvas = canvas.GetComponent<Canvas>();
            canvas.GetComponent<CanvasScaler>().referenceResolution = new Vector2(WindowWidth, WindowHeight);
            _root = UiRoot.CreateRect("ChatWindow", canvas.transform,
                Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero);

            var background = _root.gameObject.AddComponent<Image>();
            background.color = new Color(0.07f, 0.08f, 0.11f, 0.97f);

            // 顶部拖动条
            RectTransform header = UiRoot.CreateRect("Header", _root,
                new Vector2(0f, 1f), new Vector2(1f, 1f), new Vector2(0f, -40f), Vector2.zero);
            var headerImage = header.gameObject.AddComponent<Image>();
            headerImage.color = new Color(0.14f, 0.16f, 0.22f, 1f);
            var dragArea = header.gameObject.AddComponent<Button>();
            dragArea.transition = Selectable.Transition.None;

            Text title = UiRoot.CreateText("Title", header, 17, new Color(0.86f, 0.90f, 0.98f), TextAnchor.MiddleLeft);
            UiRoot.Stretch(title.rectTransform, 14f, 2f, 120f, 2f);
            title.text = "和白未晞说话";

            Text close = UiRoot.CreateText("Close", header, 16, new Color(0.8f, 0.84f, 0.92f), TextAnchor.MiddleCenter);
            close.rectTransform.anchorMin = new Vector2(1f, 0.5f);
            close.rectTransform.anchorMax = new Vector2(1f, 0.5f);
            close.rectTransform.pivot = new Vector2(1f, 0.5f);
            close.rectTransform.anchoredPosition = new Vector2(-8f, 0f);
            close.rectTransform.sizeDelta = new Vector2(84f, 26f);
            close.text = "收起 (Esc)";
            var closeButton = close.gameObject.AddComponent<Button>();
            close.raycastTarget = true;
            closeButton.onClick.AddListener(RequestHide);
            UiLatencyProbe.Bind(close.gameObject, "close");

            Text quit = UiRoot.CreateText("Quit", header, 16, new Color(0.72f, 0.78f, 0.9f), TextAnchor.MiddleCenter);
            quit.rectTransform.anchorMin = new Vector2(1f, 0.5f);
            quit.rectTransform.anchorMax = new Vector2(1f, 0.5f);
            quit.rectTransform.pivot = new Vector2(1f, 0.5f);
            quit.rectTransform.anchoredPosition = new Vector2(-96f, 0f);
            quit.rectTransform.sizeDelta = new Vector2(64f, 26f);
            quit.text = "退出程序";
            quit.raycastTarget = true;
            var quitButton = quit.gameObject.AddComponent<Button>();
            quitButton.onClick.AddListener(RequestQuit);

            Text wallpaper = UiRoot.CreateText("ModeWallpaper", header, 16, new Color(0.72f, 0.78f, 0.9f), TextAnchor.MiddleCenter);
            wallpaper.rectTransform.anchorMin = new Vector2(1f, 0.5f);
            wallpaper.rectTransform.anchorMax = new Vector2(1f, 0.5f);
            wallpaper.rectTransform.pivot = new Vector2(1f, 0.5f);
            wallpaper.rectTransform.anchoredPosition = new Vector2(-164f, 0f);
            wallpaper.rectTransform.sizeDelta = new Vector2(98f, 26f);
            wallpaper.text = "恢复桌面壁纸";
            wallpaper.raycastTarget = true;
            var wallpaperButton = wallpaper.gameObject.AddComponent<Button>();
            wallpaperButton.onClick.AddListener(() => RequestMode("wallpaper"));
            Text room = UiRoot.CreateText("RoomButton", header, 16, new Color(.72f, .78f, .9f), TextAnchor.MiddleCenter);
            room.rectTransform.anchorMin = room.rectTransform.anchorMax = new Vector2(1, .5f);
            room.rectTransform.pivot = new Vector2(1, .5f);
            room.rectTransform.anchoredPosition = new Vector2(-274, 0);
            room.rectTransform.sizeDelta = new Vector2(64, 26);
            room.text = "房间";
            room.raycastTarget = true;
            room.gameObject.AddComponent<Button>().onClick.AddListener(() =>
            {
                bool show = !_roomControls.Visible;
                _roomControls.SetVisible(show);
                _input.interactable = !show;
                if (show) _input.DeactivateInputField();
                _historyText.transform.parent.gameObject.SetActive(!show);
                room.text = show ? "对话" : "房间";
                UiLatencyProbe.RoomToggled(show);
            });
            UiLatencyProbe.Bind(room.gameObject, "room");

            // 立绘
            RectTransform portraitRect = UiRoot.CreateRect("Portrait", _root,
                new Vector2(0f, 0f), new Vector2(0f, 1f), new Vector2(10f, 10f), new Vector2(230f, -50f));
            _portrait = portraitRect.gameObject.AddComponent<Image>();
            _portrait.color = new Color(1f, 1f, 1f, 1f);
            _portrait.preserveAspect = true;
            LoadPortrait();

            // 历史
            RectTransform historyRect = UiRoot.CreateRect("History", _root,
                new Vector2(0f, 0f), new Vector2(1f, 1f), new Vector2(242f, 64f), new Vector2(-10f, -50f));
            // Long histories must not draw over the header or the room controls.
            historyRect.gameObject.AddComponent<RectMask2D>();
            _historyText = UiRoot.CreateText("HistoryText", historyRect, 17, new Color(0.92f, 0.93f, 0.97f), TextAnchor.LowerLeft);
            UiRoot.Stretch(_historyText.rectTransform, 0f, 0f, 0f, 0f);
            _historyText.text = string.Empty;

            // 输入行
            RectTransform inputRect = UiRoot.CreateRect("Input", _root,
                new Vector2(0f, 0f), new Vector2(1f, 0f), new Vector2(242f, 14f), new Vector2(-104f, 50f));
            var inputBackground = inputRect.gameObject.AddComponent<Image>();
            inputBackground.color = new Color(1f, 1f, 1f, 0.94f);
            _input = inputRect.gameObject.AddComponent<InputField>();
            _input.lineType = InputField.LineType.SingleLine;
            _input.characterLimit = 1000;

            Text inputText = UiRoot.CreateText("Text", inputRect, 17, new Color(0.08f, 0.08f, 0.11f), TextAnchor.MiddleLeft);
            UiRoot.Stretch(inputText.rectTransform, 8f, 4f, 8f, 4f);
            Text placeholder = UiRoot.CreateText("Placeholder", inputRect, 17, new Color(0.45f, 0.47f, 0.52f), TextAnchor.MiddleLeft);
            UiRoot.Stretch(placeholder.rectTransform, 8f, 4f, 8f, 4f);
            placeholder.text = "说点什么（回车发送）";
            _input.textComponent = inputText;
            _input.placeholder = placeholder;
            _input.targetGraphic = inputBackground;

            RectTransform sendRect = UiRoot.CreateRect("Send", _root,
                new Vector2(1f, 0f), new Vector2(1f, 0f), new Vector2(-94f, 14f), new Vector2(-10f, 50f));
            var sendImage = sendRect.gameObject.AddComponent<Image>();
            sendImage.color = new Color(0.32f, 0.52f, 0.78f, 0.95f);
            var sendButton = sendRect.gameObject.AddComponent<Button>();
            sendButton.targetGraphic = sendImage;
            sendButton.onClick.AddListener(() => Submit());
            Text sendLabel = UiRoot.CreateText("Label", sendRect, 17, Color.white, TextAnchor.MiddleCenter);
            UiRoot.Stretch(sendLabel.rectTransform, 2f, 2f, 2f, 2f);
            sendLabel.text = "发送";

            _status = UiRoot.CreateText("Status", _root, 14, new Color(0.66f, 0.72f, 0.82f), TextAnchor.MiddleRight);
            _status.rectTransform.anchorMin = new Vector2(1f, 0f);
            _status.rectTransform.anchorMax = new Vector2(1f, 0f);
            _status.rectTransform.pivot = new Vector2(1f, 0f);
            _status.rectTransform.anchoredPosition = new Vector2(-10f, 54f);
            _status.rectTransform.sizeDelta = new Vector2(420f, 22f);
            _status.text = string.Empty;
            _roomControls = gameObject.AddComponent<RoomControlsPanel>();
            _roomControls.Initialize(_root, _mainEndpoint);
        }

        private void LoadPortrait()
        {
            string path = Path.Combine(Application.streamingAssetsPath, "portrait.png");
            try
            {
                if (!File.Exists(path))
                {
                    Debug.Log("[AiPeople] 未找到立绘：" + path + "（使用占位色块）");
                    _portrait.color = new Color(0.18f, 0.20f, 0.26f, 1f);
                    return;
                }

                byte[] bytes = File.ReadAllBytes(path);
                var texture = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                if (texture.LoadImage(bytes))
                {
                    _portrait.sprite = Sprite.Create(
                        texture,
                        new Rect(0f, 0f, texture.width, texture.height),
                        new Vector2(0.5f, 0.5f));
                    _portrait.color = Color.white;
                }
            }
            catch (Exception exception)
            {
                Debug.LogWarning("[AiPeople] 立绘加载失败：" + exception.Message);
            }
        }

        private void Update()
        {
            if (_roomControls != null) _roomControls.WindowVisible = _visible;
            if (_visible)
            {
                HandleDrag();
                HandleKeys();
            }

            _pollTimer -= Time.unscaledDeltaTime;
            if (_pollTimer > 0f || _pollInFlight || _hideInFlight)
            {
                return;
            }

            StartCoroutine(PollState());
        }

        private void HandleDrag()
        {
            if (Mouse.current == null)
            {
                return;
            }

            Vector2 mouse = Mouse.current.position.ReadValue();
            float headerHeight = 40f;
            bool overHeader = mouse.y >= Screen.height - headerHeight && mouse.x < Screen.width - 350;

            if (Mouse.current.leftButton.wasPressedThisFrame && overHeader)
            {
                if (ChatWindowNative.TryGetCurrentWindowRect(out int x, out int y, out _, out _))
                {
                    if (ChatWindowNative.TryGetCursorPosition(out int cursorX, out int cursorY))
                    {
                        _dragging = true;
                        _dragOffset = new Vector2Int(cursorX - x, cursorY - y);
                    }
                }
            }

            if (_dragging && ChatWindowNative.IsMouseLeftDown())
            {
                if (ChatWindowNative.TryGetCursorPosition(out int cursorX, out int cursorY))
                {
                    _windowPosition = new Vector2Int(cursorX - _dragOffset.x, cursorY - _dragOffset.y);
                    ChatWindowNative.MoveCurrentWindow(_windowPosition.x, _windowPosition.y);
                }
            }
            else if (_dragging)
            {
                _dragging = false;
                SavePosition();
            }
        }

        private void HandleKeys()
        {
            if (Keyboard.current == null)
            {
                return;
            }

            bool enter = Keyboard.current.enterKey.wasPressedThisFrame || Keyboard.current.numpadEnterKey.wasPressedThisFrame;
            if (_input != null && _input.isFocused && enter)
            {
                Submit();
                return;
            }

            if (Keyboard.current.escapeKey.wasPressedThisFrame)
            {
                RequestHide();
            }
        }

        private void Submit()
        {
            if (_input == null)
            {
                return;
            }

            string text = (_input.text ?? string.Empty).Trim();
            if (text.Length == 0)
            {
                return;
            }

            _input.text = string.Empty;
            StartCoroutine(SendText(text));
        }

        private IEnumerator SendText(string text)
        {
            string payload = "{\"text\":" + JsonUtil.Str(text) + "}";
            using var request = new UnityWebRequest(MainEndpoint + "/ui/send", "POST");
            request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(payload));
            request.uploadHandler.contentType = "application/json";
            request.downloadHandler = new DownloadHandlerBuffer();
            request.timeout = 10;
            yield return request.SendWebRequest();

            if (request.result != UnityWebRequest.Result.Success)
            {
                _status.text = "主程序未响应（消息未发送）";
                yield break;
            }

            if (request.downloadHandler.text.Contains("\"accepted\":false"))
            {
                _status.text = "她还在说上一句，稍等";
            }
        }

        private IEnumerator PollState()
        {
            if (_pollInFlight || _hideInFlight) yield break;
            _pollInFlight = true;
            int epoch = _visibilityEpoch;
            bool visibilityOnly = !_visible;
            bool failed = false;
            try
            {
                using var request = UnityWebRequest.Get(_mainEndpoint + (visibilityOnly ? "/ui/visibility" : "/ui/state"));
                _activePoll = request;
                request.timeout = 5;
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    failed = true;
                    if (_visible)
                    {
                        _status.text = "主程序未响应";
                    }

                    yield break;
                }

                string json = request.downloadHandler.text;
                if (epoch != _visibilityEpoch) yield break; // 收起前发出的回应不得重新打开窗口。
                bool show = json.Contains("\"show\":true");
                if (_hideUnconfirmed)
                {
                    if (show)
                    {
                        StartCoroutine(HideSelf());
                        yield break;
                    }
                    _hideUnconfirmed = false;
                }
                if (show != _visible)
                {
                    if (show)
                    {
                        ShowWindowSelf();
                    }
                    else
                    {
                        HideWindowImmediately();
                    }
                }

                if (!visibilityOnly && _visible && json != _lastStateJson)
                {
                    _lastStateJson = json;
                    RenderState(json);
                }
                if (!visibilityOnly && _visible) UiLatencyProbe.HistoryReceived();
            }
            finally
            {
                _activePoll = null;
                _pollInFlight = false;
                if (epoch != _visibilityEpoch) _pollTimer = 0f;
                else if (failed) _pollTimer = _visible ? 1f : 0.1f;
                else _pollTimer = visibilityOnly && _visible ? 0f : (_visible ? VisiblePollSeconds : HiddenPollSeconds);
            }
        }

        private void ShowWindowSelf()
        {
            UiLatencyTrace.Mark("show_begin");
            Application.targetFrameRate = 60;
            if (_canvas != null) _canvas.enabled = true;
            if (!Application.isEditor)
            {
                PlaceWindow();
                ChatWindowNative.ShowCurrentWindow();
            }
            _visible = true;
            UiLatencyTrace.Mark("native_shown");
            UiLatencyProbe.Shown();
            _visibleSince = Time.unscaledTime;
            _input?.ActivateInputField();
            if (!Application.isEditor)
            {
                if (_focusRetry != null) StopCoroutine(_focusRetry);
                _focusRetry = StartCoroutine(FocusAfterShow());
            }
            Debug.Log("[AiPeople] 小窗已显示 utc=" + DateTime.UtcNow.ToString("O"));
        }

        private IEnumerator FocusAfterShow()
        {
            // 显示路径不等待复杂的前台切换；下一帧再补焦点，避免打开动画被 Win32 阻塞。
            yield return null;
            ChatWindowNative.FocusCurrentWindow();
            _focusRetry = null;
        }

        private void RequestHide()
        {
            UiLatencyTrace.Mark("hide_request");
            if (!_visible || _hideInFlight) return;
            _visibilityEpoch++;
            _hideUnconfirmed = true;
            HideWindowImmediately();
            _activePoll?.Abort(); // 旧历史查询不能阻塞下一次唤出。
            StartCoroutine(HideSelf());
        }

        private void HideWindowImmediately()
        {
            if (!Application.isEditor) ChatWindowNative.HideCurrentWindow();
            UiLatencyTrace.Mark("native_hidden");
            _visible = false;
            _dragging = false;
            _input?.DeactivateInputField();
            if (_canvas != null) _canvas.enabled = false;
            // 停止 UI 绘制，但保留响应显隐请求的主循环。
            Application.targetFrameRate = HiddenFrameRate;
            Debug.Log("[AiPeople] 小窗已隐藏 utc=" + DateTime.UtcNow.ToString("O"));
        }

        /// <summary>请求主程序切换窗口模式（wallpaper / normal / auto）。</summary>
        private void RequestMode(string mode)
        {
            StartCoroutine(PostMode(mode));
        }

        private IEnumerator PostMode(string mode)
        {
            string payload = "{\"mode\":" + JsonUtil.Str(mode) + "}";
            using var request = new UnityWebRequest(MainEndpoint + "/ui/mode", "POST");
            request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(payload));
            request.uploadHandler.contentType = "application/json";
            request.downloadHandler = new DownloadHandlerBuffer();
            request.timeout = 10;
            yield return request.SendWebRequest();
            if (request.result == UnityWebRequest.Result.Success)
            {
                _status.text = mode == "wallpaper" ? "已切换为壁纸模式" : "已切换为普通窗口";
            }
            else
            {
                _status.text = "切换失败";
            }
        }

        /// <summary>请求主程序退出（联动关闭两个进程）。</summary>
        private void RequestQuit()
        {
            StartCoroutine(QuitWholeApp());
        }

        private IEnumerator QuitWholeApp()
        {
            using var request = new UnityWebRequest(MainEndpoint + "/ui/quit", "POST");
            request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes("{}"));
            request.uploadHandler.contentType = "application/json";
            request.downloadHandler = new DownloadHandlerBuffer();
            request.timeout = 5;
            yield return request.SendWebRequest();
            ChatWindowNative.HideCurrentWindow();
            Application.Quit();
        }

        private IEnumerator HideSelf()
        {
            _hideInFlight = true;
            try
            {
                using var request = new UnityWebRequest(_mainEndpoint + "/ui/hide", "POST");
                request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes("{}"));
                request.uploadHandler.contentType = "application/json";
                request.downloadHandler = new DownloadHandlerBuffer();
                request.timeout = 5;
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                    Debug.LogWarning("[AiPeople] 小窗已本地收起，但主进程收起确认失败：" + request.error);
                else
                    _hideUnconfirmed = false;
            }
            finally
            {
                _hideInFlight = false;
                _pollTimer = 0f;
            }
        }

        /// <summary>失去前台焦点（用户点了别处）→ 自动收起。</summary>
        private void LateUpdate()
        {
            if (!_visible || _dragging)
            {
                return;
            }

            // 刚显示时给 1.2s 宽限，避免抢焦点尚未成功就自动收起
            if (Time.unscaledTime - _visibleSince < 1.2f)
            {
                return;
            }

            if (!ChatWindowNative.IsCurrentWindowForeground())
            {
                RequestHide();
            }
        }

        private void RenderState(string json)
        {
            var builder = new StringBuilder(1024);
            bool generating = json.Contains("\"generating\":true");

            // 解析 messages 数组（简单扫描，容忍字段顺序）
            int index = json.IndexOf("\"messages\":[", StringComparison.Ordinal);
            if (index >= 0)
            {
                int cursor = index + 12;
                while (cursor < json.Length)
                {
                    int roleIndex = json.IndexOf("\"role\":\"", cursor, StringComparison.Ordinal);
                    if (roleIndex < 0)
                    {
                        break;
                    }

                    int closeIndex = json.IndexOf("\"", roleIndex + 8, StringComparison.Ordinal);
                    string role = json.Substring(roleIndex + 8, closeIndex - roleIndex - 8);

                    string text = string.Empty;
                    int afterText = closeIndex;
                    int textKey = json.IndexOf("\"text\":", closeIndex, StringComparison.Ordinal);
                    if (textKey >= 0)
                    {
                        int quote = json.IndexOf('"', textKey + 7);
                        if (quote >= 0 && JsonUtil.TryReadStringAt(json, quote, out string parsed, out int after))
                        {
                            text = parsed;
                            afterText = after;
                        }
                    }

                    bool isError = false;
                    int errorIndex = json.IndexOf("\"error\":", afterText, StringComparison.Ordinal);
                    if (errorIndex >= 0)
                    {
                        int end = Math.Min(errorIndex + 20, json.Length);
                        isError = json.Substring(errorIndex, end - errorIndex).Contains("true");
                    }

                    string speaker = role == "user" ? "你" : "白未晞";
                    builder.Append(speaker).Append("：");
                    if (isError)
                    {
                        builder.Append("<color=#F0938A>");
                    }

                    builder.Append(text);
                    if (isError)
                    {
                        builder.Append("</color>");
                    }

                    builder.Append('\n');
                    cursor = Math.Max(errorIndex, afterText) + 1;
                }
            }

            if (generating)
            {
                builder.Append("<color=#9FB6D4>白未晞正在说…</color>");
            }

            _historyText.text = builder.ToString();
            if (!_visible)
            {
                _status.text = string.Empty;
            }
        }

        private void LoadPosition()
        {
            try
            {
                string path = Path.Combine(Application.persistentDataPath, "aipeople_chatwindow.json");
                if (File.Exists(path))
                {
                    string json = File.ReadAllText(path);
                    JsonLine.TryGetString(json, "x", out string xText);
                    JsonLine.TryGetString(json, "y", out string yText);
                    if (int.TryParse(xText, out int x) && int.TryParse(yText, out int y))
                    {
                        _windowPosition = new Vector2Int(x, y);
                        return;
                    }
                }
            }
            catch (Exception exception)
            {
                Debug.LogWarning("[AiPeople] 小窗位置读取失败：" + exception.Message);
            }

            _windowPosition = new Vector2Int(
                Mathf.Max(0, (Screen.currentResolution.width - WindowWidth) / 2),
                Mathf.Max(0, Screen.currentResolution.height - WindowHeight - 80));
        }

        private void SavePosition()
        {
            try
            {
                string path = Path.Combine(Application.persistentDataPath, "aipeople_chatwindow.json");
                File.WriteAllText(path, "{\"x\":" + _windowPosition.x + ",\"y\":" + _windowPosition.y + "}");
            }
            catch (Exception exception)
            {
                Debug.LogWarning("[AiPeople] 小窗位置保存失败：" + exception.Message);
            }
        }

        private void PlaceWindow()
        {
            ChatWindowNative.MoveCurrentWindow(_windowPosition.x, _windowPosition.y);
        }
    }
}
