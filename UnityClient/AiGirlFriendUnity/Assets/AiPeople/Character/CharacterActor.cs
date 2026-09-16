using System.Collections.Generic;
using AiPeople.Core;
using UnityEngine;

namespace AiPeople.Character
{
    /// <summary>
    /// 白未晞表现：优先使用正式模型（静态网格，自动按身高缩放、落地、贴图转 URP 材质）；
    /// 模型缺失时降级为胶囊占位 + 名牌。
    /// 当前模型无骨骼与动画（D3 资产路线未定），因此不做任何伪造动画。
    /// </summary>
    public sealed class CharacterActor : MonoBehaviour
    {
        public const float DefaultHeight = 1.62f;

        private static readonly int BaseMapId = Shader.PropertyToID("_BaseMap");
        private static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");
        private static readonly int ColorId = Shader.PropertyToID("_Color");
        private static readonly int MainTexId = Shader.PropertyToID("_MainTex");
        private static readonly int SmoothnessId = Shader.PropertyToID("_Smoothness");

        private bool _hasModel;

        public bool HasModel => _hasModel;

        /// <summary>模型上的 Animator（无模型/无 Animator 时为 null）。</summary>
        public Animator Animator { get; private set; }

        public static CharacterActor Create(
            Vector3 position,
            float yaw,
            GameObject modelPrefab,
            RuntimeAnimatorController animatorController = null,
            float targetHeight = DefaultHeight)
        {
            var root = new GameObject("BaiWeixi");
            root.transform.position = position;
            root.transform.rotation = Quaternion.Euler(0f, yaw, 0f);
            CharacterActor actor = root.AddComponent<CharacterActor>();
            actor.Build(modelPrefab, animatorController, targetHeight);
            return actor;
        }

        private void Build(GameObject modelPrefab, RuntimeAnimatorController animatorController, float targetHeight)
        {
            if (modelPrefab != null)
            {
                GameObject model = Instantiate(modelPrefab, transform, false);
                model.name = "Model";
                FitInstanceToHeight(model, targetHeight);
                ApplyUrpMaterials(model);

                Animator animator = model.GetComponentInChildren<Animator>();
                if (animator != null && animatorController != null)
                {
                    animator.runtimeAnimatorController = animatorController;
                }

                Animator = animator;
                _hasModel = true;
                var presentation = gameObject.AddComponent<CharacterPresentation>();
                presentation.Bind(model.transform);
                return;
            }

            GameObject capsule = GameObject.CreatePrimitive(PrimitiveType.Capsule);
            capsule.name = "PlaceholderBody";
            capsule.transform.SetParent(transform, false);
            capsule.transform.localPosition = new Vector3(0f, 0.85f, 0f);
            capsule.transform.localScale = new Vector3(0.5f, 0.85f, 0.5f);
            capsule.GetComponent<MeshRenderer>().sharedMaterial =
                MaterialLibrary.Get("HeroinePlaceholder", new Color(0.88f, 0.89f, 0.95f));

            BuildPlaceholderLabel(targetHeight);
            var placeholderPresentation = gameObject.AddComponent<CharacterPresentation>();
            placeholderPresentation.Bind(capsule.transform);
        }

        private void BuildPlaceholderLabel(float targetHeight)
        {
            var labelGo = new GameObject("NameLabel");
            labelGo.transform.SetParent(transform, false);
            labelGo.transform.localPosition = new Vector3(0f, targetHeight + 0.3f, 0f);
            var textMesh = labelGo.AddComponent<TextMesh>();
            textMesh.text = "白未晞（占位）";
            textMesh.characterSize = 0.055f;
            textMesh.fontSize = 64;
            textMesh.anchor = TextAnchor.MiddleCenter;
            textMesh.alignment = TextAlignment.Center;
            textMesh.color = new Color(0.92f, 0.94f, 1f);
            Font font = FontLibrary.Get();
            if (font != null)
            {
                textMesh.font = font;
                labelGo.GetComponent<MeshRenderer>().sharedMaterial = font.material;
            }
        }

        private static Bounds MeasureBounds(GameObject instance)
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

        /// <summary>按目标身高缩放模型并使其底部落地（y=0）。返回是否成功拟合。</summary>
        public static bool FitInstanceToHeight(GameObject instance, float targetHeight)
        {
            Bounds bounds = MeasureBounds(instance);
            if (bounds.size.y <= 0.0001f)
            {
                return false;
            }

            float scale = targetHeight / bounds.size.y;
            instance.transform.localScale = Vector3.one * scale;
            instance.transform.localPosition = new Vector3(0f, -bounds.min.y * scale, 0f);
            return true;
        }

        /// <summary>导入的 FBX 材质为内置管线，URP 下会显示为粉色；此处按原贴图重建 URP Lit 材质。</summary>
        public static void ApplyUrpMaterials(GameObject model)
        {
            var cache = new Dictionary<Texture, Material>();
            foreach (Renderer renderer in model.GetComponentsInChildren<Renderer>(true))
            {
                Material[] sources = renderer.sharedMaterials;
                var converted = new Material[sources.Length];
                bool changed = false;
                for (int i = 0; i < sources.Length; i++)
                {
                    Material urp = ConvertMaterial(sources[i], cache);
                    converted[i] = urp;
                    if (urp != sources[i])
                    {
                        changed = true;
                    }
                }

                if (changed)
                {
                    renderer.sharedMaterials = converted;
                }
            }
        }

        private static Material ConvertMaterial(Material source, Dictionary<Texture, Material> cache)
        {
            Texture texture = source != null ? source.mainTexture : null;
            if (texture != null && cache.TryGetValue(texture, out Material cached))
            {
                return cached;
            }

            Shader shader = Shader.Find("Universal Render Pipeline/Lit");
            if (shader == null)
            {
                shader = Shader.Find("Standard");
            }

            var material = new Material(shader)
            {
                name = "AiPeople/BaiWeixi" + (texture != null ? "_" + texture.name : string.Empty),
            };

            if (texture != null)
            {
                if (material.HasProperty(BaseMapId))
                {
                    material.SetTexture(BaseMapId, texture);
                }

                if (material.HasProperty(MainTexId))
                {
                    material.SetTexture(MainTexId, texture);
                }
            }

            Color tint = source != null && source.HasProperty(ColorId) ? source.color : Color.white;
            if (material.HasProperty(BaseColorId))
            {
                material.SetColor(BaseColorId, tint);
            }

            if (material.HasProperty(ColorId))
            {
                material.SetColor(ColorId, tint);
            }

            if (material.HasProperty(SmoothnessId))
            {
                material.SetFloat(SmoothnessId, 0.08f);
            }

            if (texture != null)
            {
                cache[texture] = material;
            }

            return material;
        }
    }
}
