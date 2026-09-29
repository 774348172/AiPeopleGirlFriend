using System;

namespace AiPeople.Core
{
    /// <summary>
    /// 与后端 tools/baiweixi_chat_app.py 对齐的线协议数据对象。
    /// 字段名即 JSON 键名（snake_case），不要改名。
    /// </summary>
    [Serializable]
    public sealed class SceneFactData
    {
        public string label;
        public string value;
    }

    [Serializable]
    public sealed class FactObservationData
    {
        public string key, label, state, value, source;
        public long version, observed_at_ms;
    }

    [Serializable]
    public sealed class ActorFactData
    {
        public string actor_id;
        public string location_id;
        public string location_label;
        public string motion;
        public float x, y, z;
    }

    [Serializable]
    public sealed class EntityFactData
    {
        public string entity_id, label, owner_id, location_id, location_label, state;
        public float x, y, z;
    }

    [Serializable]
    public sealed class DeviceFactData
    {
        public string device_id, label, location_id, location_label, operating_state, door_state;
        public float x, y, z;
    }

    [Serializable]
    public sealed class ActionFactData
    {
        public string instance_id, action_id, status, target_location_id, item_id, result_entity_id, reason;
        public float remaining_seconds;
    }

    [Serializable]
    public sealed class GameClockData
    {
        public bool ready;
        public double elapsed_seconds;
        public float time_scale = 1;
        public bool paused;
    }

    [Serializable]
    public sealed class GameSnapshotData
    {
        public int schema_version = 5;
        public GameClockData game_clock;
        public string source = "unity_main_thread";
        public long instance_started_at_ms;
        public long observed_at_ms;
        public string save_id;
        public string world_id = "songjiangfu";
        public string instance_id;
        public long sequence;
        public ActorFactData[] actors = Array.Empty<ActorFactData>();
        public EntityFactData[] entities = Array.Empty<EntityFactData>();
        public DeviceFactData[] devices = Array.Empty<DeviceFactData>();
        public FactObservationData[] facts = Array.Empty<FactObservationData>();
        public ActionFactData[] actions = Array.Empty<ActionFactData>();
        public ActionFactData[] receipts = Array.Empty<ActionFactData>();
    }

    [Serializable]
    public sealed class WorldInputData
    {
        public string location_id = "apartment_table";
        public string location_label = "出租屋餐桌旁";
        public string activity = "坐着休息";
        public string body = "有些疲惫，没有受伤";
        public string held_item = "";
        public string scene = "窗外下着雨，屋里亮着暖灯";
        public SceneFactData[] scene_facts = Array.Empty<SceneFactData>();
        public GameSnapshotData game_snapshot;
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
        public GameClockData game_clock;
        public WorldStateView world;
        public HeroineView heroine;
    }
}
