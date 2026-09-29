using System.Collections.Generic;
using AiPeople.Core;

namespace AiPeople.World
{
    /// <summary>Read scene components on the main thread when composing a turn.
    /// Never infer completed effects from requested actions or decorative geometry.</summary>
    public static class SceneFactProjection
    {
        public static string DoorState(bool open, bool moving, bool targetOpen, bool blocked)
        {
            if (moving)
                return blocked ? (targetOpen ? "打开受阻，尚未完成" : "关闭受阻，尚未完成")
                    : (targetOpen ? "正在打开，尚未完成" : "正在关闭，尚未完成");
            return open ? "已打开" : "已关闭";
        }

        public static SceneFactData[] Capture(ApartmentNavigation navigation, SceneAtmosphere atmosphere,
            bool legacyWindow, HeroineDirector director)
        {
            var facts = new List<SceneFactData>();
            if (atmosphere != null)
            {
                Add(facts, "室内灯光", atmosphere.LightOn ? "灯亮着" : "灯关着");
                if (legacyWindow) Add(facts, "客厅窗户", atmosphere.WindowOpen ? "已打开" : "已关闭");
            }
            if (navigation != null)
                foreach (var door in navigation.Doors)
                    if (door != null && door.isActiveAndEnabled)
                        Add(facts, door.displayName, DoorState(door.IsOpen, door.IsMoving, door.TargetOpen, door.IsObstructed));
            if (director != null)
                Add(facts, "白未晞实际移动", director.IsWaitingForPassage ? "通路受阻，正在等待"
                    : director.IsWalking ? "正在行走，尚未到达目标" : "当前没有行走");
            return facts.ToArray();
        }

        public static FactObservationData[] CaptureObservations(ApartmentNavigation navigation,
            SceneAtmosphere atmosphere, bool legacyWindow, HeroineDirector director,
            long version, long observedAtMs)
        {
            var facts = new List<FactObservationData>();
            AddObservation(facts, "interior_light", "室内灯光",
                atmosphere == null ? null : atmosphere.LightOn ? "灯亮着" : "灯关着", version, observedAtMs);
            AddObservation(facts, "living_room_window", "客厅窗户",
                atmosphere == null || !legacyWindow ? null : atmosphere.WindowOpen ? "已打开" : "已关闭",
                version, observedAtMs);
            if (navigation != null)
                foreach (var door in navigation.Doors)
                    if (door != null && door.isActiveAndEnabled)
                        AddObservation(facts, "door_" + door.doorId.ToLowerInvariant(), door.displayName,
                            DoorState(door.IsOpen, door.IsMoving, door.TargetOpen, door.IsObstructed),
                            version, observedAtMs);
            AddObservation(facts, "heroine_motion", "白未晞实际移动",
                director == null ? null : director.IsWaitingForPassage ? "通路受阻，正在等待"
                    : director.IsWalking ? "正在行走，尚未到达目标" : "当前没有行走",
                version, observedAtMs);
            return facts.ToArray();
        }

        private static void AddObservation(List<FactObservationData> facts, string key, string label,
            string value, long version, long observedAtMs)
        {
            facts.Add(new FactObservationData { key = key, label = label,
                state = value == null ? "unknown" : "current", value = value ?? string.Empty,
                source = "unity_main_thread", version = version, observed_at_ms = observedAtMs });
        }

        private static void Add(List<SceneFactData> facts, string label, string value)
        {
            facts.Add(new SceneFactData { label = label, value = value });
        }
    }
}
