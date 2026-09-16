using System;
using AiPeople.Core;
using UnityEngine;
using UnityEngine.UI;

namespace AiPeople.UI
{
    /// <summary>
    /// 桌面形态下的常驻对话框条：始终可见（底部居中），显示最近一句对白与输入提示。
    /// 壁纸模式下窗口不接收输入，点击由外壳轮询判定（DesktopShellController.ChatBarPixelRect）；
    /// 普通窗口模式下按钮可直接点击。
    /// </summary>
    public sealed class DesktopChatBar : MonoBehaviour
    {
        private Text _message;
        private Text _hint;
        private LocalHistory _history;

        public static DesktopChatBar Create(
            Transform canvasRoot,
            LocalHistory history,
            Action onClicked,
            Action onSettings)
        {
            RectTransform panel = UiRoot.CreateRect("DesktopChatBar", canvasRoot,
                new Vector2(0.5f, 0f), new Vector2(0.5f, 0f),
                new Vector2(-460f, 40f), new Vector2(460f, 128f));
            var background = panel.gameObject.AddComponent<Image>();
            background.color = new Color(0.05f, 0.06f, 0.09f, 0.62f);

            var self = panel.gameObject.AddComponent<DesktopChatBar>();
            self._history = history;

            Text message = UiRoot.CreateText("Message", panel, 21, new Color(0.95f, 0.95f, 0.97f), TextAnchor.MiddleLeft);
            UiRoot.Stretch(message.rectTransform, 18f, 40f, 120f, 12f);
            message.text = "……";
            self._message = message;

            Text hint = UiRoot.CreateText("Hint", panel, 15, new Color(0.66f, 0.72f, 0.82f), TextAnchor.LowerLeft);
            UiRoot.Stretch(hint.rectTransform, 18f, 10f, 120f, 42f);
            hint.text = "点击这里和她说话（热键 Ctrl+Alt+B）";
            self._hint = hint;

            RectTransform talkButton = UiRoot.CreateRect("Talk", panel,
                new Vector2(1f, 0f), new Vector2(1f, 0f), new Vector2(-104f, 12f), new Vector2(-12f, 76f));
            var talkImage = talkButton.gameObject.AddComponent<Image>();
            talkImage.color = new Color(0.32f, 0.52f, 0.78f, 0.92f);
            var button = talkButton.gameObject.AddComponent<Button>();
            button.targetGraphic = talkImage;
            button.onClick.AddListener(() => onClicked?.Invoke());
            Text talkLabel = UiRoot.CreateText("Label", talkButton, 18, Color.white, TextAnchor.MiddleCenter);
            UiRoot.Stretch(talkLabel.rectTransform, 2f, 2f, 2f, 2f);
            talkLabel.text = "说点什么";

            if (onSettings != null)
            {
                RectTransform gear = UiRoot.CreateRect("Settings", panel,
                    new Vector2(1f, 1f), new Vector2(1f, 1f), new Vector2(-34f, -34f), new Vector2(-6f, -6f));
                var gearImage = gear.gameObject.AddComponent<Image>();
                gearImage.color = new Color(1f, 1f, 1f, 0.16f);
                var gearButton = gear.gameObject.AddComponent<Button>();
                gearButton.targetGraphic = gearImage;
                gearButton.onClick.AddListener(() => onSettings?.Invoke());
                Text gearLabel = UiRoot.CreateText("Label", gear, 16, new Color(0.9f, 0.92f, 0.96f), TextAnchor.MiddleCenter);
                UiRoot.Stretch(gearLabel.rectTransform, 1f, 1f, 1f, 1f);
                gearLabel.text = "⚙";
            }

            return self;
        }

        public void SetVisible(bool visible)
        {
            gameObject.SetActive(visible);
            if (visible)
            {
                Refresh();
            }
        }

        public void Refresh()
        {
            string last = null;
            for (int i = _history.Messages.Count - 1; i >= 0; i--)
            {
                DisplayMessage entry = _history.Messages[i];
                if (entry.role == "heroine" && !entry.error && !string.IsNullOrEmpty(entry.text))
                {
                    last = entry.text;
                    break;
                }
            }

            _message.text = string.IsNullOrEmpty(last) ? "……" : last;
        }

        public void SetHint(string text)
        {
            if (_hint != null)
            {
                _hint.text = text ?? string.Empty;
            }
        }

        /// <summary>返回常驻对话框的屏幕像素区域（原点左下），供壁纸模式点击判定。</summary>
        public Rect GetPixelRect()
        {
            var rect = (RectTransform)transform;
            var corners = new Vector3[4];
            rect.GetWorldCorners(corners);
            float minX = corners[0].x;
            float minY = corners[0].y;
            float maxX = corners[2].x;
            float maxY = corners[2].y;
            for (int i = 1; i < 4; i++)
            {
                minX = Mathf.Min(minX, corners[i].x);
                minY = Mathf.Min(minY, corners[i].y);
                maxX = Mathf.Max(maxX, corners[i].x);
                maxY = Mathf.Max(maxY, corners[i].y);
            }

            return new Rect(minX, minY, maxX - minX, maxY - minY);
        }
    }
}
