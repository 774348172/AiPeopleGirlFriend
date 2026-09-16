using System;
using System.Text;
using AiPeople.Core;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.UI;

namespace AiPeople.UI
{
    /// <summary>
    /// 对话面板：流式显示（delta 追加 / done 定稿 / error 标记），本地显示历史。
    /// 规则：只有 done 提交成功才定稿为角色说过的话；失败会显式标记，不伪装成她说的话。
    /// </summary>
    public sealed class ChatPanel : MonoBehaviour
    {
        public const int MaxInputLength = 1000;

        private static readonly Color UserColor = new Color(0.72f, 0.83f, 0.98f);
        private static readonly Color HeroineColor = new Color(0.95f, 0.95f, 0.97f);
        private static readonly Color ErrorColor = new Color(0.96f, 0.58f, 0.52f);
        private static readonly Color MetaColor = new Color(0.66f, 0.72f, 0.82f);

        private ScrollRect _scroll;
        private RectTransform _content;
        private InputField _input;
        private Text _hint;
        private LocalHistory _history;
        private Action<string> _onSubmit;
        private Action<bool> _onFocusChanged;

        private bool _lastFocusState;
        private Text _streamText;
        private readonly StringBuilder _streamBuffer = new StringBuilder();

        public static ChatPanel Create(
            Transform canvasRoot,
            LocalHistory history,
            Action<string> onSubmit,
            Action<bool> onFocusChanged)
        {
            RectTransform panel = UiRoot.CreateRect("ChatPanel", canvasRoot,
                new Vector2(0f, 0f), new Vector2(1f, 0f),
                new Vector2(24f, 24f), new Vector2(-24f, 264f));
            var panelImage = panel.gameObject.AddComponent<Image>();
            panelImage.color = new Color(0.05f, 0.06f, 0.09f, 0.55f);

            ChatPanel self = panel.gameObject.AddComponent<ChatPanel>();
            self._history = history;
            self._onSubmit = onSubmit;
            self._onFocusChanged = onFocusChanged;
            self.BuildBody(panel);
            return self;
        }

        public void RebuildFromHistory()
        {
            for (int i = _content.childCount - 1; i >= 0; i--)
            {
                Destroy(_content.GetChild(i).gameObject);
            }

            foreach (DisplayMessage message in _history.Messages)
            {
                AppendMessageVisual(message);
            }

            ScrollToBottom();
        }

        public void ShowUser(string text)
        {
            DisplayMessage message = _history.Add("user", text, "你");
            AppendMessageVisual(message);
            ScrollToBottom();
        }

        public void BeginHeroine()
        {
            _streamBuffer.Length = 0;
            DisplayMessage message = _history.Add("heroine", "…", "白未晞");
            _streamText = AppendMessageVisual(message);
            _streamText.color = HeroineColor;
            SetHint("正在说…");
            ScrollToBottom();
        }

        public void AppendDelta(string delta)
        {
            if (_streamText == null)
            {
                return;
            }

            _streamBuffer.Append(delta);
            _streamText.text = _streamBuffer.ToString();
            ScrollToBottom();
        }

        public void CompleteHeroine(string committedReply)
        {
            if (_streamText == null)
            {
                return;
            }

            string finalText = string.IsNullOrEmpty(committedReply) ? _streamBuffer.ToString() : committedReply;
            _streamText.text = finalText;
            _streamText.color = HeroineColor;
            _history.UpdateLast(finalText);
            _streamText = null;
            _streamBuffer.Length = 0;
            SetHint(string.Empty);
            ScrollToBottom();
        }

        public void FailHeroine(string error)
        {
            if (_streamText == null)
            {
                return;
            }

            string partial = _streamBuffer.Length > 0 ? _streamBuffer.ToString() + "\n" : string.Empty;
            string finalText = partial + "（生成失败：" + error + "）";
            _streamText.text = finalText;
            _streamText.color = ErrorColor;
            _history.UpdateLast(finalText, true);
            _streamText = null;
            _streamBuffer.Length = 0;
            SetHint("生成失败");
            ScrollToBottom();
        }

        public void SetHint(string text)
        {
            if (_hint != null)
            {
                _hint.text = text ?? string.Empty;
            }
        }

        public bool IsInputFocused => _input != null && _input.isFocused;

        /// <summary>诊断用：当前输入框内容。</summary>
        public string CurrentInputText => _input != null ? _input.text : string.Empty;

        /// <summary>桌面形态：会话态显示、退出后隐藏（保留本地历史）。</summary>
        public void SetVisible(bool visible)
        {
            gameObject.SetActive(visible);
        }

        public void FocusInput()
        {
            if (_input == null)
            {
                return;
            }

            _input.ActivateInputField();
            _input.Select();
        }

        public void Blur()
        {
            if (_input != null)
            {
                _input.DeactivateInputField();
            }
        }

        private void BuildBody(RectTransform panel)
        {
            RectTransform viewport = UiRoot.CreateRect("Viewport", panel,
                Vector2.zero, Vector2.one, new Vector2(10f, 56f), new Vector2(-10f, -10f));
            var viewportImage = viewport.gameObject.AddComponent<Image>();
            viewportImage.color = new Color(0f, 0f, 0f, 0.01f);
            viewport.gameObject.AddComponent<RectMask2D>();

            _scroll = panel.gameObject.AddComponent<ScrollRect>();
            _scroll.viewport = viewport;
            _scroll.horizontal = false;
            _scroll.vertical = true;
            _scroll.movementType = ScrollRect.MovementType.Clamped;
            _scroll.scrollSensitivity = 32f;

            RectTransform content = UiRoot.CreateRect("Content", viewport,
                new Vector2(0f, 1f), new Vector2(1f, 1f), Vector2.zero, Vector2.zero);
            content.pivot = new Vector2(0.5f, 1f);
            content.anchoredPosition = Vector2.zero;
            var layout = content.gameObject.AddComponent<VerticalLayoutGroup>();
            layout.padding = new RectOffset(8, 8, 8, 8);
            layout.spacing = 8f;
            layout.childControlWidth = true;
            layout.childControlHeight = true;
            layout.childForceExpandWidth = true;
            layout.childForceExpandHeight = false;
            var fitter = content.gameObject.AddComponent<ContentSizeFitter>();
            fitter.verticalFit = ContentSizeFitter.FitMode.PreferredSize;
            _scroll.content = content;
            _content = content;

            RectTransform inputRect = UiRoot.CreateRect("Input", panel,
                new Vector2(0f, 0f), new Vector2(1f, 0f), new Vector2(10f, 10f), new Vector2(-104f, 46f));
            var inputBg = inputRect.gameObject.AddComponent<Image>();
            inputBg.color = new Color(1f, 1f, 1f, 0.94f);

            _input = inputRect.gameObject.AddComponent<InputField>();
            _input.lineType = InputField.LineType.SingleLine;
            _input.characterLimit = MaxInputLength;

            Text inputText = UiRoot.CreateText("Text", inputRect, 19, new Color(0.09f, 0.09f, 0.12f), TextAnchor.MiddleLeft);
            UiRoot.Stretch(inputText.rectTransform, 8f, 4f, 8f, 4f);
            Text placeholder = UiRoot.CreateText("Placeholder", inputRect, 19, new Color(0.45f, 0.47f, 0.52f), TextAnchor.MiddleLeft);
            UiRoot.Stretch(placeholder.rectTransform, 8f, 4f, 8f, 4f);
            placeholder.text = "输入要对她说的话（回车发送）";
            _input.textComponent = inputText;
            _input.placeholder = placeholder;
            _input.targetGraphic = inputBg;
            _input.onSubmit.AddListener(_ => Submit());

            RectTransform buttonRect = UiRoot.CreateRect("Send", panel,
                new Vector2(1f, 0f), new Vector2(1f, 0f), new Vector2(-92f, 10f), new Vector2(-10f, 46f));
            var buttonImage = buttonRect.gameObject.AddComponent<Image>();
            buttonImage.color = new Color(0.32f, 0.52f, 0.78f, 0.95f);
            var button = buttonRect.gameObject.AddComponent<Button>();
            button.targetGraphic = buttonImage;
            button.onClick.AddListener(() => Submit());
            Text buttonLabel = UiRoot.CreateText("Label", buttonRect, 19, Color.white, TextAnchor.MiddleCenter);
            UiRoot.Stretch(buttonLabel.rectTransform, 2f, 2f, 2f, 2f);
            buttonLabel.text = "发送";

            _hint = UiRoot.CreateText("Hint", panel, 16, MetaColor, TextAnchor.UpperRight);
            _hint.rectTransform.anchorMin = new Vector2(1f, 1f);
            _hint.rectTransform.anchorMax = new Vector2(1f, 1f);
            _hint.rectTransform.pivot = new Vector2(1f, 1f);
            _hint.rectTransform.anchoredPosition = new Vector2(-10f, -8f);
            _hint.rectTransform.sizeDelta = new Vector2(360f, 24f);
            _hint.text = string.Empty;
        }

        private Text AppendMessageVisual(DisplayMessage message)
        {
            RectTransform item = UiRoot.CreateRect("Message", _content,
                new Vector2(0f, 1f), new Vector2(1f, 1f), Vector2.zero, Vector2.zero);
            var layout = item.gameObject.AddComponent<VerticalLayoutGroup>();
            layout.spacing = 2f;
            layout.childControlWidth = true;
            layout.childControlHeight = true;
            layout.childForceExpandWidth = true;
            layout.childForceExpandHeight = false;

            Text meta = UiRoot.CreateText("Meta", item, 14, MetaColor, TextAnchor.UpperLeft);
            meta.text = string.IsNullOrEmpty(message.meta)
                ? (message.role == "user" ? "你" : "白未晞")
                : message.meta;

            Color color = message.error ? ErrorColor : (message.role == "user" ? UserColor : HeroineColor);
            Text body = UiRoot.CreateText("Body", item, 19, color, TextAnchor.UpperLeft);
            body.text = message.text;
            return body;
        }

        /// <summary>外部触发提交（桌面外壳的回车轮询通道）。</summary>
        public void TriggerSubmit()
        {
            if (!gameObject.activeInHierarchy)
            {
                return;
            }

            Submit();
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

            Debug.Log("[AiPeople] 提交对白：" + text);
            _input.text = string.Empty;
            _onSubmit?.Invoke(text);
            FocusInput();
        }

        private void ScrollToBottom()
        {
            Canvas.ForceUpdateCanvases();
            if (_scroll != null)
            {
                _scroll.verticalNormalizedPosition = 0f;
            }
        }

        private void Update()
        {
            if (_input != null)
            {
                bool focused = _input.isFocused;
                if (focused != _lastFocusState)
                {
                    _lastFocusState = focused;
                    _onFocusChanged?.Invoke(focused);
                }
            }

            bool enterPressed = EnterPressedThisFrame();

            // 输入框已聚焦：回车提交（新/旧输入系统双通道，避免某一路拿不到事件）
            if (_lastFocusState)
            {
                if (enterPressed)
                {
                    Submit();
                }

                return;
            }

            bool focusKey = enterPressed
                || (Keyboard.current != null && Keyboard.current.tKey.wasPressedThisFrame)
                || LegacyKeyPressed(KeyCode.T);

            if (focusKey)
            {
                FocusInput();
            }
        }

        private static bool _legacyInputAvailable = true;

        /// <summary>回车检测：新输入系统 + legacy 双通道（部分环境注入/特殊键盘事件只有一路可见）。</summary>
        private static bool EnterPressedThisFrame()
        {
            if (Keyboard.current != null
                && (Keyboard.current.enterKey.wasPressedThisFrame || Keyboard.current.numpadEnterKey.wasPressedThisFrame))
            {
                Debug.Log("[AiPeople] 回车通道=新输入系统");
                return true;
            }

            if (LegacyKeyPressed(KeyCode.Return) || LegacyKeyPressed(KeyCode.KeypadEnter))
            {
                Debug.Log("[AiPeople] 回车通道=legacy");
                return true;
            }

            return false;
        }

        private static bool LegacyKeyPressed(KeyCode key)
        {
            if (!_legacyInputAvailable)
            {
                return false;
            }

            try
            {
                return Input.GetKeyDown(key);
            }
            catch (System.InvalidOperationException)
            {
                _legacyInputAvailable = false;
                Debug.LogWarning("[AiPeople] legacy 输入不可用（Active Input Handling 需为 Both）");
                return false;
            }
        }
    }
}
