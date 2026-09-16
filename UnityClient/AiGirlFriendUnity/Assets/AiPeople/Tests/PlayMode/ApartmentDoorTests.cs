#if UNITY_EDITOR
using System.Collections;
using AiPeople.World;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace AiPeople.Tests
{
    public sealed class ApartmentDoorTests
    {
        private ApartmentBuilder.Result _world;
        private ApartmentNavigation _nav;
        private float _timeScale;

        [UnitySetUp]
        public IEnumerator SetUp()
        {
            _timeScale = Time.timeScale;
            _world = ApartmentBuilder.Build(AssetDatabase.LoadAssetAtPath<GameObject>(
                "Assets/Art/Apartment/ImportedShell/ApartmentShell.prefab"));
            _nav = _world.Root.GetComponentInChildren<ApartmentNavigation>();
            yield return null;
            Physics.SyncTransforms();
        }

        [UnityTest]
        public IEnumerator EveryDoor_ClosesPhysically_BlocksNavigation_AndReopensRepeatedly()
        {
            Assert.AreEqual(6, _nav.Doors.Length);
            foreach (var door in _nav.Doors)
            {
                Vector3 target, start, direction;
                switch (door.doorId)
                {
                    case "KitchenDoor": target = new Vector3(-3.4f, 0, 3.2f); start = new Vector3(-3.65f, .12f, .7f); direction = Vector3.forward; break;
                    case "MasterDoor": target = new Vector3(.3f, 0, 2.5f); start = new Vector3(.2f, .12f, .1f); direction = Vector3.forward; break;
                    case "SecondaryDoor": target = new Vector3(4, 0, 3); start = new Vector3(3.7f, .12f, -.6f); direction = Vector3.forward; break;
                    case "BathroomDoor": target = new Vector3(4, 0, -3); start = new Vector3(4, .12f, -.85f); direction = Vector3.back; break;
                    case "HallDoor": target = new Vector3(4, 0, -.7f); start = new Vector3(2.2f, .12f, -.75f); direction = Vector3.right; break;
                    default: target = new Vector3(0, -.16f, -5.8f); start = new Vector3(-.5f, .12f, -4.2f); direction = Vector3.back; break;
                }
                Vector3 openPosition = door.leaf.localPosition;
                Quaternion openRotation = door.leaf.localRotation;
                for (int cycle = 0; cycle < 2; cycle++)
                {
                    Assert.IsTrue(_nav.TryPath(ImportedApartmentLayout.EntryPosition, target, out _), door.doorId + " initially inaccessible");
                    Assert.IsTrue(door.RequestState(false, out string reason), reason);
                    int version = _nav.Revision;
                    Assert.IsTrue(door.RequestState(false, out _));
                    Assert.AreEqual(version, _nav.Revision, "Duplicate close restarted animation");
                    Assert.IsFalse(_nav.TryPath(ImportedApartmentLayout.EntryPosition, target, out _), door.doorId + " stale open path accepted before carving");
                    yield return Finish(door);
                    Assert.IsFalse(door.IsOpen);
                    Assert.IsFalse(_nav.TryPath(ImportedApartmentLayout.EntryPosition, target, out _));
                    var player = new GameObject("ClosedDoorPlayer");
                    player.transform.SetParent(_world.Root);
                    player.transform.position = start;
                    var controller = player.AddComponent<CharacterController>();
                    PlayerController.ConfigureCapsule(controller);
                    for (int i = 0; i < 100; i++) controller.Move(direction * .025f + Vector3.down * .03f);
                    Assert.Less(Vector3.Dot(player.transform.position-start, direction), .85f,
                        door.doorId + " visible closed leaf did not block the player: " + player.transform.position);
                    Assert.Greater(player.transform.position.y, -.3f);
                    Object.Destroy(player);
                    yield return null;
                    Assert.IsTrue(door.RequestState(true, out reason), reason);
                    yield return Finish(door);
                    Assert.IsTrue(_nav.TryPath(ImportedApartmentLayout.EntryPosition, target, out _), door.doorId + " failed to reconnect");
                    Assert.Less(Vector3.Distance(openPosition, door.leaf.localPosition), .001f);
                    Assert.Less(Quaternion.Angle(openRotation, door.leaf.localRotation), .01f);
                }
                Debug.Log("DOOR_CYCLE_OK " + door.doorId);
            }
        }

        [UnityTest]
        public IEnumerator OccupiedDoor_RejectsClosing_AndPausesIfSomeoneEntersDuringMotion()
        {
            var door = Find("HallDoor");
            var player = new GameObject("DoorwayOccupant");
            player.transform.SetParent(_world.Root);
            player.transform.position = door.transform.TransformPoint(door.portal.center) - Vector3.up*.8f;
            var controller = player.AddComponent<CharacterController>();
            PlayerController.ConfigureCapsule(controller);
            Physics.SyncTransforms();
            Assert.IsFalse(door.RequestState(false, out _));
            Assert.IsTrue(door.IsOpen);
            controller.enabled = false;
            player.transform.position = new Vector3(-2, .12f, -3);
            controller.enabled = true;
            Physics.SyncTransforms();
            Assert.IsTrue(door.RequestState(false, out _));
            yield return null;
            controller.enabled = false;
            player.transform.position = door.transform.TransformPoint(door.portal.center) - Vector3.up*.8f;
            controller.enabled = true;
            Physics.SyncTransforms();
            Quaternion before = door.leaf.localRotation;
            yield return new WaitForSeconds(.15f);
            Assert.IsTrue(door.IsObstructed);
            Assert.Less(Quaternion.Angle(before, door.leaf.localRotation), .01f);
            Object.Destroy(player);
            yield return null;
            yield return Finish(door);
            Assert.IsFalse(door.IsOpen);
            // The heroine has no CharacterController; its registered body must also protect the doorway.
            Assert.IsTrue(door.RequestState(true, out _));
            yield return Finish(door);
            var actor = new GameObject("DoorwayHeroine");
            actor.transform.SetParent(_world.Root);
            actor.transform.position = door.transform.TransformPoint(door.portal.center) - Vector3.up*.85f;
            var director = _world.Root.gameObject.AddComponent<HeroineDirector>();
            director.Configure(new GameWorldState(), actor.transform, null, null, _world.Root, null);
            Assert.IsFalse(door.RequestState(false, out _));
        }

        [UnityTest]
        public IEnumerator Heroine_WaitsForClosedDoor_AndResumesAfterOpening()
        {
            var actor = new GameObject("DoorWaitingHeroine");
            actor.transform.SetParent(_world.Root);
            actor.transform.position = ImportedApartmentLayout.EntryPosition;
            var state = new GameWorldState();
            var director = _world.Root.gameObject.AddComponent<HeroineDirector>();
            director.Configure(state, actor.transform, null, null, _world.Root, null);
            Vector3 destination = new Vector3(-3.4f, 0, 3.2f);
            Assert.IsTrue(director.TryWalkToInteriorPoint(destination));
            var door = Find("KitchenDoor");
            Assert.IsTrue(door.RequestState(false, out _));
            yield return Finish(door);
            Assert.IsTrue(director.IsWaitingForPassage);
            Vector3 waiting = actor.transform.position;
            yield return new WaitForSeconds(.3f);
            Assert.Less(Vector3.Distance(waiting, actor.transform.position), .01f);
            Assert.IsTrue(door.RequestState(true, out _));
            yield return Finish(door);
            Time.timeScale = 4;
            float deadline = Time.realtimeSinceStartup + 15;
            while (director.IsWalking && Time.realtimeSinceStartup < deadline)
            {
                yield return null;
                var p = actor.transform.position;
                Assert.IsFalse(Physics.CheckCapsule(p + Vector3.up*.32f, p + Vector3.up*1.4f, .24f), "Heroine crossed an obstacle");
            }
            Assert.IsFalse(director.IsWalking);
            Assert.Less(Vector3.Distance(actor.transform.position, destination), .2f);
            Assert.IsEmpty(state.HeroineLocationId);
            Assert.IsEmpty(state.PendingSnapshot());
        }

        private ApartmentDoor Find(string id) => System.Array.Find(_nav.Doors, door => door.doorId == id);
        private static IEnumerator Finish(ApartmentDoor door)
        {
            float deadline = Time.realtimeSinceStartup + 3;
            while (door.IsMoving && Time.realtimeSinceStartup < deadline) yield return null;
            Assert.IsFalse(door.IsMoving, door.doorId + " animation stuck");
            yield return null;
            yield return null; // Let Unity apply/remove carving.
        }
        [UnityTearDown]
        public IEnumerator TearDown()
        {
            Time.timeScale = _timeScale;
            Object.DestroyImmediate(_world.Root.gameObject);
            yield return null;
        }
    }
}
#endif

