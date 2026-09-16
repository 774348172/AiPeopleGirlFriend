using UnityEngine;
using UnityEngine.AI;

namespace AiPeople.World
{
    /// <summary>A local, reversible door pose. It never executes backend actions.</summary>
    public sealed class ApartmentDoor : MonoBehaviour
    {
        public string doorId;
        public string displayName;
        public Transform leaf;
        public float closedYaw;
        public float openYaw;
        public Vector3 openOffset;
        public Bounds portal;
        public float duration = .65f;
        public bool IsOpen { get; private set; } = true;
        public bool IsMoving { get; private set; }
        public bool IsObstructed { get; private set; }
        public bool TargetOpen { get; private set; } = true;
        public string Prompt { get; private set; }
        public bool BlocksPassage => !IsOpen || IsMoving;
        private float _amount = 1;
        private Bounds _leafBounds;
        private ApartmentNavigation _navigation;
        private NavMeshObstacle _obstacle;
        private readonly Collider[] _overlaps = new Collider[64];

        private void Awake()
        {
            _navigation = GetComponentInParent<ApartmentNavigation>();
            _leafBounds = leaf.GetComponent<MeshFilter>().sharedMesh.bounds;
            var gate = new GameObject("NavigationGate");
            gate.transform.SetParent(transform, false);
            _obstacle = gate.AddComponent<NavMeshObstacle>();
            _obstacle.shape = NavMeshObstacleShape.Box;
            _obstacle.center = portal.center;
            _obstacle.size = portal.size;
            _obstacle.carving = true;
            _obstacle.carveOnlyStationary = false;
            _obstacle.enabled = false;
            ApplyPose();
            RefreshPrompt();
        }

        public bool RequestState(bool open, out string reason)
        {
            reason = string.Empty;
            if (open == TargetOpen) return true; // Repeated requests never reverse an animation.
            if (!open && Occupied(portal, Quaternion.identity, Vector3.zero))
            {
                reason = "门口有人，请先让开。";
                return false;
            }
            // Check the whole swept arc before starting, then check again during movement.
            if (SweepOccupied(_amount, open ? 1 : 0))
            {
                reason = "门旁有人，请先让开。";
                return false;
            }
            TargetOpen = open;
            IsMoving = true;
            IsObstructed = false;
            _obstacle.enabled = true;
            Changed();
            return true;
        }

        private void Update()
        {
            if (!IsMoving) return;
            float next = Mathf.MoveTowards(_amount, TargetOpen ? 1 : 0, Time.deltaTime / duration);
            bool blocked = SweepOccupied(_amount, next)
                || (!TargetOpen && Occupied(portal, Quaternion.identity, Vector3.zero));
            if (blocked)
            {
                if (!IsObstructed) { IsObstructed = true; Changed(); }
                return;
            }
            if (IsObstructed) { IsObstructed = false; Changed(); }
            _amount = next;
            ApplyPose();
            if (_amount == (TargetOpen ? 1 : 0))
            {
                IsMoving = false;
                IsOpen = TargetOpen;
                _obstacle.enabled = !IsOpen;
                Changed();
            }
        }

        private void ApplyPose()
        {
            leaf.localRotation = Quaternion.Euler(0, Mathf.Lerp(closedYaw, openYaw, _amount), 0);
            leaf.localPosition = openOffset * _amount;
            Physics.SyncTransforms();
        }

        private bool SweepOccupied(float from, float to)
        {
            int steps = Mathf.Max(1, Mathf.CeilToInt(Mathf.Abs(to-from) * 36));
            for (int i = 1; i <= steps; i++)
            {
                float t = Mathf.Lerp(from, to, i / (float)steps);
                if (Occupied(_leafBounds, Quaternion.Euler(0, Mathf.Lerp(closedYaw, openYaw, t), 0), openOffset*t))
                    return true;
            }
            return false;
        }

        private bool Occupied(Bounds bounds, Quaternion rotation, Vector3 offset)
        {
            Vector3 center = transform.TransformPoint(offset + rotation*bounds.center);
            Quaternion worldRotation = transform.rotation*rotation;
            int count = Physics.OverlapBoxNonAlloc(center, bounds.extents + Vector3.one*.025f,
                _overlaps, worldRotation, ~0, QueryTriggerInteraction.Ignore);
            if (count == _overlaps.Length) return true;
            for (int i = 0; i < count; i++)
                if (_overlaps[i] is CharacterController controller && controller.enabled) return true;
            return _navigation != null && _navigation.ActorOverlaps(center, bounds.extents + Vector3.one*.025f, worldRotation);
        }

        public bool BlocksSegment(Vector3 from, Vector3 to)
        {
            if (!BlocksPassage) return false;
            var bounds = portal;
            bounds.Expand(new Vector3(.48f, 0, .48f));
            from = transform.InverseTransformPoint(from);
            to = transform.InverseTransformPoint(to);
            from.y = to.y = bounds.center.y;
            if (bounds.Contains(from) || bounds.Contains(to)) return true;
            Vector3 delta = to-from;
            return bounds.IntersectRay(new Ray(from, delta.normalized), out float distance) && distance <= delta.magnitude;
        }

        private void Changed()
        {
            RefreshPrompt();
            _navigation?.NotifyDoorChanged();
        }

        private void RefreshPrompt()
        {
            Prompt = IsObstructed ? "有人挡住，请让开（按 E 反向）"
                : IsMoving ? (TargetOpen ? "正在打开" : "正在关闭") + displayName + "（按 E 反向）"
                : "按 E " + (IsOpen ? "关闭" : "打开") + displayName;
        }
    }
}
