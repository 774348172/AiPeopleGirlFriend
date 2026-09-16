using UnityEngine;

namespace AiPeople.World
{
    /// <summary>
    /// 场景位置锚点：玩家靠近即成为"男主当前所在位置"，随对话上报。
    /// location_id / location_label 必须与后端预设词汇一致（见后端 index.html locationPreset）。
    /// </summary>
    public sealed class Waypoint : MonoBehaviour
    {
        public string locationId = "apartment_table";
        public string locationLabel = "出租屋餐桌旁";
        public float radius = 1.6f;

        [Header("随位置上报的男主状态")]
        public string activity = "站着";
        public string body = "没有明显不适";
        public string heldItem = "";
        [TextArea] public string scene = "屋里亮着暖灯";

        public bool Contains(Vector3 position)
        {
            Vector3 delta = position - transform.position;
            delta.y = 0f;
            return delta.sqrMagnitude <= radius * radius;
        }

        private void OnDrawGizmosSelected()
        {
            Gizmos.color = new Color(0.4f, 0.8f, 1f, 0.6f);
            Gizmos.DrawWireSphere(transform.position, radius);
        }
    }
}
