using System.IO;
using System.Linq;
using System.Text;
using AiPeople.App;
using AiPeople.Character;
using AiPeople.World;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.AI;

namespace AiPeople.EditorTools
{
    /// <summary>Refresh furniture and navigation without reimporting or reducing the shell.</summary>
    public static class ApartmentFurnishingRefresh
    {
        private const string Output = "../output/apartment-furnishing-layout";

        [MenuItem("AiPeople/Apartment/Refresh furnishings and preview")]
        public static void Refresh()
        {
            Directory.CreateDirectory(Output);
            var scene = EditorSceneManager.OpenScene("Assets/Scenes/Apartment.unity");
            var app = Object.FindFirstObjectByType<AiPeopleApp>();
            ApartmentShellImporter.ConfigureApp(app);
            EditorUtility.SetDirty(app);
            EditorSceneManager.SaveScene(scene);
            var world = ApartmentBuilder.Build(app.apartmentShellPrefab);
            ApartmentShellImporter.BakeNavigation(world);
            var character = CharacterActor.Create(app.heroinePosition, app.heroineYaw,
                app.heroineModelPrefab, app.heroineAnimatorController, app.heroineHeight);
            Physics.SyncTransforms();
            Audit(world);
            void Render(string name, Vector3 position, Vector3 target, bool top = false, int width = 1920, int height = 1080)
            {
                ApartmentShellImporter.Render(world.Root.gameObject,"furnished-"+name,position,target,top,width,height);
                File.Copy("../output/apartment-import/furnished-"+name+".png",Output+"/"+name+".png",true);
            }
            Render("unity-top",new Vector3(0,20,0),Vector3.zero,true);
            Render("unity-overview",ImportedApartmentLayout.OverviewPosition,Vector3.zero);
            Render("unity-wallpaper-16x9",ImportedApartmentLayout.CameraPosition,ImportedApartmentLayout.CameraTarget);
            Render("unity-wallpaper-16x10",ImportedApartmentLayout.CameraPosition,ImportedApartmentLayout.CameraTarget,false,1920,1200);
            Render("unity-wallpaper-21x9",ImportedApartmentLayout.CameraPosition,ImportedApartmentLayout.CameraTarget,false,2520,1080);
            Object.DestroyImmediate(character.gameObject);
            Object.DestroyImmediate(world.Root.gameObject);
            Debug.Log("FURNISHING_REFRESH_OK");
        }

        private static void Audit(ApartmentBuilder.Result world)
        {
            var furniture = world.Root.Find("Furniture");
            var report = new StringBuilder("Furniture geometry audit. Coordinates in Unity metres.\n");
            var meshes = furniture.GetComponentsInChildren<MeshFilter>().Where(m=>m.GetComponent<Renderer>().enabled).ToArray();
            report.AppendLine($"furnitureTriangles={meshes.Sum(m=>m.sharedMesh.triangles.Length/3)} renderers={meshes.Length}");
            foreach(Transform item in furniture)
                if(item.gameObject.activeSelf) report.AppendLine($"item={item.name} position={item.position:F3} scale={item.localScale:F3}");
            var nav = world.Root.GetComponentInChildren<ApartmentNavigation>();
            var navInstance = NavMesh.AddNavMeshData(nav.data);
            foreach(var room in ImportedApartmentLayout.TraversalPoints)
            {
                bool found=nav.TryPath(ImportedApartmentLayout.EntryPosition,room.Position,out var corners);
                report.AppendLine($"room={room.Name} reachable={found} corners={(found?string.Join(";",corners.Select(p=>p.ToString("F3"))):"none")}");
            }
            for(int iz=-52;iz<=-44;iz++)
            for(int ix=-8;ix<=2;ix++)
            {
                var p=new Vector3(ix*.1f,0,iz*.1f);
                if(!NavMesh.SamplePosition(p,out var floor,.4f,NavMesh.AllAreas)) continue;
                p.y=floor.position.y;
                var overlaps=Physics.OverlapCapsule(p+Vector3.up*.32f,p+Vector3.up*1.4f,.24f);
                if(overlaps.Length>0) report.AppendLine($"balconyOverlap={p:F3} nearest={floor.position:F3} colliders={string.Join(",",overlaps.Select(c=>c.name))}");
            }
            navInstance.Remove();
            foreach(var collider in furniture.GetComponentsInChildren<BoxCollider>().Where(c=>c.enabled))
            {
                var bounds=collider.bounds;
                // Ignore contact with floor at the base and intended support surfaces.
                var center=bounds.center;var half=bounds.extents-Vector3.one*.025f;
                if(half.x<=0 || half.y<=0 || half.z<=0) continue;
                foreach(var other in Physics.OverlapBox(center,half,Quaternion.identity,~0,QueryTriggerInteraction.Ignore))
                    if(!other.transform.IsChildOf(furniture) && other!=collider)
                        report.AppendLine($"structureOverlap={collider.name} with={other.name}");
            }
            var doors=world.Root.GetComponentsInChildren<ApartmentDoor>();
            foreach(var door in doors)
            {
                var bounds=door.leaf.GetComponent<MeshFilter>().sharedMesh.bounds;
                var blockers=new System.Collections.Generic.HashSet<string>();
                for(int i=0;i<=36;i++)
                {
                    float t=i/36f;
                    var rotation=Quaternion.Euler(0,Mathf.Lerp(door.closedYaw,door.openYaw,t),0);
                    var center=door.transform.TransformPoint(door.openOffset*t+rotation*bounds.center);
                    foreach(var hit in Physics.OverlapBox(center,bounds.extents+Vector3.one*.025f,door.transform.rotation*rotation))
                        if(hit.transform.IsChildOf(furniture)) blockers.Add(hit.name);
                }
                report.AppendLine($"doorSweep={door.doorId} furnitureBlockers={string.Join(",",blockers)}");
            }
            for(int ix=19;ix>=-30;ix--)
            {
                float x=ix*.1f;
                bool visible=true;
                string blocker="";
                for(int state=0;state<2;state++)
                {
                    foreach(var door in doors)
                    {
                        door.leaf.localRotation=Quaternion.Euler(0,state==0?door.openYaw:door.closedYaw,0);
                        door.leaf.localPosition=state==0?door.openOffset:Vector3.zero;
                    }
                    Physics.SyncTransforms();
                    foreach(float eye in new[]{.95f,1.5f})
                    {
                        Physics.Raycast(ImportedApartmentLayout.EntryPosition+Vector3.up*eye,Vector3.forward,out var entrance,4);
                        var from=new Vector3(x,eye,-4.15f);var delta=entrance.point-from;
                        if(Physics.Raycast(from,delta.normalized,out var hit,delta.magnitude-.04f,~0,QueryTriggerInteraction.Ignore))
                        { visible=false;blocker=hit.collider.name; }
                    }
                }
                report.AppendLine($"boxCandidateX={x:F2} bothEyesBothDoorStates={visible} blocker={blocker}");
            }
            foreach(var door in doors)
            {
                door.leaf.localRotation=Quaternion.Euler(0,door.openYaw,0);
                door.leaf.localPosition=door.openOffset;
            }
            Physics.SyncTransforms();
            File.WriteAllText(Output+"/geometry-audit.txt",report.ToString());
            Debug.Log(report.ToString());
        }
    }
}
