using System.Collections.Generic;
using AiPeople.Core;
using UnityEngine;

namespace AiPeople.World
{
    /// <summary>
    /// 出租屋 blockout：程序化生成房间、家具与位置锚点（M1 占位，M2 起细化美术）。
    /// 必备陈设依据世界正典：门、窗、餐桌椅、厨房与锅具、床、纸箱（不可移除）。
    /// </summary>
    public static class ApartmentBuilder
    {
        public sealed class Result
        {
            public Transform Root;
            public readonly List<Waypoint> Waypoints = new List<Waypoint>();
            public Vector3 PlayerSpawn;
            public float PlayerYaw;
            public Light Sun;
            public Light Lamp;
            public Renderer WindowRenderer;
            public ParticleSystem Rain;
            public GameObject Glass;
            public GameObject CardboardBox;
            public Transform TableTop;
        }

        public static Result Build(GameObject shellPrefab = null)
        {
            var result = new Result();
            var root = new GameObject("Apartment").transform;
            result.Root = root;

            var structure = new GameObject("Structure").transform;
            structure.SetParent(root, false);
            var furniture = new GameObject("Furniture").transform;
            furniture.SetParent(root, false);
            var lights = new GameObject("Lights").transform;
            lights.SetParent(root, false);
            var anchors = new GameObject("Waypoints").transform;
            anchors.SetParent(root, false);

            Material floorMaterial = MaterialLibrary.Get("Floor", new Color(0.38f, 0.30f, 0.24f));
            Material wallMaterial = MaterialLibrary.Get("Wall", new Color(0.80f, 0.78f, 0.74f));
            Material ceilingMaterial = MaterialLibrary.Get("Ceiling", new Color(0.86f, 0.85f, 0.83f));
            Material doorMaterial = MaterialLibrary.Get("Door", new Color(0.42f, 0.28f, 0.18f));
            Material windowMaterial = MaterialLibrary.Get("Window", new Color(0.62f, 0.72f, 0.82f));
            Material woodMaterial = MaterialLibrary.Get("Wood", new Color(0.52f, 0.36f, 0.22f));
            Material fabricMaterial = MaterialLibrary.Get("Fabric", new Color(0.55f, 0.58f, 0.66f));
            Material counterMaterial = MaterialLibrary.Get("Counter", new Color(0.60f, 0.62f, 0.64f));
            Material potMaterial = MaterialLibrary.Get("Pot", new Color(0.35f, 0.36f, 0.38f));
            Material boxMaterial = MaterialLibrary.Get("Cardboard", new Color(0.66f, 0.50f, 0.30f));
            Material rugMaterial = MaterialLibrary.Get("Rug", new Color(0.45f, 0.40f, 0.44f));

            if (shellPrefab != null)
            {
                Object.Instantiate(shellPrefab, structure, false).name = "ImportedApartmentShell";
            }
            else
            {
                // 全屋实时 3D 结构骨架：12m(x) × 10m(z) × 2.9m(y)。
                // 坐标约定：客厅居中；厨房在后左；玄关在后中；主卧/次卧在右侧；卫生间靠右前；阳台在前侧。
                Box(structure, "Floor", new Vector3(0f, -0.05f, 0f), new Vector3(12f, 0.1f, 10f), floorMaterial);
                Box(structure, "Ceiling", new Vector3(0f, 2.95f, 0f), new Vector3(12f, 0.1f, 10f), ceilingMaterial);

                // 外墙：前侧为阳台，后侧为玄关/厨房/卧室外墙。
                Box(structure, "WallBackLeft", new Vector3(-4.25f, 1.45f, 5f), new Vector3(3.5f, 2.9f, 0.18f), wallMaterial);
                Box(structure, "WallBackMiddle", new Vector3(0.0f, 1.45f, 5f), new Vector3(2.8f, 2.9f, 0.18f), wallMaterial);
                Box(structure, "WallBackRight", new Vector3(4.7f, 1.45f, 5f), new Vector3(2.3f, 2.9f, 0.18f), wallMaterial);
                Box(structure, "WallLeft", new Vector3(-6f, 1.45f, 0f), new Vector3(0.18f, 2.9f, 10f), wallMaterial);
                Box(structure, "WallRight", new Vector3(6f, 1.45f, 0.6f), new Vector3(0.18f, 2.9f, 8.8f), wallMaterial);
                Box(structure, "WallFrontLeft", new Vector3(-4.3f, 1.45f, -5f), new Vector3(3.4f, 2.9f, 0.18f), wallMaterial);
                Box(structure, "WallFrontRight", new Vector3(4.9f, 1.45f, -5f), new Vector3(2.2f, 2.9f, 0.18f), wallMaterial);

                // 厨房与玄关分隔，保留进入客厅的开口。
                Box(structure, "PartitionKitchen", new Vector3(-2.35f, 1.45f, 2.0f), new Vector3(0.18f, 2.9f, 3.0f), wallMaterial);
                Box(structure, "PartitionKitchenBack", new Vector3(-4.25f, 1.45f, 2.25f), new Vector3(3.5f, 2.9f, 0.18f), wallMaterial);
                Box(structure, "PartitionEntryLeft", new Vector3(-0.8f, 1.45f, 3.25f), new Vector3(0.18f, 2.9f, 1.75f), wallMaterial);
                Box(structure, "PartitionEntryRight", new Vector3(0.85f, 1.45f, 3.25f), new Vector3(0.18f, 2.9f, 1.75f), wallMaterial);

                // 右侧主卧、次卧与卫生间隔墙，房间均向公共空间单独开门。
                Box(structure, "PartitionBedrooms", new Vector3(2.0f, 1.45f, 2.0f), new Vector3(0.18f, 2.9f, 6.0f), wallMaterial);
                Box(structure, "PartitionBedroomSplit", new Vector3(4.0f, 1.45f, 0.25f), new Vector3(0.18f, 2.9f, 3.5f), wallMaterial);
                Box(structure, "PartitionBathroom", new Vector3(4.9f, 1.45f, -2.25f), new Vector3(2.0f, 2.9f, 0.18f), wallMaterial);
                Box(structure, "BathroomRight", new Vector3(4.9f, 1.45f, -3.7f), new Vector3(2.0f, 2.9f, 0.18f), wallMaterial);

                // 入户门、主要窗洞和阳台结构均为独立对象，后续替换为正式 Prefab。
                Box(structure, "ApartmentDoor", new Vector3(0f, 1.05f, 4.9f), new Vector3(1.0f, 2.1f, 0.14f), doorMaterial);
                GameObject window = Box(structure, "LivingWindow", new Vector3(-2.5f, 1.7f, -4.9f), new Vector3(2.2f, 1.2f, 0.08f), windowMaterial);
                result.WindowRenderer = window.GetComponent<MeshRenderer>();
                Box(structure, "MasterWindow", new Vector3(3.25f, 1.7f, 4.9f), new Vector3(1.3f, 1.1f, 0.08f), windowMaterial);
                Box(structure, "SecondaryWindow", new Vector3(5.9f, 1.7f, 1.5f), new Vector3(0.08f, 1.1f, 1.3f), windowMaterial);
                Box(structure, "BathroomWindow", new Vector3(5.9f, 1.7f, -3.7f), new Vector3(0.08f, 0.8f, 0.8f), windowMaterial);
                Box(structure, "BalconyFloor", new Vector3(-0.8f, 0.02f, -5.65f), new Vector3(8.4f, 0.12f, 1.3f), floorMaterial);
                Box(structure, "BalconyRail", new Vector3(-0.8f, 0.75f, -6.2f), new Vector3(8.4f, 1.2f, 0.08f), counterMaterial);
            }

            // 家具
            GameObject tableTop = Box(furniture, "TableTop", new Vector3(-1.2f, 0.76f, 0.9f), new Vector3(1.6f, 0.08f, 0.9f), woodMaterial);
            result.TableTop = tableTop.transform;
            Box(furniture, "TableBase", new Vector3(-1.2f, 0.36f, 0.9f), new Vector3(1.3f, 0.72f, 0.7f), woodMaterial);
            Box(furniture, "ChairA", new Vector3(-1.9f, 0.22f, 1.5f), new Vector3(0.45f, 0.45f, 0.45f), woodMaterial);
            Box(furniture, "ChairB", new Vector3(-0.5f, 0.22f, 1.5f), new Vector3(0.45f, 0.45f, 0.45f), woodMaterial);
            Box(furniture, "BedBase", new Vector3(2.35f, 0.2f, 0.9f), new Vector3(1.3f, 0.4f, 2.0f), woodMaterial);
            Box(furniture, "Mattress", new Vector3(2.35f, 0.47f, 0.9f), new Vector3(1.2f, 0.14f, 1.9f), fabricMaterial);
            Box(furniture, "KitchenCounter", new Vector3(-2.1f, 0.45f, -1.9f), new Vector3(2.2f, 0.9f, 0.6f), counterMaterial);
            Box(furniture, "Rug", new Vector3(-0.6f, 0.01f, 0.2f), new Vector3(2.4f, 0.02f, 1.7f), rugMaterial);
            GameObject cardboardBox = Box(furniture, "纸箱", new Vector3(0.75f, 0.26f, 2.05f), new Vector3(0.55f, 0.52f, 0.55f), boxMaterial);
            cardboardBox.AddComponent<Interactable>().kind = InteractKind.ExamineBox;
            result.CardboardBox = cardboardBox;

            var pot = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            pot.name = "Pot";
            pot.transform.SetParent(furniture, false);
            pot.transform.localPosition = new Vector3(-2.4f, 0.95f, -1.9f);
            pot.transform.localScale = new Vector3(0.32f, 0.06f, 0.32f);
            pot.GetComponent<MeshRenderer>().sharedMaterial = potMaterial;

            GameObject glass = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            glass.name = "水杯";
            glass.transform.SetParent(furniture, false);
            glass.transform.localPosition = new Vector3(-1.0f, 0.84f, 0.9f);
            glass.transform.localScale = new Vector3(0.08f, 0.06f, 0.08f);
            glass.GetComponent<MeshRenderer>().sharedMaterial = MaterialLibrary.Get("Glass", new Color(0.78f, 0.86f, 0.90f));
            glass.AddComponent<Interactable>().kind = InteractKind.TakeWater;
            result.Glass = glass;

            GameObject lightSwitch = Box(furniture, "LightSwitch", new Vector3(-0.8f, 1.25f, 4.72f), new Vector3(0.12f, 0.18f, 0.06f), counterMaterial);
            lightSwitch.AddComponent<Interactable>().kind = InteractKind.ToggleLight;

            GameObject windowHandle = Box(furniture, "WindowHandle", new Vector3(-2.5f, 1.12f, -4.72f), new Vector3(0.1f, 0.14f, 0.08f), counterMaterial);
            windowHandle.AddComponent<Interactable>().kind = InteractKind.ToggleWindow;

            // 灯光：窗外冷光 + 屋内暖灯
            var sun = new GameObject("WindowLight");
            sun.transform.SetParent(lights, false);
            sun.transform.rotation = Quaternion.Euler(55f, -35f, 0f);
            var sunLight = sun.AddComponent<Light>();
            sunLight.type = LightType.Directional;
            sunLight.intensity = 0.45f;
            sunLight.color = new Color(0.78f, 0.84f, 0.95f);

            var lamp = new GameObject("WarmLamp");
            lamp.transform.SetParent(lights, false);
            lamp.transform.localPosition = new Vector3(0.2f, 2.5f, 0.2f);
            var lampLight = lamp.AddComponent<Light>();
            lampLight.type = LightType.Point;
            lampLight.range = 9f;
            lampLight.intensity = 2.2f;
            lampLight.color = new Color(1f, 0.87f, 0.70f);

            result.Sun = sunLight;
            result.Lamp = lampLight;

            // 窗外雨/雪粒子（默认关闭，由 SceneAtmosphere 按 scene 描述启停）
            var rainGo = new GameObject("Rain");
            rainGo.transform.SetParent(root, false);
            rainGo.transform.localPosition = new Vector3(1.3f, 2.6f, 3.6f);
            rainGo.transform.localRotation = Quaternion.Euler(90f, 0f, 0f);
            var rain = rainGo.AddComponent<ParticleSystem>();
            ParticleSystem.MainModule rainMain = rain.main;
            rainMain.startLifetime = 1.1f;
            rainMain.startSpeed = 6.5f;
            rainMain.startSize = 0.035f;
            rainMain.gravityModifier = 1.2f;
            rainMain.maxParticles = 800;
            rainMain.simulationSpace = ParticleSystemSimulationSpace.World;
            ParticleSystem.EmissionModule rainEmission = rain.emission;
            rainEmission.rateOverTime = 0f;
            ParticleSystem.ShapeModule rainShape = rain.shape;
            rainShape.shapeType = ParticleSystemShapeType.Box;
            rainShape.scale = new Vector3(2.6f, 0.2f, 2.2f);
            Material rainMaterial = ParticleMaterial();
            if (rainMaterial != null)
            {
                rain.GetComponent<ParticleSystemRenderer>().sharedMaterial = rainMaterial;
            }

            result.Rain = rain;
            rain.Stop();

            // 位置锚点（location_id 对齐后端预设词汇）
            result.Waypoints.Add(CreateWaypoint(anchors, "Waypoint_Table",
                new Vector3(-1.2f, 0f, 1.7f), "apartment_table", "出租屋餐桌旁",
                "站在餐桌旁", "没有明显不适", "", "窗外阴着，屋里亮着暖灯"));

            result.Waypoints.Add(CreateWaypoint(anchors, "Waypoint_Kitchen",
                new Vector3(-2.1f, 0f, -1.0f), "apartment_kitchen", "出租屋厨房",
                "站在厨房边", "没有明显不适", "", "灶台边的灯亮着，窗外很安静"));

            result.Waypoints.Add(CreateWaypoint(anchors, "Waypoint_Door",
                new Vector3(-0.6f, 0f, -1.8f), "apartment_door", "出租屋门口",
                "站在门口", "没有明显不适", "", "门关着，走廊的灯从门缝里透进来"));

            result.PlayerSpawn = new Vector3(0.6f, 0.1f, -0.6f);
            result.PlayerYaw = -50f;
            if (shellPrefab != null) ImportedApartmentLayout.Apply(result, furniture);
            return result;
        }

        private static Waypoint CreateWaypoint(
            Transform parent,
            string name,
            Vector3 position,
            string locationId,
            string locationLabel,
            string activity,
            string body,
            string heldItem,
            string scene)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent, false);
            go.transform.localPosition = position;
            var waypoint = go.AddComponent<Waypoint>();
            waypoint.locationId = locationId;
            waypoint.locationLabel = locationLabel;
            waypoint.activity = activity;
            waypoint.body = body;
            waypoint.heldItem = heldItem;
            waypoint.scene = scene;
            return waypoint;
        }

        private static GameObject Box(Transform parent, string name, Vector3 position, Vector3 scale, Material material)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            go.name = name;
            go.transform.SetParent(parent, false);
            go.transform.localPosition = position;
            go.transform.localScale = scale;
            go.GetComponent<MeshRenderer>().sharedMaterial = material;
            return go;
        }

        private static Material ParticleMaterial()
        {
            Shader shader = Shader.Find("Universal Render Pipeline/Particles/Unlit");
            if (shader == null)
            {
                shader = Shader.Find("Particles/Standard Unlit");
            }

            if (shader == null)
            {
                return null;
            }

            var material = new Material(shader) { name = "AiPeople/Rain" };
            var color = new Color(0.75f, 0.82f, 0.95f, 0.6f);
            if (material.HasProperty("_BaseColor"))
            {
                material.SetColor("_BaseColor", color);
            }

            if (material.HasProperty("_Color"))
            {
                material.SetColor("_Color", color);
            }

            return material;
        }
    }
}
