using System;
using System.Collections.Generic;
using AiPeople.Core;
using AiPeople.Character;
using UnityEngine;

namespace AiPeople.World
{
    /// <summary>
    /// 女主行动导演（主线程）：把游戏世界的进行中动作表现为真实行走与实体生成。
    /// 行走播 walk、到达后回 idle（做饭等动作暂无动画，保持占位表现）；完成时生成实体（煮面 → 桌上出现面碗）。
    /// </summary>
    public sealed class HeroineDirector : MonoBehaviour
    {
        public float walkSpeed = 1.1f;
        public float turnSpeed = 8f;
        public float arriveDistance = 0.12f;

        private GameWorldState _state;
        private Transform _heroine;
        private Animator _animator;
        private CharacterPresentation _presentation;
        private Transform _tableTop;
        private Transform _entityParent;
        private Action<string> _toast;

        private readonly Dictionary<string, Vector3> _anchors = new Dictionary<string, Vector3>();
        private readonly HashSet<string> _tracked = new HashSet<string>();
        private readonly List<GameObject> _entities = new List<GameObject>();
        private string _walkingTo = string.Empty;
        private Vector3 _walkTarget;
        private ApartmentNavigation _navigation;
        private Vector3[] _path;
        private int _pathIndex;
        private int _pathRevision;
        private Vector3 _destination;
        public bool IsWaitingForPassage { get; private set; }
        private string _currentState = string.Empty;
        private float _snapshotTimer;
        private List<PendingAction> _pendingCache = new List<PendingAction>();

        public void Configure(
            GameWorldState state,
            Transform heroine,
            Animator animator,
            Transform tableTop,
            Transform entityParent,
            Action<string> toast)
        {
            _state = state;
            _heroine = heroine;
            _animator = animator;
            _presentation = heroine != null ? heroine.GetComponent<CharacterPresentation>() : null;
            _tableTop = tableTop;
            _entityParent = entityParent;
            _toast = toast;
            _navigation = entityParent != null ? entityParent.GetComponentInChildren<ApartmentNavigation>() : null;
            _navigation?.RegisterActor(heroine);
        }

        private void OnDestroy() { _navigation?.UnregisterActor(_heroine); }

        public void RegisterAnchor(string locationId, Vector3 position)
        {
            _anchors[locationId] = position;
        }

        /// <summary>Walk within the existing scene without executing an action or writing world facts.</summary>
        public bool TryWalkToInteriorPoint(Vector3 target)
        {
            if (_state == null || _heroine == null || _navigation == null) return false;
            return StartWalking(target, "interior");
        }

        public bool IsWalking => _walkingTo.Length != 0;

        private bool StartWalking(Vector3 target, string label)
        {
            _walkingTo = string.Empty;
            _path = null;
            _walkTarget = target;
            _destination = target;
            IsWaitingForPassage = false;
            if (_navigation != null)
            {
                if (!_navigation.TryPath(_heroine.position, target, out _path))
                {
                    PlayState("Idle");
                    return false;
                }
                _pathIndex = 0;
                _pathRevision = _navigation.Revision;
                _walkTarget = _path[0];
            }
            _walkingTo = label;
            _presentation?.SetWalking(true);
            PlayState("Walk");
            return true;
        }

        private void Update()
        {
            if (_state == null || _heroine == null)
            {
                return;
            }

            foreach (PendingAction completed in _state.Tick(Time.deltaTime))
            {
                OnCompleted(completed);
            }

            _snapshotTimer -= Time.deltaTime;
            if (_snapshotTimer <= 0f)
            {
                _snapshotTimer = 0.1f;
                _pendingCache = _state.PendingSnapshot();
            }

            List<PendingAction> pending = _pendingCache;
            var active = new HashSet<string>();
            foreach (PendingAction action in pending)
            {
                active.Add(action.actionId);
                if (_tracked.Add(action.actionId))
                {
                    OnStarted(action);
                }
            }

            _tracked.RemoveWhere(id => !active.Contains(id));
            Walk(Time.deltaTime);
        }

        private void OnStarted(PendingAction action)
        {
            if (!string.IsNullOrEmpty(action.travelToLocationId)
                && _anchors.TryGetValue(action.travelToLocationId, out Vector3 target))
            {
                if (!StartWalking(target, action.travelToLocationId))
                {
                    Debug.LogWarning("[AiPeople] 房屋内没有可达的表现路径：" + action.travelToLocationId);
                }
            }
            else
            {
                PlayState("Idle");
                _presentation?.SetWalking(false);
            }
        }

        private void OnCompleted(PendingAction action)
        {
            _tracked.Remove(action.actionId);
            if (!string.IsNullOrEmpty(action.spawnEntityId))
            {
                SpawnEntity(action.spawnEntityId);
            }

            if (_pendingCache.Count == 0)
            {
                _walkingTo = string.Empty;
                _presentation?.SetWalking(false);
                PlayState("Idle");
            }
        }

        private void Walk(float deltaSeconds)
        {
            if (_walkingTo.Length == 0)
            {
                return;
            }

            Vector3 position = _heroine.position;
            if (_navigation != null && (_pathRevision != _navigation.Revision || IsWaitingForPassage))
            {
                // Carving settles asynchronously. While waiting, retry at a bounded rate.
                if (_pathRevision == _navigation.Revision && Time.time < _nextPathRetry) return;
                _pathRevision = _navigation.Revision;
                _nextPathRetry = Time.time + .2f;
                if (!_navigation.TryPath(position, _destination, out _path))
                {
                    IsWaitingForPassage = true;
                    PlayState("Idle");
                    return;
                }
                IsWaitingForPassage = false;
                _pathIndex = 0;
                _walkTarget = _path[0];
            }
            var flat = new Vector3(_walkTarget.x - position.x, 0f, _walkTarget.z - position.z);
            Vector3 candidate = flat.magnitude <= arriveDistance ? _walkTarget
                : position + flat.normalized*Mathf.Min(walkSpeed*deltaSeconds, flat.magnitude);
            if (_navigation != null && (!_navigation.SegmentIsOpen(position, _walkTarget)
                || _navigation.DoorBlocksMotion(position, candidate)))
            {
                IsWaitingForPassage = true;
                PlayState("Idle");
                return;
            }
            if (flat.magnitude <= arriveDistance)
            {
                _heroine.position = new Vector3(_walkTarget.x, _navigation != null ? _walkTarget.y : position.y, _walkTarget.z);
                if (_navigation != null && _path != null && ++_pathIndex < _path.Length)
                {
                    _walkTarget = _path[_pathIndex];
                    return;
                }
                _walkingTo = string.Empty;
                PlayState("Idle");
                return;
            }

            Vector3 step = flat.normalized * Mathf.Min(walkSpeed * deltaSeconds, flat.magnitude);
            _heroine.position = position + step;
            if (_navigation != null && UnityEngine.AI.NavMesh.SamplePosition(_heroine.position, out var floor, .4f,
                    UnityEngine.AI.NavMesh.AllAreas))
                // Sample the floor height only. Snapping X/Z to the closest polygon can undo
                // a small frame's entire step at a reconstructed floor-height seam, forever.
                _heroine.position = new Vector3(_heroine.position.x, floor.position.y, _heroine.position.z);
            _heroine.rotation = Quaternion.Slerp(
                _heroine.rotation,
                Quaternion.LookRotation(flat.normalized, Vector3.up),
                turnSpeed * deltaSeconds);
            PlayState("Walk");
        }

        private float _nextPathRetry;

        private void PlayState(string stateName)
        {
            if (_currentState == stateName || _animator == null || _animator.runtimeAnimatorController == null)
            {
                return;
            }

            if (!_animator.HasState(0, Animator.StringToHash(stateName)))
            {
                return;
            }

            _animator.CrossFadeInFixedTime(stateName, 0.2f);
            _currentState = stateName;
        }

        private void SpawnEntity(string entityId)
        {
            if (entityId == "bowl")
            {
                var bowl = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
                bowl.name = "实体_面碗";
                bowl.transform.SetParent(_entityParent != null ? _entityParent : transform, true);
                Vector3 basePosition = _tableTop != null ? _tableTop.position : new Vector3(-1.2f, 0.76f, 0.9f);
                bowl.transform.position = basePosition + new Vector3(0f, 0.075f, 0f);
                bowl.transform.localScale = new Vector3(0.18f, 0.03f, 0.18f);
                bowl.GetComponent<MeshRenderer>().sharedMaterial =
                    MaterialLibrary.Get("Bowl", new Color(0.92f, 0.90f, 0.86f));
                _entities.Add(bowl);
                _toast?.Invoke("桌上多了一碗热汤面。");
            }
        }
    }
}
