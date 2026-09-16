using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

namespace AiPeople.Core
{
    [Serializable]
    public sealed class DisplayMessage
    {
        public string role;   // "user" / "heroine"
        public string text;
        public string meta;
        public bool error;
        public long utcTicks;
    }

    [Serializable]
    public sealed class DisplayHistoryData
    {
        public List<DisplayMessage> messages = new List<DisplayMessage>();
    }

    /// <summary>
    /// 对话显示历史：仅本地显示缓存（事实以后端提交事务为准）。
    /// 与 Web 参考前端同语义：按 saveId 分文件保存于 persistentDataPath。
    /// </summary>
    public sealed class LocalHistory
    {
        private const int MaxMessages = 200;

        private readonly object _lock = new object();
        private readonly string _path;
        private readonly DisplayHistoryData _data = new DisplayHistoryData();

        public LocalHistory(string saveId)
        {
            string safe = string.IsNullOrEmpty(saveId) ? "default" : saveId;
            _path = Path.Combine(Application.persistentDataPath, "aipeople_history_" + safe + ".json");
            Load();
        }

        /// <summary>显示历史副本（跨线程读取安全：主线程写入、小窗接口线程读取）。</summary>
        public IReadOnlyList<DisplayMessage> Messages
        {
            get
            {
                lock (_lock)
                {
                    return new List<DisplayMessage>(_data.messages);
                }
            }
        }

        public DisplayMessage Add(string role, string text, string meta = null, bool error = false)
        {
            var message = new DisplayMessage
            {
                role = role,
                text = text ?? string.Empty,
                meta = meta,
                error = error,
                utcTicks = DateTime.UtcNow.Ticks,
            };

            lock (_lock)
            {
                _data.messages.Add(message);
                Trim();
                Save();
            }

            return message;
        }

        public void UpdateLast(string text, bool error = false)
        {
            if (_data.messages.Count == 0)
            {
                return;
            }

            lock (_lock)
            {
                DisplayMessage last = _data.messages[_data.messages.Count - 1];
                last.text = text ?? string.Empty;
                last.error = error;
                Save();
            }
        }

        public void Clear()
        {
            lock (_lock)
            {
                _data.messages.Clear();
                Save();
            }
        }

        private void Load()
        {
            try
            {
                if (!File.Exists(_path))
                {
                    return;
                }

                DisplayHistoryData loaded = JsonUtility.FromJson<DisplayHistoryData>(File.ReadAllText(_path));
                if (loaded?.messages != null)
                {
                    _data.messages.Clear();
                    _data.messages.AddRange(loaded.messages);
                    Trim();
                }
            }
            catch (Exception exception)
            {
                Debug.LogWarning("[AiPeople] 本地对话历史读取失败，按空历史继续：" + exception.Message);
            }
        }

        private void Save()
        {
            try
            {
                File.WriteAllText(_path, JsonUtility.ToJson(_data));
            }
            catch (Exception exception)
            {
                Debug.LogWarning("[AiPeople] 本地对话历史写入失败：" + exception.Message);
            }
        }

        private void Trim()
        {
            int overflow = _data.messages.Count - MaxMessages;
            if (overflow > 0)
            {
                _data.messages.RemoveRange(0, overflow);
            }
        }
    }
}
