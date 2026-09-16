using System;
using UnityEditor;
using UnityEngine;

namespace AiPeople.EditorTools
{
    /// <summary>
    /// 角色模型导入规范：
    /// - Rig = Humanoid（便于 Mixamo/Unity Humanoid 动作重定向）；
    /// - 导入动画与 BlendShape；按命名把待机/走/跑类动作设为循环；
    /// - 不导入相机/灯光；材质贴图由运行时转换为 URP（CharacterActor.ApplyUrpMaterials）。
    /// </summary>
    public sealed class AiPeopleCharacterModelPostprocessor : AssetPostprocessor
    {
        private const string CharactersRoot = "Assets/Art/Characters/";

        private void OnPreprocessModel()
        {
            if (!assetPath.StartsWith(CharactersRoot, StringComparison.Ordinal))
            {
                return;
            }

            if (!(assetImporter is ModelImporter importer))
            {
                return;
            }

            importer.animationType = ModelImporterAnimationType.Human;
            importer.importAnimation = true;
            importer.importBlendShapes = true;
            importer.importCameras = false;
            importer.importLights = false;

            Debug.Log($"[AiPeople] 角色模型导入设置：{assetPath} → Rig=Humanoid, 导入动画=是");
        }

        private void OnPreprocessAnimation()
        {
            if (!assetPath.StartsWith(CharactersRoot, StringComparison.Ordinal))
            {
                return;
            }

            if (!(assetImporter is ModelImporter importer))
            {
                return;
            }

            ModelImporterClipAnimation[] clips = importer.defaultClipAnimations;
            if (clips == null || clips.Length == 0)
            {
                return;
            }

            foreach (ModelImporterClipAnimation clip in clips)
            {
                string name = clip.name.ToLowerInvariant();
                clip.loopTime = name.Contains("idle") || name.Contains("walk") || name.Contains("run")
                    || name.Contains("待机") || name.Contains("走") || name.Contains("跑");
            }

            importer.clipAnimations = clips;
            Debug.Log($"[AiPeople] 动画片段设置：{assetPath} → {clips.Length} 个片段（待机/走/跑设为循环）");
        }
    }
}
