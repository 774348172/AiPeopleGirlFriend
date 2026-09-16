using AiPeople.Core;
using UnityEngine;

namespace AiPeople.Character
{
    /// <summary>
    /// 无骨骼角色的安全表现层：只对模型局部变换做极小幅度呼吸和重心变化。
    /// 有正式 Animator 时仍保留轻微根部呼吸；没有动画资产时作为降级表现。
    /// </summary>
    public sealed class CharacterPresentation : MonoBehaviour
    {
        public float breathingAmplitude = 0.006f;
        public float breathingFrequency = 0.72f;
        public float swayAmplitude = 0.45f;

        private Transform _model;
        private Vector3 _basePosition;
        private Vector3 _baseScale;
        private bool _walking;
        private float _moodScale = 1f;

        public void Bind(Transform model)
        {
            _model = model;
            if (_model != null)
            {
                _basePosition = _model.localPosition;
                _baseScale = _model.localScale;
            }
        }

        public void SetWalking(bool walking) => _walking = walking;

        public void ApplyMind(LivingMindView mind)
        {
            string text = (mind?.emotion ?? string.Empty) + " " + (mind?.attention ?? string.Empty);
            _moodScale = text.Contains("紧张") || text.Contains("警觉") ? 1.25f
                : (text.Contains("疲惫") || text.Contains("低落") ? .72f : 1f);
        }

        private void LateUpdate()
        {
            if (_model == null) return;

            float t = Time.time * breathingFrequency * Mathf.PI * 2f;
            float breath = Mathf.Sin(t) * breathingAmplitude * _moodScale;
            float sway = _walking ? 0f : Mathf.Sin(t * .5f + .7f) * swayAmplitude * _moodScale;
            _model.localPosition = _basePosition + new Vector3(0f, breath, 0f);
            _model.localScale = _baseScale * (1f + breath * .35f);
            _model.localRotation = Quaternion.Euler(0f, 0f, sway);
        }
    }
}
