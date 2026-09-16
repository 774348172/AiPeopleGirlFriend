using System.Collections.Generic;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

namespace AiPeople.EditorTools
{
    /// <summary>
    /// 从女主模型中的动画片段生成 AnimatorController（待机类片段设为默认状态），供运行时挂载。
    /// 已存在 Controller 时直接复用，不覆盖手工调整。
    /// </summary>
    public static class AiPeopleAnimatorBuilder
    {
        public const string ControllerPath = "Assets/Art/Characters/baiweixi/BaiWeixiAnimator.controller";

        [MenuItem("AiPeople/生成女主 Animator Controller")]
        public static void BuildFromMenu()
        {
            RuntimeAnimatorController controller = EnsureController();
            Debug.Log("[AiPeople] Animator Controller：" + (controller != null ? controller.name : "未生成"));
        }

        public static RuntimeAnimatorController EnsureController()
        {
            var existing = AssetDatabase.LoadAssetAtPath<AnimatorController>(ControllerPath);
            if (existing != null)
            {
                return existing;
            }

            List<AnimationClip> clips = LoadClips();
            if (clips.Count == 0)
            {
                Debug.LogWarning("[AiPeople] 模型没有动画片段，跳过 Animator Controller 生成");
                return null;
            }

            AnimatorController controller = AnimatorController.CreateAnimatorControllerAtPath(ControllerPath);
            AnimatorStateMachine stateMachine = controller.layers[0].stateMachine;
            AnimatorState defaultState = null;
            var usedNames = new HashSet<string>();
            foreach (AnimationClip clip in clips)
            {
                string stateName = FriendlyStateName(clip.name);
                if (!usedNames.Add(stateName))
                {
                    stateName = stateName + "_" + usedNames.Count;
                    usedNames.Add(stateName);
                }

                AnimatorState state = stateMachine.AddState(stateName);
                state.motion = clip;
                if (stateName == "Idle" && defaultState == null)
                {
                    defaultState = state;
                }
            }

            stateMachine.defaultState = defaultState ?? stateMachine.states[0].state;
            AssetDatabase.SaveAssets();
            Debug.Log(
                $"[AiPeople] Animator Controller 生成：{ControllerPath}，默认状态={stateMachine.defaultState.name}，片段={clips.Count}");
            return controller;
        }

        /// <summary>把 Tripo 的片段名（如 preset:biped:idle.001）映射为运行时可用的稳定状态名。</summary>
        private static string FriendlyStateName(string clipName)
        {
            string lower = clipName.ToLowerInvariant();
            if (lower.Contains("idle") || lower.Contains("待机"))
            {
                return "Idle";
            }

            if (lower.Contains("walk") || lower.Contains("走"))
            {
                return "Walk";
            }

            if (lower.Contains("run") || lower.Contains("跑"))
            {
                return "Run";
            }

            if (lower.Contains("afraid") || lower.Contains("fear") || lower.Contains("害怕"))
            {
                return "Afraid";
            }

            if (lower.Contains("sing") || lower.Contains("唱"))
            {
                return "Sing";
            }

            if (lower.Contains("sit") || lower.Contains("坐"))
            {
                return "Sit";
            }

            return clipName;
        }

        public static List<AnimationClip> LoadClips()
        {
            var clips = new List<AnimationClip>();
            foreach (Object asset in AssetDatabase.LoadAllAssetsAtPath(AiPeopleModelProbe.BaiweixiModelPath))
            {
                if (asset is AnimationClip clip && !clip.name.StartsWith("__preview__"))
                {
                    clips.Add(clip);
                }
            }

            return clips;
        }
    }
}