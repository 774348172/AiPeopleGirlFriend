using System;
using System.Globalization;
using System.IO;
using Unity.Profiling;
using UnityEngine;

namespace AiPeople.Shell
{
    /// <summary>Opt-in release-player measurements. No recorder, file or component in normal launches.</summary>
    public sealed class PerformanceProbe : MonoBehaviour
    {
        private StreamWriter _writer;
        private readonly FrameTiming[] _timing = new FrameTiming[1];
        private ProfilerRecorder _draws, _triangles, _memory;
        private DesktopShellController _shell;
        private double _flush;
        private ulong _lastTimestamp;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void StartIfRequested()
        {
            string folder = Environment.GetEnvironmentVariable("AIPEOPLE_PERF_OUTPUT");
            if (Application.isEditor || string.IsNullOrWhiteSpace(folder)) return;
            try
            {
                Directory.CreateDirectory(folder);
                var probe = new GameObject("PerformanceProbe").AddComponent<PerformanceProbe>();
                DontDestroyOnLoad(probe.gameObject);
                probe.Begin(folder);
            }
            catch (Exception e) { Debug.LogWarning("Performance probe unavailable: " + e.Message); }
        }

        private void Begin(string folder)
        {
            int pid = System.Diagnostics.Process.GetCurrentProcess().Id;
            string role = Array.Exists(Environment.GetCommandLineArgs(), a => a == "--chat-window") ? "chat" : "main";
            File.WriteAllText(Path.Combine(folder, role + "-" + pid + "-device.txt"),
                $"Unity={Application.unityVersion}\nGPU={SystemInfo.graphicsDeviceName}\nAPI={SystemInfo.graphicsDeviceType}\nVRAM_MB={SystemInfo.graphicsMemorySize}\nCPU={SystemInfo.processorType}\nRAM_MB={SystemInfo.systemMemorySize}\n");
            _writer = new StreamWriter(Path.Combine(folder, role + "-" + pid + "-frames.csv"));
            _writer.WriteLine("utc_ms,frame_ms,cpu_frame_ms,cpu_main_ms,cpu_render_ms,gpu_ms,timing_fresh,paused,width,height,target_fps,draw_calls,triangles,unity_used_bytes");
            _draws = ProfilerRecorder.StartNew(ProfilerCategory.Render, "Draw Calls Count", 1);
            _triangles = ProfilerRecorder.StartNew(ProfilerCategory.Render, "Triangles Count", 1);
            _memory = ProfilerRecorder.StartNew(ProfilerCategory.Memory, "Total Used Memory", 1);
        }

        private static string Number(double value) => value > 0 ? value.ToString("F4", CultureInfo.InvariantCulture) : "";
        private static string Counter(ProfilerRecorder r) => r.Valid ? r.LastValue.ToString(CultureInfo.InvariantCulture) : "";

        private void LateUpdate()
        {
            if (_writer == null) return;
            FrameTimingManager.CaptureFrameTimings();
            uint count = FrameTimingManager.GetLatestTimings(1, _timing);
            FrameTiming t = _timing[0];
            bool fresh = count > 0 && t.frameStartTimestamp != _lastTimestamp;
            if (fresh) _lastTimestamp = t.frameStartTimestamp;
            if (Time.realtimeSinceStartupAsDouble >= _flush)
            {
                _shell = FindFirstObjectByType<DesktopShellController>();
                _writer.Flush();
                _flush = Time.realtimeSinceStartupAsDouble + 1;
            }
            _writer.WriteLine(string.Join(",", DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                Number(Time.unscaledDeltaTime * 1000), fresh ? Number(t.cpuFrameTime) : "",
                fresh ? Number(t.cpuMainThreadFrameTime) : "", fresh ? Number(t.cpuRenderThreadFrameTime) : "",
                fresh ? Number(t.gpuFrameTime) : "", fresh ? 1 : 0, _shell != null && _shell.RenderPaused ? 1 : 0,
                Screen.width, Screen.height, Application.targetFrameRate, Counter(_draws), Counter(_triangles), Counter(_memory)));
        }

        private void OnDestroy()
        {
            _writer?.Dispose(); _draws.Dispose(); _triangles.Dispose(); _memory.Dispose();
        }
    }
}
