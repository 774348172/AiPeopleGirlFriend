using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;

namespace AiPeople.Shell
{
    /// <summary>Opt-in milestone log. Thread-safe; never reads Unity objects on native/HTTP threads.</summary>
    public static class UiLatencyTrace
    {
        public static readonly string Folder = Environment.GetEnvironmentVariable("AIPEOPLE_UI_LATENCY_OUTPUT");
        public static bool Enabled => !string.IsNullOrWhiteSpace(Folder) && !_failed;
        private static readonly object Gate = new object();
        private static StreamWriter _writer;
        private static bool _failed;

        public static void Mark(string stage)
        {
            if (!Enabled) return;
            long tick = Stopwatch.GetTimestamp();
            lock (Gate)
            {
                try
                {
                    if (_writer == null)
                    {
                        Directory.CreateDirectory(Folder);
                        string role = Array.Exists(Environment.GetCommandLineArgs(), a => a == "--chat-window") ? "chat" : "main";
                        _writer = new StreamWriter(Path.Combine(Folder, role + "-" + Process.GetCurrentProcess().Id + "-ui.csv"));
                        _writer.AutoFlush = true;
                        _writer.WriteLine("utc_ms,monotonic_ms,stage");
                    }
                    _writer.WriteLine(DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + ","
                        + (tick * 1000.0 / Stopwatch.Frequency).ToString("F4", CultureInfo.InvariantCulture) + "," + stage);
                }
                catch (IOException) { _failed = true; }
                catch (UnauthorizedAccessException) { _failed = true; }
            }
        }
    }
}
