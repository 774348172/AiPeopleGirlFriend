#if UNITY_EDITOR
using System.Collections;
using System.Linq;
using AiPeople.World;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace AiPeople.Tests
{
    public sealed class ApartmentFurnishingTests
    {
        private ApartmentBuilder.Result _world;
        private Transform _furniture;

        [UnitySetUp]
        public IEnumerator SetUp()
        {
            _world=ApartmentBuilder.Build(AssetDatabase.LoadAssetAtPath<GameObject>(
                "Assets/Art/Apartment/ImportedShell/ApartmentShell.prefab"));
            _furniture=_world.Root.Find("Furniture");
            yield return null;
            Physics.SyncTransforms();
        }

        [Test]
        public void FurnishedLayout_RetainsInteractions_Appliances_AndSeparateDiningArea()
        {
            Assert.Less(_world.TableTop.position.x,-3);
            Assert.Less(_world.TableTop.position.z,-3);
            Assert.Greater(_furniture.Find("LivingSofa").position.x,_furniture.Find("LivingCoffeeTable").position.x);
            Assert.Less(_furniture.Find("LivingTvConsole").position.x,_furniture.Find("LivingCoffeeTable").position.x);
            foreach(string name in new[]{"BathroomWasher","KitchenFridge","KitchenStove","KitchenHood",
                "KitchenRiceCooker","KitchenMicrowave","KitchenKettle","LivingFan","LivingTvConsole"})
                Assert.Greater(_furniture.Find(name).GetComponentsInChildren<MeshRenderer>().Length,0,name);
            Assert.AreEqual(3,_world.Waypoints.Count,"Furnishing must not invent backend locations");
            Assert.AreEqual(1,_furniture.GetComponentsInChildren<Interactable>().Count(i=>i.kind==InteractKind.ExamineBox));
            Assert.AreEqual(InteractKind.TakeWater,_world.Glass.GetComponent<Interactable>().kind);
            var glass=_world.Glass.GetComponent<Collider>().bounds;
            Assert.That(glass.min.y,Is.EqualTo(_world.TableTop.GetComponent<Collider>().bounds.max.y).Within(.035f));
            foreach(string name in new[]{"LivingSofa","LivingCoffeeTable","LivingTvConsole","KitchenCounter",
                "KitchenFridge","BathroomWasher","BathroomVanity","SecondaryDesk","SecondaryShelving","BedBase"})
            {
                var collider=_furniture.Find(name).GetComponent<Collider>();
                Assert.IsTrue(collider.enabled && !collider.isTrigger,name);
            }
            // Imported v2 models have their own measured budgets. Keep the original
            // 5k limit for procedural furniture, rather than counting missing models as a pass.
            var imports = new[] { _furniture.Find("LivingSofa/SofaV2Model"),
                _furniture.Find("LivingCoffeeTable/StoolV2Model") };
            int triangles=_furniture.GetComponentsInChildren<MeshFilter>()
                .Where(m=>m.GetComponent<Renderer>().enabled && !imports.Any(t=>t!=null && m.transform.IsChildOf(t)))
                .Sum(m=>TriangleCount(m.sharedMesh));
            Assert.Less(triangles,5000,"Procedural furniture budget");
        }

        private static int TriangleCount(Mesh mesh)
        {
            int count=0;
            for(int i=0;i<mesh.subMeshCount;i++) count+=(int)mesh.GetIndexCount(i)/3;
            return count;
        }

        [TestCase("LivingSofa", "SofaV2", 12453)]
        [TestCase("LivingCoffeeTable", "StoolV2", 24287)]
        public void ImportedFurniture_RetainsAllGeometryMaterialsAndTransforms(string group,string asset,int expectedTriangles)
        {
            var source=Resources.Load<GameObject>("ApartmentFurniture/"+asset);
            Assert.IsNotNull(source);
            var instance=_furniture.Find(group+"/"+asset+"Model");
            Assert.IsNotNull(instance,"Imported prefab was deleted by decoration batching");
            var expected=source.GetComponentsInChildren<MeshFilter>(true);
            var actual=instance.GetComponentsInChildren<MeshFilter>(true);
            Assert.AreEqual(expected.Length,actual.Length);
            Assert.AreEqual(expectedTriangles,actual.Sum(m=>TriangleCount(m.sharedMesh)));
            for(int i=0;i<expected.Length;i++)
            {
                Assert.AreSame(expected[i].sharedMesh,actual[i].sharedMesh,"FBX geometry must remain intact");
                Assert.IsTrue(actual[i].gameObject.activeInHierarchy);
                Assert.IsTrue(actual[i].GetComponent<Renderer>().enabled);
                CollectionAssert.AreEqual(expected[i].GetComponent<Renderer>().sharedMaterials,
                    actual[i].GetComponent<Renderer>().sharedMaterials);
                var a=source.transform.worldToLocalMatrix*expected[i].transform.localToWorldMatrix;
                var b=instance.worldToLocalMatrix*actual[i].transform.localToWorldMatrix;
                for(int n=0;n<16;n++) Assert.That(b[n],Is.EqualTo(a[n]).Within(.0001f),"Model scale/orientation changed");
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void DecorationBatching_PreservesImportedMultiMaterialMesh(bool readable)
        {
            var root=new GameObject("ImportedBatchProbe");
            root.transform.SetParent(_furniture,false);
            var child=new GameObject("ImportedMesh");
            child.transform.SetParent(root.transform,false);
            var mesh=new Mesh();
            mesh.vertices=new[]{Vector3.zero,Vector3.right,Vector3.up,Vector3.one};
            mesh.subMeshCount=2;
            mesh.SetTriangles(new[]{0,1,2},0); mesh.SetTriangles(new[]{1,3,2},1);
            child.AddComponent<MeshFilter>().sharedMesh=mesh;
            var renderer=child.AddComponent<MeshRenderer>();
            var mats=Resources.Load<GameObject>("ApartmentFurniture/SofaV2")
                .GetComponentInChildren<MeshRenderer>().sharedMaterials;
            renderer.sharedMaterials=new[]{mats[0],mats[0]};
            if(!readable) mesh.UploadMeshData(true);
            try
            {
                typeof(ApartmentFurnishings).GetMethod("Merge",System.Reflection.BindingFlags.NonPublic
                    |System.Reflection.BindingFlags.Static).Invoke(null,new object[]{root.transform,
                        _furniture.GetComponent<FurnitureMeshLifetime>()});
                Assert.IsTrue(child.activeSelf,"Batching must not hide unsupported source geometry");
                Assert.AreSame(mesh,child.GetComponent<MeshFilter>().sharedMesh);
                Assert.AreEqual(2,mesh.subMeshCount);
                Assert.AreEqual(2,renderer.sharedMaterials.Length);
                LogAssert.NoUnexpectedReceived();
            }
            finally { Object.DestroyImmediate(root); Object.DestroyImmediate(mesh); }
        }

        [Test]
        public void FurnitureVolumes_DoNotPierceShell_AndExteriorStillBlocksAboveFurniture()
        {
            foreach(var c in _furniture.GetComponentsInChildren<BoxCollider>().Where(c=>c.enabled))
            {
                var half=c.bounds.extents-Vector3.one*.025f;
                if(half.x<=0 || half.y<=0 || half.z<=0) continue;
                foreach(var hit in Physics.OverlapBox(c.bounds.center,half,Quaternion.identity))
                    Assert.IsTrue(hit.transform.IsChildOf(_furniture),c.name+" intersects "+hit.name);
            }
            // Directly test the real shell so newly placed furniture cannot mask a missing outer wall.
            var shell=_world.Root.Find("Structure/ImportedApartmentShell/ShellGeometry").GetComponent<MeshCollider>();
            foreach(var probe in new[]{new Vector3(-4.25f,1.8f,-.6f),new Vector3(-3.7f,1.8f,3)})
                Assert.IsTrue(shell.Raycast(new Ray(probe,Vector3.left),out _,2),"Missing left exterior wall");
            foreach(var probe in new[]{new Vector3(4.6f,1.8f,2.4f),new Vector3(3.95f,1.8f,-2.65f)})
                Assert.IsTrue(shell.Raycast(new Ray(probe,Vector3.right),out _,2),"Missing right exterior wall");
            Assert.IsTrue(shell.Raycast(new Ray(new Vector3(-3.1f,1.8f,-4.05f),Vector3.back),out _,2),
                "Missing living room exterior window beside dining area");
        }

        [Test]
        public void EveryDoor_FullSweptArcAndPortal_RemainClearOfFurniture()
        {
            foreach(var door in _world.Root.GetComponentsInChildren<ApartmentDoor>())
            {
                var bounds=door.leaf.GetComponent<MeshFilter>().sharedMesh.bounds;
                for(int step=0;step<=36;step++)
                {
                    float t=step/36f;
                    var rotation=Quaternion.Euler(0,Mathf.Lerp(door.closedYaw,door.openYaw,t),0);
                    var center=door.transform.TransformPoint(door.openOffset*t+rotation*bounds.center);
                    foreach(var hit in Physics.OverlapBox(center,bounds.extents+Vector3.one*.025f,door.transform.rotation*rotation))
                        Assert.IsFalse(hit.transform.IsChildOf(_furniture),door.doorId+" swept arc hits "+hit.name);
                }
                foreach(var hit in Physics.OverlapBox(door.transform.TransformPoint(door.portal.center),door.portal.extents,door.transform.rotation))
                    Assert.IsFalse(hit.transform.IsChildOf(_furniture),door.doorId+" portal contains "+hit.name);
            }
        }

        [UnityTest]
        public IEnumerator WorldTeardown_ReleasesGeneratedFurnitureMeshes()
        {
            var meshes=_furniture.GetComponent<FurnitureMeshLifetime>().Meshes.ToArray();
            Assert.Greater(meshes.Length,0);
            Object.DestroyImmediate(_world.Root.gameObject);
            yield return null;
            yield return null;
            foreach(var mesh in meshes) Assert.IsTrue(mesh==null,"Generated mesh leaked after world teardown");
            _world=null;
        }

        [UnityTearDown]
        public IEnumerator TearDown()
        {
            if(_world!=null) Object.DestroyImmediate(_world.Root.gameObject);
            yield return null;
        }
    }
}
#endif

