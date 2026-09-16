using System;
using System.IO;
using UnityEngine;

namespace AiPeople.Shell
{
    public enum ShellWindowMode
    {
        /// <summary>自动：构建版优先尝试壁纸层，失败降级普通窗口；Editor 恒为普通窗口。</summary>
        Auto = 0,

        /// <summary>强制壁纸层（挂载失败仍降级普通窗口）。</summary>
        Wallpaper = 1,

        /// <summary>普通窗口（可最小化，功能完整；受管设备/调试用）。</summary>
        Normal = 2,
    }

    [Serializable]
    public sealed class ShellSettingsData
    {
        public int windowMode = (int)ShellWindowMode.Auto;
        public string hotkey = "Ctrl+Alt+B";
        public bool ambientBehaviors = true;
        public bool showHud;
        public int targetFps = 30;

        /// <summary>桌面形态下是否启用男主走位（默认关；调试用）。</summary>
        public bool playerControls;
    }

    /// <summary>桌面外壳设置持久化（persistentDataPath/aipeople_shell.json）。</summary>
    public static class ShellSettingsStore
    {
        private static string FilePath => Path.Combine(Application.persistentDataPath, "aipeople_shell.json");

        public static ShellSettingsData Load()
        {
            try
            {
                if (File.Exists(FilePath))
                {
                    ShellSettingsData loaded = JsonUtility.FromJson<ShellSettingsData>(File.ReadAllText(FilePath));
                    if (loaded != null)
                    {
                        if (string.IsNullOrWhiteSpace(loaded.hotkey))
                        {
                            loaded.hotkey = "Ctrl+Alt+B";
                        }

                        if (loaded.targetFps < 5 || loaded.targetFps > 240)
                        {
                            loaded.targetFps = 30;
                        }

                        return loaded;
                    }
                }
            }
            catch (Exception exception)
            {
                Debug.LogWarning("[AiPeople] 外壳设置读取失败，使用默认值：" + exception.Message);
            }

            return new ShellSettingsData();
        }

        public static void Save(ShellSettingsData data)
        {
            try
            {
                File.WriteAllText(FilePath, JsonUtility.ToJson(data, true));
            }
            catch (Exception exception)
            {
                Debug.LogWarning("[AiPeople] 外壳设置写入失败：" + exception.Message);
            }
        }

        public static string Location => FilePath;
    }
}
