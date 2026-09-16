#if UNITY_EDITOR
using System;
using System.Collections;
using System.IO;
using System.Reflection;
using System.Threading;
using AiPeople.Shell;
using AiPeople.UI;
using AiPeople.World;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.TestTools;
using UnityEngine.UI;
using Object = UnityEngine.Object;

namespace AiPeople.Tests
{
    public sealed class RoomControlsTests
    {
        private ApartmentBuilder.Result _world;
        private ApartmentNavigation _nav;
        private ChatUiServer _server;
        private GameObject _window;
        private Canvas _canvas;
        private RoomControlsPanel _panel;
        private string _endpoint;
        private int _calls, _mainThread;
        private const BindingFlags Private = BindingFlags.NonPublic | BindingFlags.Instance;

        [UnitySetUp]
        public IEnumerator SetUp()
        {
            _mainThread = Thread.CurrentThread.ManagedThreadId;
            _calls = 0;
            _world = ApartmentBuilder.Build(AssetDatabase.LoadAssetAtPath<GameObject>(
                "Assets/Art/Apartment/ImportedShell/ApartmentShell.prefab"));
            _nav = _world.Root.GetComponentInChildren<ApartmentNavigation>();
            _server = _world.Root.gameObject.AddComponent<ChatUiServer>();
            var reservation = new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback, 0);
            reservation.Start();
            _server.port = ((System.Net.IPEndPoint)reservation.LocalEndpoint).Port;
            reservation.Stop();
            _server.ConfigureRoomControls((id, open) =>
            {
                Assert.AreEqual(_mainThread, Thread.CurrentThread.ManagedThreadId, "HTTP worker touched Unity scene");
                _calls++;
                return _nav.SetDoorState(id, open);
            });
            _server.StartServer(null, () => true, () => {}, () => {});
            _nav.DoorsChanged += Publish;
            Publish();
            _endpoint = "http://127.0.0.1:" + _server.port;
            yield return Await(() => _server.IsRunning);
            _window = new GameObject("RoomWindowTest");
            var app = _window.AddComponent<ChatWindowApp>();
            app.enabled = false;
            typeof(ChatWindowApp).GetField("_mainEndpoint", Private).SetValue(app, _endpoint);
            typeof(ChatWindowApp).GetMethod("BuildUi", Private).Invoke(app, null);
            _canvas = (Canvas)typeof(ChatWindowApp).GetField("_canvas", Private).GetValue(app);
            _panel = _window.GetComponent<RoomControlsPanel>();
            _panel.WindowVisible = true;
        }

        private void Publish() => _server.PublishRoomState(_nav.DoorsJson());

        [UnityTest]
        public IEnumerator RoomButton_ControlsActualDoor_ThroughMainThreadBridge()
        {
            var root = _canvas.transform.Find("ChatWindow");
            var button = root.Find("Header/RoomButton").GetComponent<Button>();
            Assert.IsTrue(button.GetComponent<Text>().raycastTarget);
            Assert.AreEqual(new Vector2(900, 420), _canvas.GetComponent<CanvasScaler>().referenceResolution);
            button.onClick.Invoke();
            Assert.IsTrue(_panel.Visible);
            Assert.IsFalse(root.Find("Input").GetComponent<InputField>().interactable);
            var action = root.Find("RoomControls/Door0/Action").GetComponent<Button>();
            yield return Await(() => action.interactable);
            var kitchen = Array.Find(_nav.Doors, d => d.doorId == "KitchenDoor");
            action.onClick.Invoke();
            yield return Await(() => !kitchen.IsOpen && !kitchen.IsMoving);
            yield return Await(() => action.interactable && action.GetComponentInChildren<Text>().text == "打开");
            Assert.AreEqual(1, _calls);
            yield return Capture(root);
            action.onClick.Invoke();
            yield return Await(() => kitchen.IsOpen && !kitchen.IsMoving);
            Assert.AreEqual(2, _calls);
            button.onClick.Invoke();
            Assert.IsFalse(_panel.Visible);
            Assert.IsTrue(root.Find("Input").GetComponent<InputField>().interactable);
        }

        [UnityTest]
        public IEnumerator RoomEndpoint_RejectsInvalidInput_AndDoesNotExecuteExpiredCommands()
        {
            using (var bad = Post("{\"id\":\"KitchenDoor\",\"state\":\"toggle\"}"))
            {
                yield return bad.SendWebRequest();
                Assert.AreEqual(400, bad.responseCode);
                Assert.AreEqual(0, _calls);
            }
            using (var expired = Post("{\"id\":\"KitchenDoor\",\"state\":\"closed\"}"))
            {
                // Deliberately stop pumping Unity's command queue until the HTTP timeout.
                yield return expired.SendWebRequest();
                Assert.AreEqual(503, expired.responseCode);
                while (_server.TryDequeueMainThreadCommand(out var command)) command();
                Assert.AreEqual(0, _calls, "An expired request changed the door later");
                Assert.IsTrue(_nav.Doors[0].IsOpen);
            }
        }

        private UnityWebRequest Post(string body)
        {
            var request = new UnityWebRequest(_endpoint + "/ui/door", "POST");
            request.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(body));
            request.uploadHandler.contentType = "application/json";
            request.downloadHandler = new DownloadHandlerBuffer();
            request.timeout = 5;
            return request;
        }

        private IEnumerator Await(Func<bool> condition)
        {
            float deadline = Time.realtimeSinceStartup + 5;
            while (!condition() && Time.realtimeSinceStartup < deadline)
            {
                while (_server.TryDequeueMainThreadCommand(out var command)) command();
                yield return null;
            }
            Assert.IsTrue(condition(), "Room UI did not reach the expected state");
        }

        private IEnumerator Capture(Transform root)
        {
            var camera = new GameObject("RoomUiPreviewCamera").AddComponent<Camera>();
            camera.transform.SetParent(_world.Root);
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = Color.black;
            camera.cullingMask = 1 << 5;
            foreach (var item in _canvas.GetComponentsInChildren<Transform>(true)) item.gameObject.layer = 5;
            var texture = new RenderTexture(900, 420, 24);
            camera.targetTexture = texture;
            _canvas.renderMode = RenderMode.ScreenSpaceCamera;
            _canvas.worldCamera = camera;
            _canvas.planeDistance = 1;
            yield return null;
            Canvas.ForceUpdateCanvases();
            camera.Render();
            var previous = RenderTexture.active;
            RenderTexture.active = texture;
            var image = new Texture2D(900, 420, TextureFormat.RGB24, false);
            image.ReadPixels(new Rect(0, 0, 900, 420), 0, 0);
            image.Apply();
            Directory.CreateDirectory("../output/apartment-doors");
            File.WriteAllBytes("../output/apartment-doors/room-controls.png", image.EncodeToPNG());
            RenderTexture.active = previous;
            _canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            camera.targetTexture = null;
            Object.Destroy(image); Object.Destroy(texture); Object.Destroy(camera.gameObject);
        }

        [UnityTearDown]
        public IEnumerator TearDown()
        {
            _nav.DoorsChanged -= Publish;
            Object.Destroy(_window);
            if (_canvas != null) Object.Destroy(_canvas.gameObject);
            Object.Destroy(_world.Root.gameObject);
            yield return null;
        }
    }
}
#endif
