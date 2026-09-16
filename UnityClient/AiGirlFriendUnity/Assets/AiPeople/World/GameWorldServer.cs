using System;
using System.Collections.Generic;
using System.Collections.Concurrent;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using AiPeople.Core;
using UnityEngine;

namespace AiPeople.World
{
    /// <summary>
    /// Unity 侧游戏世界 HTTP 服务（接口契约：游戏是唯一事实源与执行者）。
    /// 基于 TcpListener 的最小 HTTP/1.1 实现——不依赖 http.sys/URL ACL，仅绑定 127.0.0.1。
    /// 端点：GET /game/health · GET /game/actions · GET /game/project ·
    ///       POST /game/execute_action · POST /game/player_fact_report
    /// </summary>
    public sealed class GameWorldServer : MonoBehaviour
    {
        public int port = 8770;

        public GameWorldState State { get; private set; }
        public bool IsRunning { get; private set; }

        private TcpListener _listener;
        private Thread _thread;
        private volatile bool _running;
        private readonly ConcurrentQueue<Action> _mainThreadCommands = new ConcurrentQueue<Action>();
        private readonly SemaphoreSlim _concurrency = new SemaphoreSlim(8, 8);
        private const int MaxBodyBytes = 256 * 1024;
        private const int CommandTimeoutMs = 5000;

        public void ProcessMainThreadCommands()
        {
            while (_mainThreadCommands.TryDequeue(out Action command)) command();
        }

        private void Update()
        {
            // HTTP workers only enqueue Unity-facing work. Pump it from Unity's
            // main loop so callers cannot forget to service the queue.
            ProcessMainThreadCommands();
        }

        public void StartServer(GameWorldState state)
        {
            State = state;
            _running = true;
            _thread = new Thread(ListenLoop)
            {
                IsBackground = true,
                Name = "AiPeopleGameWorld",
            };
            _thread.Start();
        }

        private void OnDestroy()
        {
            _running = false;
            IsRunning = false;
            try
            {
                _listener?.Stop();
            }
            catch (Exception)
            {
                // 关闭期异常忽略
            }
        }

        private void ListenLoop()
        {
            try
            {
                _listener = new TcpListener(IPAddress.Loopback, port);
                _listener.Start();
                IsRunning = true;
                Debug.Log("[AiPeople] 游戏世界服务已启动：http://127.0.0.1:" + port + "/game/");

                while (_running)
                {
                    TcpClient client = _listener.AcceptTcpClient();
                    ThreadPool.QueueUserWorkItem(_ => SafeHandle(client));
                }
            }
            catch (Exception exception)
            {
                IsRunning = false;
                if (_running)
                {
                    Debug.LogError("[AiPeople] 游戏世界服务异常：" + exception.Message);
                }
            }
        }

        private void SafeHandle(TcpClient client)
        {
            if (!_concurrency.Wait(0)) { client.Close(); return; }
            using (client)
            using (NetworkStream stream = client.GetStream())
            {
                try
                {
                    if (!TryReadRequest(stream, out string method, out string path, out string body))
                    {
                        return;
                    }

                    string response = Route(method, path, body, out int status);
                    WriteResponse(stream, status, response);
                }
                catch (Exception exception)
                {
                    Debug.LogWarning("[AiPeople] 请求处理失败：" + exception.Message);
                    try
                    {
                        WriteResponse(stream, 500, "{\"detail\":\"internal error\"}");
                    }
                    catch (Exception)
                    {
                        // 客户端断开，忽略
                    }
                }
                finally { _concurrency.Release(); }
            }
        }

        private static bool TryReadRequest(NetworkStream stream, out string method, out string path, out string body)
        {
            method = string.Empty;
            path = string.Empty;
            body = string.Empty;

            stream.ReadTimeout = 5000;
            var raw = new List<byte>(8192);
            var chunk = new byte[4096];
            int headerEnd = -1;
            while (headerEnd < 0)
            {
                int read = stream.Read(chunk, 0, chunk.Length);
                if (read <= 0)
                {
                    return false;
                }

                for (int i = 0; i < read; i++)
                {
                    raw.Add(chunk[i]);
                }

                headerEnd = FindHeaderEnd(raw);
                if (raw.Count > 65536)
                {
                    return false;
                }
            }

            string headerText = Encoding.ASCII.GetString(raw.ToArray(), 0, headerEnd);
            string[] lines = headerText.Split(new[] { "\r\n" }, StringSplitOptions.None);
            string[] requestParts = lines[0].Split(' ');
            if (requestParts.Length < 2)
            {
                return false;
            }

            method = requestParts[0];
            path = requestParts[1];
            int query = path.IndexOf('?');
            if (query >= 0)
            {
                path = path.Substring(0, query);
            }

            int contentLength = 0;
            for (int i = 1; i < lines.Length; i++)
            {
                int colon = lines[i].IndexOf(':');
                if (colon <= 0)
                {
                    continue;
                }

                string name = lines[i].Substring(0, colon).Trim();
                if (string.Equals(name, "Content-Length", StringComparison.OrdinalIgnoreCase))
                {
                    int.TryParse(lines[i].Substring(colon + 1).Trim(), out contentLength);
                }
            }
            if (contentLength < 0 || contentLength > MaxBodyBytes) return false;

            var bodyBytes = new List<byte>(Math.Max(0, contentLength));
            int bodyStart = headerEnd + 4;
            for (int i = bodyStart; i < raw.Count && bodyBytes.Count < contentLength; i++)
            {
                bodyBytes.Add(raw[i]);
            }

            while (bodyBytes.Count < contentLength)
            {
                int read = stream.Read(chunk, 0, Math.Min(chunk.Length, contentLength - bodyBytes.Count));
                if (read <= 0)
                {
                    break;
                }

                for (int i = 0; i < read; i++)
                {
                    bodyBytes.Add(chunk[i]);
                }
            }

            body = Encoding.UTF8.GetString(bodyBytes.ToArray());
            return true;
        }

        private static int FindHeaderEnd(List<byte> raw)
        {
            for (int i = 0; i + 3 < raw.Count; i++)
            {
                if (raw[i] == 13 && raw[i + 1] == 10 && raw[i + 2] == 13 && raw[i + 3] == 10)
                {
                    return i;
                }
            }

            return -1;
        }

        private string Route(string method, string path, string body, out int status)
        {
            status = 200;
            if (method == "GET" && path == "/game/health")
            {
                return "{\"status\":\"ready\",\"world_id\":\"songjiangfu\"}";
            }

            if (method == "GET" && path == "/game/actions")
            {
                return ActionCatalog.ToJson();
            }

            if (method == "GET" && path == "/game/project")
            {
                return InvokeOnMainThread(() => State.ProjectJson());
            }

            if (method == "POST" && path == "/game/execute_action")
            {
                if (!JsonLine.TryGetString(body, "action_id", out string actionId) || string.IsNullOrEmpty(actionId))
                {
                    status = 400;
                    return "{\"detail\":\"缺少 action_id\"}";
                }

                Dictionary<string, string> parameters = JsonUtil.ParseFlatObject(ExtractObjectBody(body, "params"));
                bool accepted = false;
                string reason = string.Empty;
                InvokeOnMainThread(() => accepted = State.Execute(actionId, parameters, out reason));
                return accepted
                    ? "{\"accepted\":true,\"reason\":\"\"}"
                    : "{\"accepted\":false,\"reason\":" + JsonUtil.Str(reason) + "}";
            }

            if (method == "POST" && path == "/game/player_fact_report")
            {
                Dictionary<string, string> report = JsonUtil.ParseFlatObject(body);
                bool accepted = false;
                string error = string.Empty;
                InvokeOnMainThread(() => accepted = State.SubmitPlayerFactReport(report, out error));
                if (!accepted)
                {
                    status = 400;
                    return "{\"detail\":" + JsonUtil.Str(error) + "}";
                }

                return "{\"status\":\"pending_confirmation\"}";
            }

            status = 404;
            return "{\"detail\":\"not found\"}";
        }

        private string InvokeOnMainThread(Func<string> callback)
        {
            string result = string.Empty;
            using (var done = new ManualResetEventSlim(false))
            {
                _mainThreadCommands.Enqueue(() => { try { result = callback(); } finally { done.Set(); } });
                done.Wait(CommandTimeoutMs);
            }
            return result;
        }

        private void InvokeOnMainThread(Action callback)
        {
            using (var done = new ManualResetEventSlim(false))
            {
                _mainThreadCommands.Enqueue(() => { try { callback(); } finally { done.Set(); } });
                done.Wait(CommandTimeoutMs);
            }
        }

        /// <summary>取出顶层 "key": { ... } 的对象体（按大括号配平）。</summary>
        private static string ExtractObjectBody(string json, string key)
        {
            if (string.IsNullOrEmpty(json))
            {
                return string.Empty;
            }

            int keyIndex = json.IndexOf("\"" + key + "\"", StringComparison.Ordinal);
            if (keyIndex < 0)
            {
                return string.Empty;
            }

            int open = json.IndexOf('{', keyIndex);
            if (open < 0)
            {
                return string.Empty;
            }

            int depth = 0;
            for (int i = open; i < json.Length; i++)
            {
                if (json[i] == '{')
                {
                    depth++;
                }
                else if (json[i] == '}')
                {
                    depth--;
                    if (depth == 0)
                    {
                        return json.Substring(open + 1, i - open - 1);
                    }
                }
            }

            return string.Empty;
        }

        private static void WriteResponse(NetworkStream stream, int status, string body)
        {
            byte[] payload = Encoding.UTF8.GetBytes(body);
            string head = "HTTP/1.1 " + status + " " + Reason(status) + "\r\n"
                + "Content-Type: application/json; charset=utf-8\r\n"
                + "Content-Length: " + payload.Length + "\r\n"
                + "Connection: close\r\n\r\n";
            byte[] headBytes = Encoding.ASCII.GetBytes(head);
            stream.Write(headBytes, 0, headBytes.Length);
            stream.Write(payload, 0, payload.Length);
            stream.Flush();
        }

        private static string Reason(int status)
        {
            switch (status)
            {
                case 200: return "OK";
                case 400: return "Bad Request";
                case 404: return "Not Found";
                default: return "Internal Server Error";
            }
        }
    }
}
