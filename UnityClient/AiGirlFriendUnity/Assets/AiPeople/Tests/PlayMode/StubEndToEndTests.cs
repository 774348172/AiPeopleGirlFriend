using System.Collections;
using System.Collections.Generic;
using AiPeople.Core;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace AiPeople.Tests
{
    /// <summary>
    /// 契约级端到端测试：需要开发用契约桩在 127.0.0.1:8767 运行
    /// （python Tools/dev_stub/aipeople_stub.py --port 8767）。
    /// 桩未运行时跳过（Assert.Ignore）——真实模型验收仍以正式后端与人工验收为准。
    /// </summary>
    public sealed class StubEndToEndTests
    {
        private const string Endpoint = "http://127.0.0.1:8767";
        private const string SaveId = "stubtest0001";

        private static AiPeopleConfig.Data StubConfig()
        {
            return new AiPeopleConfig.Data
            {
                endpoint = Endpoint,
                saveId = SaveId,
                characterId = "baiweixi",
                requestTimeoutSeconds = 30f,
            };
        }

        [UnityTest]
        public IEnumerator Status_And_Streaming_Chat_Match_Contract()
        {
            var client = new AiPeopleClient(StubConfig());

            bool healthOk = false;
            string healthDetail = null;
            yield return client.Health((ok, detail) =>
            {
                healthOk = ok;
                healthDetail = detail;
            });

            if (!healthOk)
            {
                Assert.Ignore("契约桩未运行，跳过端到端测试：" + healthDetail);
            }

            StatusResponse status = null;
            string statusError = null;
            yield return client.FetchStatus(SaveId, r => status = r, e => statusError = e);
            Assert.IsNull(statusError, "status 失败：" + statusError);
            Assert.IsNotNull(status, "status 为空");
            StringAssert.IsMatch(@"^第\d+日 \d{2}:\d{2}:\d{2}$", status.game_time);
            Assert.IsNotNull(status.world, "world 缺失");
            Assert.IsNotNull(status.heroine?.living_mind, "heroine.living_mind 缺失");

            var deltas = new List<string>();
            string committed = null;
            string chatError = null;
            bool finished = false;
            yield return client.ChatStream(
                new ChatRequest { save_id = SaveId, text = "我回来了。", world = new WorldInputData() },
                new AiPeopleClient.ChatCallbacks
                {
                    onDelta = d => deltas.Add(d),
                    onCommitted = c => committed = c,
                    onError = e => chatError = e,
                    onFinished = () => finished = true,
                });

            Assert.IsNull(chatError, "chat 失败：" + chatError);
            Assert.IsTrue(finished, "onFinished 未触发");
            Assert.Greater(deltas.Count, 0, "未收到 delta");
            Assert.IsFalse(string.IsNullOrEmpty(committed), "未收到已提交正文");
            Assert.AreEqual(string.Concat(deltas), committed, "流式累计与已提交正文不一致");
        }

        [UnityTest]
        public IEnumerator Thirty_Turn_Loop_Without_Protocol_Errors()
        {
            var client = new AiPeopleClient(StubConfig());

            bool healthOk = false;
            yield return client.Health((ok, _) => healthOk = ok);
            if (!healthOk)
            {
                Assert.Ignore("契约桩未运行，跳过 30 轮循环测试");
            }

            for (int turn = 1; turn <= 30; turn++)
            {
                string committed = null;
                string error = null;
                var deltas = new List<string>();
                yield return client.ChatStream(
                    new ChatRequest
                    {
                        save_id = SaveId,
                        text = "第" + turn + "轮。",
                        world = new WorldInputData { activity = "第" + turn + "轮走动" },
                    },
                    new AiPeopleClient.ChatCallbacks
                    {
                        onDelta = d => deltas.Add(d),
                        onCommitted = c => committed = c,
                        onError = e => error = e,
                    });

                Assert.IsNull(error, $"第 {turn} 轮协议错误：{error}");
                Assert.IsFalse(string.IsNullOrEmpty(committed), $"第 {turn} 轮未收到已提交正文");
                Assert.AreEqual(string.Concat(deltas), committed, $"第 {turn} 轮流式与提交不一致");
            }
        }
    }
}
