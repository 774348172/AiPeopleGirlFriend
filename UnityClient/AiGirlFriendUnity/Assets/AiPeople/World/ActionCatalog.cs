using System;
using System.Collections.Generic;
using System.Text;

namespace AiPeople.World
{
    /// <summary>游戏侧动作定义（接口契约 §四：动作清单由游戏导出，runtime 消费）。</summary>
    public sealed class ActionDefinition
    {
        public string id;
        public string description;
        public int durationSeconds;
        public string[] requiredParameters = Array.Empty<string>();
        public Dictionary<string, string> itemStateEffects = new Dictionary<string, string>();

        /// <summary>完成时由导演生成的实体 id（如 cook_meal → bowl）。</summary>
        public string spawnEntityId = string.Empty;

        /// <summary>执行时她需要先走到的位置（location_id）；为空表示就地执行。</summary>
        public string travelToLocationId = string.Empty;
    }

    /// <summary>P0 动作清单（与后端 current 清单一致；单一来源在游戏侧，后端消费）。</summary>
    public static class ActionCatalog
    {
        public static readonly IReadOnlyList<ActionDefinition> All = new List<ActionDefinition>
        {
            new ActionDefinition
            {
                id = "cook_meal",
                description = "走到厨房做饭，双手被占用，大约需要 20 分钟",
                durationSeconds = 1200,
                itemStateEffects = new Dictionary<string, string> { { "桌上", "一碗热汤面" } },
                spawnEntityId = "bowl",
                travelToLocationId = "apartment_kitchen",
            },
            new ActionDefinition
            {
                id = "move_to",
                description = "走向某处，路上可以被叫住",
                durationSeconds = 60,
                requiredParameters = new[] { "target_location_id" },
            },
            new ActionDefinition
            {
                id = "pick_up_item",
                description = "拿起场景中的某个物品",
                durationSeconds = 60,
                requiredParameters = new[] { "item_id" },
            },
        };

        public static ActionDefinition Get(string actionId)
        {
            foreach (ActionDefinition action in All)
            {
                if (action.id == actionId)
                {
                    return action;
                }
            }

            return null;
        }

        /// <summary>GET /game/actions 的响应体（供后端建立白名单）。</summary>
        public static string ToJson()
        {
            var builder = new StringBuilder(512);
            builder.Append("{\"actions\":[");
            for (int i = 0; i < All.Count; i++)
            {
                ActionDefinition action = All[i];
                if (i > 0)
                {
                    builder.Append(',');
                }

                builder.Append('{');
                builder.Append("\"action_id\":").Append(Core.JsonUtil.Str(action.id)).Append(',');
                builder.Append("\"description\":").Append(Core.JsonUtil.Str(action.description)).Append(',');
                builder.Append("\"duration_game_seconds\":").Append(action.durationSeconds).Append(',');
                builder.Append("\"params\":{");
                for (int p = 0; p < action.requiredParameters.Length; p++)
                {
                    if (p > 0)
                    {
                        builder.Append(',');
                    }

                    builder.Append(Core.JsonUtil.Str(action.requiredParameters[p])).Append(":{\"required\":true}");
                }

                builder.Append("},\"effects\":{\"item_states\":{");
                bool first = true;
                foreach (KeyValuePair<string, string> pair in action.itemStateEffects)
                {
                    if (!first)
                    {
                        builder.Append(',');
                    }

                    first = false;
                    builder.Append(Core.JsonUtil.Str(pair.Key)).Append(':').Append(Core.JsonUtil.Str(pair.Value));
                }

                builder.Append("}}}");
            }

            builder.Append("]}");
            return builder.ToString();
        }
    }
}
