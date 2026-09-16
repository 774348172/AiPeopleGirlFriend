using System.Collections;
using System.IO;
using System.Reflection;
using AiPeople.App;
using AiPeople.Shell;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AiPeople.Tests
{
    /// <summary>
    /// 桌面外壳测试：热键解析、设置持久化、会话态切换（Editor 内为普通窗口模式，不做原生窗口操作）。
    /// </summary>
    public sealed class ShellTests
    {
        private string _settingsBefore;
        private bool _settingsExisted;

        [SetUp]
        public void PreserveUserSettings()
        {
            _settingsExisted = File.Exists(ShellSettingsStore.Location);
            _settingsBefore = _settingsExisted ? File.ReadAllText(ShellSettingsStore.Location) : null;
        }

        [TearDown]
        public void RestoreUserSettings()
        {
            if (_settingsExisted) File.WriteAllText(ShellSettingsStore.Location, _settingsBefore);
            else if (File.Exists(ShellSettingsStore.Location)) File.Delete(ShellSettingsStore.Location);
        }

        [UnityTest]
        public IEnumerator TrayRecovery_RepeatedSelectionNeverSavesNormalMode()
        {
            var appGo = new GameObject("TrayRecoveryTest");
            appGo.SetActive(false); // Do not start network clients or construct the game.
            var app = appGo.AddComponent<AiPeopleApp>();
            var shellGo = new GameObject("TrayRecoveryShell");
            var shell = shellGo.AddComponent<DesktopShellController>();
            shell.Initialize(new ShellSettingsData { hotkey = "Ctrl+Alt+F24" }, null);
            var privateInstance = BindingFlags.NonPublic | BindingFlags.Instance;
            typeof(AiPeopleApp).GetField("_shell", privateInstance).SetValue(app, shell);
            var restore = typeof(AiPeopleApp).GetMethod("RestoreWallpaperFromTray", privateInstance);
            var selectMenu = typeof(TrayIcon).GetMethod("EnqueueMenuCommand", privateInstance);
            var tray = new TrayIcon();
            try
            {
                selectMenu.Invoke(tray, new object[] { 0 }); // Dismissal is not a mode command.
                Assert.IsTrue(tray.Commands.IsEmpty);
                for (int i = 0; i < 3; i++)
                {
                    selectMenu.Invoke(tray, new object[] { 1003 });
                    Assert.IsTrue(tray.Commands.TryDequeue(out var command));
                    Assert.AreEqual(TrayCommand.RestoreWallpaper, command);
                    // Covers the old branch that changed Wallpaper -> Normal on every other click.
                    typeof(DesktopShellController).GetProperty("IsWallpaperMode").SetValue(shell, true);
                    restore.Invoke(app, null);
                    Assert.AreEqual((int)ShellWindowMode.Wallpaper, shell.Settings.windowMode);
                    Assert.AreEqual((int)ShellWindowMode.Wallpaper, ShellSettingsStore.Load().windowMode);
                }
            }
            finally
            {
                Object.Destroy(appGo);
                Object.Destroy(shellGo);
                tray.Dispose();
            }
            yield return null;
        }

        [Test]
        public void Hotkey_Parsing_Accepts_Common_Forms()
        {
            Assert.IsTrue(GlobalHotkey.TryParse("Ctrl+Alt+B", out uint modifiers, out uint key, out string error), error);
            Assert.AreEqual((uint)(NativeMods.Control | NativeMods.Alt), modifiers);
            Assert.AreEqual((uint)'B', key);

            Assert.IsTrue(GlobalHotkey.TryParse("ctrl+shift+f10", out uint mods2, out uint key2, out string error2), error2);
            Assert.AreEqual((uint)(NativeMods.Control | NativeMods.Shift), mods2);
            Assert.AreEqual(0x79u, key2); // F10

            Assert.IsFalse(GlobalHotkey.TryParse("Ctrl+Alt", out _, out _, out string missingKey));
            Assert.IsNotEmpty(missingKey);
            Assert.IsFalse(GlobalHotkey.TryParse("Ctrl+Alt+Foo", out _, out _, out string badKey));
            Assert.IsNotEmpty(badKey);
        }

        [Test]
        public void Settings_RoundTrip()
        {
            ShellSettingsData original = ShellSettingsStore.Load();
            var changed = new ShellSettingsData
            {
                windowMode = (int)ShellWindowMode.Wallpaper,
                hotkey = "Ctrl+Shift+J",
                ambientBehaviors = false,
                showHud = true,
                targetFps = 60,
            };

            ShellSettingsStore.Save(changed);
            ShellSettingsData loaded = ShellSettingsStore.Load();

            Assert.AreEqual(changed.windowMode, loaded.windowMode);
            Assert.AreEqual(changed.hotkey, loaded.hotkey);
            Assert.AreEqual(changed.ambientBehaviors, loaded.ambientBehaviors);
            Assert.AreEqual(changed.showHud, loaded.showHud);
            Assert.AreEqual(changed.targetFps, loaded.targetFps);

            ShellSettingsStore.Save(new ShellSettingsData());   // 还原默认
            ShellSettingsData restored = ShellSettingsStore.Load();
            Assert.AreEqual((int)ShellWindowMode.Auto, restored.windowMode);
        }

        [UnityTest]
        public IEnumerator Session_State_Toggles_And_Notifies()
        {
            var go = new GameObject("ShellTest");
            var cameraGo = new GameObject("Camera");
            Camera camera = cameraGo.AddComponent<Camera>();
            var shell = go.AddComponent<DesktopShellController>();

            int transitions = 0;
            bool lastState = false;
            shell.SessionStateChanged += active =>
            {
                transitions++;
                lastState = active;
            };

            var settings = new ShellSettingsData { windowMode = (int)ShellWindowMode.Normal, hotkey = "Ctrl+Alt+F24" };
            shell.Initialize(settings, camera);

            Assert.IsFalse(shell.IsSessionActive, "初始不应处于会话态");
            Assert.IsFalse(shell.IsDesktopShell, "Editor 内为普通窗口模式");

            shell.EngageSession();
            yield return null;
            Assert.IsTrue(shell.IsSessionActive, "应进入会话态");
            Assert.AreEqual(1, transitions);
            Assert.IsTrue(lastState);

            shell.DisengageSession();
            yield return null;
            Assert.IsFalse(shell.IsSessionActive, "应退出会话态");
            Assert.AreEqual(2, transitions);
            Assert.IsFalse(lastState);

            // 全屏暂停接口在非壁纸模式不应误触发（_hwnd 为 0）
            Assert.IsFalse(shell.RenderPaused, "普通模式下不应暂停渲染");

            Object.Destroy(go);
            Object.Destroy(cameraGo);
            yield return null;
        }

        [Test]
        public void Coverage_HandlesMaximizedBorders_Taskbar_AndOtherMonitors()
        {
            var work = new RectInt(0, 0, 1920, 1040);
            Assert.IsTrue(DesktopOcclusion.CoversWorkArea(new RectInt(-8, -8, 1936, 1056), work));
            Assert.IsTrue(DesktopOcclusion.CoversWorkArea(new RectInt(0, 0, 1920, 1080), work));
            Assert.IsFalse(DesktopOcclusion.CoversWorkArea(new RectInt(0, 0, 960, 1040), work));
            Assert.IsFalse(DesktopOcclusion.CoversWorkArea(new RectInt(1920, 0, 1920, 1080), work));
            Assert.IsFalse(DesktopOcclusion.CoversWorkArea(new RectInt(0, 100, 1920, 1040), work));
            Assert.IsFalse(DesktopOcclusion.CoversWorkArea(work, new RectInt()));
        }

        [UnityTest]
        public IEnumerator CoveredWallpaper_PreservesCanvasAndCameraState_AndIdleCommandsWakeQuickly()
        {
            int fps = Application.targetFrameRate;
            var go = new GameObject("RenderBudgetTest");
            var camera = new GameObject("View").AddComponent<Camera>();
            camera.transform.SetParent(go.transform);
            var canvas = new GameObject("Presentation").AddComponent<Canvas>();
            canvas.transform.SetParent(go.transform);
            var shell = go.AddComponent<DesktopShellController>();
            var flags = BindingFlags.Instance | BindingFlags.NonPublic;
            var pause = typeof(DesktopShellController).GetMethod("SetRenderPaused", flags);
            var last = typeof(DesktopShellController).GetField("_lastActivity", flags);
            try
            {
                shell.Initialize(new ShellSettingsData { targetFps = 30, hotkey = "Ctrl+Alt+F24" }, camera, canvas);
                pause.Invoke(shell, new object[] { true });
                Assert.IsFalse(camera.enabled);
                Assert.IsFalse(canvas.enabled);
                Assert.AreEqual(15, Application.targetFrameRate);
                shell.NotifyActivity();
                Assert.IsTrue(shell.RenderPaused, "Activity must not reveal a covered wallpaper");
                pause.Invoke(shell, new object[] { false });
                Assert.IsTrue(camera.enabled); Assert.IsTrue(canvas.enabled);
                Assert.AreEqual(30, Application.targetFrameRate);
                canvas.enabled = false;
                pause.Invoke(shell, new object[] { true });
                pause.Invoke(shell, new object[] { false });
                Assert.IsFalse(canvas.enabled, "An intentionally hidden canvas must remain hidden");

                typeof(DesktopShellController).GetProperty("IsWallpaperMode").SetValue(shell, true);
                last.SetValue(shell, Time.realtimeSinceStartupAsDouble - 60);
                yield return null;
                Assert.AreEqual(15, Application.targetFrameRate);
                shell.NotifyActivity();
                Assert.AreEqual(30, Application.targetFrameRate);
                last.SetValue(shell, Time.realtimeSinceStartupAsDouble - 60);
                shell.HasVisualActivity = () => true;
                yield return null;
                Assert.AreEqual(30, Application.targetFrameRate, "Walking/chat/door animation must retain active cadence");
            }
            finally { Object.Destroy(go); Application.targetFrameRate = fps; }
        }

        #if UNITY_EDITOR
        [Test]
        public void ChatEntryScene_DoesNotLoadApartmentOrCharacterAssets()
        {
            Assert.IsNotNull(UnityEditor.AssetDatabase.LoadAssetAtPath<UnityEditor.SceneAsset>("Assets/Scenes/ChatWindow.unity"));
            var scenes = UnityEditor.EditorBuildSettings.scenes;
            Assert.AreEqual(3, scenes.Length);
            CollectionAssert.AreEqual(new[] { "Assets/Scenes/PlayerBootstrap.unity", "Assets/Scenes/Apartment.unity",
                "Assets/Scenes/ChatWindow.unity" }, System.Array.ConvertAll(scenes, scene => scene.path));
            Assert.IsTrue(System.Array.TrueForAll(scenes, scene => scene.enabled));
            foreach (string entry in new[] { scenes[0].path, scenes[2].path })
            foreach (string dependency in UnityEditor.AssetDatabase.GetDependencies(entry))
            {
                Assert.IsFalse(dependency.Contains("Art/Characters/") || dependency.Contains("Art/Apartment/")
                    || dependency.Contains("Resources/ApartmentFurniture/")
                    || dependency == "Assets/Scenes/Apartment.unity", "Heavy entry scene dependency: " + dependency);
            }
        }
        #endif

        private static class NativeMods
        {
            public const uint Alt = 0x0001;
            public const uint Control = 0x0002;
            public const uint Shift = 0x0004;
        }
    }
}
