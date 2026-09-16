#if UNITY_EDITOR
using System.Collections;
using AiPeople.Core;
using AiPeople.World;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AiPeople.Tests
{
    public sealed class SceneAtmosphereTests
    {
        private GameObject _root;
        private SceneAtmosphere _atmosphere;

        [UnitySetUp]
        public IEnumerator SetUp()
        {
            _root = new GameObject("SceneAtmosphereTest");
            _atmosphere = _root.AddComponent<SceneAtmosphere>();
            var sun = new GameObject("Sun").AddComponent<Light>();
            var lamp = new GameObject("Lamp").AddComponent<Light>();
            var window = GameObject.CreatePrimitive(PrimitiveType.Quad).GetComponent<Renderer>();
            var rain = new GameObject("Rain").AddComponent<ParticleSystem>();
            _atmosphere.Bind(sun, lamp, window, rain);
            yield return null;
        }

        [TearDown]
        public void TearDown()
        {
            if (_root != null) Object.DestroyImmediate(_root);
            RenderSettings.fog = false;
        }

        [Test]
        public void StatusTimeAndWeather_ProjectToPresentationOnly()
        {
            var status = new StatusResponse
            {
                game_time = "第18日 22:10:00",
                world = new WorldStateView { scene = "窗外下着雨，屋里亮着暖灯" }
            };

            _atmosphere.ApplyStatus(status);

            Assert.IsTrue(RenderSettings.fog, "Night/rain should enable restrained atmospheric fog");
            Assert.That(RenderSettings.fogDensity, Is.EqualTo(.006f).Within(.0001f));
            Assert.IsTrue(_atmosphere.LightOn);
        }

        [Test]
        public void InvalidTimeKeepsLastPresentationValue()
        {
            var valid = new StatusResponse
            {
                game_time = "第18日 12:00:00",
                world = new WorldStateView { scene = "窗外晴" }
            };
            _atmosphere.ApplyStatus(valid);
            Color before = RenderSettings.ambientLight;

            _atmosphere.ApplyStatus(new StatusResponse
            {
                game_time = "unknown",
                world = new WorldStateView { scene = "窗外晴" }
            });

            Assert.That(RenderSettings.ambientLight.r, Is.EqualTo(before.r).Within(.001f));
            Assert.That(RenderSettings.ambientLight.g, Is.EqualTo(before.g).Within(.001f));
            Assert.That(RenderSettings.ambientLight.b, Is.EqualTo(before.b).Within(.001f));
        }
    }
}
#endif
