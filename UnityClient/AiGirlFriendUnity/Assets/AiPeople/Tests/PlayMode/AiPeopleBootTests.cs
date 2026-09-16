using System.Collections;
using AiPeople.App;
using AiPeople.World;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AiPeople.Tests
{
    /// <summary>
    /// M1 冒烟测试：AiPeopleApp 运行时装配不抛异常，世界/玩家/UI/占位角色齐全。
    /// 不依赖后端在线（网络失败由应用内降级路径处理）。
    /// </summary>
    public sealed class AiPeopleBootTests
    {
        [UnityTest]
        public IEnumerator Bootstrap_Creates_World_Player_And_Ui()
        {
            var appGo = new GameObject("TestApp");
            appGo.AddComponent<AiPeopleApp>();

            yield return null; // Start
            yield return null; // 首帧构建
            yield return null;

            Assert.IsNotNull(GameObject.Find("Apartment"), "出租屋 blockout 未构建");
            Assert.IsNotNull(GameObject.Find("Player"), "玩家未创建");
            Assert.IsNotNull(GameObject.Find("AiPeopleCanvas"), "UI 画布未创建");
            GameObject heroine = GameObject.Find("BaiWeixi");
            Assert.IsNotNull(heroine, "白未晞未创建");
            Assert.Greater(heroine.GetComponentsInChildren<Renderer>().Length, 0, "白未晞没有任何渲染体");
            Assert.IsNotNull(GameObject.Find("EventSystem"), "EventSystem 未创建");

            Waypoint[] waypoints = Object.FindObjectsByType<Waypoint>(FindObjectsSortMode.None);
            Assert.GreaterOrEqual(waypoints.Length, 3, "位置锚点不足 3 个（餐桌/厨房/门口）");

            // AiPeopleApp builds its runtime graph as sibling roots, so destroying
            // only the bootstrap component leaves Apartment/NavMesh/UI objects in
            // the shared test scene. Remove every owned root synchronously.
            foreach (string name in new[] { "Apartment", "Player", "BaiWeixi", "EventSystem", "AiPeopleCanvas", "TestApp" })
            {
                GameObject owned = GameObject.Find(name);
                if (owned != null) Object.DestroyImmediate(owned);
            }
            yield return null;
        }
    }
}
