using System;
using System.Collections;
using System.Text;
using UnityEngine;

namespace AiPeople.Core
{
    /// <summary>对话会话的最小对外接口（主进程持有；小窗进程通过主进程的 /ui/* 间接使用）。</summary>
    public interface IChatSession
    {
        bool IsGenerating { get; }

        /// <summary>最近一句已提交的她的对白（用于常驻条）。</summary>
        string LastHeroineLine { get; }

        bool Send(string text);

        /// <summary>状态快照 JSON：{generating, partial, last_heroine, messages:[{role,text,meta,error}]}。</summary>
        string StateJson();
    }

    /// <summary>
    /// 无界面版对话会话（主进程）：对白提交、流式累计、提交定稿与错误标记。
    /// 供桌面形态的常驻条、气泡与对话小窗（经 /ui/*）共用；Editor 内仍由 ChatPanel 直接使用本地历史。
    /// 规则与需求一致：只有后端 done 才定稿为她说的话。
    /// </summary>
    public sealed class ChatSession : MonoBehaviour, IChatSession
    {
        private const int SnapshotMessages = 60;

        private AiPeopleClient _client;
        private LocalHistory _history;
        private Func<WorldInputData> _worldProvider;
        private string _saveId = string.Empty;
        private readonly StringBuilder _partial = new StringBuilder(256);
        private bool _generating;

        public event Action Changed;

        public bool IsGenerating => _generating;
        public string LastHeroineLine { get; private set; } = string.Empty;
        public string LastError { get; private set; } = string.Empty;

        public void Initialize(
            AiPeopleClient client,
            LocalHistory history,
            string saveId,
            Func<WorldInputData> worldProvider)
        {
            _client = client;
            _history = history;
            _saveId = saveId;
            _worldProvider = worldProvider;
            RefreshLastLine();
        }

        public bool Send(string text)
        {
            text = (text ?? string.Empty).Trim();
            if (_generating || text.Length == 0 || _client == null)
            {
                return false;
            }

            _history.Add("user", text, "你");
            _history.Add("heroine", "…", "白未晞");
            _generating = true;
            _partial.Length = 0;
            LastError = string.Empty;
            Changed?.Invoke();

            StartCoroutine(RunTurn(text));
            return true;
        }

        private IEnumerator RunTurn(string text)
        {
            var request = new ChatRequest
            {
                save_id = _saveId,
                text = text,
                world = _worldProvider != null ? _worldProvider() : new WorldInputData(),
            };

            yield return _client.ChatStream(request, new AiPeopleClient.ChatCallbacks
            {
                onDelta = delta => _partial.Append(delta),
                onCommitted = committed =>
                {
                    string finalText = string.IsNullOrEmpty(committed) ? _partial.ToString() : committed;
                    _history.UpdateLast(finalText);
                    LastHeroineLine = finalText;
                },
                onError = error =>
                {
                    string partial = _partial.Length > 0 ? _partial + "\n" : string.Empty;
                    _history.UpdateLast(partial + "（生成失败：" + error + "）", true);
                    LastError = error;
                },
                onFinished = () =>
                {
                    _generating = false;
                    _partial.Length = 0;
                    RefreshLastLine();
                    Changed?.Invoke();
                },
            });
        }

        private void RefreshLastLine()
        {
            for (int i = _history.Messages.Count - 1; i >= 0; i--)
            {
                DisplayMessage message = _history.Messages[i];
                if (message.role == "heroine" && !message.error && !string.IsNullOrEmpty(message.text))
                {
                    LastHeroineLine = message.text;
                    return;
                }
            }
        }

        public string StateJson()
        {
            var builder = new StringBuilder(1024);
            builder.Append("{\"generating\":").Append(_generating ? "true" : "false");
            builder.Append(",\"partial\":").Append(JsonUtil.Str(_generating ? _partial.ToString() : string.Empty));
            builder.Append(",\"last_heroine\":").Append(JsonUtil.Str(LastHeroineLine));
            builder.Append(",\"messages\":[");

            int total = _history.Messages.Count;
            int start = Mathf.Max(0, total - SnapshotMessages);
            for (int i = start; i < total; i++)
            {
                DisplayMessage message = _history.Messages[i];
                if (i > start)
                {
                    builder.Append(',');
                }

                builder.Append('{');
                builder.Append("\"role\":").Append(JsonUtil.Str(message.role)).Append(',');
                builder.Append("\"text\":").Append(JsonUtil.Str(message.text)).Append(',');
                builder.Append("\"meta\":").Append(JsonUtil.Str(message.meta)).Append(',');
                builder.Append("\"error\":").Append(message.error ? "true" : "false");
                builder.Append('}');
            }

            builder.Append("]}");
            return builder.ToString();
        }
    }
}
