using System.Collections;
using AiPeople.Core;
using AiPeople.Shell;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.TestTools;

namespace AiPeople.Tests
{
    /// <summary>
    /// 主进程 /ui 接口测试（对话小窗进程的契约）：state / send（含中文 UTF-8）/ hide / quit。
    /// </summary>
    public sealed class ChatUiServerTests
    {
        private sealed class FakeSession : IChatSession
        {
            public bool IsGenerating => false;
            public string LastHeroineLine => "……嗯。";
            public string LastText { get; private set; } = string.Empty;

            public bool Send(string text)
            {
                if (string.IsNullOrWhiteSpace(text))
                {
                    return false;
                }

                LastText = text;
                return true;
            }

            public string StateJson()
            {
                return "{\"generating\":false,\"partial\":\"\",\"last_heroine\":\"……嗯。\","
                    + "\"messages\":[{\"role\":\"user\",\"text\":\"hi\",\"meta\":\"你\",\"error\":false}]}";
            }
        }

        [UnityTest]
        public IEnumerator Ui_Endpoints_RoundTrip()
        {
            var go = new GameObject("ChatUiServerTest");
            var session = new FakeSession();
            bool show = true;
            bool hideRequested = false;
            bool quitRequested = false;

            var server = go.AddComponent<ChatUiServer>();
            server.port = 18771;
            server.StartServer(session, () => show, () => hideRequested = true, () => quitRequested = true);
            yield return new WaitForSeconds(0.6f);
            Assert.IsTrue(server.IsRunning, "对话小窗接口未启动");

            using (UnityWebRequest state = UnityWebRequest.Get("http://127.0.0.1:18771/ui/state"))
            {
                yield return state.SendWebRequest();
                Assert.AreEqual(UnityWebRequest.Result.Success, state.result, state.error);
                StringAssert.Contains("\"show\":true", state.downloadHandler.text);
                StringAssert.Contains("……嗯。", state.downloadHandler.text);
            }

            using (var send = new UnityWebRequest("http://127.0.0.1:18771/ui/send", "POST"))
            {
                send.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes("{\"text\":\"你好，小窗\"}"));
                send.uploadHandler.contentType = "application/json";
                send.downloadHandler = new DownloadHandlerBuffer();
                yield return SendAndPump(send, server);
                Assert.AreEqual(UnityWebRequest.Result.Success, send.result, send.error);
                StringAssert.Contains("\"accepted\":true", send.downloadHandler.text);
                Assert.AreEqual("你好，小窗", session.LastText, "中文文本未按 UTF-8 正确解析");
            }

            using (var hide = new UnityWebRequest("http://127.0.0.1:18771/ui/hide", "POST"))
            {
                hide.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes("{}"));
                hide.uploadHandler.contentType = "application/json";
                hide.downloadHandler = new DownloadHandlerBuffer();
                yield return SendAndPump(hide, server);
                StringAssert.Contains("hidden", hide.downloadHandler.text);
                Assert.IsTrue(hideRequested, "hide 未触发");
            }

            using (var quit = new UnityWebRequest("http://127.0.0.1:18771/ui/quit", "POST"))
            {
                quit.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes("{}"));
                quit.uploadHandler.contentType = "application/json";
                quit.downloadHandler = new DownloadHandlerBuffer();
                yield return SendAndPump(quit, server);
                Assert.IsTrue(quitRequested, "quit 未触发");
            }

            Object.Destroy(go);
            yield return null;
        }

        private static IEnumerator SendAndPump(UnityWebRequest request, ChatUiServer server)
        {
            var operation = request.SendWebRequest();
            while (!operation.isDone)
            {
                while (server.TryDequeueMainThreadCommand(out System.Action command)) command();
                yield return null;
            }
            while (server.TryDequeueMainThreadCommand(out System.Action remaining)) remaining();
        }
    }
}
