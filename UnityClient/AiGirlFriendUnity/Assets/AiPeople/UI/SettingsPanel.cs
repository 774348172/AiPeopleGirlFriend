using System;
using AiPeople.Shell;
using UnityEngine;
using UnityEngine.UI;

namespace AiPeople.UI
{
    /// <summary>
    /// 桌面形态设置面板（会话态内按 F10 或点击常驻条上的齿轮打开）。
    /// 窗口模式切换、帧率、HUD、自主行为开关与退出程序。
    /// </summary>
    public sealed class SettingsPanel : MonoBehaviour
    {
        private Text _status;
        private Text _modeValue;
        private Text _fpsValue;
        private Text _hudValue;
        private Text _ambientValue;

        public static SettingsPanel Create(
            Transform canvasRoot,
            Action onClose,
            Action onQuit,
            Action<ShellWindowMode> onModeChanged,
            Action<int> onFpsChanged,
            Action<bool> onHudChanged,
            Action<bool> onAmbientChanged)
        {
            RectTransform panel = UiRoot.CreateRect("SettingsPanel", canvasRoot,
                new Vector2(0.5f, 0.5f), new Vector2(0.5f, 0.5f),
                new Vector2(-400f, -280f), new Vector2(400f, 280f));
            var background = panel.gameObject.AddComponent<Image>();
            background.color = new Color(0.06f, 0.07f, 0.10f, 0.96f);

            SettingsPanel self = panel.gameObject.AddComponent<SettingsPanel>();

            Text title = UiRoot.CreateText("Title", panel, 26, new Color(0.95f, 0.96f, 1f), TextAnchor.UpperLeft);
            UiRoot.Stretch(title.rectTransform, 24f, 0f, 24f, 20f);
            title.rectTransform.offsetMin = new Vector2(24f, 510f);
            title.text = "设置";

            self._status = UiRoot.CreateText("Status", panel, 15, new Color(0.68f, 0.74f, 0.84f), TextAnchor.UpperLeft);
            UiRoot.Stretch(self._status.rectTransform, 24f, 0f, 24f, 0f);
            self._status.rectTransform.offsetMin = new Vector2(24f, 420f);
            self._status.rectTransform.offsetMax = new Vector2(-24f, -60f);
            self._status.text = string.Empty;

            float y = 380f;
            self._modeValue = self.AddRow(panel, "窗口模式", y, out RectTransform modeButtons);
            CreateButton(modeButtons, "自动", 0f, () => onModeChanged?.Invoke(ShellWindowMode.Auto));
            CreateButton(modeButtons, "壁纸层", 110f, () => onModeChanged?.Invoke(ShellWindowMode.Wallpaper));
            CreateButton(modeButtons, "普通窗口", 220f, () => onModeChanged?.Invoke(ShellWindowMode.Normal));

            y -= 70f;
            self._fpsValue = self.AddRow(panel, "目标帧率", y, out RectTransform fpsButtons);
            CreateButton(fpsButtons, "30", 0f, () => onFpsChanged?.Invoke(30));
            CreateButton(fpsButtons, "60", 110f, () => onFpsChanged?.Invoke(60));

            y -= 70f;
            self._hudValue = self.AddRow(panel, "显示状态 HUD", y, out RectTransform hudButtons);
            CreateButton(hudButtons, "开", 0f, () => onHudChanged?.Invoke(true));
            CreateButton(hudButtons, "关", 110f, () => onHudChanged?.Invoke(false));

            y -= 70f;
            self._ambientValue = self.AddRow(panel, "自主环境行为", y, out RectTransform ambientButtons);
            CreateButton(ambientButtons, "开", 0f, () => onAmbientChanged?.Invoke(true));
            CreateButton(ambientButtons, "关", 110f, () => onAmbientChanged?.Invoke(false));

            RectTransform footer = UiRoot.CreateRect("Footer", panel,
                new Vector2(0f, 0f), new Vector2(1f, 0f), new Vector2(24f, 24f), new Vector2(-24f, 84f));
            CreateButton(footer, "关闭（Esc）", 0f, () => onClose?.Invoke());
            CreateButton(footer, "退出程序", 170f, () => onQuit?.Invoke());

            panel.gameObject.SetActive(false);
            return self;
        }

        public void SetVisible(bool visible)
        {
            gameObject.SetActive(visible);
        }

        public void Refresh(ShellSettingsData settings, string statusLine)
        {
            if (settings == null)
            {
                return;
            }

            _status.text = statusLine ?? string.Empty;
            _modeValue.text = ((ShellWindowMode)settings.windowMode).ToString();
            _fpsValue.text = settings.targetFps.ToString();
            _hudValue.text = settings.showHud ? "开" : "关";
            _ambientValue.text = settings.ambientBehaviors ? "开" : "关";
        }

        private Text AddRow(RectTransform panel, string label, float y, out RectTransform buttonArea)
        {
            Text text = UiRoot.CreateText(label + "Value", panel, 19, new Color(0.88f, 0.90f, 0.95f), TextAnchor.MiddleLeft);
            text.rectTransform.anchorMin = new Vector2(0f, 0f);
            text.rectTransform.anchorMax = new Vector2(0f, 0f);
            text.rectTransform.pivot = new Vector2(0f, 0f);
            text.rectTransform.anchoredPosition = new Vector2(24f, y);
            text.rectTransform.sizeDelta = new Vector2(520f, 40f);
            text.text = label + "：—";

            buttonArea = UiRoot.CreateRect("Buttons", panel,
                new Vector2(0f, 0f), new Vector2(0f, 0f), new Vector2(24f, y - 44f), new Vector2(504f, y - 8f));
            return text;
        }

        private static void CreateButton(RectTransform parent, string label, float x, Action onClick)
        {
            RectTransform button = UiRoot.CreateRect("Button_" + label, parent,
                new Vector2(0f, 0f), new Vector2(0f, 1f), new Vector2(x, 0f), new Vector2(x + 100f, 0f));
            var image = button.gameObject.AddComponent<Image>();
            image.color = new Color(0.26f, 0.32f, 0.42f, 0.95f);
            var component = button.gameObject.AddComponent<Button>();
            component.targetGraphic = image;
            component.onClick.AddListener(() => onClick?.Invoke());
            Text text = UiRoot.CreateText("Label", button, 17, Color.white, TextAnchor.MiddleCenter);
            UiRoot.Stretch(text.rectTransform, 2f, 2f, 2f, 2f);
            text.text = label;
        }
    }
}
