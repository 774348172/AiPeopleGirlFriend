using UnityEngine;
using AiPeople.Core;

namespace AiPeople.World
{
    public static class ApartmentLocations
    {
        // Conservative interior zones for the calibrated September 14 shell.
        // Wall/doorway margins deliberately remain unclassified. No house resize.
        private static readonly string[] Ids = { "apartment_living_room", "apartment_kitchen",
            "apartment_master_bedroom", "apartment_secondary_bedroom", "apartment_bathroom", "apartment_hall", "apartment_balcony" };
        private static readonly string[] Labels = { "出租屋客厅", "出租屋厨房", "出租屋主卧", "出租屋次卧", "出租屋浴室", "出租屋过道", "出租屋阳台" };
        private static readonly Rect[] Zones = {
            Rect.MinMaxRect(-5.3f,-4.5f,2.4f,.2f), Rect.MinMaxRect(-5.3f,1.7f,-1.8f,4.2f),
            Rect.MinMaxRect(-.7f,1.1f,2.4f,4.3f), Rect.MinMaxRect(3f,1f,5.5f,4.3f),
            Rect.MinMaxRect(3.2f,-4.3f,5.5f,-2f), Rect.MinMaxRect(3.2f,-1.2f,5.4f,.1f),
            Rect.MinMaxRect(-4.5f,-6.1f,2.5f,-5.2f) };

        public static ActorFactData Locate(string actorId, Vector3 position, bool imported, string motion)
        {
            var fact = new ActorFactData { actor_id = actorId, x = position.x, y = position.y, z = position.z,
                location_id = "apartment_interior", location_label = "出租屋内（具体房间未确认）", motion = motion };
            if (!imported || position.y < -.6f || position.y > 2.5f) return fact;
            for (int i = 0; i < Zones.Length; i++)
                if (Zones[i].Contains(new Vector2(position.x, position.z)))
                { fact.location_id = Ids[i]; fact.location_label = Labels[i]; break; }
            return fact;
        }
    }
}
