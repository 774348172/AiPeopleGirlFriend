using System;
using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace AiPeople.EditorTools
{
    /// <summary>
    /// 构建 Windows 播放器（桌面形态验证用）。
    /// 批处理：-executeMethod AiPeople.EditorTools.AiPeoplePlayerBuilder.BuildWindowsPlayer
    /// </summary>
    public static class AiPeoplePlayerBuilder
    {
        [MenuItem("AiPeople/构建 Windows 播放器")]
        public static void BuildWindowsPlayer()
        {
            string projectRoot = Directory.GetParent(Application.dataPath).FullName;
            string executable = Environment.GetEnvironmentVariable("AIPEOPLE_BUILD_PATH")
                ?? Path.Combine(Directory.GetParent(projectRoot).FullName,
                    "Builds", "CatGirlfriend", "CatGirlfriend.exe");
            BuildWindowsPlayerAtPath(executable);
        }

        // Both the menu/CLI release build and the apartment preview use this policy.
        public static void BuildWindowsPlayerAtPath(string executable)
        {
            string[] scenes = AiPeopleSceneBuilder.GetPlayerEntryScenes();
            executable = Path.GetFullPath(executable);
            Directory.CreateDirectory(Path.GetDirectoryName(executable));
            bool previousTiming = PlayerSettings.enableFrameTimingStats;
            bool previousDefaultApi = PlayerSettings.GetUseDefaultGraphicsAPIs(BuildTarget.StandaloneWindows64);
            var previousApis = PlayerSettings.GetGraphicsAPIs(BuildTarget.StandaloneWindows64);
            try
            {
                PlayerSettings.enableFrameTimingStats = true;
                PlayerSettings.SetUseDefaultGraphicsAPIs(BuildTarget.StandaloneWindows64, false);
                PlayerSettings.SetGraphicsAPIs(BuildTarget.StandaloneWindows64, new[] {
                    UnityEngine.Rendering.GraphicsDeviceType.Direct3D11,
                    UnityEngine.Rendering.GraphicsDeviceType.Direct3D12 });
                var options = new BuildPlayerOptions
                {
                    scenes = scenes,
                    locationPathName = executable,
                    target = BuildTarget.StandaloneWindows64,
                    options = BuildOptions.None,
                };

                BuildReport report = BuildPipeline.BuildPlayer(options);
                BuildSummary summary = report.summary;
                Debug.Log(
                    "[AiPeople] Windows 构建：" + summary.result
                    + " 输出=" + executable
                    + " 大小=" + (summary.totalSize / (1024 * 1024)) + "MB"
                    + " 耗时=" + summary.totalTime.TotalSeconds.ToString("F1") + "s");

                if (summary.result != BuildResult.Succeeded)
                {
                    throw new Exception("[AiPeople] 构建失败，错误数=" + summary.totalErrors);
                }
                Debug.Log("[AiPeople] 打包场景顺序：" + string.Join(" -> ", scenes));
            }
            finally
            {
                PlayerSettings.enableFrameTimingStats = previousTiming;
                PlayerSettings.SetGraphicsAPIs(BuildTarget.StandaloneWindows64, previousApis);
                PlayerSettings.SetUseDefaultGraphicsAPIs(BuildTarget.StandaloneWindows64, previousDefaultApi);
            }
        }
    }
}
