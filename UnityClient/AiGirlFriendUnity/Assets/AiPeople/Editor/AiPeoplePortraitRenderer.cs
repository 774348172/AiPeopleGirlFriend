using System.IO;
using AiPeople.Character;
using UnityEditor;
using UnityEngine;

namespace AiPeople.EditorTools
{
    /// <summary>
    /// 用女主模型离屏渲染一张半身像 PNG，作为对话小窗的临时立绘（D12）。
    /// 批处理：-executeMethod AiPeople.EditorTools.AiPeoplePortraitRenderer.RenderPortrait
    /// 输出：Assets/StreamingAssets/portrait.png（512×768，透明背景；正式立绘到位后直接替换该文件）
    /// </summary>
    public static class AiPeoplePortraitRenderer
    {
        private const int Width = 512;
        private const int Height = 768;
        private const string OutputRelativePath = "StreamingAssets/portrait.png";

        [MenuItem("AiPeople/渲染女主临时立绘")]
        public static void RenderPortrait()
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(AiPeopleModelProbe.BaiweixiModelPath);
            if (prefab == null)
            {
                Debug.LogError("[AiPeople] 立绘渲染失败：模型未找到 " + AiPeopleModelProbe.BaiweixiModelPath);
                return;
            }

            GameObject instance = Object.Instantiate(prefab);
            instance.transform.position = Vector3.zero;
            CharacterActor.FitInstanceToHeight(instance, CharacterActor.DefaultHeight);
            CharacterActor.ApplyUrpMaterials(instance);

            var cameraGo = new GameObject("PortraitCamera");
            var camera = cameraGo.AddComponent<Camera>();
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0f, 0f, 0f, 0f);
            camera.orthographic = true;
            camera.orthographicSize = 0.55f;
            camera.transform.position = new Vector3(0f, 1.25f, 1.7f);
            camera.transform.rotation = Quaternion.Euler(0f, 180f, 0f);
            camera.nearClipPlane = 0.05f;
            camera.farClipPlane = 10f;
            camera.targetTexture = new RenderTexture(Width, Height, 24, RenderTextureFormat.ARGB32);

            var keyGo = new GameObject("PortraitKey");
            var key = keyGo.AddComponent<Light>();
            key.type = LightType.Directional;
            key.intensity = 1.15f;
            keyGo.transform.rotation = Quaternion.Euler(30f, 200f, 0f);

            var fillGo = new GameObject("PortraitFill");
            var fill = fillGo.AddComponent<Light>();
            fill.type = LightType.Directional;
            fill.intensity = 0.55f;
            fillGo.transform.rotation = Quaternion.Euler(12f, 20f, 0f);

            var renderTexture = (RenderTexture)camera.targetTexture;
            RenderTexture previousActive = RenderTexture.active;
            camera.Render();

            RenderTexture.active = renderTexture;
            var texture = new Texture2D(Width, Height, TextureFormat.RGBA32, false);
            texture.ReadPixels(new Rect(0f, 0f, Width, Height), 0, 0);
            texture.Apply();
            RenderTexture.active = previousActive;

            byte[] png = texture.EncodeToPNG();
            string outputPath = Path.Combine(Application.dataPath, OutputRelativePath);
            Directory.CreateDirectory(Path.GetDirectoryName(outputPath) ?? Application.dataPath);
            File.WriteAllBytes(outputPath, png);
            AssetDatabase.Refresh();

            Debug.Log("[AiPeople] 临时立绘已渲染：" + outputPath + "（" + (png.Length / 1024) + "KB，"
                + Width + "×" + Height + "）");

            Object.DestroyImmediate(texture);
            renderTexture.Release();
            Object.DestroyImmediate(renderTexture);
            Object.DestroyImmediate(cameraGo);
            Object.DestroyImmediate(keyGo);
            Object.DestroyImmediate(fillGo);
            Object.DestroyImmediate(instance);
        }
    }
}
