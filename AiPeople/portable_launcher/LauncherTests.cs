using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace AiPeople.PortableLauncher
{
    internal static class LauncherTests
    {
        private static readonly List<string> Failures = new List<string>();

        public static int Main(string[] arguments)
        {
            string launcherExe = arguments.Length == 1 ? Path.GetFullPath(arguments[0]) : null;
            string temporaryRoot = Path.Combine(
                Path.GetTempPath(),
                "AiPeopleLauncherTests-" + Guid.NewGuid().ToString("N"));

            try
            {
                Directory.CreateDirectory(temporaryRoot);
                TestMissingFiles(Path.Combine(temporaryRoot, "missing layout"));
                TestArguments(Path.Combine(temporaryRoot, "valid layout with spaces"));
                TestHealthContract();
                TestShutdownRequest();
                TestShutdownToken();
                TestOptions();
                TestMutexIdentity(temporaryRoot);
                if (launcherExe != null)
                {
                    TestCompiledValidateOnly(launcherExe, Path.Combine(temporaryRoot, "compiled smoke"));
                }
            }
            catch (Exception error)
            {
                Failures.Add("Unhandled test exception: " + error);
            }
            finally
            {
                try
                {
                    if (Directory.Exists(temporaryRoot))
                    {
                        Directory.Delete(temporaryRoot, true);
                    }
                }
                catch (Exception error)
                {
                    Failures.Add("Could not remove test directory: " + error.Message);
                }
            }

            if (Failures.Count != 0)
            {
                Console.Error.WriteLine("Launcher tests failed:");
                foreach (string failure in Failures)
                {
                    Console.Error.WriteLine("- " + failure);
                }
                return 1;
            }

            Console.WriteLine("Launcher tests passed.");
            return 0;
        }

        private static void TestMissingFiles(string root)
        {
            Directory.CreateDirectory(root);
            LauncherPaths paths = LauncherPaths.FromRoot(root);
            IList<string> missing = paths.MissingRequiredFiles();
            Equal(3, missing.Count, "empty layout required-file count");
            True(ContainsPath(missing, paths.PythonExe), "missing runtime/python.exe");
            True(ContainsPath(missing, paths.AppScript), "missing app script");
            True(ContainsPath(missing, paths.ModelManifest), "missing model manifest");
        }

        private static void TestArguments(string root)
        {
            LauncherPaths paths = LauncherPaths.FromRoot(root);
            CreatePlaceholder(paths.PythonExe);
            CreatePlaceholder(paths.AppScript);
            CreatePlaceholder(paths.ModelManifest);

            Equal(0, paths.MissingRequiredFiles().Count, "complete layout validation");

            string expected = "-I -B -X utf8 -u "
                + WindowsCommandLine.Quote(paths.AppScript)
                + " --asset-root " + WindowsCommandLine.Quote(paths.AssetRoot)
                + " --data-root " + WindowsCommandLine.Quote(paths.DataRoot)
                + " --port 8767 --model-profile primary";
            string actual = paths.BuildPythonArguments();
            Equal(expected, actual, "Python arguments");
            True(actual.IndexOf("--database-path", StringComparison.Ordinal) < 0, "legacy database argument is absent");
            True(actual.IndexOf(" -B ", StringComparison.Ordinal) >= 0, "bytecode writes disabled by command line");

            ProcessStartInfo startInfo = new ProcessStartInfo();
            string token = ShutdownToken.Create();
            PythonEnvironment.Configure(startInfo, paths, token);
            Equal("1", startInfo.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"], "bytecode writes disabled by environment");
            Equal(token, startInfo.EnvironmentVariables["AIPEOPLE_SHUTDOWN_TOKEN"], "shutdown token environment");
        }

        private static void TestHealthContract()
        {
            string valid = "{\"status\":\"ready\",\"model_profile\":\"primary\","
                + "\"model\":\"local/baiweixi-gemma-4-12B-v3-2611-epoch1\","
                + "\"adapter_id\":\"baiweixi-v3-2611-epoch1-final-adapter\"}";
            string detail;
            True(HealthContract.IsExpectedReady(valid, out detail), "valid health identity");

            string invalid = "{\"status\":\"ready\",\"model_profile\":\"primary\","
                + "\"model\":\"local/baiweixi-gemma-4-12B-v3-2611-epoch1\","
                + "\"adapter_id\":\"wrong-adapter\"}";
            True(!HealthContract.IsExpectedReady(invalid, out detail), "wrong adapter is rejected");
            True(detail.IndexOf("wrong-adapter", StringComparison.Ordinal) >= 0, "wrong adapter diagnostic");
        }

        private static void TestOptions()
        {
            LauncherOptions options;
            string error;
            True(
                LauncherOptions.TryParse(new string[] { "--no-browser", "--validate-only" }, out options, out error),
                "supported launcher options");
            True(options != null && options.NoBrowser, "--no-browser value");
            True(options != null && options.ValidateOnly, "--validate-only value");
            True(
                !LauncherOptions.TryParse(new string[] { "--unknown" }, out options, out error),
                "unknown option is rejected");
        }

        private static void TestMutexIdentity(string root)
        {
            string first = LauncherPaths.FromRoot(root).MutexName();
            string same = LauncherPaths.FromRoot(root + Path.DirectorySeparatorChar).MutexName();
            string other = LauncherPaths.FromRoot(root + "-other").MutexName();
            Equal(first, same, "stable mutex identity");
            Equal(first, other, "global fixed-port mutex identity");
            Equal(@"Global\AiPeople-Service-Port-8767", first, "global fixed-port mutex name");
        }

        private static void TestShutdownToken()
        {
            string first = ShutdownToken.Create();
            string second = ShutdownToken.Create();
            Equal(64, first.Length, "shutdown token length");
            True(!String.Equals(first, second, StringComparison.Ordinal), "shutdown token randomness");
            foreach (char item in first)
            {
                True(
                    (item >= '0' && item <= '9') || (item >= 'a' && item <= 'f'),
                    "shutdown token hexadecimal encoding");
            }
        }

        private static void TestShutdownRequest()
        {
            TcpListener listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            int port = ((IPEndPoint)listener.LocalEndpoint).Port;
            string token = ShutdownToken.Create();
            string requestLine = null;
            string tokenHeader = null;
            Exception serverError = null;

            Thread server = new Thread(delegate()
            {
                try
                {
                    using (TcpClient client = listener.AcceptTcpClient())
                    using (NetworkStream stream = client.GetStream())
                    using (StreamReader reader = new StreamReader(stream, Encoding.ASCII, false, 1024, true))
                    {
                        requestLine = reader.ReadLine();
                        string line;
                        while (!String.IsNullOrEmpty(line = reader.ReadLine()))
                        {
                            int separator = line.IndexOf(':');
                            if (separator > 0
                                && String.Equals(
                                    line.Substring(0, separator),
                                    ShutdownProbe.TokenHeader,
                                    StringComparison.OrdinalIgnoreCase))
                            {
                                tokenHeader = line.Substring(separator + 1).Trim();
                            }
                        }

                        byte[] response = Encoding.ASCII.GetBytes(
                            "HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                        stream.Write(response, 0, response.Length);
                        stream.Flush();
                    }
                }
                catch (Exception error)
                {
                    serverError = error;
                }
            });
            server.IsBackground = true;
            server.Start();

            try
            {
                string detail;
                bool accepted = ShutdownProbe.Request(
                    new Uri("http://127.0.0.1:" + port + "/api/launcher/shutdown"),
                    token,
                    3000,
                    out detail);
                True(accepted, "shutdown endpoint acceptance: " + detail);
                True(server.Join(3000), "shutdown request server completion");
                True(serverError == null, "shutdown request server error: " + serverError);
                True(
                    requestLine != null
                        && requestLine.StartsWith("POST /api/launcher/shutdown ", StringComparison.Ordinal),
                    "shutdown request method and path");
                Equal(token, tokenHeader, "shutdown request token header");
            }
            finally
            {
                listener.Stop();
            }
        }

        private static void TestCompiledValidateOnly(string launcherExe, string root)
        {
            if (!File.Exists(launcherExe))
            {
                Failures.Add("Compiled launcher does not exist: " + launcherExe);
                return;
            }

            Directory.CreateDirectory(root);
            string copiedLauncher = Path.Combine(root, "AiPeople.exe");
            File.Copy(launcherExe, copiedLauncher);
            Equal(2, RunValidateOnly(copiedLauncher), "compiled missing-layout exit code");

            LauncherPaths paths = LauncherPaths.FromRoot(root);
            CreatePlaceholder(paths.PythonExe);
            CreatePlaceholder(paths.AppScript);
            CreatePlaceholder(paths.ModelManifest);
            Equal(0, RunValidateOnly(copiedLauncher), "compiled valid-layout exit code");
        }

        private static int RunValidateOnly(string launcherExe)
        {
            ProcessStartInfo startInfo = new ProcessStartInfo();
            startInfo.FileName = launcherExe;
            startInfo.Arguments = "--validate-only";
            startInfo.UseShellExecute = false;
            startInfo.CreateNoWindow = true;
            using (Process process = Process.Start(startInfo))
            {
                if (!process.WaitForExit(10000))
                {
                    process.Kill();
                    Failures.Add("--validate-only did not exit within 10 seconds.");
                    return -1;
                }
                return process.ExitCode;
            }
        }

        private static bool ContainsPath(IEnumerable<string> values, string expected)
        {
            foreach (string value in values)
            {
                if (String.Equals(value, expected, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        private static void CreatePlaceholder(string path)
        {
            string directory = Path.GetDirectoryName(path);
            if (!String.IsNullOrEmpty(directory))
            {
                Directory.CreateDirectory(directory);
            }
            File.WriteAllText(path, "test");
        }

        private static void True(bool value, string name)
        {
            if (!value)
            {
                Failures.Add(name);
            }
        }

        private static void Equal<T>(T expected, T actual, string name)
        {
            if (!EqualityComparer<T>.Default.Equals(expected, actual))
            {
                Failures.Add(name + ": expected <" + expected + ">, actual <" + actual + ">");
            }
        }
    }
}
