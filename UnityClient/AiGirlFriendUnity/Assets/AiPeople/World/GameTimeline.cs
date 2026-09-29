using System;
using AiPeople.Core;
using UnityEngine;

namespace AiPeople.World
{
    // One game clock for actions, movement and animation. Real HTTP timeouts and
    // snapshot freshness remain on wall/unscaled time, including while paused.
    [DefaultExecutionOrder(-200)]
    public sealed class GameTimeline : MonoBehaviour
    {
        public bool Ready { get; private set; }
        public double Seconds { get; private set; }
        public float Speed { get; private set; } = 1;
        public bool Paused { get; private set; }
        public float Delta { get; private set; }
        public bool ApplyUnityScale = true;
        private string _key;
        private float _checkpoint;

        public void Configure(string saveId) { _key = "AiPeople.GameClock.v1." + saveId; }

        public void Initialize(GameClockData server)
        {
            if (Ready || !Valid(server)) return;
            GameClockData selected = server;
            if (!string.IsNullOrEmpty(_key) && PlayerPrefs.HasKey(_key))
            {
                try
                {
                    var local = JsonUtility.FromJson<GameClockData>(PlayerPrefs.GetString(_key));
                    if (Valid(local) && local.elapsed_seconds >= server.elapsed_seconds) selected = local;
                }
                catch (Exception) { Debug.LogWarning("[AiPeople] 本地时间记录无效，使用服务端存档时间"); }
            }
            Seconds = selected.elapsed_seconds;
            Speed = selected.time_scale;
            Paused = selected.paused;
            Ready = true;
            SetUnityScale();
            Save();
        }

        private static bool Valid(GameClockData value) => value != null && value.ready
            && !double.IsNaN(value.elapsed_seconds) && !double.IsInfinity(value.elapsed_seconds)
            && value.elapsed_seconds >= 0 && value.elapsed_seconds <= 3000000000d
            && value.time_scale >= .1f && value.time_scale <= 8;

        public bool Control(bool paused, float speed)
        {
            if (!Ready || float.IsNaN(speed) || speed < .1f || speed > 8) return false;
            Paused = paused; Speed = speed; Delta = 0; SetUnityScale(); Save(); return true;
        }

        public void Advance(float realSeconds)
        {
            Delta = Ready && !Paused && realSeconds > 0 && !float.IsInfinity(realSeconds)
                ? realSeconds * Speed : 0;
            Seconds += Delta;
        }
        private void Update()
        {
            Advance(Time.unscaledDeltaTime);
            if (!Ready) return;
            _checkpoint += Time.unscaledDeltaTime;
            if (_checkpoint >= 5) { _checkpoint = 0; Save(); }
        }
        public GameClockData Capture() => new GameClockData {
            ready = Ready, elapsed_seconds = Seconds, time_scale = Speed, paused = Paused };
        private void SetUnityScale() { if (ApplyUnityScale) Time.timeScale = Ready && !Paused ? Speed : 0; }
        private void Save()
        {
            if (!Ready || string.IsNullOrEmpty(_key)) return;
            PlayerPrefs.SetString(_key, JsonUtility.ToJson(Capture()));
            PlayerPrefs.Save();
        }
        private void OnApplicationQuit() { Save(); }
        private void OnDestroy() { Save(); if (ApplyUnityScale) Time.timeScale = 1; }
    }
}
