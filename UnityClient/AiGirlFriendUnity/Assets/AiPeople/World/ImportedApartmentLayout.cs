using UnityEngine;

namespace AiPeople.World
{
    /// <summary>Placement for the user's September 14 shell, normalized to 12m width.</summary>
    public static class ImportedApartmentLayout
    {
        public static readonly Vector3 HeroinePosition = new Vector3(-2f, 0f, -3.2f);
        public const float HeroineYaw = 315f;
        public static readonly Vector3 CameraPosition = new Vector3(-4.85f, 2.6f, -3.9f);
        public static readonly Vector3 CameraTarget = new Vector3(-1.5f, .15f, -1.55f);
        public const float WallpaperFieldOfView = 54f;
        public const float WallpaperAspect = 16f / 9f;
        public static readonly Vector3 OverviewPosition = new Vector3(-10f, 15f, -18f);

        // Narrow displays retain horizontal composition. Wide displays gain room context up to
        // the measured wall edge; beyond that, adjust FOV instead of exposing the shell exterior.
        public static float CameraFieldOfView(float aspect) =>
            2f * Mathf.Atan(Mathf.Tan(WallpaperFieldOfView * Mathf.Deg2Rad * .5f)
                * Mathf.Clamp(aspect, WallpaperAspect, 2.2f) / Mathf.Max(.5f, aspect)) * Mathf.Rad2Deg;

        // Local geometry targets only: deliberately not Waypoints or authoritative location IDs.
        public readonly struct RoomPoint
        {
            public readonly string Name;
            public readonly Vector3 Position;
            public RoomPoint(string name, Vector3 position) { Name = name; Position = position; }
        }

        public static readonly RoomPoint[] TraversalPoints = {
            new RoomPoint("Living", new Vector3(-2, 0, -3.2f)),
            new RoomPoint("Kitchen", new Vector3(-3.4f, 0, 3.2f)),
            new RoomPoint("Master", new Vector3(.3f, 0, 2.5f)),
            new RoomPoint("Secondary", new Vector3(4, 0, 3)),
            new RoomPoint("Bathroom", new Vector3(4, 0, -3)),
            new RoomPoint("Balcony", new Vector3(0, -.16f, -5.8f)),
            new RoomPoint("Hall", new Vector3(4, 0, -.7f)),
        };
        public static readonly Vector3 EntryPosition = new Vector3(-1.1f, 0, 4.5f);

        public static void Apply(ApartmentBuilder.Result world, Transform furniture)
        {
            Move(furniture, "TableTop", -2f, -1f);
            Move(furniture, "TableBase", -2f, -1f);
            Move(furniture, "ChairA", -2.7f, -.4f);
            Move(furniture, "ChairB", -1.3f, -.4f);
            Move(furniture, "水杯", -1.8f, -1f);
            Move(furniture, "Rug", -2f, -1f);
            furniture.Find("Rug").gameObject.SetActive(false); // Reconstructed floor is not perfectly planar.
            Move(furniture, "BedBase", 1.5f, 3.5f);
            Move(furniture, "Mattress", 1.5f, 3.5f);
            Move(furniture, "KitchenCounter", -4f, 4.4f);
            Move(furniture, "Pot", -4.3f, 4.4f);
            Move(furniture, "纸箱", -2.8f, -3.6f);
            Move(furniture, "LightSwitch", -1.65f, 4.5f);
            Physics.SyncTransforms();
            var shellCollider = world.Root.Find("Structure/ImportedApartmentShell").GetComponentInChildren<MeshCollider>();
            if (shellCollider.Raycast(new Ray(new Vector3(-1.1f, 1.25f, 4.5f), Vector3.left), out var wall, 2f))
            {
                var lightSwitch = furniture.Find("LightSwitch");
                lightSwitch.position = wall.point + wall.normal * .035f;
                lightSwitch.rotation = Quaternion.LookRotation(wall.normal);
            }
            // The imported windows share the whole-house atlas and cannot be animated separately.
            // Do not tint that renderer or place a misleading floating window switch.
            furniture.Find("WindowHandle").gameObject.SetActive(false);
            world.WindowRenderer = null;
            world.Waypoints[0].transform.localPosition = new Vector3(-2f, 0f, -.1f);
            world.Waypoints[1].transform.localPosition = new Vector3(-3.4f, 0f, 3.2f);
            world.Waypoints[2].transform.localPosition = EntryPosition;
            world.PlayerSpawn = new Vector3(-2f, .12f, -2.1f);
            world.PlayerYaw = -30f;
            world.Lamp.transform.localPosition = new Vector3(-1f, 2.3f, -1f);
            world.Rain.transform.localPosition = new Vector3(-1.5f, 3.2f, -6.4f);
            ApartmentFurnishings.Build(world, furniture);
        }

        private static void Move(Transform parent, string name, float x, float z)
        {
            Transform item = parent.Find(name);
            item.localPosition = new Vector3(x, item.localPosition.y, z);
        }
    }
}
