using System.Collections;
using System.Collections.Generic;
using AiPeople.World;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.TestTools;

namespace AiPeople.Tests
{
    /// <summary>
    /// M3 游戏世界测试：动作清单校验、时间推进与效果、以及本地 HTTP 服务的投影/转发往返。
    /// </summary>
    public sealed class GameWorldTests
    {
        [UnityTest]
        public IEnumerator State_Machine_Enforces_Catalog_And_Produces_Effects()
        {
            var state = new GameWorldState();

            Assert.IsTrue(
                state.Execute("move_to", new Dictionary<string, string> { { "target_location_id", "apartment_kitchen" } }, out string okReason),
                okReason);
            Assert.IsFalse(state.Execute("dance", new Dictionary<string, string>(), out string unknownReason));
            StringAssert.Contains("不在游戏清单", unknownReason);
            Assert.IsFalse(state.Execute("move_to", new Dictionary<string, string>(), out string missingReason));
            StringAssert.Contains("缺少必要参数", missingReason);
            Assert.IsFalse(
                state.Execute("move_to", new Dictionary<string, string> { { "target_location_id", "apartment_door" } }, out string duplicateReason));
            StringAssert.Contains("同一件事", duplicateReason);

            Assert.AreEqual(0, state.Tick(30f).Count, "未到期不应完成");
            List<PendingAction> completed = state.Tick(40f);
            Assert.AreEqual(1, completed.Count);
            Assert.AreEqual("move_to", completed[0].actionId);

            Assert.IsTrue(state.Execute("cook_meal", new Dictionary<string, string>(), out string cookReason), cookReason);
            Assert.AreEqual("apartment_kitchen", state.PendingSnapshot()[0].travelToLocationId, "做饭应先走到厨房");
            Assert.AreEqual(1, state.Tick(1200f).Count);
            Assert.AreEqual("一碗热汤面", state.ItemStatesSnapshot()["桌上"]);
            StringAssert.Contains("一碗热汤面", state.ProjectJson());
            yield return null;
        }

        [UnityTest]
        public IEnumerator Http_Server_Serves_Projection_And_Accepts_Actions()
        {
            var go = new GameObject("TestGameWorld");
            var state = new GameWorldState();
            var server = go.AddComponent<GameWorldServer>();
            server.port = 18770;
            server.StartServer(state);
            yield return new WaitForSeconds(0.6f);
            Assert.IsTrue(server.IsRunning, "游戏世界服务未启动");

            using (UnityWebRequest actions = UnityWebRequest.Get("http://127.0.0.1:18770/game/actions"))
            {
                yield return actions.SendWebRequest();
                Assert.AreEqual(UnityWebRequest.Result.Success, actions.result, actions.error);
                StringAssert.Contains("cook_meal", actions.downloadHandler.text);
                StringAssert.Contains("move_to", actions.downloadHandler.text);
            }

            using (var execute = new UnityWebRequest("http://127.0.0.1:18770/game/execute_action", "POST"))
            {
                execute.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(
                    "{\"action_id\":\"move_to\",\"params\":{\"target_location_id\":\"apartment_door\"}}"));
                execute.uploadHandler.contentType = "application/json";
                execute.downloadHandler = new DownloadHandlerBuffer();
                yield return execute.SendWebRequest();
                Assert.AreEqual(UnityWebRequest.Result.Success, execute.result, execute.error);
                StringAssert.Contains("\"accepted\":true", execute.downloadHandler.text);
            }

            using (UnityWebRequest project = UnityWebRequest.Get("http://127.0.0.1:18770/game/project"))
            {
                yield return project.SendWebRequest();
                Assert.AreEqual(UnityWebRequest.Result.Success, project.result, project.error);
                StringAssert.Contains("move_to", project.downloadHandler.text);
                StringAssert.Contains("apartment_door", project.downloadHandler.text);
            }

            using (var report = new UnityWebRequest("http://127.0.0.1:18770/game/player_fact_report", "POST"))
            {
                report.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(
                    "{\"report_id\":\"r1\",\"subject\":\"protagonist\",\"predicate\":\"ate\",\"value\":\"面\"}"));
                report.uploadHandler.contentType = "application/json";
                report.downloadHandler = new DownloadHandlerBuffer();
                yield return report.SendWebRequest();
                Assert.AreEqual(UnityWebRequest.Result.Success, report.result, report.error);
                StringAssert.Contains("pending_confirmation", report.downloadHandler.text);
            }

            Object.Destroy(go);
            yield return null;
        }
    }
}
