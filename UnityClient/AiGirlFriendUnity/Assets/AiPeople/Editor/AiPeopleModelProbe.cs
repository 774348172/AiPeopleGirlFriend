using AiPeople.Character;
using UnityEditor;
using UnityEngine;

namespace AiPeople.EditorTools
{
    /// <summary>
    /// 女主模型探测：导入后校验资源可用性，并实测"缩放落地 + URP 材质转换"结果（批处理可调用）。
    /// </summary>
    public static class AiPeopleModelProbe
    {
        public const string BaiweixiModelPath =
            "Assets/Art/Characters/baiweixi/tripo_convert_f38d68cf-bfbe-4c94-a761-8782c849031a.fbx";

        [MenuItem("AiPeople/探测女主模型")]
        public static void Probe()
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(BaiweixiModelPath);
            if (prefab == null)
            {
                Debug.LogError("[AiPeople] 女主模型未找到：" + BaiweixiModelPath);
                return;
            }

            GameObject instance = Object.Instantiate(prefab);
            instance.transform.position = Vector3.zero;

            Renderer[] renderers = instance.GetComponentsInChildren<Renderer>(true);
            Bounds before = Measure(instance);
            int materialCount = 0;
            int texturedCount = 0;
            foreach (Renderer renderer in renderers)
            {
                foreach (Material material in renderer.sharedMaterials)
                {
                    if (material == null)
                    {
                        continue;
                    }

                    materialCount++;
                    if (material.mainTexture != null)
                    {
                        texturedCount++;
                    }
                }
            }

            float targetHeight = CharacterActor.DefaultHeight;
            bool fitted = CharacterActor.FitInstanceToHeight(instance, targetHeight);
            Bounds after = Measure(instance);
            CharacterActor.ApplyUrpMaterials(instance);

            string shaderName = "无";
            if (renderers.Length > 0 && renderers[0].sharedMaterials.Length > 0 && renderers[0].sharedMaterials[0] != null)
            {
                shaderName = renderers[0].sharedMaterials[0].shader != null
                    ? renderers[0].sharedMaterials[0].shader.name
                    : "null";
            }

            int skinnedCount = instance.GetComponentsInChildren<SkinnedMeshRenderer>(true).Length;
            int transformCount = instance.GetComponentsInChildren<Transform>(true).Length;
            Animator animator = instance.GetComponentInChildren<Animator>();
            string avatarInfo = animator == null || animator.avatar == null
                ? "无 Avatar"
                : $"Avatar(isValid={animator.avatar.isValid}, isHuman={animator.avatar.isHuman})";
            var clipNames = new System.Collections.Generic.List<string>();
            foreach (Object asset in AssetDatabase.LoadAllAssetsAtPath(BaiweixiModelPath))
            {
                if (asset is AnimationClip clip && !clip.name.StartsWith("__preview__"))
                {
                    clipNames.Add(clip.name);
                }
            }

            Debug.Log(
                "[AiPeople] 女主模型探测：" +
                $"renderers={renderers.Length} 蒙皮网格={skinnedCount} 节点数={transformCount} " +
                $"materials={materialCount} 带贴图={texturedCount} " +
                $"原始尺寸=({before.size.x:F3},{before.size.y:F3},{before.size.z:F3}) 原始底部Y={before.min.y:F3} " +
                $"拟合={fitted} 拟合后身高={after.size.y:F3}m 拟合后底部Y={after.min.y:F3} " +
                $"转换后材质shader={shaderName} {avatarInfo} 动画片段={clipNames.Count}[{string.Join(", ", clipNames)}]");

            Object.DestroyImmediate(instance);
        }

        private static Bounds Measure(GameObject instance)
        {
            Renderer[] renderers = instance.GetComponentsInChildren<Renderer>(true);
            if (renderers.Length == 0)
            {
                return new Bounds(Vector3.zero, Vector3.zero);
            }

            Bounds bounds = renderers[0].bounds;
            for (int i = 1; i < renderers.Length; i++)
            {
                bounds.Encapsulate(renderers[i].bounds);
            }

            return bounds;
        }
    }
}
