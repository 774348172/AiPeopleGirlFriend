using UnityEngine;

namespace AiPeople.World
{
    public enum InteractKind
    {
        ToggleLight,
        ToggleWindow,
        TakeWater,
        ExamineBox,
        ToggleDoor,
    }

    /// <summary>
    /// 玩家可交互物：只产生男主侧事实（灯/窗/手持物/查看），不改写女主状态与剧情事实。
    /// </summary>
    public sealed class Interactable : MonoBehaviour
    {
        public InteractKind kind = InteractKind.ToggleLight;
        public string examineText = "";
    }
}
