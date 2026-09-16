using UnityEditor;
using UnityEngine;
using System.Text;

public static class ApartmentFurnitureAudit
{
    private static readonly string[] Roots = {
        "Assets/Art/Apartment/Furniture/Generated-v1",
        "Assets/Art/Apartment/Furniture/Processed-v1",
        "Assets/Art/Apartment/Furniture/Processed-v2"
    };

    [MenuItem("Tools/Apartment/Audit Generated Furniture")]
    public static void Audit()
    {
        var sb = new StringBuilder("Generated furniture audit\n");
        foreach (var guid in AssetDatabase.FindAssets("t:Model", Roots))
        {
            var path = AssetDatabase.GUIDToAssetPath(guid);
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            if (!model) continue;

            int vertices = 0, triangles = 0, meshes = 0;
            var bounds = new Bounds();
            bool hasBounds = false;
            foreach (var mf in model.GetComponentsInChildren<MeshFilter>(true))
            {
                if (!mf.sharedMesh) continue;
                meshes++;
                vertices += mf.sharedMesh.vertexCount;
                for (int sub=0;sub<mf.sharedMesh.subMeshCount;sub++) triangles += (int)mf.sharedMesh.GetIndexCount(sub) / 3;
                Encapsulate(ref bounds, ref hasBounds, mf.sharedMesh.bounds, mf.transform.localToWorldMatrix);
            }
            foreach (var smr in model.GetComponentsInChildren<SkinnedMeshRenderer>(true))
            {
                if (!smr.sharedMesh) continue;
                meshes++;
                vertices += smr.sharedMesh.vertexCount;
                for (int sub=0;sub<smr.sharedMesh.subMeshCount;sub++) triangles += (int)smr.sharedMesh.GetIndexCount(sub) / 3;
                Encapsulate(ref bounds, ref hasBounds, smr.localBounds, smr.transform.localToWorldMatrix);
            }
            sb.AppendLine($"{path}: meshes={meshes}, vertices={vertices}, triangles={triangles}, imported XYZ bounds={bounds.size.ToString("F6")}");
        }
        var projectRoot = System.IO.Directory.GetParent(Application.dataPath).FullName;
        var tempDir = System.IO.Path.Combine(projectRoot, "Temp");
        System.IO.Directory.CreateDirectory(tempDir);
        var outputPath = System.IO.Path.Combine(tempDir, "generated-furniture-audit.txt");
        System.IO.File.WriteAllText(outputPath, sb.ToString(), System.Text.Encoding.UTF8);
        AssetDatabase.Refresh();
        Debug.Log($"{sb}\nFurniture audit written to: {outputPath}");
    }
    private static void Encapsulate(ref Bounds result, ref bool hasBounds, Bounds local, Matrix4x4 matrix)
    {
        for(int i=0;i<8;i++)
        {
            var p=matrix.MultiplyPoint3x4(local.center+Vector3.Scale(local.extents,
                new Vector3((i&1)==0?-1:1,(i&2)==0?-1:1,(i&4)==0?-1:1)));
            if(!hasBounds) { result=new Bounds(p,Vector3.zero); hasBounds=true; }
            else result.Encapsulate(p);
        }
    }
}
