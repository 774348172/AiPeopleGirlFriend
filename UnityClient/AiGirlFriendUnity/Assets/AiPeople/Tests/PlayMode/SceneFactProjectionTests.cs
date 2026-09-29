#if UNITY_EDITOR
using System.Collections;
using System.Linq;
using AiPeople.Core;
using AiPeople.World;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace AiPeople.Tests
{
    public sealed class SceneFactProjectionTests
    {
        [TestCase(true, false, true, false, "已打开")]
        [TestCase(false, false, false, false, "已关闭")]
        [TestCase(true, true, false, false, "正在关闭，尚未完成")]
        [TestCase(false, true, true, false, "正在打开，尚未完成")]
        [TestCase(true, true, false, true, "关闭受阻，尚未完成")]
        [TestCase(false, true, true, true, "打开受阻，尚未完成")]
        public void DoorFactsDoNotClaimTargetAlreadyReached(bool open, bool moving, bool target,
            bool blocked, string expected)
        {
            Assert.AreEqual(expected, SceneFactProjection.DoorState(open, moving, target, blocked));
        }

        [UnityTest]
        public IEnumerator ImportedDoorChangesReplaceFactsAndSerializeToWire()
        {
            var world = ApartmentBuilder.Build(AssetDatabase.LoadAssetAtPath<GameObject>(
                "Assets/Art/Apartment/ImportedShell/ApartmentShell.prefab"));
            try
            {
                var nav = world.Root.GetComponentInChildren<ApartmentNavigation>();
                var atmosphere = world.Root.gameObject.AddComponent<SceneAtmosphere>();
                atmosphere.Bind(world.Sun, world.Lamp, world.WindowRenderer, world.Rain);
                var door = nav.Doors.Single(d => d.doorId == "KitchenDoor");
                var before = SceneFactProjection.Capture(nav, atmosphere, false, null);
                Assert.AreEqual(7, before.Length); // Six real doors/windows and one lamp.
                Assert.IsFalse(before.Any(f => f.label == "客厅窗户"));
                Assert.IsTrue(door.RequestState(false, out var reason), reason);
                var moving = SceneFactProjection.Capture(nav, atmosphere, false, null);
                Assert.AreEqual("正在关闭，尚未完成", moving.Single(f => f.label == door.displayName).value);
                float deadline = Time.realtimeSinceStartup + 4;
                while (door.IsMoving && Time.realtimeSinceStartup < deadline) yield return null;
                Assert.IsFalse(door.IsMoving);
                atmosphere.ToggleLight();
                var after = SceneFactProjection.Capture(nav, atmosphere, false, null);
                Assert.AreEqual("已关闭", after.Single(f => f.label == door.displayName).value);
                Assert.AreEqual("灯关着", after.Single(f => f.label == "室内灯光").value);
                Assert.AreEqual("已打开", before.Single(f => f.label == door.displayName).value);
                var wire = JsonUtility.ToJson(new WorldInputData { scene_facts = after });
                var restored = JsonUtility.FromJson<WorldInputData>(wire);
                Assert.AreEqual(after.Length, restored.scene_facts.Length);
                var observations = SceneFactProjection.CaptureObservations(nav, atmosphere,
                    false, null, 12, 123456);
                var kitchen = observations.Single(f => f.key == "door_kitchendoor");
                Assert.AreEqual("已关闭", kitchen.value);
                Assert.AreEqual("current", kitchen.state);
                Assert.AreEqual(12, kitchen.version);
                Assert.AreEqual("unknown", observations.Single(f => f.key == "living_room_window").state);
                var snapshot = new GameSnapshotData { sequence = 12, observed_at_ms = 123456,
                    facts = observations };
                var restoredSnapshot = JsonUtility.FromJson<GameSnapshotData>(JsonUtility.ToJson(snapshot));
                Assert.AreEqual(5, restoredSnapshot.schema_version);
                Assert.AreEqual(123456, restoredSnapshot.facts[0].observed_at_ms);
            }
            finally { Object.DestroyImmediate(world.Root.gameObject); }
        }

        [Test]
        public void HeldItemSurvivesMissingWaypointAndClearsOnPutDown()
        {
            var go = new GameObject("FactPlayer");
            try
            {
                var player = go.AddComponent<PlayerController>();
                player.SetHeldItem("一杯温水");
                Assert.AreEqual("一杯温水", player.ComposeWorldInput().held_item);
                player.SetHeldItem("");
                Assert.AreEqual("", player.ComposeWorldInput().held_item);
            }
            finally { Object.DestroyImmediate(go); }
        }
    }
}
#endif
