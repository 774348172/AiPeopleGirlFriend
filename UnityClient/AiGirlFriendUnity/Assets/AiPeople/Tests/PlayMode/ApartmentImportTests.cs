#if UNITY_EDITOR
using System.Collections;
using AiPeople.World;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace AiPeople.Tests
{
    public sealed class ApartmentImportTests
    {
        private ApartmentBuilder.Result _world;

        [UnityTest]
        public IEnumerator ImportedHouse_HasTexturedGeometry_ClearAnchors_AndConnectedRoutes()
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(
                "Assets/Art/Apartment/ImportedShell/ApartmentShell.prefab");
            Assert.IsNotNull(prefab);
            _world = ApartmentBuilder.Build(prefab);
            yield return null;
            Physics.SyncTransforms();
            Assert.IsNull(_world.Root.Find("Structure/Ceiling"));
            Assert.IsNull(_world.WindowRenderer, "The whole-house atlas must not be tinted as a window");
            var shell = _world.Root.Find("Structure/ImportedApartmentShell");
            var renderer = shell.GetComponentInChildren<MeshRenderer>();
            Assert.IsNotNull(renderer.sharedMaterial.GetTexture("_BaseMap"));
            Assert.AreEqual("Universal Render Pipeline/Lit", renderer.sharedMaterial.shader.name);
            Assert.IsNotNull(shell.Find("KitchenDoor"), "Kitchen door must be independently positioned");
            int visualTriangles = 0, collisionTriangles = 0;
            foreach (var filter in shell.GetComponentsInChildren<MeshFilter>())
            {
                visualTriangles += filter.sharedMesh.triangles.Length / 3;
                Assert.AreEqual(UnityEngine.Rendering.IndexFormat.UInt16, filter.sharedMesh.indexFormat);
            }
            foreach (var collider in shell.GetComponentsInChildren<MeshCollider>())
                collisionTriangles += collider.sharedMesh.triangles.Length / 3;
            Assert.LessOrEqual(visualTriangles, 15300);
            Assert.LessOrEqual(collisionTriangles, 10000);
            Assert.IsNotNull(_world.Glass.GetComponent<Interactable>());
            Assert.IsNotNull(_world.CardboardBox.GetComponent<Interactable>());
            var points = new System.Collections.Generic.List<Vector3> {
                _world.PlayerSpawn, ImportedApartmentLayout.HeroinePosition };
            foreach (var waypoint in _world.Waypoints) points.Add(waypoint.transform.position);
            var nav = shell.GetComponent<ApartmentNavigation>();
            Assert.IsNotNull(nav);
            foreach (var p in points)
            {
                Assert.IsTrue(Physics.Raycast(p + Vector3.up * .5f, Vector3.down, 1), "Missing floor: " + p);
                Assert.IsFalse(Physics.CheckCapsule(new Vector3(p.x, .38f, p.z),
                    new Vector3(p.x, 1.4f, p.z), .25f), "Blocked anchor: " + p);
            }
            Assert.IsTrue(nav.TryPath(ImportedApartmentLayout.HeroinePosition,
                _world.Waypoints[0].transform.position, out var path), "Living-room table must be reachable");
            Assert.Greater(path.Length, 1);
            Assert.IsTrue(nav.TryPath(ImportedApartmentLayout.HeroinePosition,
                _world.Waypoints[2].transform.position, out _), "Entrance must be reachable");
            Assert.IsTrue(nav.TryPath(ImportedApartmentLayout.HeroinePosition,
                _world.Waypoints[1].transform.position, out _), "Kitchen must be reachable");
            Assert.IsTrue(nav.TryPath(_world.Waypoints[1].transform.position,
                ImportedApartmentLayout.HeroinePosition, out _), "Kitchen exit must be reachable");
        }

        [UnityTest]
        public IEnumerator Kitchen_PlayerCrossesDoorway_AndDirectorWalksIntoRoom()
        {
            _world = ApartmentBuilder.Build(AssetDatabase.LoadAssetAtPath<GameObject>(
                "Assets/Art/Apartment/ImportedShell/ApartmentShell.prefab"));
            yield return null;
            Physics.SyncTransforms();

            var player = new GameObject("KitchenCrossingProbe");
            player.transform.SetParent(_world.Root);
            player.transform.position = new Vector3(-3.65f, .12f, .8f);
            var controller = player.AddComponent<CharacterController>();
            controller.height = 1.7f;
            controller.radius = .28f;
            controller.center = new Vector3(0, .85f, 0);
            controller.skinWidth = .02f;
            controller.stepOffset = .25f;
            for (int i = 0; i < 80; i++) controller.Move(new Vector3(0, -.03f, .025f));
            Assert.Greater(player.transform.position.z, 2.65f, "Player blocked entering kitchen");
            for (int i = 0; i < 80; i++) controller.Move(new Vector3(0, -.03f, -.025f));
            Assert.Less(player.transform.position.z, 1f, "Player blocked leaving kitchen");
            Assert.Greater(player.transform.position.y, -.1f, "Player fell through doorway floor");
            Object.Destroy(player);
            yield return null;

            var actor = new GameObject("KitchenWalkingProbe");
            actor.transform.SetParent(_world.Root);
            actor.transform.position = ImportedApartmentLayout.HeroinePosition;
            var state = new GameWorldState();
            var director = _world.Root.gameObject.AddComponent<HeroineDirector>();
            director.Configure(state, actor.transform, null, _world.TableTop, _world.Root, null);
            var destination = _world.Waypoints[1].transform.position;
            director.RegisterAnchor("apartment_kitchen", destination);
            Assert.IsTrue(state.Execute("move_to", new System.Collections.Generic.Dictionary<string, string> {
                { "target_location_id", "apartment_kitchen" } }, out var reason), reason);
            float deadline = Time.realtimeSinceStartup + 15f;
            bool arrived = false;
            while (Time.realtimeSinceStartup < deadline)
            {
                yield return null;
                var p = actor.transform.position;
                Assert.IsFalse(Physics.CheckCapsule(p + Vector3.up * .3f, p + Vector3.up * 1.4f, .2f),
                    "Director walked into an obstacle: " + p);
                if (Vector2.Distance(new Vector2(p.x, p.z), new Vector2(destination.x, destination.z)) < .16f)
                {
                    arrived = true;
                    break;
                }
            }
            Assert.IsTrue(arrived, "Director never reached the kitchen; position=" + actor.transform.position
                + " target=" + destination + " waiting=" + director.IsWaitingForPassage + " walking=" + director.IsWalking
                + " corner=" + typeof(HeroineDirector).GetField("_walkTarget", System.Reflection.BindingFlags.Instance
                    | System.Reflection.BindingFlags.NonPublic).GetValue(director));
        }

        [UnityTest]
        public IEnumerator TinyMovementSteps_CrossReconstructedFloorSeamsWithoutSticking()
        {
            _world = ApartmentBuilder.Build(AssetDatabase.LoadAssetAtPath<GameObject>(
                "Assets/Art/Apartment/ImportedShell/ApartmentShell.prefab"));
            yield return null;
            Physics.SyncTransforms();
            var actor = new GameObject("SmallStepProbe");
            actor.transform.SetParent(_world.Root);
            actor.transform.position = ImportedApartmentLayout.HeroinePosition;
            var director = _world.Root.gameObject.AddComponent<HeroineDirector>();
            director.Configure(new GameWorldState(), actor.transform, null, _world.TableTop, _world.Root, null);
            var destination = _world.Waypoints[1].transform.position;
            Assert.IsTrue(director.TryWalkToInteriorPoint(destination));
            var walk = typeof(HeroineDirector).GetMethod("Walk", System.Reflection.BindingFlags.Instance
                | System.Reflection.BindingFlags.NonPublic);
            var step = new object[] { .001f };
            for (int i = 0; i < 20000 && director.IsWalking; i++)
            {
                walk.Invoke(director, step);
                if (i % 100 == 0)
                {
                    var p = actor.transform.position;
                    Assert.IsFalse(Physics.CheckCapsule(p + Vector3.up * .32f, p + Vector3.up * 1.4f, .24f));
                    Assert.IsTrue(Physics.Raycast(p + Vector3.up * .35f, Vector3.down, 1));
                }
            }
            Assert.IsFalse(director.IsWalking, "Small steps were erased by floor projection at " + actor.transform.position);
            Assert.Less(Vector3.Distance(actor.transform.position, destination), .16f);
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

