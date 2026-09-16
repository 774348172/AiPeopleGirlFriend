using System;

namespace AiPeople.Core
{
    /// <summary>
    /// 与后端 tools/baiweixi_chat_app.py 对齐的线协议数据对象。
    /// 字段名即 JSON 键名（snake_case），不要改名。
    /// </summary>
    [Serializable]
    public sealed class WorldInputData
    {
        public string location_id = "apartment_table";
        public string location_label = "出租屋餐桌旁";
        public string activity = "坐着休息";
        public string body = "有些疲惫，没有受伤";
        public string held_item = "";
        public string scene = "窗外下着雨，屋里亮着暖灯";
    }

    [Serializable]
    public sealed class ChatRequest
    {
        public string save_id;
        public string text;
        public WorldInputData world;
    }

    [Serializable]
    public sealed class StatusRequest
    {
        public string save_id;
    }

    [Serializable]
    public sealed class WorldStateView
    {
        public int version;
        public string location_id;
        public string location_label;
        public string activity;
        public string body;
        public string held_item;
        public string scene;
    }

    [Serializable]
    public sealed class LivingMindView
    {
        public string emotion;
        public string attention;
        public string current_activity;
        public string immediate_intent;
    }

    [Serializable]
    public sealed class RelationshipView
    {
        public string stage;
        public string trust;
    }

    [Serializable]
    public sealed class HeroineView
    {
        public int version;
        public LivingMindView living_mind;
        public RelationshipView relationship;
    }

    [Serializable]
    public sealed class StatusResponse
    {
        public string save_id;
        public string game_time;
        public WorldStateView world;
        public HeroineView heroine;
    }
}
