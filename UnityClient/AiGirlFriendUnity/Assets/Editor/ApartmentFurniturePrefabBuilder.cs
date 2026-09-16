using System;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class ApartmentFurniturePrefabBuilder
{
    const string OutDir = "Assets/Art/Apartment/Furniture/Prefabs-v2";
    const string ScenePath = "Assets/Scenes/ApartmentFurnitureV2Review.unity";
    static readonly StringBuilder Report = new StringBuilder();

    [MenuItem("Tools/Apartment/Create Processed-v2 Furniture Prefabs")]
    public static void CreatePrefabs()
    {
        if (EditorApplication.isPlaying) throw new InvalidOperationException("Exit Play Mode before preparing furniture.");
        Directory.CreateDirectory(OutDir);
        AssetDatabase.Refresh();
        Report.Clear();
        var sofa = Create("sofa", "SofaV2", 2.5f);
        var stool = Create("stool", "StoolV2", 1.0f);
        AssetDatabase.SaveAssets();
        Directory.CreateDirectory("Temp");
        File.WriteAllText("Temp/furniture-v2-prefab-report.txt", Report.ToString());
        Debug.Log(Report + "\nFurniture v2 prefabs saved successfully.");
        Selection.activeObject = sofa;
        EditorGUIUtility.PingObject(sofa);
    }

    static GameObject Create(string asset, string name, float horizontalSpan)
    {
        string input = $"Assets/Art/Apartment/Furniture/Processed-v2/{asset}";
        var source = AssetDatabase.LoadAssetAtPath<GameObject>($"{input}/{asset}-v2.fbx");
        if (!source) throw new FileNotFoundException(input);
        ConfigureTexture(input + "/basecolor-2048.jpg", false);
        ConfigureTexture(input + "/normal-2048.jpg", true);
        string matPath = OutDir + "/" + name + ".mat";
        var material = AssetDatabase.LoadAssetAtPath<Material>(matPath);
        if (!material)
        {
            material = new Material(Shader.Find("Universal Render Pipeline/Lit"));
            AssetDatabase.CreateAsset(material, matPath);
        }
        material.SetTexture("_BaseMap", AssetDatabase.LoadAssetAtPath<Texture2D>(input + "/basecolor-2048.jpg"));
        material.SetColor("_BaseColor", Color.white);
        material.SetTexture("_BumpMap", AssetDatabase.LoadAssetAtPath<Texture2D>(input + "/normal-2048.jpg"));
        material.SetFloat("_BumpScale", 1);
        material.SetFloat("_Metallic", 0);
        material.SetFloat("_Smoothness", .15f);
        material.EnableKeyword("_NORMALMAP");
        EditorUtility.SetDirty(material);

        var root = new GameObject(name);
        try
        {
            var model = (GameObject)PrefabUtility.InstantiatePrefab(source, root.transform);
            model.name = "Model";
            foreach (var renderer in model.GetComponentsInChildren<Renderer>())
            {
                var slots = renderer.sharedMaterials;
                for (int i=0;i<slots.Length;i++) slots[i]=material;
                renderer.sharedMaterials=slots;
            }
            var raw = BoundsIn(root.transform);
            Report.AppendLine($"{asset}: imported bounds={raw.size.ToString("F6")}");
            // FBX children carry axis conversion. Measure after those transforms, not mesh.bounds.
            // Keep the native orientation and use one scalar on a separate wrapper.
            var wrapper = new GameObject("UniformScale").transform;
            wrapper.SetParent(root.transform, false);
            model.transform.SetParent(wrapper, false);
            float factor = horizontalSpan / Mathf.Max(raw.size.x, raw.size.z);
            wrapper.localScale = Vector3.one * factor;
            var bounds = BoundsIn(root.transform);
            wrapper.localPosition = new Vector3(-bounds.center.x, -bounds.min.y, -bounds.center.z);
            bounds = BoundsIn(root.transform);
            var collider=root.AddComponent<BoxCollider>();
            collider.center=bounds.center; collider.size=bounds.size;
            long triangles=0;
            foreach(var filter in root.GetComponentsInChildren<MeshFilter>())
                for(int s=0;s<filter.sharedMesh.subMeshCount;s++) triangles+=(long)filter.sharedMesh.GetIndexCount(s)/3;
            if (Mathf.Abs(bounds.min.y)>.001f || bounds.size.y<.1f) throw new Exception("Invalid floor/height: "+asset);
            Report.AppendLine($"{asset}: triangles={triangles}, uniformScale={factor:F6}, final bounds={bounds.size.ToString("F6")}, floor={bounds.min.y:F6}");
            var prefab=PrefabUtility.SaveAsPrefabAsset(root, OutDir+"/"+name+".prefab");
            if(!prefab) throw new Exception("Could not save prefab "+name);
            return prefab;
        }
        finally { UnityEngine.Object.DestroyImmediate(root); }
    }

    static void ConfigureTexture(string path, bool normal)
    {
        var importer=(TextureImporter)AssetImporter.GetAtPath(path);
        if(importer==null) throw new FileNotFoundException(path);
        bool change=importer.textureType!=(normal?TextureImporterType.NormalMap:TextureImporterType.Default)
            || importer.sRGBTexture==normal || importer.maxTextureSize!=2048;
        importer.textureType=normal?TextureImporterType.NormalMap:TextureImporterType.Default;
        importer.sRGBTexture=!normal; importer.maxTextureSize=2048;
        if(change) importer.SaveAndReimport();
    }

    static Bounds BoundsIn(Transform root)
    {
        bool first=true; var result=new Bounds();
        foreach(var filter in root.GetComponentsInChildren<MeshFilter>())
        {
            var b=filter.sharedMesh.bounds;
            var matrix=root.worldToLocalMatrix*filter.transform.localToWorldMatrix;
            for(int i=0;i<8;i++)
            {
                var p=matrix.MultiplyPoint3x4(b.center+Vector3.Scale(b.extents,new Vector3((i&1)==0?-1:1,(i&2)==0?-1:1,(i&4)==0?-1:1)));
                if(first){result=new Bounds(p,Vector3.zero); first=false;} else result.Encapsulate(p);
            }
        }
        if(first) throw new Exception("No mesh found");
        return result;
    }

    [MenuItem("Tools/Apartment/Open Furniture V2 Living Room Review %#F8")]
    public static void OpenReview()
    {
        CreatePrefabs();
        // Additive scene keeps the user's open, unsaved scene intact.
        var previous=SceneManager.GetActiveScene();
        if(string.IsNullOrEmpty(previous.path))
        {
            Directory.CreateDirectory("Assets/Scenes/UserDrafts");
            AssetDatabase.Refresh();
            var backup=AssetDatabase.GenerateUniqueAssetPath("Assets/Scenes/UserDrafts/UntitledBeforeFurnitureReview.unity");
            if(!EditorSceneManager.SaveScene(previous,backup)) throw new Exception("Could not preserve unsaved scene");
        }
        var existing=SceneManager.GetSceneByPath(ScenePath);
        if(existing.IsValid() && existing.isLoaded)
        {
            SceneManager.SetActiveScene(existing);
            Selection.activeGameObject=existing.GetRootGameObjects()[1];
            return;
        }
        var scene=EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Additive);
        SceneManager.SetActiveScene(scene);
        var shell=AssetDatabase.LoadAssetAtPath<GameObject>("Assets/Art/Apartment/ImportedShell/ApartmentShell.prefab");
        var house=(GameObject)PrefabUtility.InstantiatePrefab(shell,scene);
        house.name="ApartmentShell";
        Physics.SyncTransforms();
        Place("SofaV2",new Vector3(1.45f,0,-2.55f),0,house,scene);
        Place("StoolV2",new Vector3(.05f,0,-2.55f),0,house,scene);
        var light=new GameObject("Review Daylight").AddComponent<Light>();
        light.type=LightType.Directional; light.intensity=1.5f;
        light.transform.rotation=Quaternion.Euler(45,-45,0);
        RenderSettings.ambientMode=UnityEngine.Rendering.AmbientMode.Flat;
        RenderSettings.ambientLight=new Color(.65f,.65f,.65f);
        var camera=new GameObject("Review Camera").AddComponent<Camera>();
        camera.tag="MainCamera";
        camera.transform.position=new Vector3(-3.2f,2.4f,-4.2f);
        camera.transform.LookAt(new Vector3(.7f,.55f,-2.55f));
        camera.nearClipPlane=.03f; camera.farClipPlane=50; camera.fieldOfView=52;
        if(!EditorSceneManager.SaveScene(scene,ScenePath)) throw new Exception("Review scene save failed");
        Selection.activeGameObject=scene.GetRootGameObjects()[1];
        if(SceneView.lastActiveSceneView!=null) SceneView.lastActiveSceneView.LookAt(new Vector3(.7f,.55f,-2.55f),camera.transform.rotation,4);
        File.AppendAllText("Temp/furniture-v2-prefab-report.txt","Review saved: "+ScenePath+"\n");
        Debug.Log("Living room review saved; original open scene preserved. "+ScenePath);
    }

    [MenuItem("Tools/Apartment/Run Apartment Furniture V2 %#F10")]
    public static void RunApartmentFurnitureV2()
    {
        if(EditorApplication.isPlaying) { EditorApplication.isPlaying=false; return; }
        var scene=EditorSceneManager.OpenScene("Assets/Scenes/Apartment.unity",OpenSceneMode.Single);
        if(!scene.IsValid()) throw new Exception("Apartment scene could not be opened");
        EditorApplication.isPlaying=true;
    }

    [MenuItem("Tools/Apartment/Validate Furniture V2 Review %#F9")]
    public static void ValidateReview()
    {
        var scene=SceneManager.GetSceneByPath(ScenePath);
        if(!scene.IsValid() || !scene.isLoaded) throw new Exception("Open furniture review first");
        var report=new StringBuilder();
        for(int i=SceneManager.sceneCount-1;i>=0;i--)
        {
            var other=SceneManager.GetSceneAt(i);
            if(other.path.StartsWith("Assets/Scenes/UserDrafts/UntitledBeforeFurnitureReview") && !other.isDirty)
                EditorSceneManager.CloseScene(other,true);
        }
        Physics.SyncTransforms();
        foreach(var root in scene.GetRootGameObjects())
        {
            if(root.name!="SofaV2" && root.name!="StoolV2") continue;
            var collider=root.GetComponent<BoxCollider>();
            var b=BoundsIn(root.transform);
            if((collider.size-b.size).sqrMagnitude>1e-6f || (collider.center-b.center).sqrMagnitude>1e-6f)
                throw new Exception(root.name+": collider mismatch");
            if(Mathf.Abs(b.min.y)>.001f) throw new Exception(root.name+": incorrect floor pivot");
            var scale=root.transform.Find("UniformScale").localScale;
            if(Mathf.Abs(scale.x-scale.y)>1e-5f || Mathf.Abs(scale.x-scale.z)>1e-5f)
                throw new Exception(root.name+": nonuniform scaling");
            foreach(var renderer in root.GetComponentsInChildren<Renderer>())
                foreach(var mat in renderer.sharedMaterials)
                    if(!AssetDatabase.Contains(mat) || !mat.GetTexture("_BaseMap") || !mat.GetTexture("_BumpMap"))
                        throw new Exception(root.name+": missing persistent material/texture");
            report.AppendLine(root.name+": PASS uniform scale, floor pivot, persistent textures, collider dimensions");
            var box=collider.bounds;
            foreach(var hit in Physics.OverlapBox(box.center,box.extents-Vector3.one*.025f,Quaternion.identity))
                if(!hit.transform.IsChildOf(root.transform) && hit.gameObject.scene==scene)
                    report.AppendLine("Intersection to review: "+root.name+" -> "+hit.name);
        }
        Camera camera=null;
        foreach(var root in scene.GetRootGameObjects()) if(root.name=="Review Camera") camera=root.GetComponent<Camera>();
        if(camera==null) throw new Exception("Review camera missing");
        camera.depth=10;
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        var rt=new RenderTexture(1280,720,24);
        var oldTarget=camera.targetTexture; var oldActive=RenderTexture.active;
        var tex=new Texture2D(1280,720,TextureFormat.RGB24,false);
        try
        {
            camera.targetTexture=rt;
            camera.Render();
            RenderTexture.active=rt;
            tex.ReadPixels(new Rect(0,0,1280,720),0,0); tex.Apply();
            Directory.CreateDirectory("../output/furniture-v2-review");
            File.WriteAllBytes("../output/furniture-v2-review/living-room.png",tex.EncodeToPNG());
        }
        finally
        {
            camera.targetTexture=oldTarget; RenderTexture.active=oldActive;
            UnityEngine.Object.DestroyImmediate(tex); UnityEngine.Object.DestroyImmediate(rt);
        }
        File.WriteAllText("../output/furniture-v2-review/validation.txt",report.ToString());
        Debug.Log(report.ToString());
    }

    static void Place(string name,Vector3 position,float yaw,GameObject shell,Scene scene)
    {
        var prefab=AssetDatabase.LoadAssetAtPath<GameObject>(OutDir+"/"+name+".prefab");
        var item=(GameObject)PrefabUtility.InstantiatePrefab(prefab,scene);
        float floor=float.NegativeInfinity;
        foreach(var collider in shell.GetComponentsInChildren<Collider>())
            if(collider.Raycast(new Ray(new Vector3(position.x,1.25f,position.z),Vector3.down),out var hit,3)) floor=Mathf.Max(floor,hit.point.y);
        position.y=float.IsNegativeInfinity(floor)?0:floor;
        item.transform.SetPositionAndRotation(position,Quaternion.Euler(0,yaw,0));
    }
}


