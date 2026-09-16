using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using AiPeople.Core;
using UnityEngine;

namespace AiPeople.Shell
{
    /// <summary>
    /// 主进程的本地 UI 接口（供对话小窗进程调用）：
    ///   GET  /ui/health → 就绪
    ///   GET  /ui/state  → {"show":bool, ...对话状态（历史/生成中/最近一句）}
    ///   POST /ui/send   → {"text": "..."} 转发给主进程对话会话
    ///   POST /ui/hide   → 小窗请求收起（主进程把 show 置 false）
    /// 仅绑定 127.0.0.1；TcpListener 最小 HTTP（无 http.sys 依赖）。
    /// </summary>
    public sealed class ChatUiServer : MonoBehaviour
    {
        public int port = 8771;

        private TcpListener _listener;
        private Thread _thread;
        private volatile bool _running;
        private readonly SemaphoreSlim _concurrency = new SemaphoreSlim(8, 8);
        private IChatSession _session;
        private Func<bool> _showProvider;
        private Action _hideRequested;
        private Action _quitRequested;
        private Action _activateRequested;
        private Action<string> _modeRequested;
        private Func<string, bool, string> _doorRequested;
        private volatile string _roomJson = "{\"doors\":[]}";

        public void ConfigureRoomControls(Func<string, bool, string> request) { _doorRequested = request; }
        // Publish immutable JSON on Unity's thread; HTTP workers never inspect scene objects.
        public void PublishRoomState(string json) { _roomJson = json; }

        public bool IsRunning { get; private set; }
        public string LastRequestSummary { get; private set; } = string.Empty;

        private readonly ConcurrentQueue<Action> _mainThreadCommands = new ConcurrentQueue<Action>();

        /// <summary>
        /// 主线程取出待执行命令。HTTP 工作线程**不得**直接调用 Unity API
        /// （Application.Quit / Screen.* / 窗口操作等），一律投递到这里由主线程执行。
        /// </summary>
        public bool TryDequeueMainThreadCommand(out Action command)
        {
            return _mainThreadCommands.TryDequeue(out command);
        }

        public void StartServer(
            IChatSession session,
            Func<bool> showProvider,
            Action hideRequested,
            Action quitRequested,
            Action activateRequested = null,
            Action<string> modeRequested = null)
        {
            _session = session;
            _showProvider = showProvider;
            _hideRequested = hideRequested;
            _quitRequested = quitRequested;
            _activateRequested = activateRequested;
            _modeRequested = modeRequested;
            _running = true;
            _thread = new Thread(ListenLoop) { IsBackground = true, Name = "AiPeopleChatUi" };
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
                Debug.Log("[AiPeople] 对话小窗接口已启动：http://127.0.0.1:" + port + "/ui/");

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
                    Debug.LogError("[AiPeople] 对话小窗接口异常：" + exception.Message);
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
                    if (!MiniHttp.TryReadRequest(stream, out string method, out string path, out string body))
                    {
                        return;
                    }

                    LastRequestSummary = method + " " + path;
                    string response = Route(method, path, body, out int status);
                    MiniHttp.WriteResponse(stream, status, response);
                }
                catch (Exception exception)
                {
                    Debug.LogWarning("[AiPeople] 小窗接口请求失败：" + exception.Message);
                    try
                    {
                        MiniHttp.WriteResponse(stream, 500, "{\"detail\":\"internal error\"}");
                    }
                    catch (Exception)
                    {
                        // 客户端断开，忽略
                    }
                }
                finally { _concurrency.Release(); }
            }
        }

        private string Route(string method, string path, string body, out int status)
        {
            status = 200;

            if (method == "GET" && path == "/ui/room") return _roomJson;
            if (method == "POST" && path == "/ui/door")
            {
                if (!JsonLine.TryGetString(body, "id", out string id) || string.IsNullOrWhiteSpace(id)
                    || !JsonLine.TryGetString(body, "state", out string desired)
                    || (desired != "open" && desired != "closed"))
                {
                    status = 400;
                    return "{\"accepted\":false,\"reason\":\"需要门窗 id 和 open/closed 状态\"}";
                }
                if (_doorRequested == null || !_running)
                {
                    status = 503;
                    return "{\"accepted\":false,\"reason\":\"房间尚未就绪\"}";
                }
                var completed = new TaskCompletionSource<string>();
                int dispatched = 0;
                _mainThreadCommands.Enqueue(() =>
                {
                    if (Interlocked.CompareExchange(ref dispatched, 1, 0) != 0) return;
                    try { completed.TrySetResult(_running ? _doorRequested(id, desired == "open")
                        : "{\"accepted\":false,\"reason\":\"程序正在关闭\"}"); }
                    catch (Exception error) { completed.TrySetException(error); }
                });
                if (!completed.Task.Wait(2000))
                {
                    Interlocked.CompareExchange(ref dispatched, 2, 0); // Do not execute an expired queued click.
                    status = 503;
                    return "{\"accepted\":false,\"reason\":\"房间暂未响应，请重试\"}";
                }
                return completed.Task.Result;
            }

            if (method == "GET" && path == "/ui/health")
            {
                return "{\"status\":\"ready\"}";
            }

            if (method == "GET" && path == "/ui/visibility")
            {
                return "{\"show\":" + (_showProvider != null && _showProvider() ? "true" : "false") + "}";
            }

            if (method == "GET" && path == "/ui/state")
            {
                bool show = _showProvider != null && _showProvider();
                string sessionJson = _session != null ? _session.StateJson() : "{}";
                return "{\"show\":" + (show ? "true" : "false") + "," + sessionJson.Substring(1);
            }

            if (method == "POST" && path == "/ui/send")
            {
                if (!JsonLine.TryGetString(body, "text", out string text) || string.IsNullOrWhiteSpace(text))
                {
                    status = 400;
                    return "{\"accepted\":false,\"reason\":\"缺少 text\"}";
                }

                if (_session == null || _session.IsGenerating)
                {
                    return "{\"accepted\":false,\"reason\":\"正在生成或会话未就绪\"}";
                }

                _mainThreadCommands.Enqueue(() => _session.Send(text));
                return "{\"accepted\":true,\"reason\":\"\"}";
            }

            if (method == "POST" && path == "/ui/hide")
            {
                var completed = new TaskCompletionSource<bool>();
                _mainThreadCommands.Enqueue(() =>
                {
                    try { _hideRequested?.Invoke(); completed.TrySetResult(true); }
                    catch (Exception error) { completed.TrySetException(error); }
                });
                // 只在 HTTP 工作线程等待实际确认，小窗已在本地立即隐藏。
                if (!completed.Task.Wait(2000))
                {
                    status = 503;
                    return "{\"detail\":\"hide confirmation timed out\"}";
                }
                return "{\"status\":\"hidden\"}";
            }

            if (method == "POST" && path == "/ui/activate")
            {
                _mainThreadCommands.Enqueue(() => _activateRequested?.Invoke());
                return "{\"status\":\"activated\"}";
            }

            if (method == "POST" && path == "/ui/mode")
            {
                if (!JsonLine.TryGetString(body, "mode", out string mode) || string.IsNullOrWhiteSpace(mode))
                {
                    status = 400;
                    return "{\"detail\":\"缺少 mode\"}";
                }

                _mainThreadCommands.Enqueue(() => _modeRequested?.Invoke(mode));
                return "{\"status\":\"ok\",\"mode\":" + JsonUtil.Str(mode) + "}";
            }

            if (method == "POST" && path == "/ui/quit")
            {
                _mainThreadCommands.Enqueue(() => _quitRequested?.Invoke());
                return "{\"status\":\"quitting\"}";
            }

            status = 404;
            return "{\"detail\":\"not found\"}";
        }
    }

    /// <summary>最小 HTTP/1.1 读写（与游戏世界服务同一实现思路；仅用于本地回环）。</summary>
    internal static class MiniHttp
    {
        internal const int MaxBodyBytes = 256 * 1024;
        internal static bool TryReadRequest(NetworkStream stream, out string method, out string path, out string body)
        {
            method = string.Empty;
            path = string.Empty;
            body = string.Empty;

            stream.ReadTimeout = 5000;
            var raw = new List<byte>(4096);
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

                if (string.Equals(lines[i].Substring(0, colon).Trim(), "Content-Length", StringComparison.OrdinalIgnoreCase))
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

        internal static void WriteResponse(NetworkStream stream, int status, string body)
        {
            byte[] payload = Encoding.UTF8.GetBytes(body);
            string head = "HTTP/1.1 " + status + " " + (status == 200 ? "OK" : status == 400 ? "Bad Request" : status == 404 ? "Not Found" : "Internal Server Error") + "\r\n"
                + "Content-Type: application/json; charset=utf-8\r\n"
                + "Content-Length: " + payload.Length + "\r\n"
                + "Connection: close\r\n\r\n";
            byte[] headBytes = Encoding.ASCII.GetBytes(head);
            stream.Write(headBytes, 0, headBytes.Length);
            stream.Write(payload, 0, payload.Length);
            stream.Flush();
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
    }
}
