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
        public GameTimeline Timeline;
        public KitchenDevice Stove;
        public KitchenDevice Hood;

        private GameWorldState _state;
        private Transform _heroine;
        private Animator _animator;
        private CharacterPresentation _presentation;
        private Transform _tableTop;
        private Transform _entityParent;
        private Action<string> _toast;

        private readonly Dictionary<string, Vector3> _anchors = new Dictionary<string, Vector3>();
        private readonly List<WorldEntity> _entities = new List<WorldEntity>();
        private string _activeActionId;
        private bool _restoreStove, _restoreHood;
        private bool _cookingStarted;
        private int _stoveManualRevision, _hoodManualRevision;
        public bool UsesImportedLayout { get; set; }
        public Transform ActorTransform => _heroine;
        public string Motion => Timeline != null && (!Timeline.Ready || Timeline.Paused) ? "idle"
            : IsWaitingForPassage ? "blocked" : IsWalking ? "walking" : "idle";
        public void RegisterEntity(WorldEntity entity) { if (entity != null && !_entities.Contains(entity)) _entities.Add(entity); }
        public WorldEntity FindEntity(string id) => _entities.Find(e => e != null && e.entityId == id);
        public EntityFactData[] CaptureEntities(Transform player)
        {
            _entities.RemoveAll(e => e == null);
            return _entities.ConvertAll(e => e.Capture(UsesImportedLayout,
                e.ownerId == "baiweixi" ? _heroine : e.ownerId == "protagonist" ? player : null)).ToArray();
        }
        private string _walkingTo = string.Empty;
        private Vector3 _walkTarget;
        private ApartmentNavigation _navigation;
        private Vector3[] _path;
        private int _pathIndex;
        private int _pathRevision;
        private Vector3 _destination;
        public bool IsWaitingForPassage { get; private set; }
        private string _currentState = string.Empty;

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
            if (_state.PendingSnapshot().Count != 0) return false;
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

            var pending = _state.PendingSnapshot();
            float delta = Timeline != null ? Timeline.Delta : Time.deltaTime;
            if (Timeline != null && (!Timeline.Ready || Timeline.Paused || delta <= 0)) return;
            var action = pending.Count > 0 ? pending[0] : null;
            if (action != null && _activeActionId != action.instanceId)
            {
                _activeActionId = action.instanceId;
                if (!OnStarted(action)) { _activeActionId = null; return; }
            }
            Walk(delta);
            if (action == null) { _activeActionId = null; return; }
            if (IsWalking)
            {
                _state.SetActionStatus(action.instanceId, IsWaitingForPassage ? "blocked" : "moving");
                return;
            }
            if (action.actionId == "move_to" || action.actionId == "pick_up_item")
            {
                _state.ReadyForConfirmation(action.instanceId);
                OnCompleted(action);
                return;
            }
            if (action.actionId == "cook_meal")
            {
                if (!_cookingStarted) { StartCookingDevices(); _cookingStarted = true; }
                if (Stove == null || !Stove.IsOn) { Fail(action, "灶台已关闭，做饭未完成"); return; }
            }
            _state.SetActionStatus(action.instanceId, "running");
            foreach (PendingAction ready in _state.Tick(delta)) OnCompleted(ready);
        }

        private bool OnStarted(PendingAction action)
        {
            if (action.actionId == "cook_meal") _cookingStarted = false;
            Vector3 target;
            if (action.actionId == "pick_up_item")
            {
                action.parameters.TryGetValue("item_id", out var id);
                var entity = FindEntity(id);
                if (entity == null || !entity.canPickUp || entity.ownerId.Length != 0)
                    return Fail(action, "物品不存在、不可拾取或已被持有");
                // Approach beside the item, not through its supporting table.
                var delta = _heroine.position - entity.transform.position;
                delta.y = 0;
                target = entity.transform.position + (delta.sqrMagnitude > .01f ? delta.normalized : Vector3.back)*.65f;
                target.y = _heroine.position.y;
            }
            else if (!_anchors.TryGetValue(action.travelToLocationId, out target))
                return Fail(action, "目标位置未登记");
            if (!StartWalking(target, string.IsNullOrEmpty(action.travelToLocationId) ? action.instanceId : action.travelToLocationId))
                return Fail(action, "没有可达路径，尚未到达目标");
            _state.SetActionStatus(action.instanceId, "moving");
            return true;
        }

        private bool Fail(PendingAction action, string reason)
        {
            if (action.actionId == "cook_meal") RestoreCookingDevices();
            _state.ConfirmAction(action.instanceId, false, reason);
            _walkingTo = string.Empty;
            IsWaitingForPassage = false;
            _presentation?.SetWalking(false);
            PlayState("Idle");
            _activeActionId = null;
            _cookingStarted = false;
            return false;
        }

        private void StartCookingDevices()
        {
            _restoreStove = Stove != null && !Stove.IsOn;
            _restoreHood = Hood != null && !Hood.IsOn;
            Stove?.SetRunning(true);
            Hood?.SetRunning(true);
            _stoveManualRevision = Stove != null ? Stove.ManualRevision : 0;
            _hoodManualRevision = Hood != null ? Hood.ManualRevision : 0;
        }

        private void RestoreCookingDevices()
        {
            if (_restoreStove && Stove != null && Stove.ManualRevision == _stoveManualRevision)
                Stove.SetRunning(false);
            if (_restoreHood && Hood != null && Hood.ManualRevision == _hoodManualRevision)
                Hood.SetRunning(false);
            _restoreStove = _restoreHood = false;
        }

        private void OnCompleted(PendingAction action)
        {
            string resultId = "";
            if (action.actionId == "pick_up_item")
            {
                action.parameters.TryGetValue("item_id", out var id);
                var entity = FindEntity(id);
                if (entity == null || !entity.canPickUp || entity.ownerId.Length != 0
                    || Vector3.Distance(_heroine.position, new Vector3(entity.transform.position.x, _heroine.position.y, entity.transform.position.z)) > 1.1f)
                { Fail(action, "拾取前校验失败，物品未转移"); return; }
                entity.transform.SetParent(_heroine, true);
                entity.transform.localPosition = new Vector3(0, 1f, .35f);
                foreach (var collider in entity.GetComponentsInChildren<Collider>()) collider.enabled = false;
                entity.ownerId = "baiweixi";
                entity.state = "在白未晞手中";
                resultId = entity.entityId;
            }
            else if (!string.IsNullOrEmpty(action.spawnEntityId))
            {
                var entity = SpawnEntity(action.spawnEntityId);
                if (entity == null) { Fail(action, "实体未能生成，动作未完成"); return; }
                resultId = entity.entityId;
            }
            if (action.actionId == "cook_meal") RestoreCookingDevices();
            _state.ConfirmAction(action.instanceId, true, "执行器已确认结果", resultId);
            _activeActionId = null;
            _cookingStarted = false;
            _presentation?.SetWalking(false);
            PlayState("Idle");
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
                _presentation?.SetWalking(false);
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

        private WorldEntity SpawnEntity(string entityId)
        {
            _entities.RemoveAll(e => e == null);
            if (_entities.Count >= 32 || _tableTop == null) return null;
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
                var entity = bowl.AddComponent<WorldEntity>();
                entity.entityId = "bowl_" + Guid.NewGuid().ToString("N");
                entity.displayName = "一碗热汤面";
                entity.state = "在餐桌上";
                entity.canPickUp = true;
                RegisterEntity(entity);
                _toast?.Invoke("桌上多了一碗热汤面。");
                return entity;
            }
            return null;
        }
    }
}
