using System.Collections.Generic;
using AiPeople.World;
using UnityEditor;
using UnityEngine;

namespace AiPeople.EditorTools
{
    /// <summary>
    /// 导出游戏世界契约样例（真实 JSON），供后端适配器测试作为固定样例，
    /// 避免跨语言契约靠手抄。批处理：-executeMethod AiPeople.EditorTools.AiPeopleGameWorldContractProbe.Dump
    /// </summary>
    public static class AiPeopleGameWorldContractProbe
    {
        [MenuItem("AiPeople/导出游戏世界契约样例")]
        public static void Dump()
        {
            var state = new GameWorldState();
            state.Execute("move_to", new Dictionary<string, string> { { "target_location_id", "apartment_kitchen" } }, out _);
            state.Tick(30f);
            state.Execute("cook_meal", new Dictionary<string, string>(), out _);

            Debug.Log("[AiPeople] ACTIONS_JSON=" + ActionCatalog.ToJson());
            Debug.Log("[AiPeople] PROJECT_JSON=" + state.ProjectJson());

            state.Execute("dance", new Dictionary<string, string>(), out _);   // 非法动作 → 拒绝反馈
            state.Tick(1200f);                                                  // 煮面完成 → 面上桌
            Debug.Log("[AiPeople] PROJECT_DONE_JSON=" + state.ProjectJson());
        }
    }
}
