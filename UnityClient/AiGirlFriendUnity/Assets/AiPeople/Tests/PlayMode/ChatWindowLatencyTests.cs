using System;
using System.Collections;
using System.Reflection;
using System.Threading;
using AiPeople.Core;
using AiPeople.Shell;
using AiPeople.UI;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.TestTools;

namespace AiPeople.Tests
{
    public sealed class ChatWindowLatencyTests
    {
        private sealed class Session : IChatSession
        {
            public bool IsGenerating => false;
            public string LastHeroineLine => "";
            public int Reads;
            public readonly ManualResetEventSlim Entered = new ManualResetEventSlim();
            public readonly ManualResetEventSlim Release = new ManualResetEventSlim(true);
            public bool Send(string text) => true;
            public string StateJson()
            {
                Interlocked.Increment(ref Reads);
                Entered.Set();
                Release.Wait(3000);
                return "{\"messages\":[],\"generating\":false}";
            }
        }

        private GameObject _serverGo, _windowGo;
        private ChatUiServer _server;
        private ChatWindowApp _window;
        private Session _session;
        private bool _show;
        private int _savedFps;
        private string _endpoint;
        private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
        private object Call(string method) => typeof(ChatWindowApp).GetMethod(method, Private).Invoke(_window, null);
        private void Set(string name, object value) => typeof(ChatWindowApp).GetField(name, Private).SetValue(_window, value);
        private bool Flag(string name) => (bool)typeof(ChatWindowApp).GetField(name, Private).GetValue(_window);

        [UnitySetUp]
        public IEnumerator SetUp()
        {
            _savedFps = Application.targetFrameRate;
            _show = true;
            _session = new Session();
            _serverGo = new GameObject("LatencyServer");
            _server = _serverGo.AddComponent<ChatUiServer>();
            // Reserve an OS-selected loopback port for this test.
            var probe = new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback, 0);
            probe.Start();
            _server.port = ((System.Net.IPEndPoint)probe.LocalEndpoint).Port;
            probe.Stop();
            _endpoint = "http://127.0.0.1:" + _server.port;
            _server.StartServer(_session, () => _show, () => _show = false, () => {});
            float deadline = Time.realtimeSinceStartup + 3;
            while (!_server.IsRunning && Time.realtimeSinceStartup < deadline) yield return null;
            Assert.IsTrue(_server.IsRunning);
            _windowGo = new GameObject("LatencyWindow");
            _window = _windowGo.AddComponent<ChatWindowApp>();
            _window.enabled = false; // Exercise coroutines without initializing native windows in Editor.
            Set("_mainEndpoint", _endpoint);
        }

        [UnityTearDown]
        public IEnumerator TearDown()
        {
            _session.Release.Set();
            _window.StopAllCoroutines();
            UnityEngine.Object.Destroy(_windowGo);
            UnityEngine.Object.Destroy(_serverGo);
            Application.targetFrameRate = _savedFps;
            yield return null;
        }

        [UnityTest]
        public IEnumerator HiddenPoll_OpensWithoutReadingHistory()
        {
            Call("HideWindowImmediately");
            Assert.GreaterOrEqual(Application.targetFrameRate, 30, "Hidden message pump must not run at 1 FPS");
            yield return (IEnumerator)Call("PollState");
            Assert.IsTrue(Flag("_visible"));
            Assert.AreEqual(0, _session.Reads, "Wakeup should not serialize history");
        }

        [UnityTest]
        public IEnumerator Hide_IsImmediate_AndStaleResponseCannotReopen()
        {
            Set("_visible", true);
            _session.Release.Reset();
            _window.StartCoroutine((IEnumerator)Call("PollState"));
            float deadline = Time.realtimeSinceStartup + 2;
            while (!_session.Entered.IsSet && Time.realtimeSinceStartup < deadline) yield return null;
            Assert.IsTrue(_session.Entered.IsSet);
            Assert.IsFalse(((IEnumerator)Call("PollState")).MoveNext(), "Polls must not overlap");
            Call("RequestHide");
            Assert.IsFalse(Flag("_visible"), "Hide must happen before the HTTP response");
            Assert.IsTrue(_show, "Main thread has intentionally not acknowledged hide yet");
            deadline = Time.realtimeSinceStartup + 2;
            while ((Flag("_pollInFlight") || Flag("_hideInFlight")) && Time.realtimeSinceStartup < deadline)
            {
                while (_server.TryDequeueMainThreadCommand(out Action command)) command();
                yield return null;
            }
            Assert.IsFalse(Flag("_pollInFlight"));
            Assert.IsFalse(Flag("_hideInFlight"));
            Assert.IsFalse(Flag("_visible"), "Old show=true must be discarded");
            Assert.IsFalse(_show);
            _show = true;
            yield return (IEnumerator)Call("PollState");
            Assert.IsTrue(Flag("_visible"), "Subsequent open must still work");
            Assert.IsFalse(_session.Release.IsSet, "Reopen must not wait for the previous history response");
            _session.Release.Set();
        }
    }
}
