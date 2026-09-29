using System;
using System.Collections;
using System.Diagnostics;
using System.IO;
using System.Net;
using AiPeople.Core;
using UnityEngine;
using UnityEngine.Networking;

namespace AiPeople.App
{
    // Owns only a backend started by this player; an existing service is reused.
    public sealed class LocalBackendLauncher
    {
        [Serializable] private sealed class HealthReply
        {
            public string status;
            public string model_profile;
            public string model;
            public string adapter_id;
        }

        private const string ExpectedModel = "local/baiweixi-gemma-4-12B-v3-2611-epoch1";
        private const string ExpectedAdapter = "baiweixi-v3-2611-epoch1-final-adapter";

        private readonly AiPeopleConfig.Data _config;
        private readonly object _logLock = new object();
        private Process _process;
        private string _shutdownToken;
        private string _logPath;
        private bool _stopping;

        public bool Ready { get; private set; }
        public string Error { get; private set; } = string.Empty;
        public bool OwnsProcess => _process != null;

        public LocalBackendLauncher(AiPeopleConfig.Data config) { _config = config; }

        public static string ResolveScriptPath(string playerDirectory, string configuredPath)
        {
            if (string.IsNullOrWhiteSpace(configuredPath)) return string.Empty;
            return Path.GetFullPath(Path.IsPathRooted(configuredPath)
                ? configuredPath : Path.Combine(playerDirectory, configuredPath));
        }

        public IEnumerator EnsureReady(Action<string> progress)
        {
            if (Application.isEditor || Application.platform != RuntimePlatform.WindowsPlayer
                || !_config.autoStartBackend) yield break;

            bool portable = !string.IsNullOrWhiteSpace(_config.backendPortableRoot);
            yield return CheckHealth();
            if (Ready)
            {
                if (portable) Error = "模型端口已被其他程序占用，请先退出正在运行的版本";
                else progress?.Invoke("已连接本机模型服务");
                yield break;
            }

            if (!Uri.TryCreate(_config.endpoint, UriKind.Absolute, out Uri endpoint)
                || endpoint.Scheme != Uri.UriSchemeHttp
                || (endpoint.Host != "127.0.0.1" && endpoint.Host != "localhost"))
            {
                Error = "自动启动仅支持本机 HTTP 模型地址";
                yield break;
            }

            string playerDirectory = Directory.GetParent(Application.dataPath).FullName;
            string portableRoot = portable
                ? ResolveScriptPath(playerDirectory, _config.backendPortableRoot) : string.Empty;
            string python = portable ? Path.Combine(portableRoot, "runtime", "python.exe") : _config.backendPythonExe;
            string script = portable ? Path.Combine(portableRoot, "app", "tools", "baiweixi_chat_app.py")
                : ResolveScriptPath(playerDirectory, _config.backendScriptPath);
            if (!File.Exists(script)) { Error = "未找到模型启动文件：" + script; yield break; }
            if (!string.IsNullOrWhiteSpace(python) && !File.Exists(python))
            {
                Error = "未找到 Python：" + python;
                yield break;
            }
            if (portable && !File.Exists(Path.Combine(portableRoot, "assets", "gemma4_nf4_runtime_manifest.json")))
            {
                Error = "模型资源不完整：" + portableRoot;
                yield break;
            }

            try
            {
                _shutdownToken = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
                _logPath = Path.Combine(Application.persistentDataPath, "backend-startup.log");
                ProcessStartInfo start;
                if (portable)
                {
                    string dataRoot = Path.Combine(portableRoot, "data");
                    Directory.CreateDirectory(dataRoot);
                    start = new ProcessStartInfo(python) {
                        Arguments = "-I -B -X utf8 -u " + Quote(script)
                            + " --asset-root " + Quote(Path.Combine(portableRoot, "assets"))
                            + " --data-root " + Quote(dataRoot)
                            + " --port " + endpoint.Port + " --model-profile primary",
                        WorkingDirectory = Path.Combine(portableRoot, "app"),
                        UseShellExecute = false, CreateNoWindow = true,
                        RedirectStandardOutput = true, RedirectStandardError = true,
                    };
                    ConfigurePortableEnvironment(start, portableRoot, dataRoot);
                }
                else
                {
                    string powerShell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),
                        @"WindowsPowerShell\v1.0\powershell.exe");
                    start = new ProcessStartInfo(powerShell) {
                        Arguments = "-NoProfile -ExecutionPolicy Bypass -File " + Quote(script)
                            + " -Port " + endpoint.Port
                            + (string.IsNullOrWhiteSpace(python) ? "" : " -PythonExe " + Quote(python)),
                        WorkingDirectory = Path.GetDirectoryName(script),
                        UseShellExecute = false, CreateNoWindow = true,
                        RedirectStandardOutput = true, RedirectStandardError = true,
                    };
                }
                start.EnvironmentVariables["AIPEOPLE_SHUTDOWN_TOKEN"] = _shutdownToken;
                start.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                _process = new Process { StartInfo = start, EnableRaisingEvents = true };
                _process.OutputDataReceived += (_, line) => AppendLog(line.Data);
                _process.ErrorDataReceived += (_, line) => AppendLog(line.Data);
                if (!_process.Start()) throw new InvalidOperationException("启动进程失败");
                _process.BeginOutputReadLine();
                _process.BeginErrorReadLine();
                progress?.Invoke("本机模型启动中");
                UnityEngine.Debug.Log("[AiPeople] 本机模型启动中，进程=" + _process.Id);
            }
            catch (Exception exception)
            {
                Error = "无法启动本机模型：" + exception.Message;
                StopOwned();
                yield break;
            }

            float deadline = Time.realtimeSinceStartup + (portable ? 300f : 180f);
            while (Time.realtimeSinceStartup < deadline)
            {
                yield return new WaitForSecondsRealtime(2f);
                yield return CheckHealth();
                if (Ready) { progress?.Invoke("本机模型已就绪"); yield break; }
                if (_process.HasExited)
                {
                    Error = "模型启动进程已退出，检查 " + _logPath;
                    yield break;
                }
            }
            Error = "等待模型就绪超时，检查 " + _logPath;
        }

        private IEnumerator CheckHealth()
        {
            Ready = false;
            using var request = UnityWebRequest.Get(_config.endpoint + "/api/health");
            request.timeout = 2;
            yield return request.SendWebRequest();
            if (request.result != UnityWebRequest.Result.Success) yield break;
            try
            {
                HealthReply health = JsonUtility.FromJson<HealthReply>(request.downloadHandler.text);
                Ready = health?.status == "ready"
                    && (!string.IsNullOrWhiteSpace(_config.backendPortableRoot)
                        ? health.model_profile == "primary" && health.model == ExpectedModel
                            && health.adapter_id == ExpectedAdapter
                        : true);
            }
            catch (ArgumentException) { Ready = false; }
        }

        private static void ConfigurePortableEnvironment(ProcessStartInfo start, string root, string dataRoot)
        {
            start.EnvironmentVariables.Remove("PYTHONHOME");
            start.EnvironmentVariables.Remove("PYTHONPATH");
            start.EnvironmentVariables["AIPEOPLE_PORTABLE"] = "1";
            start.EnvironmentVariables["AIPEOPLE_DATA_ROOT"] = dataRoot;
            start.EnvironmentVariables["HF_HUB_OFFLINE"] = "1";
            start.EnvironmentVariables["TRANSFORMERS_OFFLINE"] = "1";
            start.EnvironmentVariables["HF_DATASETS_OFFLINE"] = "1";
            start.EnvironmentVariables["HF_HUB_DISABLE_TELEMETRY"] = "1";
            start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
            start.EnvironmentVariables["TOKENIZERS_PARALLELISM"] = "false";
            start.EnvironmentVariables["NO_PROXY"] = "127.0.0.1,localhost";
            string[] runtimePaths = { "runtime", @"runtime\DLLs", @"runtime\Library\bin",
                @"runtime\Scripts", @"runtime\Lib\site-packages\torch\lib" };
            string path = start.EnvironmentVariables["PATH"] ?? string.Empty;
            foreach (string relative in runtimePaths)
            {
                string candidate = Path.Combine(root, relative);
                if (Directory.Exists(candidate)) path = candidate + Path.PathSeparator + path;
            }
            start.EnvironmentVariables["PATH"] = path;
        }

        private static string Quote(string value) => "\"" + value.Replace("\"", "\\\"") + "\"";

        private void AppendLog(string line)
        {
            if (line == null) return;
            lock (_logLock)
            {
                try { File.AppendAllText(_logPath, line + Environment.NewLine); }
                catch (IOException) { }
            }
        }

        public void StopOwned()
        {
            if (_process == null || _stopping) return;
            _stopping = true;
            try
            {
                if (!_process.HasExited && Ready)
                {
                    var request = (HttpWebRequest)WebRequest.Create(_config.endpoint + "/api/launcher/shutdown");
                    request.Method = "POST";
                    request.Timeout = 1500;
                    request.ReadWriteTimeout = 1500;
                    request.ContentLength = 0;
                    request.Headers["X-AiPeople-Shutdown-Token"] = _shutdownToken;
                    using var response = (HttpWebResponse)request.GetResponse();
                    if (response.StatusCode == HttpStatusCode.Accepted) return;
                }
            }
            catch (WebException) { }
            if (!_process.HasExited)
            {
                try
                {
                    Process.Start(new ProcessStartInfo("taskkill.exe", "/T /F /PID " + _process.Id) {
                        UseShellExecute = false, CreateNoWindow = true,
                    })?.WaitForExit(3000);
                }
                catch (Exception exception) { UnityEngine.Debug.LogWarning("[AiPeople] 关闭模型进程失败：" + exception.Message); }
            }
        }
    }
}
