using AiPeople.Core;
using UnityEngine;

namespace AiPeople.World
{
    /// <summary>
    /// 场景氛围：按后端 game_time 的小时驱动昼夜光照，按 scene 文本关键字驱动天气表现。
    /// 只读已提交状态；解析失败时保留上次有效值（默认按正典锚点：傍晚 18:00 / 阴）。
    /// </summary>
    public sealed class SceneAtmosphere : MonoBehaviour
    {
        private static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");
        private static readonly int ColorId = Shader.PropertyToID("_Color");

        private Light _sun;
        private Light _lamp;
        private Renderer _windowRenderer;
        private ParticleSystem _rain;

        private float _hour = 18f;
        private string _weather = "阴";
        private bool _lightOverridden;
        private bool _parseWarned;

        public bool LightOn { get; private set; } = true;
        public bool WindowOpen { get; private set; }

        public void Bind(Light sun, Light lamp, Renderer windowRenderer, ParticleSystem rain)
        {
            _sun = sun;
            _lamp = lamp;
            _windowRenderer = windowRenderer;
            _rain = rain;
            ApplyImmediate();
        }

        public void ApplyStatus(StatusResponse status)
        {
            if (status != null && TryParseHour(status.game_time, out float hour))
            {
                _hour = hour;
            }
            else if (status != null && !_parseWarned)
            {
                _parseWarned = true;
                Debug.LogWarning("[AiPeople] game_time 无法解析，氛围保持默认/上次值：" + status.game_time);
            }

            _weather = DetectWeather(status?.world?.scene);
            ApplyImmediate();
        }

        public bool ToggleLight()
        {
            _lightOverridden = true;
            LightOn = !LightOn;
            ApplyImmediate();
            return LightOn;
        }

        public bool ToggleWindow()
        {
            WindowOpen = !WindowOpen;
            ApplyWindow();
            return WindowOpen;
        }

        /// <summary>解析后端格式 "第N日 HH:MM:SS"（容忍缺秒）。</summary>
        public static bool TryParseHour(string gameTime, out float hour)
        {
            hour = 0f;
            if (string.IsNullOrEmpty(gameTime))
            {
                return false;
            }

            int space = gameTime.LastIndexOf(' ');
            string token = space >= 0 ? gameTime.Substring(space + 1) : gameTime;
            string[] parts = token.Split(':');
            if (parts.Length < 2 || !int.TryParse(parts[0], out int hours) || hours < 0 || hours > 23)
            {
                return false;
            }

            int minutes = 0;
            int.TryParse(parts[1], out minutes);
            hour = hours + Mathf.Clamp(minutes, 0, 59) / 60f;
            return true;
        }

        private static string DetectWeather(string scene)
        {
            if (string.IsNullOrEmpty(scene))
            {
                return "阴";
            }

            if (scene.Contains("雪"))
            {
                return "雪";
            }

            if (scene.Contains("雨"))
            {
                return "雨";
            }

            if (scene.Contains("晴"))
            {
                return "晴";
            }

            return "阴";
        }

        private void ApplyImmediate()
        {
            ApplySun();
            ApplyLamp();
            ApplyWindow();
            ApplyRain();
            ApplyEnvironment();
        }

        /// <summary>
        /// 将同一份已提交的时间/天气状态投影到室内环境。只改表现层的
        /// RenderSettings，不写回 WorldState，也不创建新的世界事实。
        /// </summary>
        private void ApplyEnvironment()
        {
            bool night = _hour >= 20f || _hour < 5f;
            bool dawn = _hour >= 5f && _hour < 7f;
            bool dusk = _hour >= 17f && _hour < 20f;

            Color dayAmbient = new Color(0.62f, 0.68f, 0.76f);
            Color nightAmbient = new Color(0.09f, 0.11f, 0.18f);
            Color dawnAmbient = new Color(0.42f, 0.34f, 0.38f);
            Color ambient = night ? nightAmbient : (dawn || dusk ? dawnAmbient : dayAmbient);
            if (_weather == "雨") ambient = Color.Lerp(ambient, new Color(0.34f, 0.39f, 0.48f), 0.28f);
            if (_weather == "雪") ambient = Color.Lerp(ambient, new Color(0.72f, 0.78f, 0.86f), 0.22f);

            RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat;
            RenderSettings.ambientLight = ambient;
            RenderSettings.fog = night || _weather == "雨" || _weather == "雪";
            RenderSettings.fogColor = Color.Lerp(ambient, new Color(0.5f, 0.55f, 0.62f), 0.25f);
            RenderSettings.fogDensity = night ? 0.006f : (_weather == "雨" ? 0.0035f : 0.0015f);
        }

        private void ApplySun()
        {
            if (_sun == null)
            {
                return;
            }

            bool day = _hour >= 6f && _hour <= 18f;
            float dayT = Mathf.Clamp01((_hour - 6f) / 12f);
            float dayFactor = day ? Mathf.Sin(dayT * Mathf.PI) : 0f;
            float weatherDim = _weather == "晴" ? 1f : (_weather == "阴" ? 0.75f : 0.55f);

            _sun.intensity = 0.05f + 0.6f * dayFactor * weatherDim;
            if (!day)
            {
                _sun.color = new Color(0.55f, 0.65f, 0.92f);
                _sun.transform.rotation = Quaternion.Euler(12f, -35f, 0f);
            }
            else if (_hour < 7f || _hour > 17f)
            {
                _sun.color = new Color(1f, 0.72f, 0.52f);
                _sun.transform.rotation = Quaternion.Euler(Mathf.Lerp(8f, 160f, dayT), -35f, 0f);
            }
            else
            {
                _sun.color = Color.Lerp(new Color(1f, 0.85f, 0.70f), new Color(1f, 0.97f, 0.92f), dayFactor);
                _sun.transform.rotation = Quaternion.Euler(Mathf.Lerp(8f, 160f, dayT), -35f, 0f);
            }
        }

        private void ApplyLamp()
        {
            if (_lamp == null)
            {
                return;
            }

            LightOn = _lightOverridden ? LightOn : (_hour < 7f || _hour >= 17f);
            _lamp.intensity = LightOn ? 2.2f : 0.02f;
        }

        private void ApplyWindow()
        {
            if (_windowRenderer == null)
            {
                return;
            }

            Color day = new Color(0.62f, 0.72f, 0.82f);
            Color evening = new Color(0.86f, 0.58f, 0.42f);
            Color night = new Color(0.07f, 0.09f, 0.16f);

            Color color;
            if (_hour >= 7f && _hour < 17f)
            {
                color = Color.Lerp(evening, day, Mathf.Clamp01((_hour - 7f) / 3f));
            }
            else if (_hour >= 17f && _hour < 20f)
            {
                color = Color.Lerp(day, evening, (_hour - 17f) / 3f);
            }
            else if (_hour >= 20f || _hour < 5f)
            {
                color = night;
            }
            else
            {
                color = Color.Lerp(night, day, (_hour - 5f) / 2f);
            }

            if (_weather == "雨" || _weather == "雪")
            {
                color = Color.Lerp(color, new Color(0.40f, 0.46f, 0.55f), 0.35f);
            }

            Material material = _windowRenderer.sharedMaterial;
            if (material == null)
            {
                return;
            }

            if (material.HasProperty(BaseColorId))
            {
                material.SetColor(BaseColorId, color);
            }
            else if (material.HasProperty(ColorId))
            {
                material.SetColor(ColorId, color);
            }
        }

        private void ApplyRain()
        {
            if (_rain == null)
            {
                return;
            }

            bool active = _weather == "雨" || _weather == "雪";
            ParticleSystem.MainModule main = _rain.main;
            main.startSpeed = _weather == "雪" ? 1.1f : 6.5f;
            main.startColor = _weather == "雪"
                ? new Color(1f, 1f, 1f, 0.9f)
                : new Color(0.75f, 0.82f, 0.95f, 0.6f);

            ParticleSystem.EmissionModule emission = _rain.emission;
            emission.rateOverTime = active ? (_weather == "雪" ? 50f : 140f) : 0f;

            if (!active)
            {
                _rain.Clear();
            }
            else if (!_rain.isPlaying)
            {
                _rain.Play();
            }
        }
    }
}
