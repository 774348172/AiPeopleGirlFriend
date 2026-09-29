using UnityEngine;
using AiPeople.Core;

namespace AiPeople.World
{
    public sealed class WorldEntity : MonoBehaviour
    {
        public string entityId, displayName;
        public string ownerId = "";
        public string state = "在场景中";
        public bool canPickUp;

        public EntityFactData Capture(bool imported, Transform owner = null)
        {
            var location = ApartmentLocations.Locate("entity", owner != null ? owner.position : transform.position, imported, "");
            return new EntityFactData { entity_id = entityId, label = displayName, owner_id = ownerId,
                state = state, location_id = location.location_id, location_label = location.location_label,
                x = location.x, y = location.y, z = location.z };
        }
    }
}
