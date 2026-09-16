using System.Collections.Generic;
using AiPeople.Core;
using UnityEngine;

namespace AiPeople.World
{
    /// <summary>Replaceable low-poly furnishings following the user's September 14 plan.</summary>
    public static class ApartmentFurnishings
    {
        // Right-window placement cannot see the entrance through the bedroom wall/closed door.
        // Keep the box indoors beside the left window, clear of the balcony portal.
        public static readonly Vector3 BoxPosition = new Vector3(-1.9f, 0, -4.15f);
        private static Mesh _cube;
        private static Material Wood => MaterialLibrary.Get("Furniture/Walnut", new Color(.40f,.28f,.19f));
        private static Material LightWood => MaterialLibrary.Get("Furniture/Oak", new Color(.65f,.48f,.30f));
        private static Material Cream => MaterialLibrary.Get("Furniture/Cream", new Color(.77f,.77f,.68f));
        private static Material Dark => MaterialLibrary.Get("Furniture/Ink", new Color(.11f,.15f,.16f));
        private static Material Sage => MaterialLibrary.Get("Furniture/Sage", new Color(.33f,.46f,.44f));
        private static Material Linen => MaterialLibrary.Get("Furniture/Linen", new Color(.75f,.69f,.56f));
        private static Material Steel => MaterialLibrary.Get("Furniture/Steel", new Color(.44f,.50f,.50f));
        private static Material Leaf => MaterialLibrary.Get("Furniture/Leaves", new Color(.28f,.40f,.22f));
        private static Material Clay => MaterialLibrary.Get("Furniture/Clay", new Color(.52f,.33f,.23f));
        private static Vector3 V(float x,float y,float z) => new Vector3(x,y,z);

        public static void Build(ApartmentBuilder.Result world, Transform furniture)
        {
            var shell = world.Root.Find("Structure/ImportedApartmentShell").GetComponentInChildren<MeshCollider>();
            var lifetime = furniture.gameObject.AddComponent<FurnitureMeshLifetime>();
            float Floor(float x,float z) => FloorY(shell,x,z);
            Transform Group(string name,float x,float z, Vector3 collision = default)
            {
                var g=new GameObject(name).transform;
                g.SetParent(furniture,false); g.localPosition=V(x,Floor(x,z),z);
                if(collision != Vector3.zero)
                {
                    var c=g.gameObject.AddComponent<BoxCollider>(); c.size=collision;c.center=V(0,collision.y/2,0);
                }
                return g;
            }
            void Existing(string name,float x,float z,float y,Vector3 size,Material material)
            {
                var t=furniture.Find(name);t.localPosition=V(x,Floor(x,z)+y,z);t.localScale=size;
                t.GetComponent<Renderer>().sharedMaterial=material;
            }

            // Dining table belongs at lower left. The backend table anchor remains the same ID.
            Existing("TableTop",-4.12f,-3.30f,.76f,V(1.15f,.08f,.82f),LightWood);
            furniture.Find("TableBase").gameObject.SetActive(false);
            var g=Group("DiningTableLegs",-4.12f,-3.30f);
            Legs(g,1.02f,.7f,.72f,Wood);
            Existing("ChairA",-4.12f,-2.56f,.43f,V(.44f,.09f,.44f),LightWood);
            Existing("ChairB",-4.12f,-3.97f,.43f,V(.44f,.09f,.44f),LightWood);
            Chair(Group("DiningChairA",-4.12f,-2.56f,V(.44f,.88f,.44f)),1);
            Chair(Group("DiningChairB",-4.12f,-3.97f,V(.44f,.88f,.44f)),-1);
            Existing("水杯",-4.0f,-3.20f,.84f,V(.08f,.06f,.08f),Cream);
            world.Waypoints[0].transform.localPosition=V(-3.05f,Floor(-3.05f,-3.30f),-3.30f);

            // Television on the left wall; sofa faces -X, as in the selected top view.
            g=Group("LivingTvConsole",-4.85f,-1.2f,V(.38f,.51f,1.95f));
            Cube(g,"Cabinet",V(0,.29f,0),V(.38f,.42f,1.95f),Wood);
            for(int i=0;i<3;i++) Cube(g,"Drawer",V(.20f,.3f,(i-1)*.62f),V(.025f,.32f,.57f),LightWood);
            Cube(g,"TvStand",V(.04f,.56f,0),V(.23f,.10f,.5f),Dark);
            Cube(g,"Television",V(-.035f,1.03f,0),V(.08f,.79f,1.45f),Dark);
            Cube(g,"Screen",V(.01f,1.03f,0),V(.01f,.70f,1.36f),Steel);
            g=Group("LivingSofa",1.45f,-2.55f,V(.94f,.92f,2.5f));
            if(!TryRuntimePrefab(g,"SofaV2"))
            {
                Cube(g,"Base",V(0,.23f,0),V(.94f,.30f,2.5f),Wood);
                            Cube(g,"Back",V(.38f,.66f,0),V(.18f,.54f,2.5f),Sage);
                            for(int i=0;i<3;i++)
                            {
                                Cube(g,"Seat",V(-.10f,.46f,(i-1)*.75f),V(.71f,.18f,.72f),Sage);
                                Cube(g,"BackCushion",V(.20f,.7f,(i-1)*.75f),V(.20f,.37f,.72f),Sage);
                            }
                            for(int i=-1;i<=1;i+=2)
                            {
                                Cube(g,"Arm",V(0,.53f,i*1.18f),V(.93f,.37f,.14f),Sage);
                                Cube(g,"Pillow",V(.04f,.7f,i*.84f),V(.25f,.31f,.34f),Linen);
                            }
            }
            // Keep a broad west aisle and a straight approach to the usable balcony threshold.
            g=Group("LivingCoffeeTable",.05f,-2.55f,V(1.0f,.47f,1.18f));
            if(!TryRuntimePrefab(g,"StoolV2"))
            {
                Cube(g,"Top",V(0,.43f,0),V(1f,.08f,1.18f),LightWood);
                            Legs(g,.82f,1f,.40f,Wood);
                            Cube(g,"Tray",V(.14f,.49f,.16f),V(.35f,.04f,.4f),Wood);
                            Cube(g,"Book",V(-.22f,.50f,-.31f),V(.25f,.035f,.31f),Cream);
            }
            // A flat rug sits above the highest sampled floor relief and has no collider.
            var rug=furniture.Find("Rug");rug.gameObject.SetActive(true);
            float rugY=Floor(.23f,-2.55f);
            for(int x=-1;x<=1;x++) for(int z=-1;z<=1;z++) rugY=Mathf.Max(rugY,Floor(.23f+x,-2.55f+z*1.25f));
            rug.localPosition=V(.23f,rugY+.008f,-2.55f);rug.localScale=V(2.4f,.012f,2.9f);
            rug.GetComponent<Renderer>().sharedMaterial=Linen;
            rug.GetComponent<Collider>().enabled=false;
            g=Group("RugBorder",.23f,-2.55f);
            for(int i=-1;i<=1;i+=2)
            {
                Cube(g,"Border",V(i*1.13f,rugY-g.position.y+.019f,0),V(.055f,.004f,2.78f),Clay);
                Cube(g,"Border",V(0,rugY-g.position.y+.019f,i*1.36f),V(2.27f,.004f,.055f),Clay);
            }

            // L-shaped worktop leaves the middle of the narrow kitchen free.
            Existing("KitchenCounter",-4.75f,3.12f,.45f,V(.58f,.9f,2.85f),Cream);
            g=Group("KitchenLeftDetails",-4.75f,3.12f);
            Cube(g,"Worktop",V(0,.93f,0),V(.64f,.06f,2.91f),Steel);
            for(int i=0;i<4;i++)
            {
                Cube(g,"CabinetFront",V(.301f,.47f,-1.07f+i*.71f),V(.025f,.76f,.66f),Cream);
                Cube(g,"Handle",V(.329f,.75f,-1.07f+i*.71f),V(.027f,.025f,.18f),Dark);
            }
            g=Group("KitchenWindowCounter",-3.65f,4.80f,V(1.7f,.9f,.58f));
            Cube(g,"Cabinet",V(0,.45f,0),V(1.7f,.9f,.58f),Cream);
            Cube(g,"Worktop",V(0,.93f,0),V(1.76f,.06f,.64f),Steel);
            Cube(g,"SinkRim",V(-.14f,.968f,0),V(.72f,.018f,.44f),Cream);
            Cube(g,"SinkBasin",V(-.14f,.98f,0),V(.62f,.015f,.33f),Dark);
            Cube(g,"Tap",V(-.14f,1.10f,.23f),V(.045f,.25f,.045f),Steel);
            Cube(g,"TapSpout",V(-.14f,1.22f,.15f),V(.045f,.045f,.18f),Steel);
            g=Group("KitchenStove",-4.75f,2.8f);
            Cube(g,"Hob",V(0,.978f,0),V(.49f,.025f,.66f),Dark);
            for(int i=-1;i<=1;i+=2) Cube(g,"Burner",V(0,.999f,i*.17f),V(.24f,.02f,.22f),Steel);
            Existing("Pot",-4.75f,2.63f,1.05f,V(.24f,.06f,.24f),Dark);
            g=Group("KitchenHood",-4.78f,2.8f);
            Cube(g,"Hood",V(0,1.82f,0),V(.51f,.12f,.72f),Steel);
            Cube(g,"Flue",V(-.14f,2.1f,0),V(.2f,.5f,.34f),Cream);
            g=Group("KitchenFridge",-2.77f,3.85f,V(.57f,1.68f,.65f));
            Cube(g,"Body",V(0,.84f,0),V(.57f,1.68f,.65f),Cream);
            Cube(g,"Seam",V(-.292f,1.1f,0),V(.012f,.025f,.61f),Dark);
            Cube(g,"Handle",V(-.315f,1.31f,-.24f),V(.04f,.23f,.035f),Steel);
            Cube(g,"Handle",V(-.315f,.81f,-.24f),V(.04f,.23f,.035f),Steel);
            g=Group("KitchenMicrowave",-4.73f,4.0f);
            Cube(g,"Body",V(0,1.14f,0),V(.45f,.32f,.52f),Cream);
            Cube(g,"Door",V(.233f,1.14f,-.035f),V(.02f,.24f,.34f),Dark);
            Cube(g,"Controls",V(.245f,1.14f,.20f),V(.015f,.18f,.065f),Steel);
            g=Group("KitchenRiceCooker",-4.73f,1.93f);
            Cube(g,"Body",V(0,1.095f,0),V(.31f,.25f,.34f),Cream);
            Cube(g,"Lid",V(0,1.235f,0),V(.32f,.045f,.35f),Dark);
            g=Group("KitchenKettle",-3.0f,4.77f);
            Cube(g,"Body",V(0,1.1f,0),V(.19f,.28f,.21f),Steel);
            Cube(g,"Handle",V(.13f,1.12f,0),V(.05f,.18f,.07f),Dark);
            g=Group("KitchenWallCabinet",-4.86f,4.04f);
            Cube(g,"Body",V(0,1.92f,0),V(.36f,.64f,.80f),Cream);

            // Bed, storage room and bathroom remain distinct spaces.
            Existing("BedBase",1.35f,3.5f,.2f,V(1.35f,.4f,2.05f),Wood);
            Existing("Mattress",1.35f,3.5f,.47f,V(1.29f,.14f,1.98f),Cream);
            g=Group("MasterBedDetails",1.35f,3.5f);
            Cube(g,"Headboard",V(0,.67f,1.04f),V(1.43f,1.07f,.1f),Wood);
            Cube(g,"Duvet",V(0,.565f,-.23f),V(1.3f,.10f,1.48f),Linen);
            for(int i=-1;i<=1;i+=2) Cube(g,"Pillow",V(i*.31f,.585f,.70f),V(.53f,.13f,.35f),Cream);
            g=Group("MasterBedside",2.32f,4.22f,V(.39f,.49f,.45f));
            Cube(g,"Cabinet",V(0,.25f,0),V(.39f,.48f,.45f),LightWood);
            Cube(g,"LampBase",V(0,.58f,0),V(.08f,.18f,.08f),Dark);
            Cube(g,"Shade",V(0,.73f,0),V(.25f,.20f,.25f),Linen);
            g=Group("EntryShoeCabinet",-1.76f,3.04f,V(.27f,.83f,1.05f));
            Cube(g,"Cabinet",V(0,.43f,0),V(.27f,.79f,1.05f),Wood);
            for(int i=-1;i<=1;i+=2) Cube(g,"Door",V(.142f,.44f,i*.26f),V(.018f,.66f,.48f),LightWood);
            g=Group("SecondaryShelving",3.25f,3.72f,V(.38f,1.68f,1.4f));
            for(int i=0;i<4;i++) Cube(g,"Shelf",V(0,.12f+i*.48f,0),V(.38f,.055f,1.4f),Wood);
            for(int i=-1;i<=1;i+=2) Cube(g,"Post",V(0,.84f,i*.68f),V(.38f,1.68f,.055f),Wood);
            for(int i=0;i<3;i++) Cube(g,"StorageBox",V(.035f,.31f+i*.48f,(i%2==0?-.3f:.3f)),V(.30f,.32f,.49f),Linen);
            g=Group("SecondaryDesk",4.99f,3.65f,V(.57f,.76f,1.25f));
            Cube(g,"Top",V(0,.73f,0),V(.57f,.06f,1.25f),LightWood);
            Legs(g,.44f,1.10f,.70f,Wood);
            g=Group("SecondaryDeskChair",4.45f,3.78f,V(.4f,.85f,.4f));
            Cube(g,"Seat",V(0,.43f,0),V(.4f,.08f,.4f),Sage);
            Cube(g,"Back",V(-.18f,.66f,0),V(.06f,.36f,.4f),Sage);
            Legs(g,.29f,.29f,.4f,Wood);
            g=Group("BathroomWasher",4.65f,-2.9f,V(.63f,.88f,.67f));
            Cube(g,"Body",V(0,.44f,0),V(.63f,.88f,.67f),Cream);
            Cube(g,"FrontPanel",V(-.323f,.45f,0),V(.02f,.72f,.59f),Steel);
            Cube(g,"WashDoor",V(-.34f,.40f,0),V(.022f,.43f,.43f),Dark);
            Cube(g,"ControlPanel",V(-.347f,.77f,0),V(.024f,.08f,.50f),Cream);
            g=Group("BathroomVanity",4.69f,-3.99f,V(.6f,.82f,.70f));
            Cube(g,"Cabinet",V(0,.4f,0),V(.6f,.8f,.7f),Cream);
            Cube(g,"Basin",V(0,.84f,0),V(.63f,.075f,.73f),Steel);
            Cube(g,"BasinInset",V(-.03f,.885f,0),V(.43f,.012f,.49f),Cream);
            Cube(g,"Mirror",V(.32f,1.42f,0),V(.035f,.62f,.62f),Steel);
            g=Group("BathroomShower",3.5f,-4.22f);
            Cube(g,"Drain",V(0,.035f,0),V(.18f,.014f,.18f),Dark);
            Cube(g,"ShowerRail",V(-.21f,1.3f,0),V(.03f,1.3f,.03f),Steel);
            Cube(g,"ShowerHead",V(-.14f,1.94f,0),V(.18f,.035f,.12f),Steel);

            // Room-detail pass: visual-only accents, kept outside navigation and fact state.
            g=Group("LivingCurtains",-2.5f,-4.68f);
            Cube(g,"Rod",V(0,2.18f,0),V(.035f,.035f,2.05f),Steel);
            Cube(g,"CurtainLeft",V(0,1.35f,-.82f),V(.055f,1.55f,.62f),Linen);
            Cube(g,"CurtainRight",V(0,1.35f,.82f),V(.055f,1.55f,.62f),Linen);
            Cube(g,"TieLeft",V(0,1.22f,-.52f),V(.08f,.06f,.12f),Clay);
            Cube(g,"TieRight",V(0,1.22f,.52f),V(.08f,.06f,.12f),Clay);

            g=Group("EntryDetails",-1.76f,2.32f);
            Cube(g,"EntryMirror",V(.16f,1.42f,0),V(.035f,.72f,.52f),Steel);
            Cube(g,"KeyTray",V(.16f,.98f,0),V(.18f,.035f,.38f),Clay);
            Cube(g,"UmbrellaStand",V(-.04f,.34f,.42f),V(.22f,.62f,.22f),Clay);
            Cube(g,"Umbrella",V(-.04f,.90f,.42f),V(.035f,1.0f,.035f),Dark);

            g=Group("BathroomDetails",4.68f,-3.72f);
            Cube(g,"TowelRail",V(.34f,1.08f,0),V(.035f,.035f,.58f),Steel);
            Cube(g,"Towel",V(.35f,.91f,0),V(.045f,.36f,.52f),Linen);
            Cube(g,"SoapDish",V(-.02f,.96f,-.28f),V(.16f,.035f,.12f),Clay);
            Cube(g,"ToothbrushCup",V(.08f,1.06f,-.28f),V(.10f,.18f,.10f),Cream);

            // Second detail pass: bedroom storage, desk life and balcony laundry accents.
            g=Group("MasterRoomDetails",2.32f,3.58f);
            Cube(g,"BedsideBook",V(0,.88f,0),V(.16f,.035f,.22f),Cream);
            Cube(g,"BedsideMug",V(.13f,.93f,0),V(.07f,.10f,.07f),Clay);
            Cube(g,"WallPrint",V(2.55f,1.45f,.08f),V(.025f,.48f,.66f),Linen);
            Cube(g,"WallPrintInner",V(2.565f,1.45f,.08f),V(.012f,.36f,.52f),Sage);

            g=Group("SecondaryRoomDetails",3.25f,4.18f);
            Cube(g,"ShelfBooks",V(.03f,.54f,.02f),V(.30f,.18f,.48f),Clay);
            Cube(g,"ShelfBooks2",V(.03f,1.02f,-.20f),V(.30f,.18f,.36f),Sage);
            Cube(g,"DeskLampStem",V(0,1.01f,-.24f),V(.035f,.28f,.035f),Steel);
            Cube(g,"DeskLampShade",V(0,1.18f,-.24f),V(.16f,.12f,.16f),Linen);
            Cube(g,"DeskNotebook",V(.08f,.80f,-.10f),V(.28f,.025f,.38f),Cream);

            g=Group("BalconyDetails",-.3f,-5.78f);
            Cube(g,"LaundryClipA",V(-.65f,2.19f,0),V(.08f,.10f,.05f),Clay);
            Cube(g,"LaundryClipB",V(.18f,2.19f,0),V(.08f,.10f,.05f),Sage);
            Cube(g,"LaundryClipC",V(.92f,2.19f,0),V(.08f,.10f,.05f),Cream);
            Cube(g,"LaundryBasket",V(.92f,.24f,.30f),V(.34f,.40f,.42f),Linen);

            g=Group("LivingFan",-4.72f,.15f,V(.34f,1.18f,.34f));
            Cube(g,"Base",V(0,.05f,0),V(.32f,.08f,.32f),Dark);
            Cube(g,"Stem",V(0,.55f,0),V(.04f,.95f,.04f),Steel);
            Cube(g,"Housing",V(0,1.03f,0),V(.10f,.34f,.34f),Cream);
            for(int i=-1;i<=1;i++) Cube(g,"Grille",V(.06f,1.03f+i*.1f,0),V(.015f,.016f,.33f),Dark);
            Plant(Group("LivingPlant",-4.75f,.72f,V(.28f,.56f,.28f)));
            // Floor-standing planters: tall enough not to become climbable steps beside the rail.
            g=Group("BalconyPlantLeft",-3.08f,-6.15f,V(.28f,.56f,.28f));
            Plant(g);g.localScale=V(1,1.5f,1);
            g=Group("BalconyPlantRight",2.22f,-6.15f,V(.28f,.56f,.28f));
            Plant(g);g.localScale=V(1,1.5f,1);
            g=Group("BalconyEmptyClothesRail",-.3f,-5.85f);
            Cube(g,"Rail",V(0,2.18f,0),V(2.5f,.035f,.035f),Steel);
            for(int i=-1;i<=1;i+=2) Cube(g,"Support",V(i*1.2f,2.05f,0),V(.035f,.26f,.035f),Steel);

            // This is a visual layout change, not a backend move event or a second box.
            var box=world.CardboardBox.transform;box.localScale=Vector3.one;
            box.localPosition=V(BoxPosition.x,Floor(BoxPosition.x,BoxPosition.z)+.26f,BoxPosition.z);
            box.GetComponent<Renderer>().enabled=false;
            var boxCollider=box.GetComponent<BoxCollider>();boxCollider.size=V(.55f,.52f,.55f);
            for(int i=-1;i<=1;i+=2)
            {
                Cube(box,"Side",V(i*.262f,0,0),V(.026f,.52f,.55f),LightWood);
                Cube(box,"End",V(0,0,i*.262f),V(.52f,.52f,.026f),LightWood);
            }
            Cube(box,"Blanket",V(0,-.06f,0),V(.48f,.10f,.48f),Sage);

            // Only our generated decoration cubes may be replaced by merged meshes.
            // Imported prefab hierarchies own their meshes, materials and transforms.
            foreach(Transform item in furniture) Merge(item,lifetime);
            Physics.SyncTransforms();
        }

        public static float FloorY(MeshCollider shell,float x,float z)
        {
            return shell.Raycast(new Ray(V(x,.3f,z),Vector3.down),out var hit,1f) ? hit.point.y+.012f : 0f;
        }

        private static void Chair(Transform g,int back)
        {
            Legs(g,.32f,.32f,.39f,Wood);
            Cube(g,"Back",V(0,.69f,back*.20f),V(.44f,.38f,.055f),Wood);
        }
        private static void Legs(Transform g,float width,float depth,float height,Material material)
        {
            for(int x=-1;x<=1;x+=2) for(int z=-1;z<=1;z+=2)
                Cube(g,"Leg",V(x*width/2,height/2,z*depth/2),V(.065f,height,.065f),material);
        }
        private static void Plant(Transform g)
        {
            Cube(g,"Pot",V(0,.14f,0),V(.24f,.27f,.24f),Clay);
            Cube(g,"Stem",V(0,.4f,0),V(.025f,.38f,.025f),Wood);
            for(int i=0;i<5;i++)
            {
                var leaf=Cube(g,"Leaf",V(Mathf.Sin(i*2.4f)*.12f,.39f+i*.045f,Mathf.Cos(i*2.4f)*.12f),V(.22f,.04f,.11f),Leaf);
                leaf.localRotation=Quaternion.Euler(20,i*137,20);
            }
        }
        private static Transform Cube(Transform parent,string name,Vector3 center,Vector3 size,Material material)
        {
            if(_cube==null)
            {
                var template=GameObject.CreatePrimitive(PrimitiveType.Cube);
                _cube=template.GetComponent<MeshFilter>().sharedMesh;
                template.SetActive(false);Discard(template);
            }
            var g=new GameObject(name).transform;g.SetParent(parent,false);g.localPosition=center;g.localScale=size;
            g.gameObject.AddComponent<MeshFilter>().sharedMesh=_cube;
            g.gameObject.AddComponent<MeshRenderer>().sharedMaterial=material;
            return g;
        }
        private static void Merge(Transform root,FurnitureMeshLifetime lifetime)
        {
            var byMaterial=new Dictionary<Material,List<CombineInstance>>();
            var originals=new List<GameObject>();
            foreach(var mf in root.GetComponentsInChildren<MeshFilter>())
            {
                if(mf.transform==root) continue;
                // FBX meshes can be GPU-only in players. CombineMeshes then fails,
                // and deleting the source would make the furniture disappear.
                // Preserve imported meshes even if readable (including all submeshes).
                if(mf.sharedMesh!=_cube || _cube==null || !_cube.isReadable) continue;
                var r=mf.GetComponent<Renderer>();if(r==null || !r.enabled) continue;
                if(!byMaterial.TryGetValue(r.sharedMaterial,out var list)) byMaterial[r.sharedMaterial]=list=new List<CombineInstance>();
                list.Add(new CombineInstance { mesh=mf.sharedMesh,transform=root.worldToLocalMatrix*mf.transform.localToWorldMatrix });
                originals.Add(mf.gameObject);
            }
            foreach(var pair in byMaterial)
            {
                var mesh=new Mesh { name="Furniture/"+root.name };mesh.CombineMeshes(pair.Value.ToArray(),true,true);
                lifetime.Meshes.Add(mesh);
                var g=new GameObject("Visual");g.transform.SetParent(root,false);
                g.AddComponent<MeshFilter>().sharedMesh=mesh;g.AddComponent<MeshRenderer>().sharedMaterial=pair.Key;
            }
            foreach(var g in originals) { g.SetActive(false);Discard(g); }
        }
        private static bool TryRuntimePrefab(Transform parent,string resourceName)
        {
            var prefab=Resources.Load<GameObject>("ApartmentFurniture/"+resourceName);
            if(prefab==null) { Debug.LogWarning("Runtime furniture prefab missing: ApartmentFurniture/"+resourceName); return false; }
            var instance=Object.Instantiate(prefab,parent);
            instance.name=resourceName+"Model"; Debug.Log("Runtime furniture prefab loaded: "+resourceName);
            foreach(var collider in instance.GetComponentsInChildren<Collider>(true)) collider.enabled=false;
            return true;
        }

        internal static void Discard(Object obj) { if(Application.isPlaying) Object.Destroy(obj);else Object.DestroyImmediate(obj); }
    }

    public sealed class FurnitureMeshLifetime : MonoBehaviour
    {
        public readonly List<Mesh> Meshes=new List<Mesh>();
        private void OnDestroy() { foreach(var mesh in Meshes) if(mesh!=null) ApartmentFurnishings.Discard(mesh); }
    }
}
