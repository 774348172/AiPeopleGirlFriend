using System.IO;
using System.Text;
using AiPeople.World;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.AI;

namespace AiPeople.EditorTools
{
    /// <summary>Read-only physics/navigation survey of the installed apartment.</summary>
    public static class ApartmentTraversalAudit
    {
        public static void Survey()
        {
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var world = ApartmentBuilder.Build(AssetDatabase.LoadAssetAtPath<GameObject>(ApartmentShellImporter.PrefabPath));
            Physics.SyncTransforms();
            var nav = world.Root.GetComponentInChildren<ApartmentNavigation>();
            var instance = NavMesh.AddNavMeshData(nav.data);
            Directory.CreateDirectory("../output/apartment-traversal");
            try
            {
                var report = new StringBuilder();
                foreach (var point in ImportedApartmentLayout.TraversalPoints)
                {
                    bool found = nav.TryPath(ImportedApartmentLayout.EntryPosition, point.Position, out var corners);
                    report.AppendLine($"room={point.Name} point={point.Position} reachable={found} corners={corners?.Length ?? 0}");
                }
                var cc = new GameObject("CrossingProbe").AddComponent<CharacterController>();
                PlayerController.ConfigureCapsule(cc);
                foreach (float x in new[] { .2f, .3f, .4f, 3.8f, 4.1f, 4.3f })
                {
                    cc.enabled = false;
                    cc.transform.position = new Vector3(x, .12f, x < 1 ? .05f : .45f);
                    cc.enabled = true;
                    for (int i = 0; i < 120; i++) cc.Move(new Vector3(0, -.03f, .025f));
                    report.AppendLine($"crossing x={x} end={cc.transform.position:F3}");
                }
                Object.DestroyImmediate(cc.gameObject);
                File.WriteAllText("../output/apartment-traversal/survey.txt", report.ToString());
                using (var file = new StreamWriter("../output/apartment-traversal/grid.csv"))
                {
                    file.WriteLine("x,z,floor,blocked,reachable");
                    for (int iz = -70; iz <= 70; iz++)
                    for (int ix = -65; ix <= 65; ix++)
                    {
                        float x = ix * .1f, z = iz * .1f;
                        bool floor = Physics.Raycast(new Vector3(x, .6f, z), Vector3.down, out var hit, 1.2f);
                        bool blocked = Physics.CheckCapsule(new Vector3(x, .38f, z), new Vector3(x, 1.42f, z), .28f);
                        bool reachable = !blocked && floor && nav.TryPath(world.PlayerSpawn, new Vector3(x, hit.point.y, z), out _);
                        file.WriteLine(System.FormattableString.Invariant($"{x:F2},{z:F2},{(floor ? hit.point.y : -9):F3},{(blocked ? 1 : 0)},{(reachable ? 1 : 0)}"));
                    }
                }
                Debug.Log(report.ToString());
            }
            finally { instance.Remove(); Object.DestroyImmediate(world.Root.gameObject); }
        }
    }
}

