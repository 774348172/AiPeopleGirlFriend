#if UNITY_EDITOR
using System.Collections;
using AiPeople.World;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace AiPeople.Tests
{
    public sealed class ApartmentTraversalTests
    {
        private ApartmentBuilder.Result _world;
        private ApartmentNavigation _navigation;
        private float _timeScale;

        [UnitySetUp]
        public IEnumerator SetUp()
        {
            _timeScale = Time.timeScale;
            _world = ApartmentBuilder.Build(AssetDatabase.LoadAssetAtPath<GameObject>(
                "Assets/Art/Apartment/ImportedShell/ApartmentShell.prefab"));
            _navigation = _world.Root.GetComponentInChildren<ApartmentNavigation>();
            yield return null;
            Physics.SyncTransforms();
        }

        [UnityTest]
        public IEnumerator Player_WalksFromEntranceToEveryRoom_AndReturns()
        {
            var controller = CreatePlayer(ImportedApartmentLayout.EntryPosition);
            foreach (var room in ImportedApartmentLayout.TraversalPoints)
            {
                yield return WalkPlayer(controller, room.Position, "Entry -> " + room.Name);
                yield return WalkPlayer(controller, ImportedApartmentLayout.EntryPosition, room.Name + " -> Entry");
            }
            // Walk directly between private rooms as well, through the public hall.
            yield return WalkPlayer(controller, new Vector3(.3f, 0, 2.5f), "Entry -> Master");
            yield return WalkPlayer(controller, new Vector3(4, 0, 3), "Master -> Secondary");
            yield return WalkPlayer(controller, new Vector3(4, 0, -3), "Secondary -> Bathroom");
            yield return WalkPlayer(controller, new Vector3(0, -.16f, -5.8f), "Bathroom -> Balcony");
        }

        [UnityTest, Timeout(240000)]
        public IEnumerator Heroine_WalksEveryRouteWithoutObstacles_OrWritingLocationFacts()
        {
            var actor = new GameObject("WholeHouseHeroineProbe");
            actor.transform.SetParent(_world.Root);
            actor.transform.position = ImportedApartmentLayout.EntryPosition;
            var state = new GameWorldState();
            var director = _world.Root.gameObject.AddComponent<HeroineDirector>();
            director.Configure(state, actor.transform, null, _world.TableTop, _world.Root, null);
            // Accelerate game time only. The production director retains its normal walk speed.
            Time.timeScale = 4;
            foreach (var room in ImportedApartmentLayout.TraversalPoints)
            {
                yield return WalkHeroine(director, actor.transform, room.Position, "Entry -> " + room.Name);
                yield return WalkHeroine(director, actor.transform, ImportedApartmentLayout.EntryPosition, room.Name + " -> Entry");
                Assert.IsEmpty(state.HeroineLocationId, "Geometry validation must not invent an authoritative location");
                Assert.IsEmpty(state.PendingSnapshot());
                Assert.IsEmpty(state.ItemStatesSnapshot());
            }
            Assert.IsFalse(director.TryWalkToInteriorPoint(new Vector3(0, 0, -8)), "Outside destination accepted");
            Assert.IsFalse(director.IsWalking);
        }

        [UnityTest]
        public IEnumerator ExteriorWallsWindowsAndBalcony_BlockEscape_WithoutFalling()
        {
            var controller = CreatePlayer(ImportedApartmentLayout.EntryPosition);
            // Approach from free aisles in the furnished house, then push toward each exterior side.
            var probes = new[] {
                new Vector3(-4.25f, 0, -.6f), new Vector3(-3.7f, 0, 3),
                new Vector3(-1.2f, 0, 5.6f), new Vector3(.3f, 0, 4.8f),
                new Vector3(4, 0, 4.8f), new Vector3(4.6f, 0, 2.4f),
                new Vector3(3.95f, 0, -2.65f), new Vector3(4, 0, -4.2f),
                new Vector3(-3.1f, 0, -4.05f),
                new Vector3(-2.7f, -.15f, -5.75f), new Vector3(0, -.15f, -5.75f),
                new Vector3(1.9f, -.15f, -5.75f),
                new Vector3(-2.7f, -.15f, -5.75f), new Vector3(1.9f, -.15f, -5.75f),
            };
            var directions = new[] { Vector3.left, Vector3.left, Vector3.forward, Vector3.forward,
                Vector3.forward, Vector3.right, Vector3.right, Vector3.back, Vector3.back,
                new Vector3(-1, 0, -1).normalized, Vector3.back, new Vector3(1, 0, -1).normalized,
                Vector3.left, Vector3.right };
            for (int i = 0; i < probes.Length; i++)
            {
                yield return WalkPlayer(controller, probes[i], "Exterior approach " + i);
                Vector3 start = controller.transform.position;
                float gravity = 0;
                for (int frame = 0; frame < 240; frame++)
                {
                    Move(controller, directions[i], ref gravity);
                    AssertGrounded(controller.transform.position, "Exterior push " + i);
                }
                Assert.Less(Vector3.Dot(controller.transform.position-start, directions[i]), 1.6f, "Escaped boundary " + i);
                Assert.IsFalse(_navigation.TryPath(probes[i], probes[i]+directions[i]*4, out _), "Navigation left the house " + i);
                Debug.Log($"BOUNDARY_OK index={i} stopped={controller.transform.position:F3}");
                // Return under normal movement, without teleporting away from the boundary.
                yield return WalkPlayer(controller, probes[i], "Exterior return " + i);
            }
        }

        [UnityTest]
        public IEnumerator InteriorPartitions_BlockDirectCrossing_ButAllowReturnToDoorway()
        {
            var controller = CreatePlayer(ImportedApartmentLayout.EntryPosition);
            var probes = new[] { new Vector3(-1.1f, 0, 3), new Vector3(1.3f, 0, -.6f),
                new Vector3(2.1f, 0, 2), new Vector3(2.32f, 0, -3) };
            var directions = new[] { Vector3.left, Vector3.forward, Vector3.right, Vector3.right };
            for (int i = 0; i < probes.Length; i++)
            {
                yield return WalkPlayer(controller, probes[i], "Partition approach " + i);
                Vector3 start = controller.transform.position;
                float gravity = 0;
                for (int frame = 0; frame < 180; frame++)
                {
                    Move(controller, directions[i], ref gravity);
                    AssertGrounded(controller.transform.position, "Partition push " + i);
                }
                Assert.Less(Vector3.Dot(controller.transform.position-start, directions[i]), .85f, "Crossed partition " + i);
                yield return WalkPlayer(controller, ImportedApartmentLayout.EntryPosition, "Partition return " + i);
                Debug.Log("PARTITION_OK index=" + i);
            }
        }

        private CharacterController CreatePlayer(Vector3 position)
        {
            var player = new GameObject("WholeHousePlayerProbe");
            player.transform.SetParent(_world.Root);
            player.transform.position = position + Vector3.up * .12f;
            var controller = player.AddComponent<CharacterController>();
            PlayerController.ConfigureCapsule(controller);
            return controller;
        }

        private IEnumerator WalkPlayer(CharacterController controller, Vector3 target, string label)
        {
            Assert.IsTrue(_navigation.TryPath(controller.transform.position, target, out var corners), label + ": no path");
            float gravity = 0;
            foreach (var corner in corners)
            {
                int frames = 0;
                while (FlatDistance(controller.transform.position, corner) > .055f && frames++ < 900)
                {
                    var delta = corner-controller.transform.position;
                    delta.y = 0;
                    Move(controller, delta.normalized * Mathf.Min(1, delta.magnitude / (2.6f / 60)), ref gravity);
                    AssertGrounded(controller.transform.position, label);
                    if (frames % 60 == 0) yield return null;
                }
                Assert.LessOrEqual(FlatDistance(controller.transform.position, corner), .06f,
                    label + $": player stuck at {controller.transform.position:F3}, corner={corner:F3}");
            }
            Assert.Less(FlatDistance(controller.transform.position, target), .22f, label + ": wrong room endpoint");
            Debug.Log($"PLAYER_ROUTE_OK {label} position={controller.transform.position:F3}");
        }

        private IEnumerator WalkHeroine(HeroineDirector director, Transform actor, Vector3 target, string label)
        {
            Assert.IsTrue(director.TryWalkToInteriorPoint(target), label + ": no heroine path");
            float deadline = Time.realtimeSinceStartup + 30;
            Vector3 previous = actor.position;
            while (director.IsWalking && Time.realtimeSinceStartup < deadline)
            {
                yield return null;
                Vector3 p = actor.position;
                AssertGrounded(p, label);
                // Include intermediate samples so a long frame cannot jump through a wall unnoticed.
                int samples = Mathf.Max(1, Mathf.CeilToInt(Vector3.Distance(previous, p) / .04f));
                for (int i = 1; i <= samples; i++)
                {
                    Vector3 sample = Vector3.Lerp(previous, p, i / (float)samples);
                    Assert.IsFalse(Physics.CheckCapsule(sample + Vector3.up * .32f,
                        sample + Vector3.up * 1.4f, .24f), label + ": heroine intersects obstacle at " + sample);
                }
                previous = p;
            }
            Assert.IsFalse(director.IsWalking, label + ": heroine timed out");
            Assert.Less(FlatDistance(actor.position, target), .22f, label + ": heroine wrong endpoint");
            Debug.Log($"HEROINE_ROUTE_OK {label} position={actor.position:F3}");
        }

        private static void Move(CharacterController controller, Vector3 direction, ref float verticalSpeed)
        {
            const float dt = 1f / 60;
            verticalSpeed = controller.isGrounded ? -1 : verticalSpeed + Physics.gravity.y * dt;
            controller.Move((direction * 2.6f + Vector3.up * verticalSpeed) * dt);
        }

        private static void AssertGrounded(Vector3 p, string label)
        {
            Assert.Greater(p.y, -.35f, label + ": fell below house floor at " + p);
            Assert.IsTrue(Physics.Raycast(p + Vector3.up * .3f, Vector3.down, .6f,
                ~0, QueryTriggerInteraction.Ignore), label + ": no floor under character at " + p);
        }

        private static float FlatDistance(Vector3 a, Vector3 b) => Vector2.Distance(new Vector2(a.x, a.z), new Vector2(b.x, b.z));

        [UnityTearDown]
        public IEnumerator TearDown()
        {
            Time.timeScale = _timeScale;
            if (_world != null) Object.DestroyImmediate(_world.Root.gameObject);
            yield return null;
        }
    }
}
#endif

