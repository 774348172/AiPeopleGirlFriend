using System;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using AiPeople.App;
using AiPeople.World;
using AiPeople.Character;
using UnityEditor.Build.Reporting;

namespace AiPeople.EditorTools
{
    public static class ApartmentShellImporter
    {
        public const string Folder = "Assets/Art/Apartment/ImportedShell";
        public const string ModelPath = Folder + "/ApartmentShell.fbx";
        public const string PrefabPath = Folder + "/ApartmentShell.prefab";

        [Serializable]
        private sealed class DoorRepair
        {
            public float scale;
            public float[] offset;
            public float[] door_pivot;
            public float open_yaw;
        }

        private static Vector3 Vector(float[] values) => new Vector3(values[0], values[1], values[2]);

        [Serializable]
        private sealed class PassagePart
        {
            public string name;
            public float[] pivot;
            public float yaw;
            public float[] shift;
        }

        [Serializable]
        private sealed class PassageRepair { public PassagePart[] doors; }

        public static void ConfigureApp(AiPeopleApp app)
        {
            app.apartmentShellPrefab = AssetDatabase.LoadAssetAtPath<GameObject>(PrefabPath);
            if (app.apartmentShellPrefab == null) return;
            app.heroinePosition = ImportedApartmentLayout.HeroinePosition;
            app.heroineYaw = ImportedApartmentLayout.HeroineYaw;
            app.desktopCameraPosition = ImportedApartmentLayout.CameraPosition;
            app.desktopCameraEuler = Quaternion.LookRotation(
                ImportedApartmentLayout.CameraTarget - app.desktopCameraPosition).eulerAngles;
        }

        [MenuItem("AiPeople/Apartment/Install optimized shell")]
        public static void Install()
        {
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var repair = JsonUtility.FromJson<DoorRepair>(File.ReadAllText("../output/apartment-import/kitchen-door-repair.json"));
            var passages = JsonUtility.FromJson<PassageRepair>(File.ReadAllText("../output/apartment-import/room-passages.json"));
            Vector3 Calibrate(Vector3 p)
            {
                float y = p.y * repair.scale + repair.offset[1];
                float z = p.x * repair.scale + repair.offset[2];
                p.y = (ApartmentMetricCalibration.ShellHeight(y, z) - repair.offset[1]) / repair.scale;
                return p;
            }
            Mesh visual = ReadMesh("walkable-render-mesh.bin", "ApartmentShell_Render.asset", Calibrate);
            Mesh collision = ReadMesh("walkable-collision-mesh.bin", "ApartmentShell_Collision.asset", Calibrate);
            var root = new GameObject("ApartmentShell");
            var model = new GameObject("ShellGeometry");
            model.transform.SetParent(root.transform, false);
            model.AddComponent<MeshFilter>().sharedMesh = visual;
            var renderer = model.AddComponent<MeshRenderer>();
            renderer.sharedMaterial = AssetDatabase.LoadAssetAtPath<Material>(Folder + "/ApartmentShell.mat");
            model.transform.localRotation = Quaternion.Euler(0, -90, 0);
            // Use the measured original calibration for both parts, independent of the split bounds.
            model.transform.localScale = Vector3.one * repair.scale;
            model.transform.localPosition = Vector(repair.offset);
            var collider = model.AddComponent<MeshCollider>();
            collider.sharedMesh = collision;
            InstallDoor(root.transform, "KitchenDoor", "厨房门", Vector(repair.door_pivot),
                ReadMesh("kitchen-door-render-mesh.bin", "ApartmentShell_KitchenDoor.asset"),
                ReadMesh("kitchen-door-collision-mesh.bin", "ApartmentShell_KitchenDoorCollision.asset"),
                new Vector3(1, 0, 1).normalized, 1, 0, -90, Vector3.zero,
                new Vector3(-3.65f, .85f, 1.3f), new Vector3(1.2f, 1.9f, .35f), renderer.sharedMaterial);
            foreach (var part in passages.doors)
            {
                Vector3 direction, center, size;
                string label;
                float width = 1, closed = 0, open = 0;
                switch (part.name)
                {
                    case "MasterDoor":
                        label = "主卧门"; direction = new Vector3(.83205f, 0, .5547f); width = 1.1f; open = -90;
                        center = new Vector3(.12f, .85f, .7f); size = new Vector3(1.2f, 1.9f, .4f); break;
                    case "SecondaryDoor":
                        label = "次卧门"; direction = new Vector3(-.447214f, 0, .894427f); width = 1.75f; closed = 180; open = 270;
                        center = new Vector3(3.78f, .85f, .46f); size = new Vector3(1.95f, 1.9f, .35f); break;
                    case "BathroomDoor":
                        label = "浴室门"; direction = new Vector3(-1, 0, -1).normalized; width = .85f; closed = 180; open = 90;
                        center = new Vector3(4.05f, .85f, -1.63f); size = new Vector3(.92f, 1.9f, .35f); break;
                    case "HallDoor":
                        label = "过道门"; direction = Vector3.back; closed = 90; open = 180;
                        center = new Vector3(2.87f, .85f, -.65f); size = new Vector3(.35f, 1.9f, 1.3f); break;
                    default:
                        label = "阳台移窗"; direction = Vector3.right;
                        center = new Vector3(-.45f, .75f, -4.84f); size = new Vector3(1.64f, 1.9f, .35f); break;
                }
                InstallDoor(root.transform, part.name, label, Vector(part.pivot),
                    ReadMesh(part.name + "-render.bin", "ApartmentShell_" + part.name + ".asset"),
                    ReadMesh(part.name + "-collision.bin", "ApartmentShell_" + part.name + "Collision.asset"),
                    direction.normalized, width, closed, open, Vector(part.shift), center, size, renderer.sharedMaterial);
            }
            // Continuous collision behind the existing rail pickets, including both corner returns.
            AddGuard(root.transform, "BalconyFrontGuard", new Vector3(-.4f, .4f, -6.45f), new Vector3(6.0f, 1.12f, .12f));
            AddGuard(root.transform, "BalconyLeftGuard", new Vector3(-3.4f, .4f, -5.8f), new Vector3(.12f, 1.12f, 1.4f));
            AddGuard(root.transform, "BalconyRightGuard", new Vector3(2.6f, .4f, -5.8f), new Vector3(.12f, 1.12f, 1.4f));
            // The reconstruction has no floor under closed panels. Real, visible sill pieces
            // bridge those holes; using only NavMesh links would let characters fall through.
            var sillMaterial = AssetDatabase.LoadAssetAtPath<Material>(Folder + "/ApartmentShell_Sills.mat");
            if (sillMaterial == null)
            {
                sillMaterial = new Material(Shader.Find("Universal Render Pipeline/Lit"));
                sillMaterial.SetColor("_BaseColor", new Color(.72f, .68f, .57f));
                sillMaterial.SetFloat("_Smoothness", 0);
                AssetDatabase.CreateAsset(sillMaterial, Folder + "/ApartmentShell_Sills.mat");
            }
            AddSill(root.transform, "HallSill", new Vector3(2.87f, -.015f, -.65f), new Vector3(.48f, .1f, 1.18f), sillMaterial);
            AddSill(root.transform, "BalconySill", new Vector3(-.45f, -.075f, -4.87f), new Vector3(1.64f, .1f, .5f), sillMaterial);
            AddSill(root.transform, "MasterSill", new Vector3(.1f, 0, .6f), new Vector3(1.16f, .1f, .6f), sillMaterial);
            AddSill(root.transform, "BathroomSill", new Vector3(4.05f, -.035f, -1.63f), new Vector3(.9f, .1f, .45f), sillMaterial);
            AddSill(root.transform, "MasterJamb", new Vector3(.68f, 1.2175f, .59f), new Vector3(.025f, 2.365f, .3f), sillMaterial);
            int renderTriangles = 0, collisionTriangles = 0;
            foreach (var filter in root.GetComponentsInChildren<MeshFilter>())
                renderTriangles += filter.sharedMesh.triangles.Length / 3;
            foreach (var meshCollider in root.GetComponentsInChildren<MeshCollider>())
                collisionTriangles += meshCollider.sharedMesh.triangles.Length / 3;
            if (renderTriangles > 15300 || collisionTriangles > 10000)
                throw new InvalidOperationException("Repaired shell exceeded the mesh budget");
            Physics.SyncTransforms();
            if (!collider.Raycast(new Ray(new Vector3(0, 1, -2), Vector3.down), out var floor, 2f))
                throw new InvalidOperationException("Shell living-room floor not found");
            Physics.SyncTransforms();
            PrefabUtility.SaveAsPrefabAsset(root, PrefabPath);
            UnityEngine.Object.DestroyImmediate(root);
            AssetDatabase.SaveAssets();

            var scene = EditorSceneManager.OpenScene("Assets/Scenes/Apartment.unity");
            var app = UnityEngine.Object.FindFirstObjectByType<AiPeopleApp>();
            ConfigureApp(app);
            EditorUtility.SetDirty(app);
            EditorSceneManager.SaveScene(scene);

            var world = ApartmentBuilder.Build(app.apartmentShellPrefab);
            BakeNavigation(world);
            var character = CharacterActor.Create(app.heroinePosition, app.heroineYaw, app.heroineModelPrefab,
                app.heroineAnimatorController, app.heroineHeight);
            Physics.SyncTransforms();
            Render(world.Root.gameObject, "game-house", app.desktopCameraPosition,
                ImportedApartmentLayout.CameraTarget, false);
            Render(world.Root.gameObject, "game-house-top", new Vector3(0, 20, 0), Vector3.zero, true);
            Render(world.Root.gameObject, "game-house-overview", ImportedApartmentLayout.OverviewPosition, Vector3.zero, false);
            Render(world.Root.gameObject, "wallpaper-16x10", app.desktopCameraPosition, ImportedApartmentLayout.CameraTarget, false, 1920, 1200);
            Render(world.Root.gameObject, "wallpaper-21x9", app.desktopCameraPosition, ImportedApartmentLayout.CameraTarget, false, 2520, 1080);
            Render(world.Root.gameObject, "kitchen-door-repaired", new Vector3(-5.6f, 4f, -2f), new Vector3(-3.7f, .6f, 1.6f), false);
            Validate(world, app);
            ApartmentMetricCalibration.Audit(world, character.gameObject);
            UnityEngine.Object.DestroyImmediate(character.gameObject);
            UnityEngine.Object.DestroyImmediate(world.Root.gameObject);
            Debug.Log($"SHELL_INSTALL_OK visualTriangles={renderTriangles} collisionTriangles={collisionTriangles}");
        }

        private static void InstallDoor(Transform parent, string name, string label, Vector3 pivot,
            Mesh visual, Mesh collision, Vector3 direction, float width, float closed, float open,
            Vector3 offset, Vector3 center, Vector3 size, Material material)
        {
            var root = new GameObject(name);
            root.transform.SetParent(parent, false);
            root.transform.localPosition = pivot;
            var leaf = new GameObject("Leaf");
            leaf.transform.SetParent(root.transform, false);
            leaf.transform.localRotation = Quaternion.Euler(0, open, 0);
            leaf.transform.localPosition = offset;
            float originalTop = visual.bounds.max.y;
            leaf.AddComponent<MeshFilter>().sharedMesh = CanonicalDoorMesh(visual, direction, width, originalTop, name + "_Moving");
            leaf.AddComponent<MeshRenderer>().sharedMaterial = material;
            leaf.AddComponent<MeshCollider>().sharedMesh = CanonicalDoorMesh(collision, direction, width, originalTop, name + "_MovingCollision");
            root.AddComponent<Interactable>().kind = InteractKind.ToggleDoor;
            var door = root.AddComponent<ApartmentDoor>();
            door.doorId = name;
            door.displayName = label;
            door.leaf = leaf.transform;
            door.closedYaw = closed;
            door.openYaw = open;
            door.openOffset = offset;
            center.y = 1.025f;
            size.y = 2.25f;
            door.portal = new Bounds(center - pivot, size);
        }

        private static Mesh CanonicalDoorMesh(Mesh source, Vector3 direction, float width, float originalTop, string name)
        {
            string path = Folder + "/ApartmentShell_" + name + ".asset";
            var mesh = AssetDatabase.LoadAssetAtPath<Mesh>(path);
            bool create = mesh == null;
            if (create) mesh = new Mesh();
            mesh.Clear();
            mesh.name = name;
            var vertices = source.vertices;
            var normal = Vector3.Cross(direction, Vector3.up);
            for (int i = 0; i < vertices.Length; i++)
            {
                var p = vertices[i];
                float y = p.y <= .12f ? p.y : .12f + (p.y - .12f) * (2.1f - .12f) / (originalTop - .12f);
                vertices[i] = new Vector3(Vector3.Dot(p, direction)*width, y, Vector3.Dot(p, normal));
            }
            mesh.vertices = vertices;
            mesh.uv = source.uv;
            mesh.triangles = source.triangles;
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            if (create) AssetDatabase.CreateAsset(mesh, path);
            else EditorUtility.SetDirty(mesh);
            return mesh;
        }

        private static void AddGuard(Transform root, string name, Vector3 center, Vector3 size)
        {
            var item = new GameObject(name);
            item.transform.SetParent(root, false);
            item.transform.localPosition = center;
            item.AddComponent<BoxCollider>().size = size;
        }

        private static void AddSill(Transform root, string name, Vector3 center, Vector3 size, Material material)
        {
            var item = GameObject.CreatePrimitive(PrimitiveType.Cube);
            item.name = name;
            item.transform.SetParent(root, false);
            item.transform.localPosition = center;
            item.transform.localScale = size;
            item.GetComponent<MeshRenderer>().sharedMaterial = material;
        }

        private static Mesh ReadMesh(string input, string assetName, Func<Vector3, Vector3> calibrate = null)
        {
            using (var reader = new BinaryReader(File.OpenRead("../output/apartment-import/" + input)))
            {
                int count = reader.ReadInt32(), indexCount = reader.ReadInt32();
                var positions = new Vector3[count];
                var uv = new Vector2[count];
                for (int i = 0; i < count; i++)
                {
                    positions[i] = new Vector3(reader.ReadSingle(), reader.ReadSingle(), reader.ReadSingle());
                    if (calibrate != null) positions[i] = calibrate(positions[i]);
                    uv[i] = new Vector2(reader.ReadSingle(), reader.ReadSingle());
                }
                var indices = new int[indexCount];
                for (int i = 0; i < indexCount; i++) indices[i] = reader.ReadInt32();
                string path = Folder + "/" + assetName;
                var existing = AssetDatabase.LoadAssetAtPath<Mesh>(path);
                // Update through the Mesh API so a 32 -> 16 bit change also refreshes GPU buffers.
                var mesh = existing != null ? existing : new Mesh();
                mesh.Clear();
                mesh.name = Path.GetFileNameWithoutExtension(assetName);
                mesh.indexFormat = count <= 65535 ? UnityEngine.Rendering.IndexFormat.UInt16
                    : UnityEngine.Rendering.IndexFormat.UInt32;
                mesh.vertices = positions;
                mesh.uv = uv;
                mesh.triangles = indices;
                mesh.RecalculateNormals();
                mesh.RecalculateBounds();
                MeshUtility.Optimize(mesh);
                if (existing != null)
                {
                    EditorUtility.SetDirty(existing);
                    return existing;
                }
                AssetDatabase.CreateAsset(mesh, path);
                return mesh;
            }
        }

        private static void Validate(ApartmentBuilder.Result world, AiPeopleApp app)
        {
            var report = new System.Text.StringBuilder();
            if (world.Root.Find("Structure/Ceiling") != null) throw new Exception("Legacy shell still enabled");
            var points = new System.Collections.Generic.List<Vector3> { world.PlayerSpawn, app.heroinePosition };
            foreach (var waypoint in world.Waypoints) points.Add(waypoint.transform.position);
            foreach (var p in points)
            {
                bool grounded = Physics.Raycast(p + Vector3.up * .5f, Vector3.down, out var hit, 1f);
                bool blocked = Physics.CheckCapsule(new Vector3(p.x, .38f, p.z), new Vector3(p.x, 1.4f, p.z), .25f);
                report.AppendLine($"point={p} floor={grounded} floorY={hit.point.y:F3} blocked={blocked}");
                if (!grounded || blocked) throw new Exception("SHELL_ANCHOR_INVALID " + p);
            }
            var navigation = world.Root.GetComponent<ApartmentNavigation>();
            var navigationInstance = UnityEngine.AI.NavMesh.AddNavMeshData(navigation.data);
            try
            {
                foreach (var waypoint in world.Waypoints)
                {
                    bool found = navigation.TryPath(app.heroinePosition, waypoint.transform.position, out var corners);
                    report.AppendLine($"path={waypoint.locationId} reachable={found} corners={corners?.Length ?? 0}");
                    if (!found) throw new Exception("SHELL_PATH_UNREACHABLE " + waypoint.locationId);
                }
                foreach (var room in ImportedApartmentLayout.TraversalPoints)
                {
                    bool outward = navigation.TryPath(ImportedApartmentLayout.EntryPosition, room.Position, out var corners);
                    bool inward = navigation.TryPath(room.Position, ImportedApartmentLayout.EntryPosition, out _);
                    report.AppendLine($"room={room.Name} outward={outward} return={inward} corners={corners?.Length ?? 0}");
                    if (!outward || !inward) throw new Exception("SHELL_ROOM_UNREACHABLE " + room.Name);
                }
            }
            finally { navigationInstance.Remove(); }
            File.WriteAllText("../output/apartment-import/validation.txt", report.ToString());
            Debug.Log(report.ToString());
        }

        public static void BakeNavigation(ApartmentBuilder.Result world)
        {
            var sources = new System.Collections.Generic.List<UnityEngine.AI.NavMeshBuildSource>();
            UnityEngine.AI.NavMeshBuilder.CollectSources(world.Root, ~0,
                UnityEngine.AI.NavMeshCollectGeometry.PhysicsColliders, 0,
                new System.Collections.Generic.List<UnityEngine.AI.NavMeshBuildMarkup>(), sources);
            var settings = UnityEngine.AI.NavMesh.GetSettingsByID(0);
            settings.agentRadius = .30f;
            settings.agentHeight = 1.7f;
            settings.agentClimb = .25f;
            settings.overrideVoxelSize = true;
            settings.voxelSize = .06f;
            var data = UnityEngine.AI.NavMeshBuilder.BuildNavMeshData(settings, sources,
                new Bounds(Vector3.zero, new Vector3(30, 10, 30)), Vector3.zero, Quaternion.identity);
            if (data == null) throw new Exception("House navigation bake failed");
            data.name = "ApartmentShell_Navigation";
            string path = Folder + "/ApartmentShell_Navigation.asset";
            var existing = AssetDatabase.LoadAssetAtPath<UnityEngine.AI.NavMeshData>(path);
            if (existing == null) AssetDatabase.CreateAsset(data, path);
            else
            {
                EditorUtility.CopySerialized(data, existing);
                UnityEngine.Object.DestroyImmediate(data);
                data = existing;
                EditorUtility.SetDirty(data);
            }
            var prefab = PrefabUtility.LoadPrefabContents(PrefabPath);
            var navigation = prefab.GetComponent<ApartmentNavigation>() ?? prefab.AddComponent<ApartmentNavigation>();
            navigation.data = data;
            PrefabUtility.SaveAsPrefabAsset(prefab, PrefabPath);
            PrefabUtility.UnloadPrefabContents(prefab);
            var preview = world.Root.gameObject.AddComponent<ApartmentNavigation>();
            preview.enabled = false;
            preview.data = data;
            preview.enabled = true;
            AssetDatabase.SaveAssets();
        }

        public static void BuildPreviewPlayer()
        {
            string target = Environment.GetEnvironmentVariable("AIPEOPLE_BUILD_PATH")
                ?? "../Builds/CatGirlfriend-Furnished-20260914/CatGirlfriend.exe";
            if (File.Exists(target)) throw new Exception("Use a new output directory; existing player preserved: " + target);
            Directory.CreateDirectory(Path.GetDirectoryName(target));
            AiPeoplePlayerBuilder.BuildWindowsPlayerAtPath(target);
            Debug.Log("HOUSE_BUILD_OK " + Path.GetFullPath(target));
        }

        public static void PreviewDoorStates()
        {
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var world = ApartmentBuilder.Build(AssetDatabase.LoadAssetAtPath<GameObject>(PrefabPath));
            Render(world.Root.gameObject, "doors-open", ImportedApartmentLayout.CameraPosition, ImportedApartmentLayout.CameraTarget, false);
            foreach (var door in world.Root.GetComponentsInChildren<ApartmentDoor>())
            {
                door.leaf.localPosition = Vector3.zero;
                door.leaf.localRotation = Quaternion.Euler(0, door.closedYaw, 0);
            }
            Physics.SyncTransforms();
            Render(world.Root.gameObject, "doors-closed", ImportedApartmentLayout.CameraPosition, ImportedApartmentLayout.CameraTarget, false);
            Render(world.Root.gameObject, "doors-closed-top", new Vector3(0, 20, 0), Vector3.zero, true);
            UnityEngine.Object.DestroyImmediate(world.Root.gameObject);
        }

        [MenuItem("AiPeople/Apartment/Inspect imported shell")]
        public static void Inspect()
        {
            var importer = (ModelImporter)AssetImporter.GetAtPath(ModelPath);
            importer.importAnimation = false;
            importer.animationType = ModelImporterAnimationType.None;
            importer.isReadable = true;
            importer.SaveAndReimport();
            var textureImporter = (TextureImporter)AssetImporter.GetAtPath(Folder + "/ApartmentShell_BaseColor.jpg");
            textureImporter.maxTextureSize = 4096;
            textureImporter.SaveAndReimport();
            var material = AssetDatabase.LoadAssetAtPath<Material>(Folder + "/ApartmentShell.mat");
            if (material == null)
            {
                material = new Material(Shader.Find("Universal Render Pipeline/Lit"));
                AssetDatabase.CreateAsset(material, Folder + "/ApartmentShell.mat");
            }
            material.SetTexture("_BaseMap", AssetDatabase.LoadAssetAtPath<Texture2D>(Folder + "/ApartmentShell_BaseColor.jpg"));
            material.SetColor("_BaseColor", Color.white);
            material.SetFloat("_Smoothness", 0f);
            material.SetFloat("_Cull", 0f);
            EditorUtility.SetDirty(material);
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var root = new GameObject("ApartmentShell");
            var model = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(ModelPath));
            model.transform.SetParent(root.transform, false);
            var renderers = root.GetComponentsInChildren<Renderer>();
            Bounds bounds = renderers[0].bounds;
            foreach (var r in renderers)
            {
                bounds.Encapsulate(r.bounds);
                var mats = r.sharedMaterials;
                for (int i = 0; i < mats.Length; i++) mats[i] = material;
                r.sharedMaterials = mats;
            }
            int vertices = 0, triangles = 0;
            foreach (var mf in root.GetComponentsInChildren<MeshFilter>())
            {
                vertices += mf.sharedMesh.vertexCount;
                triangles += mf.sharedMesh.triangles.Length / 3;
            }
            Debug.Log($"SHELL_PROBE meshes={renderers.Length} vertices={vertices} triangles={triangles} bounds={bounds.ToString("F4")}");
            Directory.CreateDirectory("../output/apartment-import");
            foreach (var mf in root.GetComponentsInChildren<MeshFilter>())
            {
                using (var writer = new BinaryWriter(File.Create("../output/apartment-import/source-mesh.bin")))
                {
                    var mesh = mf.sharedMesh;
                    writer.Write(mesh.vertexCount);
                    writer.Write(mesh.triangles.Length);
                    var positions = mesh.vertices;
                    var uv = mesh.uv;
                    for (int i = 0; i < positions.Length; i++)
                    {
                        var p = mf.transform.TransformPoint(positions[i]);
                        writer.Write(p.x); writer.Write(p.y); writer.Write(p.z);
                        writer.Write(uv[i].x); writer.Write(uv[i].y);
                    }
                    foreach (int index in mesh.triangles) writer.Write(index);
                }
            }
            // Normalize the imported asset uniformly; never stretch the room proportions.
            float scale = 12f / bounds.size.x;
            model.transform.localScale *= scale;
            model.transform.localPosition = -new Vector3(bounds.center.x, bounds.min.y, bounds.center.z) * scale;
            // Inspection only exports the original mesh; it must not replace the optimized game prefab.
            AssetDatabase.SaveAssets();
            Render(root, "shell", new Vector3(12, 14, -16), new Vector3(0, 0, 0), false);
            Render(root, "shell-top", new Vector3(0, 20, 0), Vector3.zero, true);
            UnityEngine.Object.DestroyImmediate(root);
        }

        public static void Render(GameObject root, string name, Vector3 position, Vector3 target, bool orthographic,
            int width = 1920, int height = 1080)
        {
            Directory.CreateDirectory("../output/apartment-import");
            RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat;
            RenderSettings.ambientLight = new Color(.75f, .75f, .75f);
            var light = new GameObject("PreviewLight").AddComponent<Light>();
            light.type = LightType.Directional;
            light.intensity = .7f;
            light.transform.rotation = Quaternion.Euler(50, -30, 0);
            var camera = new GameObject("PreviewCamera").AddComponent<Camera>();
            camera.transform.position = position;
            camera.transform.LookAt(target, orthographic ? Vector3.forward : Vector3.up);
            camera.orthographic = orthographic;
            camera.orthographicSize = 8;
            camera.fieldOfView = position == ImportedApartmentLayout.CameraPosition
                ? ImportedApartmentLayout.CameraFieldOfView((float)width / height) : 42;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(.17f, .21f, .27f);
            var rt = new RenderTexture(width, height, 24);
            camera.targetTexture = rt;
            camera.Render();
            var previous = RenderTexture.active;
            RenderTexture.active = rt;
            var image = new Texture2D(rt.width, rt.height, TextureFormat.RGB24, false);
            image.ReadPixels(new Rect(0, 0, rt.width, rt.height), 0, 0);
            image.Apply();
            File.WriteAllBytes("../output/apartment-import/" + name + ".png", image.EncodeToPNG());
            RenderTexture.active = previous;
            camera.targetTexture = null;
            UnityEngine.Object.DestroyImmediate(image);
            UnityEngine.Object.DestroyImmediate(rt);
            UnityEngine.Object.DestroyImmediate(camera.gameObject);
            UnityEngine.Object.DestroyImmediate(light.gameObject);
        }
    }
}
