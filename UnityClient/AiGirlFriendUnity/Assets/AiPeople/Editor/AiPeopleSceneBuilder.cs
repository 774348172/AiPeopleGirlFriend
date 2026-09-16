using System.IO;
using AiPeople.App;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace AiPeople.EditorTools
{
    /// <summary>
    /// 场景构建器：生成最小的 Apartment 场景（仅挂 AiPeopleApp 的 GameObject），
    /// 房间与 UI 全部由运行时代码构建（见 World/ApartmentBuilder、App/AiPeopleApp）。
    /// 菜单：AiPeople/重建 Apartment 场景；批处理：-executeMethod AiPeople.EditorTools.AiPeopleSceneBuilder.BuildApartmentScene
    /// </summary>
    public static class AiPeopleSceneBuilder
    {
        private const string ScenePath = "Assets/Scenes/Apartment.unity";

        /// <summary>Use saved entry scenes without replacing the editor's open scene or user edits.</summary>
        public static string[] GetPlayerEntryScenes()
        {
            var paths = new[] { "Assets/Scenes/PlayerBootstrap.unity", ScenePath, "Assets/Scenes/ChatWindow.unity" };
            foreach (string path in paths)
                if (AssetDatabase.LoadAssetAtPath<SceneAsset>(path) == null)
                    throw new System.InvalidOperationException("Missing player scene: " + path);
            foreach (string path in new[] { paths[0], paths[2] })
                foreach (string dependency in AssetDatabase.GetDependencies(path))
                    if (dependency.Contains("Art/Apartment/") || dependency.Contains("Art/Characters/")
                        || dependency.Contains("Resources/ApartmentFurniture/") || dependency == ScenePath)
                        throw new System.InvalidOperationException("Heavy entry scene dependency: " + path + " -> " + dependency);
            EditorBuildSettings.scenes = System.Array.ConvertAll(paths, path => new EditorBuildSettingsScene(path, true));
            return paths;
        }

        public static string[] BuildPlayerEntryScenes()
        {
            var boot = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            new GameObject("PlayerBootstrap").AddComponent<PlayerBootstrap>();
            if (!EditorSceneManager.SaveScene(boot, "Assets/Scenes/PlayerBootstrap.unity"))
                throw new System.Exception("Could not save player entry scene");
            var chat = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            new GameObject("ChatWindowApp").AddComponent<AiPeople.UI.ChatWindowApp>();
            if (!EditorSceneManager.SaveScene(chat, "Assets/Scenes/ChatWindow.unity"))
                throw new System.Exception("Could not save lightweight chat scene");
            return GetPlayerEntryScenes();
        }

        [MenuItem("AiPeople/重建 Apartment 场景")]
        public static void BuildApartmentScene()
        {
            AiPeopleModelProbe.Probe();

            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

            var appGo = new GameObject("AiPeopleApp");
            var app = appGo.AddComponent<AiPeopleApp>();
            ApartmentShellImporter.ConfigureApp(app);

            var modelPrefab = AssetDatabase.LoadAssetAtPath<GameObject>(AiPeopleModelProbe.BaiweixiModelPath);
            if (modelPrefab == null)
            {
                Debug.LogWarning("[AiPeople] 未找到女主模型，运行时将使用胶囊占位：" + AiPeopleModelProbe.BaiweixiModelPath);
            }
            else
            {
                app.heroineModelPrefab = modelPrefab;
                app.heroineAnimatorController = AiPeopleAnimatorBuilder.EnsureController();
            }

            Directory.CreateDirectory("Assets/Scenes");
            EditorSceneManager.MarkSceneDirty(scene);
            bool saved = EditorSceneManager.SaveScene(scene, ScenePath);
            GetPlayerEntryScenes();
            AssetDatabase.SaveAssets();

            Debug.Log(saved
                ? "[AiPeople] Apartment 场景已生成：" + ScenePath
                : "[AiPeople] 场景保存失败：" + ScenePath);
        }
    }
}
