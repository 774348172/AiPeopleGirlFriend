using System.Text;
using AiPeople.Core;
using UnityEngine;
using UnityEngine.UI;

namespace AiPeople.UI
{
    /// <summary>
    /// HUD：游戏时间、位置、关系与心情（全部来自后端已提交状态），连接与生成状态。
    /// </summary>
    public sealed class HudPanel : MonoBehaviour
    {
        private Text _text;
        private Text _prompt;
        private Text _toast;
        private float _toastHideAt;
        private AiPeopleConfig.Data _config;

        public static HudPanel Create(Transform canvasRoot, AiPeopleConfig.Data config)
        {
            RectTransform panel = UiRoot.CreateRect("Hud", canvasRoot,
                new Vector2(0f, 1f), new Vector2(0f, 1f), new Vector2(16f, -186f), new Vector2(496f, -16f));
            var image = panel.gameObject.AddComponent<Image>();
            image.color = new Color(0.05f, 0.06f, 0.09f, 0.55f);

            HudPanel self = panel.gameObject.AddComponent<HudPanel>();
            self._config = config;
            Text text = UiRoot.CreateText("Text", panel, 17, new Color(0.90f, 0.92f, 0.96f), TextAnchor.UpperLeft);
            UiRoot.Stretch(text.rectTransform, 12f, 10f, 12f, 10f);
            self._text = text;

            self._prompt = UiRoot.CreateText("InteractPrompt", canvasRoot, 20, new Color(0.98f, 0.92f, 0.72f), TextAnchor.MiddleCenter);
            self._prompt.rectTransform.anchorMin = new Vector2(0.5f, 0f);
            self._prompt.rectTransform.anchorMax = new Vector2(0.5f, 0f);
            self._prompt.rectTransform.pivot = new Vector2(0.5f, 0f);
            self._prompt.rectTransform.anchoredPosition = new Vector2(0f, 286f);
            self._prompt.rectTransform.sizeDelta = new Vector2(760f, 30f);
            self._prompt.text = string.Empty;

            self._toast = UiRoot.CreateText("Toast", canvasRoot, 19, new Color(0.88f, 0.94f, 1f, 0f), TextAnchor.MiddleCenter);
            self._toast.rectTransform.anchorMin = new Vector2(0.5f, 0f);
            self._toast.rectTransform.anchorMax = new Vector2(0.5f, 0f);
            self._toast.rectTransform.pivot = new Vector2(0.5f, 0f);
            self._toast.rectTransform.anchoredPosition = new Vector2(0f, 318f);
            self._toast.rectTransform.sizeDelta = new Vector2(760f, 30f);
            self._toast.text = string.Empty;
            return self;
        }

        /// <summary>显示/隐藏 HUD（含交互提示与 toast）。</summary>
        public void SetVisible(bool visible)
        {
            gameObject.SetActive(visible);
            if (_prompt != null)
            {
                _prompt.gameObject.SetActive(visible);
            }

            if (_toast != null)
            {
                _toast.gameObject.SetActive(visible);
            }
        }

        /// <summary>当前可交互提示（无交互时传空串）。</summary>
        public void SetPrompt(string text)
        {
            if (_prompt != null)
            {
                _prompt.text = text ?? string.Empty;
            }
        }

        /// <summary>短暂提示（男主侧事实反馈），自动淡出。</summary>
        public void ShowToast(string text)
        {
            if (_toast == null)
            {
                return;
            }

            _toast.text = text ?? string.Empty;
            _toast.color = new Color(0.88f, 0.94f, 1f, 1f);
            _toastHideAt = Time.unscaledTime + 2.2f;
        }

        private void Update()
        {
            if (_toast == null)
            {
                return;
            }

            Color color = _toast.color;
            if (color.a <= 0.001f)
            {
                return;
            }

            float remaining = _toastHideAt - Time.unscaledTime;
            color.a = Mathf.Clamp01(remaining / 0.8f);
            _toast.color = color;
            if (remaining <= 0f)
            {
                _toast.text = string.Empty;
            }
        }

        public void SetState(GameStateModel state, string connection, string generation)
        {
            var builder = new StringBuilder(256);
            builder.Append("<b>猫咪女友 · M1</b>\n");

            StatusResponse latest = state != null ? state.Latest : null;
            if (latest != null)
            {
                builder.Append("时间：").Append(Or(latest.game_time)).Append('\n');
                if (latest.world != null)
                {
                    builder.Append("位置：").Append(Or(latest.world.location_label)).Append('\n');
                }

                if (latest.heroine != null)
                {
                    RelationshipView relationship = latest.heroine.relationship;
                    if (relationship != null)
                    {
                        builder.Append("关系：").Append(Or(relationship.stage))
                            .Append(" · 信任：").Append(Or(relationship.trust)).Append('\n');
                    }

                    LivingMindView mind = latest.heroine.living_mind;
                    if (mind != null)
                    {
                        builder.Append("她正在：").Append(Or(mind.current_activity))
                            .Append(" · 心情：").Append(Or(mind.emotion)).Append('\n');
                    }
                }
            }
            else
            {
                builder.Append("状态：等待后端…\n");
            }

            if (state != null && !string.IsNullOrEmpty(state.LastError))
            {
                builder.Append("<color=#F0938A>状态错误：").Append(state.LastError).Append("</color>\n");
            }

            builder.Append("连接：").Append(connection).Append("　生成：").Append(generation);
            if (_config != null)
            {
                builder.Append("\n存档：").Append(_config.saveId).Append(" · ").Append(_config.endpoint);
            }

            _text.text = builder.ToString();
        }

        private static string Or(string value)
        {
            return string.IsNullOrEmpty(value) ? "—" : value;
        }
    }
}
