using System;
using System.Diagnostics;
using Debug = UnityEngine.Debug;
using System.IO;
using UnityEngine;

namespace AiPeople.Shell
{
    /// <summary>
    /// 对话小窗进程管理（仅构建版）：随主进程启动、常驻后台（隐藏），异常自动重启（有限次）。
    /// Editor 内不启动（Editor 用游戏视图内的对话面板调试）。
    /// </summary>
    public sealed class ChatWindowLauncher
    {
        private const int MaxRestarts = 5;

        private readonly string _exePath;
        private Process _process;
        private int _startCount;

        public bool IsSupported => !string.IsNullOrEmpty(_exePath);
        public bool IsRunning => _process != null && SafeHasExited() == false;
        public int ProcessId => IsRunning ? _process.Id : 0;

        public ChatWindowLauncher()
        {
            if (Application.isEditor || Application.platform != RuntimePlatform.WindowsPlayer)
            {
                _exePath = null;
                return;
            }

            string exeName = Path.GetFileNameWithoutExtension(Environment.GetCommandLineArgs()[0]);
            string buildDir = Path.GetDirectoryName(Application.dataPath) ?? string.Empty;
            string exePath = Path.Combine(buildDir, exeName + ".exe");
            _exePath = File.Exists(exePath) ? exePath : null;
        }

        public void EnsureStarted()
        {
            if (!IsSupported || IsRunning || _startCount > MaxRestarts)
            {
                if (IsSupported && _startCount > MaxRestarts)
                {
                    Debug.LogWarning("[AiPeople] 对话小窗多次启动失败，停止自动重启");
                }

                return;
            }

            try
            {
                // 单独指定日志文件，避免两个进程互相覆盖 Player.log
                string logPath = Path.Combine(
                    Application.persistentDataPath, "chatwindow.log");
                var info = new ProcessStartInfo(
                    _exePath,
                    "--chat-window " + (SystemInfo.graphicsDeviceType == UnityEngine.Rendering.GraphicsDeviceType.Direct3D11
                        ? "-force-d3d11 " : SystemInfo.graphicsDeviceType == UnityEngine.Rendering.GraphicsDeviceType.Direct3D12
                        ? "-force-d3d12 " : string.Empty) + "-logFile \"" + logPath.Replace("\\", "/") + "\"")
                {
                    UseShellExecute = false,
                    WorkingDirectory = Path.GetDirectoryName(_exePath) ?? string.Empty,
                };
                _process = Process.Start(info);
                _startCount++;
                Debug.Log("[AiPeople] 对话小窗进程已启动（第 " + _startCount + " 次，pid="
                    + (_process != null ? _process.Id.ToString() : "?") + "）");
            }
            catch (Exception exception)
            {
                Debug.LogWarning("[AiPeople] 对话小窗进程启动失败：" + exception.Message);
            }
        }

        public void Shutdown()
        {
            try
            {
                if (_process != null && SafeHasExited() == false)
                {
                    _process.Kill();
                }
            }
            catch (Exception)
            {
                // 退出期忽略
            }

            _process = null;
        }

        private bool SafeHasExited()
        {
            try
            {
                return _process == null || _process.HasExited;
            }
            catch (Exception)
            {
                return true;
            }
        }
    }
}
