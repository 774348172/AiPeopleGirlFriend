using System;
using System.Collections;
using System.Collections.Generic;
using AiPeople.Character;
using AiPeople.Core;
using AiPeople.Shell;
using AiPeople.UI;
using AiPeople.World;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.UI;

namespace AiPeople.App
{
    /// <summary>
    /// M1 运行时装配入口：配置 → 世界/玩家/角色 → UI → 与 8767 服务连线。
    /// 场景中只需要一个挂载本组件的 GameObject（见菜单 AiPeople/重建 Apartment 场景）。
    /// 边界：表现层只读已提交状态；不改写后端世界；不伪造动作来源。
    /// </summary>
    public sealed class AiPeopleApp : MonoBehaviour
    {
        private AiPeopleConfig _config;
        private AiPeopleClient _client;
        private LocalBackendLauncher _backendLauncher;
        private bool _backendStartupFinished;
        private GameStateModel _state;
        private LocalHistory _history;
        private PlayerController _player;
        private ChatPanel _chat;
        private HudPanel _hud;
        private SceneAtmosphere _atmosphere;
        private ApartmentBuilder.Result _world;
        private GameWorldState _gameState;
        private GameWorldServer _gameServer;
        private HeroineDirector _director;
        private readonly List<KitchenDevice> _kitchenDevices = new List<KitchenDevice>();
        private GameTimeline _timeline;
        private CharacterActor _heroine;
        private DesktopShellController _shell;
        private ShellSettingsData _shellSettings;
        private DesktopChatBar _chatBar;
        private SettingsPanel _settingsPanel;
        private ChatSession _chatSession;
        private ChatUiServer _uiServer;
        private ChatWindowLauncher _chatWindowLauncher;
        private TrayIcon _tray;
        private GlobalHotkey _quitHotkey;
        private bool _chatWindowShowRequested;
        private string _lastBubbledLine = string.Empty;
        private float _launcherTimer;
        private bool _desktopPresentation;
        private readonly string _snapshotInstance = Guid.NewGuid().ToString("N");
        private readonly long _snapshotStartedAtMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        private long _snapshotSequence;
        private bool _busy;
        private float _lastInputProbe;
        private string _connection = "未连接";

        [Header("房屋模型")]
        [Tooltip("已校准尺寸、材质和静态碰撞的房屋 Prefab。为空时使用程序化占位房间。")]
        public GameObject apartmentShellPrefab;

        [Header("白未晞表现")]
        [Tooltip("正式模型（FBX 资产）。为空时自动降级为胶囊占位。")]
        public GameObject heroineModelPrefab;

        [Tooltip("动画控制器（菜单 AiPeople/生成女主 Animator Controller 生成）。为空时保持绑定姿态。")]
        public RuntimeAnimatorController heroineAnimatorController;

        public Vector3 heroinePosition = new Vector3(1.2f, 0f, 1.55f);
        public float heroineYaw = 180f;
        public float heroineHeight = 1.62f;

        [Header("游戏世界（M3）")]
        [Tooltip("游戏世界 HTTP 服务端口（仅绑定 127.0.0.1，供后端 RemoteGameWorld 适配器连接）。")]
        public int gameServerPort = 8770;
        public bool gameServerEnabled = true;

        [Header("桌面形态（M5）")]
        [Tooltip("桌面外壳总开关；Editor 内恒为普通窗口模式（壁纸层需独立构建验证）。")]
        public bool desktopShellEnabled = true;

        [Tooltip("桌面形态固定机位（房间视角）。")]
        public Vector3 desktopCameraPosition = new Vector3(-0.1f, 1.45f, -1.1f);
        public Vector3 desktopCameraEuler = new Vector3(3f, 21f, 0f);

        private void Start()
        {
            // 小窗进程：只承载对话小窗，不创建房间/世界/后端客户端
            if (ChatWindowApp.IsChatWindowMode())
            {
                gameObject.AddComponent<ChatWindowApp>();
                return;
            }

            // 单实例：已有实例在运行时，请求它显示小窗后本进程退出
            if (!Application.isEditor && !SingleInstance.TryAcquire("Global\\AiPeopleCatGirlfriend"))
            {
                Debug.Log("[AiPeople] 已有实例在运行，本进程退出并唤出既有实例");
                StartCoroutine(ActivateExistingInstanceAndQuit());
                return;
            }

            // 在场景和模型装配期间提前启动隐藏小窗，避免首次点击时等待 Unity 子进程冷启动。
            if (!Application.isEditor)
            {
                _chatWindowLauncher = new ChatWindowLauncher();
                _chatWindowLauncher.EnsureStarted();
            }

            _config = AiPeopleConfig.Load();
            _client = new AiPeopleClient(_config.data);
            if (!Application.isEditor)
            {
                _backendLauncher = new LocalBackendLauncher(_config.data);
                StartCoroutine(StartBackend());
            }
            _state = new GameStateModel();
            _state.Changed += OnStateChanged;
            _history = new LocalHistory(_config.data.saveId);

            _world = ApartmentBuilder.Build(apartmentShellPrefab);
            Camera viewCamera = CreatePlayer(_world);
            CharacterActor heroine = CharacterActor.Create(heroinePosition, heroineYaw, heroineModelPrefab, heroineAnimatorController, heroineHeight);
            _heroine = heroine;

            _atmosphere = _world.Root.gameObject.AddComponent<SceneAtmosphere>();
            _atmosphere.Bind(_world.Sun, _world.Lamp, _world.WindowRenderer, _world.Rain);

            SetUpGameWorld(heroine);

            EnsureEventSystem();
            GameObject canvas = UiRoot.CreateCanvas();
            _chat = ChatPanel.Create(canvas.transform, _history, Submit, OnChatFocusChanged);
            _hud = HudPanel.Create(canvas.transform, _config.data);
            _chat.RebuildFromHistory();

            _chatBar = DesktopChatBar.Create(canvas.transform, _history, OnChatBarClicked, OpenSettings);
            _settingsPanel = SettingsPanel.Create(
                canvas.transform, CloseSettings, QuitApplication,
                OnWindowModeChanged, OnFpsChanged, OnHudChanged, OnAmbientChanged);

            _player.ChatFocusChanged += focused =>
            {
                if (!focused && _chat != null)
                {
                    _chat.Blur();
                }
            };
            _player.InteractTargetChanged += OnInteractTargetChanged;
            _player.InteractRequested += OnInteractRequested;
            _player.SetChatFocused(false);
            RefreshHud();
            _atmosphere.ApplyStatus(_state.Latest);

            SetUpDesktopShell(viewCamera);
            SetUpChatBridge();

            StartCoroutine(Boot());
        }

        private Camera CreatePlayer(ApartmentBuilder.Result world)
        {
            var playerGo = new GameObject("Player");
            playerGo.transform.position = world.PlayerSpawn;
            playerGo.transform.rotation = Quaternion.Euler(0f, world.PlayerYaw, 0f);

            var controller = playerGo.AddComponent<CharacterController>();
            PlayerController.ConfigureCapsule(controller);

            _player = playerGo.AddComponent<PlayerController>();

            var cameraGo = new GameObject("ViewCamera");
            cameraGo.transform.SetParent(playerGo.transform, false);
            cameraGo.transform.localPosition = new Vector3(0f, 1.6f, 0f);
            var camera = cameraGo.AddComponent<Camera>();
            camera.nearClipPlane = 0.05f;
            camera.farClipPlane = 200f;
            cameraGo.tag = "MainCamera";

            _player.viewCamera = camera;
            _player.waypoints = world.Waypoints.ToArray();
            return camera;
        }

        private static void EnsureEventSystem()
        {
            if (UnityEngine.Object.FindFirstObjectByType<EventSystem>() != null)
            {
                return;
            }

            var eventSystemGo = new GameObject("EventSystem");
            eventSystemGo.AddComponent<EventSystem>();
            eventSystemGo.AddComponent<InputSystemUIInputModule>();
        }

        private IEnumerator Boot()
        {
            if (_backendLauncher != null)
            {
                while (!_backendStartupFinished) yield return null;
                if (!string.IsNullOrEmpty(_backendLauncher.Error))
                {
                    _connection = _backendLauncher.Error;
                    UnityEngine.Debug.LogError("[AiPeople] " + _connection);
                    RefreshHud();
                    yield break;
                }
            }
            yield return _client.Health((ok, detail) =>
            {
                _connection = ok ? "已连接" : "连接失败：" + detail;
            });
            RefreshHud();
            yield return RefreshStatus();
            while (_timeline != null && !_timeline.Ready)
            {
                yield return new WaitForSecondsRealtime(5);
                yield return RefreshStatus();
            }
        }

        private IEnumerator StartBackend()
        {
            yield return _backendLauncher.EnsureReady(message => { _connection = message; RefreshHud(); });
            _backendStartupFinished = true;
        }

        private IEnumerator RefreshStatus()
        {
            yield return _client.FetchStatus(
                _config.data.saveId,
                response => { _state.Apply(response); _timeline?.Initialize(response.game_clock); },
                error => _state.ReportError(error));
        }

        private void Submit(string text)
        {
            if (_busy)
            {
                _chat.SetHint("正在生成，请稍候…");
                return;
            }

            text = (text ?? string.Empty).Trim();
            if (text.Length == 0)
            {
                return;
            }

            _busy = true;
            _chat.ShowUser(text);
            _chat.BeginHeroine();
            RefreshHud();

            var request = new ChatRequest
            {
                save_id = _config.data.saveId,
                text = text,
                world = ComposeWorld(),
            };

            StartCoroutine(_client.ChatStream(request, new AiPeopleClient.ChatCallbacks
            {
                onDelta = delta => _chat.AppendDelta(delta),
                onCommitted = committed => _chat.CompleteHeroine(committed),
                onError = error => _chat.FailHeroine(error),
                onFinished = () =>
                {
                    _busy = false;
                    RefreshHud();
                    if (_chatBar != null)
                    {
                        _chatBar.Refresh();
                    }

                    StartCoroutine(RefreshStatus());
                },
            }));
        }

        private void OnChatFocusChanged(bool focused)
        {
            if (_player != null)
            {
                _player.SetChatFocused(focused);
            }
        }

        private void RefreshHud()
        {
            if (_hud == null || _state == null)
            {
                return;
            }

            _hud.SetState(_state, _connection, _busy ? "生成中" : "空闲");
        }

        /// <summary>装配游戏世界：世界状态（事实源）+ 本地 HTTP 服务（供后端连接）+ 女主行动导演。</summary>
        private void SetUpGameWorld(CharacterActor heroine)
        {
            _gameState = new GameWorldState();

            if (gameServerEnabled)
            {
                _gameServer = gameObject.AddComponent<GameWorldServer>();
                _gameServer.port = gameServerPort;
                _gameServer.StartServer(_gameState);
            }

            _director = gameObject.AddComponent<HeroineDirector>();
            _timeline = gameObject.AddComponent<GameTimeline>();
            _timeline.Configure(_config.data.saveId);
            _director.Timeline = _timeline;
            _director.UsesImportedLayout = apartmentShellPrefab != null;
            if (apartmentShellPrefab != null)
            {
                string[] ids = { "apartment_living_room", "apartment_kitchen", "apartment_master_bedroom",
                    "apartment_secondary_bedroom", "apartment_bathroom", "apartment_balcony", "apartment_hall" };
                for (int i = 0; i < ids.Length; i++)
                    _director.RegisterAnchor(ids[i], ImportedApartmentLayout.TraversalPoints[i].Position);
            }
            RegisterSceneEntity(_world.Glass, "water_cup", "水杯", "在餐桌上", true);
            RegisterSceneEntity(_world.CardboardBox, "cardboard_box", "纸箱", "在场景中", false);
            RegisterKitchenDevice("KitchenStove", "kitchen_stove", "燃气灶", true);
            RegisterKitchenDevice("KitchenHood", "kitchen_hood", "抽油烟机", true);
            RegisterKitchenDevice("KitchenFridge", "kitchen_fridge", "冰箱", false);
            RegisterKitchenDevice("KitchenMicrowave", "kitchen_microwave", "微波炉", false);
            RegisterKitchenDevice("KitchenRiceCooker", "kitchen_rice_cooker", "电饭煲", false);
            RegisterKitchenDevice("KitchenKettle", "kitchen_kettle", "电热水壶", false);
            _director.Stove = FindKitchenDevice("kitchen_stove");
            _director.Hood = FindKitchenDevice("kitchen_hood");
            if (_gameServer != null)
            {
                _gameServer.SnapshotProvider = () => JsonUtility.ToJson(ComposeWorld());
                _gameServer.Timeline = _timeline;
            }
            foreach (Waypoint waypoint in _world.Waypoints)
            {
                _director.RegisterAnchor(waypoint.locationId, waypoint.transform.position);
            }

            _director.RegisterAnchor("apartment_window", heroinePosition);
            _director.Configure(
                _gameState,
                heroine.transform,
                heroine.Animator,
                _world.TableTop,
                _world.Root,
                message =>
                {
                    if (_hud != null)
                    {
                        _hud.ShowToast(message);
                    }
                });
        }

        private void RegisterSceneEntity(GameObject target, string id, string label, string state, bool canPickUp)
        {
            if (target == null) return;
            var entity = target.AddComponent<WorldEntity>();
            entity.entityId = id; entity.displayName = label; entity.state = state; entity.canPickUp = canPickUp;
            _director.RegisterEntity(entity);
        }

        private void RegisterKitchenDevice(string node, string id, string label, bool controllable)
        {
            Transform target = _world.Root.Find("Furniture/" + node);
            if (target == null) return;
            var device = target.gameObject.AddComponent<KitchenDevice>();
            device.Configure(id, label, controllable);
            device.Changed += PublishRoomControls;
            _kitchenDevices.Add(device);
        }

        private KitchenDevice FindKitchenDevice(string id) =>
            _kitchenDevices.Find(device => device != null && device.deviceId == id);

        private DeviceFactData[] CaptureKitchenDevices()
        {
            var facts = new List<DeviceFactData>();
            foreach (var device in _kitchenDevices)
                if (device != null && device.isActiveAndEnabled)
                    facts.Add(device.Capture(apartmentShellPrefab != null));
            return facts.ToArray();
        }

        private string SetKitchenDeviceState(string id, bool running)
        {
            var device = FindKitchenDevice(id);
            if (device == null || !device.controllable)
                return "{\"accepted\":false,\"reason\":\"设备未登记或没有可操作的开关\"}";
            device.SetRunning(running, true);
            PublishRoomControls();
            return "{\"accepted\":true,\"reason\":\"\"}";
        }

        private void PublishRoomControls()
        {
            if (_uiServer == null) return;
            var navigation = _world.Root.GetComponentInChildren<ApartmentNavigation>();
            _uiServer.PublishRoomState(navigation != null ? navigation.DoorsJson() : "{\"doors\":[]}",
                CaptureKitchenDevices());
        }

        private void OnStateChanged()
        {
            RefreshHud();
            var presentation = _heroine != null ? _heroine.GetComponent<CharacterPresentation>() : null;
            presentation?.ApplyMind(_state.Latest?.heroine?.living_mind);
            if (_atmosphere != null)
            {
                _atmosphere.ApplyStatus(_state.Latest);
            }
        }

        /// <summary>男主侧场景投影：在位置锚点描述上追加玩家造成的场景事实（灯/窗/手持物）。</summary>
        private WorldInputData ComposeWorld()
        {
            WorldInputData world = _desktopPresentation
                ? new WorldInputData
                {
                    location_id = "apartment_table",
                    location_label = "出租屋餐桌旁",
                    activity = "在桌边坐着",
                    body = "没有明显不适",
                    held_item = string.Empty,
                    scene = "屋里很安静",
                }
                : _player.ComposeWorldInput();

            // Waypoint prose is a design-time hint, not a sensor: it can claim
            // lights/windows are open even after the player has changed them.
            world.scene = "出租屋室内";
            world.held_item = _player != null ? _player.HeldItemLabel : string.Empty;
            var navigation = _world.Root.GetComponentInChildren<ApartmentNavigation>();
            world.scene_facts = SceneFactProjection.Capture(
                navigation, _atmosphere,
                apartmentShellPrefab == null, _director);
            var playerFact = ApartmentLocations.Locate("protagonist", _player.transform.position,
                apartmentShellPrefab != null, _player.Motion);
            var heroineFact = ApartmentLocations.Locate("baiweixi", _heroine.transform.position,
                apartmentShellPrefab != null, _director.Motion);
            world.location_id = playerFact.location_id;
            world.location_label = playerFact.location_label;
            var entities = _director.CaptureEntities(_player.transform);
            _gameState.ObserveHeroine(heroineFact.location_id);
            _gameState.ObserveEntities(entities);
            long observedAtMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            long sequence = ++_snapshotSequence;
            world.game_snapshot = new GameSnapshotData {
                save_id = _config.data.saveId,
                game_clock = _timeline.Capture(),
                instance_started_at_ms = _snapshotStartedAtMs,
                observed_at_ms = observedAtMs,
                instance_id = _snapshotInstance, sequence = sequence,
                actors = new[] { playerFact, heroineFact }, entities = entities,
                devices = CaptureKitchenDevices(),
                facts = SceneFactProjection.CaptureObservations(navigation, _atmosphere,
                    apartmentShellPrefab == null, _director, sequence, observedAtMs),
                actions = _gameState.ActionFacts(), receipts = _gameState.ReceiptFacts() };
            return world;
        }

        private void OnInteractTargetChanged(Interactable target)
        {
            if (_hud == null)
            {
                return;
            }

            if (target == null)
            {
                _hud.SetPrompt(string.Empty);
                return;
            }

            switch (target.kind)
            {
                case InteractKind.ToggleLight:
                    _hud.SetPrompt(_atmosphere != null && _atmosphere.LightOn ? "按 E 关灯" : "按 E 开灯");
                    break;
                case InteractKind.ToggleWindow:
                    _hud.SetPrompt(_atmosphere != null && _atmosphere.WindowOpen ? "按 E 关窗" : "按 E 开窗");
                    break;
                case InteractKind.TakeWater:
                    _hud.SetPrompt(_player.HasHeldItem ? "按 E 把水杯放回桌上" : "按 E 拿起水杯");
                    break;
                case InteractKind.ExamineBox:
                    _hud.SetPrompt("按 E 看看纸箱");
                    break;
                case InteractKind.ToggleDoor:
                    _hud.SetPrompt(target.GetComponent<ApartmentDoor>()?.Prompt ?? string.Empty);
                    break;
                case InteractKind.ToggleDevice:
                    var device = target.GetComponent<KitchenDevice>();
                    _hud.SetPrompt(device != null ? "按 E " + (device.IsOn ? "关闭" : "打开") + device.displayName : string.Empty);
                    break;
            }
        }

        private void OnInteractRequested(Interactable target)
        {
            if (target == null || _hud == null)
            {
                return;
            }

            switch (target.kind)
            {
                case InteractKind.ToggleDoor:
                {
                    var door = target.GetComponent<ApartmentDoor>();
                    if (door != null && !door.RequestState(!door.TargetOpen, out string reason)) _hud.ShowToast(reason);
                    break;
                }
                case InteractKind.ToggleDevice:
                {
                    var device = target.GetComponent<KitchenDevice>();
                    if (device != null && device.SetRunning(!device.IsOn, true))
                        _hud.ShowToast(device.displayName + (device.IsOn ? "已打开。" : "已关闭。"));
                    break;
                }
                case InteractKind.ToggleLight:
                {
                    bool on = _atmosphere != null && _atmosphere.ToggleLight();
                    _hud.ShowToast(on ? "你打开了灯。" : "你关掉了灯。");
                    break;
                }
                case InteractKind.ToggleWindow:
                {
                    bool open = _atmosphere != null && _atmosphere.ToggleWindow();
                    _hud.ShowToast(open ? "你推开了窗。" : "你关上了窗。");
                    break;
                }
                case InteractKind.TakeWater:
                    var entity = _director.FindEntity("water_cup");
                    if (entity != null && entity.ownerId == "baiweixi")
                    { _hud.ShowToast("水杯在白未晞手里。"); break; }
                    if (_player.HasHeldItem)
                    {
                        _player.SetHeldItem(string.Empty);
                        if (entity != null) { entity.ownerId = ""; entity.state = "在餐桌上"; }
                        if (_world.Glass != null)
                        {
                            _world.Glass.SetActive(true);
                        }
                        _hud.ShowToast("你把水杯放回了桌上。");
                    }
                    else
                    {
                        _player.SetHeldItem("一杯温水");
                        if (entity != null) { entity.ownerId = "protagonist"; entity.state = "在男主手中"; }
                        if (_world.Glass != null)
                        {
                            _world.Glass.SetActive(false);
                        }
                        _hud.ShowToast("你拿起了那杯水。");
                    }
                    break;
                case InteractKind.ExamineBox:
                    _hud.ShowToast(string.IsNullOrEmpty(target.examineText)
                        ? "这是最初收留她的纸箱。她不让扔。"
                        : target.examineText);
                    break;
            }

            OnInteractTargetChanged(_player.CurrentInteractable);
        }

        // ---------- 桌面形态（M5） ----------

        /// <summary>装配桌面外壳：窗口形态决策、会话态、常驻对话框与设置面板。</summary>
        private void SetUpDesktopShell(Camera viewCamera)
        {
            if (!desktopShellEnabled)
            {
                if (_chatBar != null)
                {
                    _chatBar.SetVisible(false);
                }

                return;
            }

            _shellSettings = ShellSettingsStore.Load();
            _shell = gameObject.AddComponent<DesktopShellController>();
            _shell.SessionStateChanged += OnSessionStateChanged;
            _shell.SubmitKeyPressed += () =>
            {
                if (_chat != null)
                {
                    _chat.TriggerSubmit();
                }
            };
            _shell.Notify += message =>
            {
                if (_hud != null && _hud.gameObject.activeSelf)
                {
                    _hud.ShowToast(message);
                }

                Debug.Log("[AiPeople] " + message);
            };
            _shell.Initialize(_shellSettings, viewCamera, _chatBar.GetComponentInParent<Canvas>());
            var doors = _world.Root.GetComponentsInChildren<ApartmentDoor>();
            _shell.ChatProcessId = () => _chatWindowLauncher?.ProcessId ?? 0;
            _shell.HasVisualActivity = () => _chatWindowShowRequested || _busy
                || (_chatSession?.IsGenerating ?? false) || (_director?.IsWalking ?? false)
                || Array.Exists(doors, door => door.IsMoving);
            _shell.InterceptHotkey = () =>
            {
                if (_shell == null || !_shell.DesktopPresentation)
                {
                    return false;
                }

                ToggleChatWindow();
                return true;
            };
            _shell.ChatBarClicked += ToggleChatWindow;

            if (_shell.DesktopPresentation)
            {
                ApplyDesktopPresentation(viewCamera);
            }
            else
            {
                _chatBar.SetVisible(false);
            }
        }

        /// <summary>桌面形态表现：固定机位、停用走位、隐藏完整对话面板、显示常驻对话框。</summary>
        private void ApplyDesktopPresentation(Camera viewCamera)
        {
            _desktopPresentation = true;

            viewCamera.transform.SetParent(null);
            viewCamera.transform.position = desktopCameraPosition;
            viewCamera.transform.rotation = Quaternion.Euler(desktopCameraEuler);
            if (apartmentShellPrefab != null)
            {
                viewCamera.fieldOfView = ImportedApartmentLayout.CameraFieldOfView(viewCamera.aspect);
                if (viewCamera.GetComponent<ApartmentWallpaperCamera>() == null)
                    viewCamera.gameObject.AddComponent<ApartmentWallpaperCamera>();
                viewCamera.clearFlags = CameraClearFlags.SolidColor;
                viewCamera.backgroundColor = new Color(.17f, .21f, .27f);
            }
            Cursor.lockState = CursorLockMode.None;
            Cursor.visible = true;

            _player.enabled = false;
            var characterController = _player.GetComponent<CharacterController>();
            if (characterController != null)
            {
                characterController.enabled = false;
            }
            // Desktop mode places the logical protagonist at the table anchor.
            _player.transform.position = _world.Waypoints[0].transform.position;

            _chat.SetVisible(false);
            _hud.SetVisible(_shellSettings.showHud);
            _chatBar.SetVisible(true);
            _chatBar.SetHint("点击这里和她说话（热键 " + _shellSettings.hotkey + "）");
            _chatBar.Refresh();
        }

        private void OnChatBarClicked()
        {
            // 构建版一律走对话小窗（旧全屏会话态仅保留给 Editor 调试，v2.3 已作废）
            if (_shell != null && !Application.isEditor)
            {
                ToggleChatWindow();
                return;
            }

            if (_shell != null && !_shell.IsSessionActive)
            {
                _shell.EngageSession();
            }
        }

        /// <summary>桌面形态：唤出/收起对话小窗（主进程只置标志，小窗进程轮询 /ui/state 自行显示）。</summary>
        private void ToggleChatWindow()
        {
            _chatWindowShowRequested = !_chatWindowShowRequested;
            if (_chatWindowShowRequested)
            {
                _chatWindowLauncher?.EnsureStarted();
            }

            Debug.Log("[AiPeople] 对话小窗显示请求=" + _chatWindowShowRequested);
        }

        /// <summary>装配对话桥：主进程的对话会话（/ui 接口与小窗共用）+ 小窗进程管理。</summary>
        private void SetUpChatBridge()
        {
            _chatSession = gameObject.AddComponent<ChatSession>();
            _chatSession.Initialize(_client, _history, _config.data.saveId, ComposeWorld);
            _chatSession.Changed += OnChatSessionChanged;

            _chatWindowLauncher ??= new ChatWindowLauncher();
            if (!Application.isEditor)
            {
                _chatWindowLauncher.EnsureStarted();
            }

            _uiServer = gameObject.AddComponent<ChatUiServer>();
            _uiServer.StartServer(
                _chatSession,
                () => _chatWindowShowRequested,
                () => { _chatWindowShowRequested = false; },
                () =>
                {
                    _chatWindowShowRequested = false;
                    QuitApplication();
                },
                () => { _chatWindowShowRequested = true; },
                mode => ApplyWindowModeFromChatWindow(mode));

            var roomNavigation = _world.Root.GetComponentInChildren<ApartmentNavigation>();
            if (roomNavigation != null)
            {
                _uiServer.ConfigureRoomControls(roomNavigation.SetDoorState);
                roomNavigation.DoorsChanged += PublishRoomControls;
            }
            _uiServer.ConfigureKitchenDevices(SetKitchenDeviceState);
            PublishRoomControls();

            // 系统托盘（构建版）：左键唤出小窗；右键菜单显示/隐藏、切换窗口模式、退出
            if (!Application.isEditor)
            {
                _tray = new TrayIcon();
                _tray.Start("猫咪女友：她住在桌面上");
                if (!_tray.IsRunning && !string.IsNullOrEmpty(_tray.LastError))
                {
                    Debug.LogWarning("[AiPeople] 托盘图标不可用：" + _tray.LastError);
                }
            }

            // 全局退出热键 Ctrl+Alt+Q
            _quitHotkey = new GlobalHotkey();
            if (!_quitHotkey.Start("Ctrl+Alt+Q", 0x4171))
            {
                Debug.LogWarning("[AiPeople] 退出热键注册失败：" + _quitHotkey.LastError);
            }
        }

        /// <summary>已有实例时的自愈路径：请求它显示小窗，然后本进程退出。</summary>
        private IEnumerator ActivateExistingInstanceAndQuit()
        {
            yield return new WaitForSecondsRealtime(0.5f);
            using (var request = new UnityEngine.Networking.UnityWebRequest("http://127.0.0.1:8771/ui/activate", "POST"))
            {
                request.uploadHandler = new UnityEngine.Networking.UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes("{}"));
                request.uploadHandler.contentType = "application/json";
                request.downloadHandler = new UnityEngine.Networking.DownloadHandlerBuffer();
                request.timeout = 5;
                yield return request.SendWebRequest();
            }

            Application.Quit();
        }

        private void OnChatSessionChanged()
        {
            _chatBar?.Refresh();

            string line = _chatSession != null ? _chatSession.LastHeroineLine : null;
            if (!string.IsNullOrEmpty(line) && line != _lastBubbledLine)
            {
                _lastBubbledLine = line;
                Debug.Log("[AiPeople] 新回复（气泡提示）：" + line);
                _chatBar?.SetHint("她刚说了一句：" + (line.Length > 26 ? line.Substring(0, 26) + "…" : line));
            }
        }

        private void OnSessionStateChanged(bool active)
        {
            bool desktop = _shell != null && _shell.DesktopPresentation;
            if (_chat != null)
            {
                _chat.SetVisible(!desktop || active);
            }

            if (_chatBar != null)
            {
                _chatBar.SetVisible(desktop && !active);
            }

            if (_hud != null)
            {
                _hud.SetVisible(!desktop || (_shellSettings != null && _shellSettings.showHud));
            }

            if (active)
            {
                StartCoroutine(FocusChatNextFrame());
            }
            else
            {
                CloseSettings();
                RefreshHud();
            }
        }

        private IEnumerator FocusChatNextFrame()
        {
            yield return null;
            if (_chat != null)
            {
                _chat.FocusInput();
            }

            yield return null;
            if (_chat != null)
            {
                bool keyboardFocus = _shell == null || _shell.HasKeyboardFocus;
                Debug.Log("[AiPeople] 会话输入焦点=" + _chat.IsInputFocused + " 窗口键盘焦点=" + keyboardFocus);
                _chat.SetHint(keyboardFocus ? string.Empty : "系统未授予键盘焦点：点一下输入框即可打字");
            }
        }

        private void OpenSettings()
        {
            // 构建版：壁纸层不可交互，齿轮改为唤出对话小窗（窗口模式切换在小窗里）
            if (_shell != null && !Application.isEditor)
            {
                ToggleChatWindow();
                return;
            }

            if (_shell != null && !_shell.IsSessionActive)
            {
                _shell.EngageSession();
            }

            ToggleSettings();
        }

        private void ToggleSettings()
        {
            if (_settingsPanel == null)
            {
                return;
            }

            bool visible = !_settingsPanel.gameObject.activeSelf;
            _settingsPanel.SetVisible(visible);
            if (visible)
            {
                RefreshSettingsPanel();
            }
        }

        private void CloseSettings()
        {
            _settingsPanel?.SetVisible(false);
        }

        private void RefreshSettingsPanel()
        {
            if (_settingsPanel == null || _shellSettings == null)
            {
                return;
            }

            string status = (_shell != null ? _shell.LastDetail : "未启用桌面外壳")
                + "\n设置文件：" + ShellSettingsStore.Location;
            _settingsPanel.Refresh(_shellSettings, status);
        }

        private void OnWindowModeChanged(ShellWindowMode mode)
        {
            _shell?.ApplyMode(mode);
            RefreshSettingsPanel();
        }

        private void OnFpsChanged(int fps)
        {
            _shell?.SetTargetFps(fps);
            RefreshSettingsPanel();
        }

        private void OnHudChanged(bool visible)
        {
            if (_shellSettings == null)
            {
                return;
            }

            _shellSettings.showHud = visible;
            ShellSettingsStore.Save(_shellSettings);
            bool desktop = _shell != null && _shell.IsDesktopShell;
            _hud?.SetVisible(!desktop || visible);
            RefreshSettingsPanel();
        }

        private void OnAmbientChanged(bool enabled)
        {
            if (_shellSettings == null)
            {
                return;
            }

            _shellSettings.ambientBehaviors = enabled;
            ShellSettingsStore.Save(_shellSettings);
            RefreshSettingsPanel();
        }

        private void QuitApplication()
        {
            _tray?.Dispose();
            _quitHotkey?.Dispose();
            _chatWindowLauncher?.Shutdown();
            _backendLauncher?.StopOwned();
            _shell?.QuitApplication();
        }

        private void OnApplicationQuit() => _backendLauncher?.StopOwned();

        private void Update()
        {
            // /ui 接口的命令一律在主线程执行（Unity API 不能在工作线程调用）
            if (_uiServer != null)
            {
                while (_uiServer.TryDequeueMainThreadCommand(out Action uiCommand))
                {
                    _shell?.NotifyActivity();
                    uiCommand?.Invoke();
                }
            }
            if (_gameServer != null)
            {
                _gameServer.ProcessMainThreadCommands();
            }

            if (_shell == null)
            {
                return;
            }

            // 诊断：会话态每秒输出输入框内容与回车原始状态（确认键盘事件是否进入）
            if (_shell.IsSessionActive && Time.unscaledTime - _lastInputProbe > 1f)
            {
                _lastInputProbe = Time.unscaledTime;
                Debug.Log("[AiPeople] 输入框内容=[" + (_chat != null ? _chat.CurrentInputText : "?")
                    + "] 回车键状态=" + (_shell.IsSubmitKeyDown ? "按下" : "抬起"));
            }

            if (_shell.IsSessionActive && Keyboard.current != null)
            {
                if (Keyboard.current.f10Key.wasPressedThisFrame)
                {
                    ToggleSettings();
                }
                else if (Keyboard.current.escapeKey.wasPressedThisFrame
                    && _settingsPanel != null && _settingsPanel.gameObject.activeSelf)
                {
                    CloseSettings();
                }
            }

            // 壁纸模式下窗口不接收输入：把常驻对话框的屏幕区域交给外壳做点击判定
            if (_shell.IsWallpaperMode && !_shell.IsSessionActive && _chatBar != null && _chatBar.gameObject.activeSelf)
            {
                _shell.ChatBarPixelRect = _chatBar.GetPixelRect();
            }

            // 小窗进程守护：被意外结束则拉起（仅构建版）
            if (_chatWindowLauncher != null && _chatWindowLauncher.IsSupported)
            {
                _launcherTimer -= Time.unscaledDeltaTime;
                if (_launcherTimer <= 0f)
                {
                    _launcherTimer = 5f;
                    if (!_chatWindowLauncher.IsRunning)
                    {
                        _chatWindowLauncher.EnsureStarted();
                    }
                }
            }

            // 托盘命令（独立线程投递，这里在主线程执行）
            if (_tray != null)
            {
                while (_tray.Commands.TryDequeue(out TrayCommand command))
                {
                    switch (command)
                    {
                        case TrayCommand.ShowChat:
                            if (!_chatWindowShowRequested)
                            {
                                ToggleChatWindow();
                            }

                            break;
                        case TrayCommand.HideChat:
                            if (_chatWindowShowRequested)
                            {
                                ToggleChatWindow();
                            }

                            break;
                        case TrayCommand.RestoreWallpaper:
                            RestoreWallpaperFromTray();
                            break;
                        case TrayCommand.Quit:
                            QuitApplication();
                            break;
                    }
                }
            }

            // 全局退出热键 Ctrl+Alt+Q
            if (_quitHotkey != null && _quitHotkey.ConsumePressed())
            {
                QuitApplication();
            }
        }

        private void RestoreWallpaperFromTray()
        {
            if (_shell == null)
            {
                return;
            }

            _shell.ApplyMode(ShellWindowMode.Wallpaper);
            Debug.Log("[AiPeople] 托盘请求恢复桌面壁纸");
        }

        /// <summary>小窗里的窗口模式切换（壁纸 / 普通窗口 / 自动）。</summary>
        private void ApplyWindowModeFromChatWindow(string mode)
        {
            if (_shell == null)
            {
                return;
            }

            ShellWindowMode target;
            switch ((mode ?? string.Empty).ToLowerInvariant())
            {
                case "wallpaper": target = ShellWindowMode.Wallpaper; break;
                case "normal": target = ShellWindowMode.Normal; break;
                default: target = ShellWindowMode.Auto; break;
            }

            _shell.ApplyMode(target);
            Debug.Log("[AiPeople] 小窗切换窗口模式 → " + target);
        }
    }
}
