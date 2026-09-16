using System;
using AiPeople.Core;
using UnityEngine;
using UnityEngine.InputSystem;

namespace AiPeople.World
{
    /// <summary>
    /// 第一人称男主控制：移动/视角/位置锚点检测；聊天聚焦时暂停移动并解锁光标。
    /// 男主客观状态只经 WorldInputData 投影，不在此处改写女主状态。
    /// </summary>
    [RequireComponent(typeof(CharacterController))]
    public sealed class PlayerController : MonoBehaviour
    {
        public static void ConfigureCapsule(CharacterController controller)
        {
            controller.height = 1.7f;
            controller.radius = .28f;
            controller.center = new Vector3(0, .85f, 0);
            controller.skinWidth = .02f;
            controller.stepOffset = .25f;
        }

        public float moveSpeed = 2.6f;
        public float lookSensitivity = 0.12f;
        public Camera viewCamera;
        public Waypoint[] waypoints = Array.Empty<Waypoint>();

        public bool ChatFocused { get; private set; }
        public Waypoint CurrentWaypoint { get; private set; }
        public Interactable CurrentInteractable { get; private set; }

        public float interactRange = 2.8f;
        public bool HasHeldItem => !string.IsNullOrEmpty(_heldItemLabel);

        /// <summary>离开/进入某个位置锚点时触发（参数为当前锚点，可为 null）。</summary>
        public event Action<Waypoint> LocationChanged;

        public event Action<bool> ChatFocusChanged;

        /// <summary>注视的可交互物变化时触发（参数可为 null）。</summary>
        public event Action<Interactable> InteractTargetChanged;

        /// <summary>玩家按下交互键（参数为当前注视的可交互物）。</summary>
        public event Action<Interactable> InteractRequested;

        private CharacterController _controller;
        private float _pitch;
        private float _verticalSpeed;
        private string _heldItemLabel;
        private string _doorPrompt;

        private void Awake()
        {
            _controller = GetComponent<CharacterController>();
        }

        private void Update()
        {
            if (Keyboard.current != null && Keyboard.current.escapeKey.wasPressedThisFrame && ChatFocused)
            {
                SetChatFocused(false);
            }

            if (!ChatFocused)
            {
                Look();
                Move();
            }

            UpdateWaypoint();
            UpdateInteraction();
        }

        public void SetHeldItem(string label)
        {
            _heldItemLabel = label ?? string.Empty;
        }

        public void SetChatFocused(bool focused)
        {
            if (ChatFocused == focused)
            {
                return;
            }

            ChatFocused = focused;
            Cursor.lockState = focused ? CursorLockMode.None : CursorLockMode.Locked;
            Cursor.visible = focused;
            ChatFocusChanged?.Invoke(focused);
        }

        public WorldInputData ComposeWorldInput()
        {
            if (CurrentWaypoint == null)
            {
                return new WorldInputData
                {
                    location_id = "apartment_door",
                    location_label = "出租屋",
                    activity = "站着",
                    body = "没有明显不适",
                    held_item = "",
                    scene = "屋里的灯亮着",
                };
            }

            return new WorldInputData
            {
                location_id = CurrentWaypoint.locationId,
                location_label = CurrentWaypoint.locationLabel,
                activity = CurrentWaypoint.activity,
                body = CurrentWaypoint.body,
                held_item = HasHeldItem ? _heldItemLabel : CurrentWaypoint.heldItem,
                scene = CurrentWaypoint.scene,
            };
        }

        private void UpdateInteraction()
        {
            Interactable target = null;
            if (!ChatFocused && viewCamera != null)
            {
                var ray = new Ray(viewCamera.transform.position, viewCamera.transform.forward);
                if (Physics.Raycast(ray, out RaycastHit hit, interactRange, ~0, QueryTriggerInteraction.Ignore))
                {
                    target = hit.collider.GetComponentInParent<Interactable>();
                }
            }

            string doorPrompt = target != null ? target.GetComponent<ApartmentDoor>()?.Prompt : null;
            if (target != CurrentInteractable || doorPrompt != _doorPrompt)
            {
                _doorPrompt = doorPrompt;
                CurrentInteractable = target;
                InteractTargetChanged?.Invoke(target);
            }

            if (target != null && Keyboard.current != null && Keyboard.current.eKey.wasPressedThisFrame)
            {
                InteractRequested?.Invoke(target);
            }
        }

        private void Look()
        {
            if (Mouse.current == null || viewCamera == null)
            {
                return;
            }

            Vector2 delta = Mouse.current.delta.ReadValue();
            transform.Rotate(0f, delta.x * lookSensitivity, 0f);
            _pitch = Mathf.Clamp(_pitch - delta.y * lookSensitivity, -75f, 75f);
            viewCamera.transform.localEulerAngles = new Vector3(_pitch, 0f, 0f);
        }

        private void Move()
        {
            if (Keyboard.current == null)
            {
                return;
            }

            Vector2 axis = Vector2.zero;
            if (Keyboard.current.wKey.isPressed) axis.y += 1f;
            if (Keyboard.current.sKey.isPressed) axis.y -= 1f;
            if (Keyboard.current.dKey.isPressed) axis.x += 1f;
            if (Keyboard.current.aKey.isPressed) axis.x -= 1f;
            axis = Vector2.ClampMagnitude(axis, 1f);

            Vector3 direction = transform.right * axis.x + transform.forward * axis.y;
            _verticalSpeed = _controller.isGrounded ? -1f : _verticalSpeed + Physics.gravity.y * Time.deltaTime;
            Vector3 motion = direction * moveSpeed + Vector3.up * _verticalSpeed;
            _controller.Move(motion * Time.deltaTime);
        }

        private void UpdateWaypoint()
        {
            Waypoint best = null;
            float bestDistance = float.MaxValue;
            foreach (Waypoint waypoint in waypoints)
            {
                if (waypoint == null || !waypoint.Contains(transform.position))
                {
                    continue;
                }

                float distance = Vector3.Distance(transform.position, waypoint.transform.position);
                if (distance < bestDistance)
                {
                    bestDistance = distance;
                    best = waypoint;
                }
            }

            if (best != CurrentWaypoint)
            {
                CurrentWaypoint = best;
                LocationChanged?.Invoke(CurrentWaypoint);
            }
        }
    }
}
