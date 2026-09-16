using System;
using System.Collections;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;

namespace AiPeople.Core
{
    /// <summary>
    /// 8767 生产服务客户端：健康检查 / 状态拉取 / NDJSON 流式对话。
    /// 所有回调都在主线程（UnityWebRequest 协程驱动）。
    /// </summary>
    public sealed class AiPeopleClient
    {
        private readonly AiPeopleConfig.Data _config;

        public AiPeopleClient(AiPeopleConfig.Data config)
        {
            _config = config;
        }

        public string Endpoint => _config.endpoint;

        public IEnumerator Health(Action<bool, string> completed)
        {
            using UnityWebRequest request = UnityWebRequest.Get(Endpoint + "/api/health");
            request.timeout = 10;
            yield return request.SendWebRequest();

            if (request.result != UnityWebRequest.Result.Success)
            {
                completed?.Invoke(false, request.error ?? ("HTTP " + request.responseCode));
                yield break;
            }

            JsonLine.TryGetString(request.downloadHandler.text, "protocol", out string protocol);
            completed?.Invoke(true, string.IsNullOrEmpty(protocol) ? "ready" : protocol);
        }

        public IEnumerator FetchStatus(
            string saveId,
            Action<StatusResponse> onSuccess,
            Action<string> onFailure)
        {
            string body = JsonUtility.ToJson(new StatusRequest { save_id = saveId });
            using UnityWebRequest request = PostJson("/api/status", body, 30);
            yield return request.SendWebRequest();

            if (request.result != UnityWebRequest.Result.Success)
            {
                onFailure?.Invoke(request.error ?? ("HTTP " + request.responseCode));
                yield break;
            }

            StatusResponse response = null;
            try
            {
                response = JsonUtility.FromJson<StatusResponse>(request.downloadHandler.text);
            }
            catch (Exception exception)
            {
                onFailure?.Invoke("状态解析失败：" + exception.Message);
                yield break;
            }

            if (response == null || string.IsNullOrEmpty(response.save_id))
            {
                onFailure?.Invoke("状态解析失败：响应为空");
                yield break;
            }

            onSuccess?.Invoke(response);
        }

        public sealed class ChatCallbacks
        {
            public Action<string> onDelta;
            public Action<string> onCommitted;
            public Action<string> onError;
            public Action onFinished;
        }

        public IEnumerator ChatStream(ChatRequest payload, ChatCallbacks callbacks)
        {
            string body = JsonUtility.ToJson(payload);
            bool done = false;
            bool sawError = false;
            string committedReply = null;
            string errorDetail = null;

            var handler = new NdjsonStreamHandler(line =>
            {
                if (!JsonLine.TryGetString(line, "type", out string type))
                {
                    return;
                }

                switch (type)
                {
                    case "delta":
                        if (JsonLine.TryGetString(line, "text", out string delta))
                        {
                            callbacks.onDelta?.Invoke(delta);
                        }
                        break;
                    case "done":
                        done = true;
                        // done.value 的结构由后端决定；能取到已提交正文就用它定稿，否则沿用流式累计。
                        JsonLine.TryGetString(line, "reply", out committedReply);
                        break;
                    case "error":
                        sawError = true;
                        if (!JsonLine.TryGetString(line, "detail", out errorDetail))
                        {
                            errorDetail = "服务返回错误";
                        }
                        break;
                }
            });

            using (UnityWebRequest request = new UnityWebRequest(Endpoint + "/api/chat/stream", "POST"))
            {
                request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
                request.uploadHandler.contentType = "application/json";
                request.downloadHandler = handler;
                request.SetRequestHeader("Accept", "application/x-ndjson");
                request.timeout = Mathf.Max(5, Mathf.CeilToInt(_config.requestTimeoutSeconds));

                yield return request.SendWebRequest();
                handler.FlushPending();

                if (sawError)
                {
                    callbacks.onError?.Invoke(errorDetail ?? "服务返回错误");
                }
                else if (!done)
                {
                    callbacks.onError?.Invoke(request.result == UnityWebRequest.Result.Success
                        ? "流式响应未完成（连接中断）"
                        : "连接失败：" + (request.error ?? ("HTTP " + request.responseCode)));
                }
                else
                {
                    callbacks.onCommitted?.Invoke(committedReply);
                }

                callbacks.onFinished?.Invoke();
            }
        }

        private UnityWebRequest PostJson(string path, string body, int timeoutSeconds)
        {
            var request = new UnityWebRequest(Endpoint + path, "POST");
            request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
            request.uploadHandler.contentType = "application/json";
            request.downloadHandler = new DownloadHandlerBuffer();
            request.timeout = timeoutSeconds;
            return request;
        }

        /// <summary>字节流 → UTF-8 增量解码 → NDJSON 行回调（主线程）。</summary>
        private sealed class NdjsonStreamHandler : DownloadHandlerScript
        {
            private readonly StreamLineParser _parser = new StreamLineParser();
            private readonly Action<string> _onLine;
            private readonly Decoder _decoder = Encoding.UTF8.GetDecoder();
            private readonly char[] _charBuffer = new char[16384];

            public NdjsonStreamHandler(Action<string> onLine) : base(new byte[16384])
            {
                _onLine = onLine;
            }

            public void FlushPending()
            {
                _parser.FlushPending(_onLine);
            }

            protected override bool ReceiveData(byte[] data, int dataLength)
            {
                if (data == null || dataLength <= 0)
                {
                    return true;
                }

                int offset = 0;
                while (offset < dataLength)
                {
                    _decoder.Convert(
                        data, offset, dataLength - offset,
                        _charBuffer, 0, _charBuffer.Length,
                        false, out int bytesUsed, out int charsUsed, out bool _);

                    offset += bytesUsed;
                    if (charsUsed > 0)
                    {
                        _parser.Push(new string(_charBuffer, 0, charsUsed), _onLine);
                    }

                    if (bytesUsed == 0 && charsUsed == 0)
                    {
                        break;
                    }
                }

                return true;
            }

            protected override void CompleteContent()
            {
                _parser.FlushPending(_onLine);
            }
        }
    }
}
