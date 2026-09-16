using UnityEngine;
using UnityEngine.AI;
using System;
using System.Collections.Generic;
using System.Text;
using AiPeople.Core;

namespace AiPeople.World
{
    /// <summary>Offline baked navigation for this static house and its current furniture.</summary>
    public sealed class ApartmentNavigation : MonoBehaviour
    {
        public NavMeshData data;
        private NavMeshDataInstance _instance;
        private ApartmentDoor[] _doors;
        private readonly List<Transform> _actors = new List<Transform>();
        private readonly RaycastHit[] _motionHits = new RaycastHit[32];
        public int Revision { get; private set; }
        public event Action DoorsChanged;

        public ApartmentDoor[] Doors => _doors ??= GetComponentsInChildren<ApartmentDoor>();

        public void RegisterActor(Transform actor) { if (!_actors.Contains(actor)) _actors.Add(actor); }
        public void UnregisterActor(Transform actor) { _actors.Remove(actor); }

        public bool ActorOverlaps(Vector3 center, Vector3 extents, Quaternion rotation)
        {
            foreach (var actor in _actors)
            {
                if (actor == null || !actor.gameObject.activeInHierarchy) continue;
                var p = Quaternion.Inverse(rotation)*(actor.position-center);
                if (p.y > extents.y || p.y+1.7f < -extents.y) continue;
                float x = Mathf.Max(0, Mathf.Abs(p.x)-extents.x);
                float z = Mathf.Max(0, Mathf.Abs(p.z)-extents.z);
                if (x*x+z*z < .28f*.28f) return true;
            }
            return false;
        }

        public void NotifyDoorChanged() { Revision++; DoorsChanged?.Invoke(); }

        public string DoorsJson()
        {
            var json = new StringBuilder("{\"doors\":[");
            foreach (var door in Doors)
            {
                if (json[json.Length-1] != '[') json.Append(',');
                json.Append("{\"id\":").Append(JsonUtil.Str(door.doorId))
                    .Append(",\"label\":").Append(JsonUtil.Str(door.displayName))
                    .Append(",\"open\":").Append(door.IsOpen ? "true" : "false")
                    .Append(",\"targetOpen\":").Append(door.TargetOpen ? "true" : "false")
                    .Append(",\"moving\":").Append(door.IsMoving ? "true" : "false")
                    .Append(",\"blocked\":").Append(door.IsObstructed ? "true" : "false").Append('}');
            }
            return json.Append("]}").ToString();
        }

        public string SetDoorState(string id, bool open)
        {
            foreach (var door in Doors)
                if (door.doorId == id)
                {
                    bool accepted = door.RequestState(open, out string reason);
                    return "{\"accepted\":" + (accepted ? "true" : "false") + ",\"reason\":" + JsonUtil.Str(reason) + "}";
                }
            return "{\"accepted\":false,\"reason\":\"未找到这扇门窗\"}";
        }

        public bool SegmentIsOpen(Vector3 from, Vector3 to)
        {
            foreach (var door in Doors) if (door.BlocksSegment(from, to)) return false;
            return true;
        }

        public bool DoorBlocksMotion(Vector3 from, Vector3 to)
        {
            Vector3 delta = to-from;
            if (delta.sqrMagnitude < .000001f) return false;
            int count = Physics.CapsuleCastNonAlloc(from + Vector3.up*.32f, from + Vector3.up*1.4f,
                .24f, delta.normalized, _motionHits, delta.magnitude, ~0, QueryTriggerInteraction.Ignore);
            if (count == _motionHits.Length) return true;
            for (int i = 0; i < count; i++)
                if (_motionHits[i].collider.GetComponentInParent<ApartmentDoor>() != null) return true;
            return false;
        }

        private void OnEnable()
        {
            if (data != null) _instance = NavMesh.AddNavMeshData(data, transform.position, transform.rotation);
        }

        private void OnDisable()
        {
            if (_instance.valid) _instance.Remove();
        }

        public bool TryPath(Vector3 start, Vector3 target, out Vector3[] corners)
        {
            corners = null;
            if (!NavMesh.SamplePosition(start, out var a, .6f, NavMesh.AllAreas)
                || !NavMesh.SamplePosition(target, out var b, .6f, NavMesh.AllAreas)) return false;
            var path = new NavMeshPath();
            if (!NavMesh.CalculatePath(a.position, b.position, NavMesh.AllAreas, path)
                || path.status != NavMeshPathStatus.PathComplete) return false;
            corners = path.corners;
            for (int i = 1; i < corners.Length; i++)
                if (!SegmentIsOpen(corners[i-1], corners[i])) { corners = null; return false; }
            return corners.Length > 0;
        }
    }
}
