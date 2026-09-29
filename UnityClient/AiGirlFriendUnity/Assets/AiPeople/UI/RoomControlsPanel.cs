using System;
using System.Collections;
using System.Text;
using AiPeople.Core;
using AiPeople.Shell;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;

namespace AiPeople.UI
{
    /// <summary>Room controls live in the chat process, keeping the wallpaper click-through.</summary>
    public sealed class RoomControlsPanel : MonoBehaviour
    {
        [Serializable] private sealed class DoorView
        {
            public string id, label;
            public bool open, targetOpen, moving, blocked;
        }
        [Serializable] private sealed class DeviceView
        {
            public string device_id, label, operating_state;
        }
        [Serializable] private sealed class RoomView { public DoorView[] doors; public DeviceView[] devices; }
        private GameObject _panel;
        private Text _message;
        private readonly Button[] _buttons = new Button[6];
        private readonly Text[] _labels = new Text[6];
        private readonly Text[] _actions = new Text[6];
        private DoorView[] _doors = Array.Empty<DoorView>();
        private DeviceView[] _devices = Array.Empty<DeviceView>();
        private bool _deviceMode;
        private string _endpoint;
        private float _nextPoll;
        private bool _polling, _sending;
        public bool WindowVisible { get; set; }
        public bool Visible => _panel != null && _panel.activeSelf;

        public void Initialize(Transform parent, string endpoint)
        {
            _endpoint = endpoint;
            var panel = UiRoot.CreateRect("RoomControls", parent, Vector2.zero, Vector2.one,
                new Vector2(242, 10), new Vector2(-10, -50));
            _panel = panel.gameObject;
            _panel.AddComponent<Image>().color = new Color(.08f, .10f, .14f, 1);
            Text title = UiRoot.CreateText("Heading", panel, 18, Color.white, TextAnchor.UpperLeft);
            UiRoot.Stretch(title.rectTransform, 14, 0, 14, 10);
            title.text = "房间";
            for (int tab = 0; tab < 2; tab++)
            {
                bool devices = tab == 1;
                var selector = UiRoot.CreateRect(devices ? "DevicesTab" : "DoorsTab", panel,
                    new Vector2(1, 1), new Vector2(1, 1),
                    new Vector2(devices ? -108 : -220, -45), new Vector2(devices ? -14 : -126, -15));
                selector.gameObject.AddComponent<Image>().color = new Color(.22f, .29f, .34f);
                selector.gameObject.AddComponent<Button>().onClick.AddListener(() => {
                    _deviceMode = devices;
                    _message.text = string.Empty;
                    foreach (var button in _buttons) button.interactable = false;
                    _nextPoll = 0;
                });
                var label = UiRoot.CreateText("TabLabel", selector, 15, Color.white, TextAnchor.MiddleCenter);
                UiRoot.Stretch(label.rectTransform, 0, 0, 0, 0);
                label.text = devices ? "厨房设备" : "门窗";
            }
            for (int i = 0; i < _buttons.Length; i++)
            {
                int index = i;
                var row = UiRoot.CreateRect("Door" + i, panel, new Vector2(0, 1), Vector2.one,
                    new Vector2(12, -80-i*40), new Vector2(-12, -44-i*40));
                _labels[i] = UiRoot.CreateText("Label", row, 16, new Color(.88f, .91f, .96f), TextAnchor.MiddleLeft);
                UiRoot.Stretch(_labels[i].rectTransform, 4, 0, 120, 0);
                var action = UiRoot.CreateRect("Action", row, new Vector2(1, 0), Vector2.one,
                    new Vector2(-100, 1), new Vector2(0, -1));
                var background = action.gameObject.AddComponent<Image>();
                background.color = new Color(.25f, .40f, .60f);
                _buttons[i] = action.gameObject.AddComponent<Button>();
                _buttons[i].targetGraphic = background;
                _buttons[i].onClick.AddListener(() => {
                    if (_sending) return;
                    if (_deviceMode && index < _devices.Length) StartCoroutine(SetDevice(_devices[index]));
                    else if (!_deviceMode && index < _doors.Length) StartCoroutine(SetDoor(_doors[index]));
                });
                UiLatencyProbe.Bind(action.gameObject, "door_" + i);
                _actions[i] = UiRoot.CreateText("ActionText", action, 16, Color.white, TextAnchor.MiddleCenter);
                UiRoot.Stretch(_actions[i].rectTransform, 0, 0, 0, 0);
                _buttons[i].interactable = false;
            }
            _message = UiRoot.CreateText("Message", panel, 14, new Color(.72f, .78f, .88f), TextAnchor.LowerLeft);
            UiRoot.Stretch(_message.rectTransform, 16, 0, 16, 10);
            _message.text = "正在连接房间…";
            SetVisible(false);
        }

        public void SetVisible(bool visible) { _panel.SetActive(visible); _nextPoll = 0; }

        private void Update()
        {
            if (Visible && WindowVisible && !_polling && Time.unscaledTime >= _nextPoll) StartCoroutine(Poll());
        }

        private IEnumerator Poll()
        {
            _polling = true;
            try
            {
                using var request = UnityWebRequest.Get(_endpoint + "/ui/room");
                request.timeout = 3;
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    _message.text = "房间暂未连接";
                    foreach (var button in _buttons) button.interactable = false;
                    yield break;
                }
                _doors = JsonUtility.FromJson<RoomView>(request.downloadHandler.text)?.doors ?? Array.Empty<DoorView>();
                _devices = JsonUtility.FromJson<RoomView>(request.downloadHandler.text)?.devices ?? Array.Empty<DeviceView>();
                for (int i = 0; i < _buttons.Length; i++)
                {
                    if (_deviceMode)
                    {
                        bool present = i < _devices.Length;
                        var device = present ? _devices[i] : null;
                        bool known = present && device.operating_state != "unknown";
                        _buttons[i].interactable = known && !_sending;
                        _labels[i].text = !present ? "" : device.label + " · " +
                            (device.operating_state == "running" ? "运行中" : known ? "已关闭" : "状态未知");
                        _actions[i].text = !known ? "—" : device.operating_state == "running" ? "关闭" : "打开";
                        continue;
                    }
                    _buttons[i].interactable = i < _doors.Length && !_sending;
                    if (i >= _doors.Length) { _labels[i].text = ""; _actions[i].text = "—"; continue; }
                    var door = _doors[i];
                    string state = door.blocked ? "有人挡住，已暂停" : door.moving ? (door.targetOpen ? "打开中" : "关闭中") : door.open ? "已打开" : "已关闭";
                    _labels[i].text = door.label + " · " + state;
                    _actions[i].text = door.targetOpen ? "关闭" : "打开";
                }
                if (_message.text == "正在连接房间…" || _message.text == "房间暂未连接" || _message.text.Length == 0)
                    _message.text = _deviceMode ? "" : _doors.Length == 0 ? "当前场景没有可控制的门窗" : "门口有人时会暂停，离开后继续。";
                if (!_sending)
                {
                    UiLatencyProbe.RoomReceived();
                    foreach (var door in _doors) UiLatencyProbe.DoorState(door.id, door.open, door.moving);
                }
            }
            finally { _polling = false; _nextPoll = Time.unscaledTime + .3f; }
        }

        private IEnumerator SetDoor(DoorView door)
        {
            UiLatencyProbe.DoorRequested(door.id, !door.targetOpen);
            _sending = true;
            foreach (var button in _buttons) button.interactable = false;
            try
            {
                string payload = "{\"id\":" + JsonUtil.Str(door.id) + ",\"state\":\"" + (door.targetOpen ? "closed" : "open") + "\"}";
                using var request = new UnityWebRequest(_endpoint + "/ui/door", "POST");
                request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(payload));
                request.uploadHandler.contentType = "application/json";
                request.downloadHandler = new DownloadHandlerBuffer();
                request.timeout = 4;
                yield return request.SendWebRequest();
                UiLatencyTrace.Mark("door_ack");
                UiLatencyProbe.Frame("door_ack");
                string json = request.downloadHandler.text ?? "";
                _message.text = request.result != UnityWebRequest.Result.Success ? "操作未确认，请稍后重试。"
                    : json.Contains("\"accepted\":true") ? "已请求" + (door.targetOpen ? "关闭" : "打开") + door.label
                    : JsonLine.TryGetString(json, "reason", out string reason) ? reason : "操作未完成";
            }
            finally { _sending = false; _nextPoll = 0; }
        }

        private IEnumerator SetDevice(DeviceView device)
        {
            if (device.operating_state == "unknown") yield break;
            _sending = true;
            foreach (var button in _buttons) button.interactable = false;
            try
            {
                bool turnOn = device.operating_state != "running";
                string payload = "{\"id\":" + JsonUtil.Str(device.device_id)
                    + ",\"state\":\"" + (turnOn ? "on" : "off") + "\"}";
                using var request = new UnityWebRequest(_endpoint + "/ui/device", "POST");
                request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(payload));
                request.uploadHandler.contentType = "application/json";
                request.downloadHandler = new DownloadHandlerBuffer();
                request.timeout = 4;
                yield return request.SendWebRequest();
                string json = request.downloadHandler.text ?? "";
                _message.text = request.result != UnityWebRequest.Result.Success ? "操作未确认，请稍后重试。"
                    : json.Contains("\"accepted\":true") ? device.label + (turnOn ? "已打开" : "已关闭")
                    : JsonLine.TryGetString(json, "reason", out string reason) ? reason : "操作未完成";
            }
            finally { _sending = false; _nextPoll = 0; }
        }
    }
}
