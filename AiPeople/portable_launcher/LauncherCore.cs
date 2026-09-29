using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;

namespace AiPeople.PortableLauncher
{
    internal sealed class LauncherOptions
    {
        private LauncherOptions()
        {
        }

        internal bool NoBrowser { get; private set; }
        internal bool ValidateOnly { get; private set; }

        internal static bool TryParse(string[] arguments, out LauncherOptions options, out string error)
        {
            options = new LauncherOptions();
            error = null;
            if (arguments == null)
            {
                return true;
            }

            foreach (string argument in arguments)
            {
                if (String.Equals(argument, "--no-browser", StringComparison.Ordinal))
                {
                    options.NoBrowser = true;
                }
                else if (String.Equals(argument, "--validate-only", StringComparison.Ordinal))
                {
                    options.ValidateOnly = true;
                }
                else
                {
                    error = "Unknown launcher option: " + argument;
                    options = null;
                    return false;
                }
            }

            return true;
        }
    }

    internal sealed class LauncherPaths
    {
        internal const int ServicePort = 8767;
        internal const string ExpectedModelProfile = "primary";
        internal const string ExpectedModel = "local/baiweixi-gemma-4-12B-v3-2611-epoch1";
        internal const string ExpectedAdapter = "baiweixi-v3-2611-epoch1-final-adapter";

        private LauncherPaths(string root)
        {
            Root = root;
            PythonExe = Path.Combine(root, "runtime", "python.exe");
            AppRoot = Path.Combine(root, "app");
            AppScript = Path.Combine(AppRoot, "tools", "baiweixi_chat_app.py");
            AssetRoot = Path.Combine(root, "assets");
            ModelManifest = Path.Combine(AssetRoot, "gemma4_nf4_runtime_manifest.json");
            DataRoot = Path.Combine(root, "data");
            LogsRoot = Path.Combine(root, "logs");
        }

        internal string Root { get; private set; }
        internal string PythonExe { get; private set; }
        internal string AppRoot { get; private set; }
        internal string AppScript { get; private set; }
        internal string AssetRoot { get; private set; }
        internal string ModelManifest { get; private set; }
        internal string DataRoot { get; private set; }
        internal string LogsRoot { get; private set; }

        internal static LauncherPaths FromRoot(string root)
        {
            if (String.IsNullOrWhiteSpace(root))
            {
                throw new ArgumentException("The launcher root cannot be empty.", "root");
            }

            string full = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            return new LauncherPaths(full);
        }

        internal IList<string> MissingRequiredFiles()
        {
            List<string> missing = new List<string>();
            AddIfMissing(missing, PythonExe);
            AddIfMissing(missing, AppScript);
            AddIfMissing(missing, ModelManifest);
            return missing;
        }

        internal string BuildPythonArguments()
        {
            return "-I -B -X utf8 -u "
                + WindowsCommandLine.Quote(AppScript)
                + " --asset-root " + WindowsCommandLine.Quote(AssetRoot)
                + " --data-root " + WindowsCommandLine.Quote(DataRoot)
                + " --port " + ServicePort.ToString(CultureInfo.InvariantCulture)
                + " --model-profile " + ExpectedModelProfile;
        }

        internal string MutexName()
        {
            return @"Global\AiPeople-Service-Port-8767";
        }

        private static void AddIfMissing(ICollection<string> missing, string path)
        {
            if (!File.Exists(path))
            {
                missing.Add(path);
            }
        }
    }

    internal static class PythonEnvironment
    {
        internal static void Configure(
            ProcessStartInfo startInfo,
            LauncherPaths paths,
            string shutdownToken)
        {
            if (startInfo == null)
            {
                throw new ArgumentNullException("startInfo");
            }
            if (paths == null)
            {
                throw new ArgumentNullException("paths");
            }
            if (String.IsNullOrEmpty(shutdownToken))
            {
                throw new ArgumentException("Shutdown token cannot be empty.", "shutdownToken");
            }

            startInfo.EnvironmentVariables.Remove("PYTHONHOME");
            startInfo.EnvironmentVariables.Remove("PYTHONPATH");
            startInfo.EnvironmentVariables["AIPEOPLE_PORTABLE"] = "1";
            startInfo.EnvironmentVariables["AIPEOPLE_DATA_ROOT"] = paths.DataRoot;
            startInfo.EnvironmentVariables["AIPEOPLE_SHUTDOWN_TOKEN"] = shutdownToken;
            startInfo.EnvironmentVariables["HF_HUB_OFFLINE"] = "1";
            startInfo.EnvironmentVariables["TRANSFORMERS_OFFLINE"] = "1";
            startInfo.EnvironmentVariables["HF_DATASETS_OFFLINE"] = "1";
            startInfo.EnvironmentVariables["HF_HUB_DISABLE_TELEMETRY"] = "1";
            startInfo.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            startInfo.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
            startInfo.EnvironmentVariables["TOKENIZERS_PARALLELISM"] = "false";
            startInfo.EnvironmentVariables["NO_PROXY"] = "127.0.0.1,localhost";

            List<string> candidates = new List<string>();
            candidates.Add(Path.Combine(paths.Root, "runtime"));
            candidates.Add(Path.Combine(paths.Root, "runtime", "DLLs"));
            candidates.Add(Path.Combine(paths.Root, "runtime", "Library", "bin"));
            candidates.Add(Path.Combine(paths.Root, "runtime", "Scripts"));
            candidates.Add(Path.Combine(paths.Root, "runtime", "Lib", "site-packages", "torch", "lib"));

            string inherited = startInfo.EnvironmentVariables["PATH"];
            StringBuilder combined = new StringBuilder();
            foreach (string candidate in candidates)
            {
                if (Directory.Exists(candidate))
                {
                    if (combined.Length != 0)
                    {
                        combined.Append(Path.PathSeparator);
                    }
                    combined.Append(candidate);
                }
            }
            if (!String.IsNullOrEmpty(inherited))
            {
                if (combined.Length != 0)
                {
                    combined.Append(Path.PathSeparator);
                }
                combined.Append(inherited);
            }
            startInfo.EnvironmentVariables["PATH"] = combined.ToString();
        }
    }

    internal static class WindowsCommandLine
    {
        internal static string Quote(string value)
        {
            if (value == null)
            {
                throw new ArgumentNullException("value");
            }

            if (value.Length > 0 && value.IndexOfAny(new char[] { ' ', '\t', '\n', '\v', '"' }) < 0)
            {
                return value;
            }

            StringBuilder result = new StringBuilder(value.Length + 2);
            result.Append('"');
            int backslashes = 0;
            foreach (char character in value)
            {
                if (character == '\\')
                {
                    backslashes++;
                    continue;
                }

                if (character == '"')
                {
                    result.Append('\\', (backslashes * 2) + 1);
                    result.Append('"');
                    backslashes = 0;
                    continue;
                }

                result.Append('\\', backslashes);
                backslashes = 0;
                result.Append(character);
            }

            result.Append('\\', backslashes * 2);
            result.Append('"');
            return result.ToString();
        }
    }

    internal sealed class HealthProbeResult
    {
        internal HealthProbeResult(bool responded, bool expectedReady, int statusCode, string detail)
        {
            Responded = responded;
            ExpectedReady = expectedReady;
            StatusCode = statusCode;
            Detail = detail ?? String.Empty;
        }

        internal bool Responded { get; private set; }
        internal bool ExpectedReady { get; private set; }
        internal int StatusCode { get; private set; }
        internal string Detail { get; private set; }
    }

    internal static class HealthProbe
    {
        internal static readonly Uri HealthUri = new Uri("http://127.0.0.1:8767/api/health");
        internal static readonly Uri BrowserUri = new Uri("http://127.0.0.1:8767/");
        internal static readonly Uri ShutdownUri = new Uri("http://127.0.0.1:8767/api/launcher/shutdown");

        internal static HealthProbeResult Probe(int timeoutMilliseconds)
        {
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(HealthUri);
                request.Method = "GET";
                request.Proxy = null;
                request.KeepAlive = false;
                request.Timeout = timeoutMilliseconds;
                request.ReadWriteTimeout = timeoutMilliseconds;
                request.UserAgent = "AiPeople-Portable-Launcher/1.0";

                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                using (Stream stream = response.GetResponseStream())
                using (StreamReader reader = new StreamReader(stream, Encoding.UTF8, true))
                {
                    string payload = reader.ReadToEnd();
                    string detail;
                    bool expected = HealthContract.IsExpectedReady(payload, out detail);
                    return new HealthProbeResult(
                        true,
                        expected,
                        (int)response.StatusCode,
                        detail);
                }
            }
            catch (WebException error)
            {
                HttpWebResponse response = error.Response as HttpWebResponse;
                if (response != null)
                {
                    int statusCode = (int)response.StatusCode;
                    response.Close();
                    return new HealthProbeResult(
                        true,
                        false,
                        statusCode,
                        "Health endpoint returned HTTP " + statusCode.ToString(CultureInfo.InvariantCulture) + ".");
                }

                return new HealthProbeResult(false, false, 0, error.Status.ToString());
            }
            catch (Exception error)
            {
                return new HealthProbeResult(false, false, 0, error.Message);
            }
        }
    }

    internal static class ShutdownToken
    {
        internal static string Create()
        {
            byte[] bytes = new byte[32];
            using (RandomNumberGenerator generator = RandomNumberGenerator.Create())
            {
                generator.GetBytes(bytes);
            }

            StringBuilder value = new StringBuilder(bytes.Length * 2);
            foreach (byte item in bytes)
            {
                value.Append(item.ToString("x2", CultureInfo.InvariantCulture));
            }
            return value.ToString();
        }
    }

    internal static class ShutdownProbe
    {
        internal const string TokenHeader = "X-AiPeople-Shutdown-Token";

        internal static bool Request(string token, int timeoutMilliseconds, out string detail)
        {
            return Request(HealthProbe.ShutdownUri, token, timeoutMilliseconds, out detail);
        }

        internal static bool Request(Uri uri, string token, int timeoutMilliseconds, out string detail)
        {
            if (uri == null)
            {
                throw new ArgumentNullException("uri");
            }
            if (String.IsNullOrEmpty(token))
            {
                throw new ArgumentException("Shutdown token cannot be empty.", "token");
            }

            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(uri);
                request.Method = "POST";
                request.Proxy = null;
                request.KeepAlive = false;
                request.AllowAutoRedirect = false;
                request.Timeout = timeoutMilliseconds;
                request.ReadWriteTimeout = timeoutMilliseconds;
                request.ContentLength = 0;
                request.UserAgent = "AiPeople-Portable-Launcher/1.0";
                request.Headers[TokenHeader] = token;

                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                {
                    int statusCode = (int)response.StatusCode;
                    if (statusCode == 200 || statusCode == 202)
                    {
                        detail = "Shutdown request accepted.";
                        return true;
                    }

                    detail = "Shutdown endpoint returned HTTP "
                        + statusCode.ToString(CultureInfo.InvariantCulture) + ".";
                    return false;
                }
            }
            catch (WebException error)
            {
                HttpWebResponse response = error.Response as HttpWebResponse;
                if (response != null)
                {
                    int statusCode = (int)response.StatusCode;
                    response.Close();
                    detail = "Shutdown endpoint returned HTTP "
                        + statusCode.ToString(CultureInfo.InvariantCulture) + ".";
                    return false;
                }

                detail = "Shutdown request failed: " + error.Status.ToString() + ".";
                return false;
            }
            catch (Exception error)
            {
                detail = "Shutdown request failed: " + error.Message;
                return false;
            }
        }
    }

    internal static class HealthContract
    {
        internal static bool IsExpectedReady(string payload, out string detail)
        {
            IDictionary<string, object> value;
            try
            {
                JavaScriptSerializer serializer = new JavaScriptSerializer();
                value = serializer.DeserializeObject(payload) as IDictionary<string, object>;
            }
            catch (Exception error)
            {
                detail = "Health response is not valid JSON: " + error.Message;
                return false;
            }

            if (value == null)
            {
                detail = "Health response must be a JSON object.";
                return false;
            }

            string status = ReadString(value, "status");
            string profile = ReadString(value, "model_profile");
            string model = ReadString(value, "model");
            string adapter = ReadString(value, "adapter_id");

            if (!String.Equals(status, "ready", StringComparison.Ordinal))
            {
                detail = "Service status is not ready.";
                return false;
            }

            if (!String.Equals(profile, LauncherPaths.ExpectedModelProfile, StringComparison.Ordinal))
            {
                detail = "Unexpected model_profile: " + Printable(profile) + ".";
                return false;
            }

            if (!String.Equals(model, LauncherPaths.ExpectedModel, StringComparison.Ordinal))
            {
                detail = "Unexpected model: " + Printable(model) + ".";
                return false;
            }

            if (!String.Equals(adapter, LauncherPaths.ExpectedAdapter, StringComparison.Ordinal))
            {
                detail = "Unexpected adapter_id: " + Printable(adapter) + ".";
                return false;
            }

            detail = "ready";
            return true;
        }

        private static string ReadString(IDictionary<string, object> value, string key)
        {
            object item;
            if (!value.TryGetValue(key, out item) || item == null)
            {
                return null;
            }

            return item as string;
        }

        private static string Printable(string value)
        {
            return value == null ? "<missing>" : value;
        }
    }

    internal static class PortProbe
    {
        internal static bool IsAvailable(int port, out string detail)
        {
            Socket socket = null;
            try
            {
                socket = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
                socket.ExclusiveAddressUse = true;
                socket.Bind(new IPEndPoint(IPAddress.Loopback, port));
                detail = "available";
                return true;
            }
            catch (SocketException error)
            {
                detail = error.Message;
                return false;
            }
            finally
            {
                if (socket != null)
                {
                    socket.Close();
                }
            }
        }
    }

    internal static class BrowserLauncher
    {
        internal static void Open()
        {
            ProcessStartInfo startInfo = new ProcessStartInfo();
            startInfo.FileName = HealthProbe.BrowserUri.AbsoluteUri;
            startInfo.UseShellExecute = true;
            Process.Start(startInfo);
        }
    }
}
