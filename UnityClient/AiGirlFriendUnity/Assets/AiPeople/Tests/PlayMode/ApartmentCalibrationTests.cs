#if UNITY_EDITOR
using System.Collections;
using AiPeople.Character;
using AiPeople.World;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace AiPeople.Tests
{
    public sealed class ApartmentCalibrationTests
    {
        private ApartmentBuilder.Result _world;

        [UnitySetUp]
        public IEnumerator SetUp()
        {
            _world = ApartmentBuilder.Build(AssetDatabase.LoadAssetAtPath<GameObject>(
                "Assets/Art/Apartment/ImportedShell/ApartmentShell.prefab"));
            yield return null;
            Physics.SyncTransforms();
        }

        [Test]
        public void CalibratedHouse_HasHumanSizedDoors_HeadLevelCollision_AndMetreSizedRail()
        {
            var shell = _world.Root.Find("Structure/ImportedApartmentShell");
            var geometry = shell.Find("ShellGeometry");
            Assert.That(geometry.GetComponent<Renderer>().bounds.max.y, Is.InRange(2.68f, 2.75f));
            var doors = shell.GetComponentsInChildren<ApartmentDoor>();
            Assert.AreEqual(6, doors.Length);
            foreach (var door in doors)
            {
                var visible = door.leaf.GetComponent<Renderer>().bounds;
                var collider = door.leaf.GetComponent<MeshCollider>();
                Assert.That(visible.max.y, Is.EqualTo(2.1f).Within(.002f), door.doorId);
                Assert.That(visible.size.y, Is.InRange(2f, 2.03f), door.doorId);
                Assert.Less(Mathf.Abs(visible.max.y - collider.bounds.max.y), .03f, door.doorId);
                var bounds = door.leaf.GetComponent<MeshFilter>().sharedMesh.bounds;
                var origin = door.leaf.TransformPoint(new Vector3(bounds.center.x, 1.85f, bounds.min.z - 1));
                Assert.IsTrue(collider.Raycast(new Ray(origin, door.leaf.forward), out _, 2),
                    door.doorId + " must physically block above the old undersized door top");
            }
            Assert.IsTrue(Physics.Raycast(new Vector3(0, .5f, -5.8f), Vector3.down, out var floor, 1));
            float railTop = float.MinValue;
            foreach (var local in geometry.GetComponent<MeshFilter>().sharedMesh.vertices)
            {
                var p = geometry.TransformPoint(local);
                if (p.z < -6.25f && Mathf.Abs(p.x) < 1) railTop = Mathf.Max(railTop, p.y);
            }
            Assert.That(railTop - floor.point.y, Is.InRange(1.05f, 1.2f));
            Assert.That(shell.Find("BalconyFrontGuard").GetComponent<Collider>().bounds.max.y,
                Is.EqualTo(railTop).Within(.04f), "Invisible guard must match the visible rail");
        }

        [UnityTest]
        public IEnumerator Box_SeatedAndStandingViews_ReachActualEntrance_WithDoorsOpenAndClosed()
        {
            Assert.That(_world.CardboardBox.transform.position.z, Is.InRange(-4.5f, -3.4f));
            var doors = _world.Root.GetComponentsInChildren<ApartmentDoor>();
            for (int state = 0; state < 2; state++)
            {
                foreach (float height in new[] { .95f, 1.5f })
                {
                    var hall = ImportedApartmentLayout.EntryPosition + Vector3.up * height;
                    Assert.IsTrue(Physics.Raycast(hall, Vector3.forward, out var exit, 4));
                    Assert.That(exit.point.z, Is.InRange(5.8f, 6.2f), "Sight must reach the entrance surface");
                    var box = _world.CardboardBox.transform.position;
                    box.y = height;
                    var delta = exit.point - box;
                    bool blocked = Physics.Raycast(box, delta.normalized, out var hit, delta.magnitude - .04f,
                        ~0, QueryTriggerInteraction.Ignore);
                    Assert.IsFalse(blocked, $"Box sight blocked by {hit.collider?.name} at {hit.point}, height {height}");
                }
                if (state == 0)
                {
                    foreach (var door in doors) Assert.IsTrue(door.RequestState(false, out var reason), reason);
                    float deadline = Time.realtimeSinceStartup + 3;
                    while (System.Array.Exists(doors, d => d.IsMoving) && Time.realtimeSinceStartup < deadline)
                        yield return null;
                    foreach (var door in doors) Assert.IsFalse(door.IsOpen || door.IsMoving);
                }
            }
        }

        [UnityTest]
        public IEnumerator WallpaperCamera_FramesActualCharacter_AndAdaptsToDesktopAspects()
        {
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(AssetDatabase.GUIDToAssetPath(
                "e9619b8a8e02bf747bb2ec868c3e41f0"));
            Assert.IsNotNull(model);
            var actor = CharacterActor.Create(ImportedApartmentLayout.HeroinePosition, ImportedApartmentLayout.HeroineYaw, model);
            actor.transform.SetParent(_world.Root);
            var camera = new GameObject("WallpaperCameraProbe").AddComponent<Camera>();
            camera.transform.SetParent(_world.Root);
            camera.transform.position = ImportedApartmentLayout.CameraPosition;
            camera.transform.LookAt(ImportedApartmentLayout.CameraTarget);
            camera.gameObject.AddComponent<ApartmentWallpaperCamera>();
            var rt = new RenderTexture(640, 360, 16);
            camera.targetTexture = rt;
            try
            {
                foreach (float aspect in new[] { 16f / 9f, 16f / 10f, 21f / 9f })
                {
                    camera.aspect = aspect;
                    camera.Render(); // Exercises the runtime component after a display-aspect change.
                    Assert.That(camera.fieldOfView, Is.EqualTo(ImportedApartmentLayout.CameraFieldOfView(aspect)).Within(.01f));
                    float minY = 1, maxY = 0;
                    foreach (var renderer in actor.GetComponentsInChildren<Renderer>())
                    {
                        var bounds = renderer.bounds;
                        for (int corner = 0; corner < 8; corner++)
                        {
                            var p = bounds.center + Vector3.Scale(bounds.extents, new Vector3(
                                (corner & 1) == 0 ? -1 : 1, (corner & 2) == 0 ? -1 : 1, (corner & 4) == 0 ? -1 : 1));
                            var uv = camera.WorldToViewportPoint(p);
                            Assert.Greater(uv.z, camera.nearClipPlane);
                            Assert.That(uv.x, Is.InRange(.04f, .96f));
                            Assert.That(uv.y, Is.InRange(.10f, .94f), "Keep character clear of the taskbar and frame edges");
                            minY = Mathf.Min(minY, uv.y); maxY = Mathf.Max(maxY, uv.y);
                        }
                    }
                    Assert.That(maxY - minY, Is.InRange(.32f, .58f), "aspect=" + aspect);
                }
                foreach (float height in new[] { .15f, .85f, 1.45f })
                {
                    var target = actor.transform.position + Vector3.up * height;
                    var delta = target - camera.transform.position;
                    Assert.IsFalse(Physics.Raycast(camera.transform.position, delta.normalized, out var hit,
                        delta.magnitude - .03f, ~0, QueryTriggerInteraction.Ignore), $"Character obscured by {hit.collider?.name}");
                }
            }
            finally { camera.targetTexture = null; Object.Destroy(rt); }
            yield return null;
        }

        [UnityTearDown]
        public IEnumerator TearDown()
        {
            if (_world != null) Object.DestroyImmediate(_world.Root.gameObject);
            yield return null;
        }
    }
}
#endif

