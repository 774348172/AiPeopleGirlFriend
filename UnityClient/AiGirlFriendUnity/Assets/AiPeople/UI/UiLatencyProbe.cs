using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using AiPeople.Shell;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.Rendering;

namespace AiPeople.UI
{
    /// <summary>Diagnostic frame evidence; completely absent unless explicitly enabled at launch.</summary>
    public sealed class UiLatencyProbe : MonoBehaviour
    {
        private static UiLatencyProbe _instance;
        private readonly List<string> _pending = new List<string>();
        private bool _scheduled, _historyPending, _roomPending, _doorPending;
        private int _capture;
        private string _doorId;
        private bool _doorTarget;

        public static void Attach(GameObject owner)
        {
            if (!Application.isEditor && UiLatencyTrace.Enabled && _instance == null)
                _instance = owner.AddComponent<UiLatencyProbe>();
        }

        public static void Bind(GameObject button, string label)
        {
            if (_instance != null) button.AddComponent<UiLatencyPointer>().Label = label;
        }

        public static void Shown()
        {
            if (_instance == null) return;
            _instance._historyPending = true;
            Frame("show");
        }

        public static void HistoryReceived()
        {
            if (_instance == null || !_instance._historyPending) return;
            _instance._historyPending = false;
            UiLatencyTrace.Mark("history_ready");
            Frame("history");
        }

        public static void RoomToggled(bool visible)
        {
            if (_instance == null) return;
            _instance._roomPending = visible;
            UiLatencyTrace.Mark(visible ? "room_open" : "room_close");
            Frame(visible ? "room_open" : "room_close");
        }

        public static void DoorRequested(string id, bool target)
        {
            if (_instance != null)
            {
                _instance._doorPending = true;
                _instance._doorId = id;
                _instance._doorTarget = target;
            }
            UiLatencyTrace.Mark("door_request");
        }

        public static void RoomReceived()
        {
            if (_instance == null) return;
            if (_instance._roomPending)
            {
                _instance._roomPending = false;
                UiLatencyTrace.Mark("room_ready");
                Frame("room_ready");
            }
        }

        public static void DoorState(string id, bool open, bool moving)
        {
            if (_instance != null && _instance._doorPending && id == _instance._doorId
                && open == _instance._doorTarget && !moving)
            {
                _instance._doorPending = false;
                UiLatencyTrace.Mark("door_settled_ui");
                Frame("door_settled_ui");
            }
        }

        public static void Frame(string label)
        {
            if (_instance == null) return;
            _instance._pending.Add(label);
            if (!_instance._scheduled) _instance.StartCoroutine(_instance.AfterFrame());
        }

        private IEnumerator AfterFrame()
        {
            _scheduled = true;
            yield return new WaitForEndOfFrame();
            string[] labels = _pending.ToArray();
            _pending.Clear();
            _scheduled = false;
            foreach (string label in labels) UiLatencyTrace.Mark(label + "_end_frame");
            if (Environment.GetEnvironmentVariable("AIPEOPLE_UI_CAPTURE") != "1") yield break;
            // GPU copy/readback is diagnostic overhead, and not a physical display-present timestamp.
            var target = new RenderTexture(Screen.width, Screen.height, 0, RenderTextureFormat.ARGB32);
            target.Create();
            ScreenCapture.CaptureScreenshotIntoRenderTexture(target);
            string file = "ui-" + (++_capture).ToString("D3") + "-" + labels[0] + ".png";
            AsyncGPUReadback.Request(target, 0, TextureFormat.RGBA32, result =>
            {
                foreach (string label in labels) UiLatencyTrace.Mark(label + (result.hasError ? "_gpu_error" : "_gpu_ready"));
                if (!result.hasError)
                {
                    var texture = new Texture2D(target.width, target.height, TextureFormat.RGBA32, false);
                    try
                    {
                        texture.LoadRawTextureData(result.GetData<byte>());
                        texture.Apply();
                        File.WriteAllBytes(Path.Combine(UiLatencyTrace.Folder, file), texture.EncodeToPNG());
                    }
                    catch (IOException) { UiLatencyTrace.Mark("capture_write_failed"); }
                    catch (UnauthorizedAccessException) { UiLatencyTrace.Mark("capture_write_failed"); }
                    finally { Destroy(texture); }
                }
                target.Release();
                Destroy(target);
            });
        }

        private void OnDestroy() { if (_instance == this) _instance = null; }
    }

    public sealed class UiLatencyPointer : MonoBehaviour, IPointerDownHandler, IPointerUpHandler
    {
        public string Label;
        public void OnPointerDown(PointerEventData data) => UiLatencyTrace.Mark(Label + "_pointer_down");
        public void OnPointerUp(PointerEventData data) => UiLatencyTrace.Mark(Label + "_pointer_up");
    }
}
