using System;
using System.Collections.Generic;
using UnityEngine;

namespace AiPeople.Core
{
    /// <summary>
    /// 运行时材质工具：优先 URP Lit，退回内置 Standard（blockout 用）。
    /// </summary>
    public static class MaterialLibrary
    {
        private static readonly Dictionary<string, Material> Cache = new Dictionary<string, Material>();

        public static Material Get(string key, Color color)
        {
            if (Cache.TryGetValue(key, out Material cached) && cached != null)
            {
                return cached;
            }

            Shader shader = Shader.Find("Universal Render Pipeline/Lit");
            if (shader == null)
            {
                shader = Shader.Find("Standard");
            }

            if (shader == null)
            {
                throw new InvalidOperationException("AiPeople 材质 Shader 不可用；请确保 URP/Lit 已加入构建资源。");
            }

            var material = new Material(shader) { name = "AiPeople/" + key };
            if (material.HasProperty("_BaseColor"))
            {
                material.SetColor("_BaseColor", color);
            }

            if (material.HasProperty("_Color"))
            {
                material.SetColor("_Color", color);
            }

            if (material.HasProperty("_Smoothness"))
            {
                material.SetFloat("_Smoothness", 0.12f);
            }

            Cache[key] = material;
            return material;
        }
    }
}
