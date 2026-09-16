using UnityEditor;
using UnityEngine;

public static class ApartmentFurnitureProcessedSetup
{
    [MenuItem("Tools/Apartment/Bind Processed Furniture Textures")]
    public static void BindTextures()
    {
        Bind("sofa");
        Bind("stool");
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();
        Debug.Log("Processed furniture textures bound. Review the models before runtime replacement.");
    }

    [MenuItem("Tools/Apartment/Bind Sofa V2 Textures")]
    public static void BindSofaV2Textures()
    {
        var modelPath = "Assets/Art/Apartment/Furniture/Processed-v2/sofa/sofa-v2.fbx";
        var color = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/Art/Apartment/Furniture/Processed-v2/sofa/basecolor-2048.jpg");
        var normal = ConfigureNormal("Assets/Art/Apartment/Furniture/Processed-v2/sofa/normal-2048.jpg");
        foreach (var obj in AssetDatabase.LoadAllAssetsAtPath(modelPath))
        {
            if (!(obj is Material material)) continue;
            material.mainTexture = color;
            if (material.HasProperty("_BaseColorMap")) material.SetTexture("_BaseColorMap", color);
            if (normal && material.HasProperty("_BumpMap")) { material.SetTexture("_BumpMap", normal); material.EnableKeyword("_NORMALMAP"); }
            EditorUtility.SetDirty(material);
        }
        AssetDatabase.SaveAssets(); AssetDatabase.Refresh();
        Debug.Log("Sofa v2 textures bound. This is a candidate asset and does not replace runtime furniture.");
    }

    [MenuItem("Tools/Apartment/Bind Stool V2 Textures")]
    public static void BindStoolV2Textures()
    {
        var modelPath = "Assets/Art/Apartment/Furniture/Processed-v2/stool/stool-v2.fbx";
        var color = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/Art/Apartment/Furniture/Processed-v2/stool/basecolor-2048.jpg");
        var normal = ConfigureNormal("Assets/Art/Apartment/Furniture/Processed-v2/stool/normal-2048.jpg");
        foreach (var obj in AssetDatabase.LoadAllAssetsAtPath(modelPath))
        {
            if (!(obj is Material material)) continue;
            material.mainTexture = color;
            if (material.HasProperty("_BaseColorMap")) material.SetTexture("_BaseColorMap", color);
            if (normal && material.HasProperty("_BumpMap")) { material.SetTexture("_BumpMap", normal); material.EnableKeyword("_NORMALMAP"); }
            EditorUtility.SetDirty(material);
        }
        AssetDatabase.SaveAssets(); AssetDatabase.Refresh();
        Debug.Log("Stool v2 textures bound. This is a candidate asset and does not replace runtime furniture.");
    }

    private static void Bind(string asset)
    {
        var modelPath = $"Assets/Art/Apartment/Furniture/Processed-v1/{asset}/{asset}-low.fbx";
        var texturePath = $"Assets/Art/Apartment/Furniture/Processed-v1/{asset}/basecolor-2048.jpg";
        var texture = AssetDatabase.LoadAssetAtPath<Texture2D>(texturePath);
        if (!texture) { Debug.LogWarning($"Missing texture: {texturePath}"); return; }
        foreach (var obj in AssetDatabase.LoadAllAssetsAtPath(modelPath))
        {
            if (!(obj is Material material)) continue;
            material.mainTexture = texture;
            if (material.HasProperty("_BaseColorMap")) material.SetTexture("_BaseColorMap", texture);
            EditorUtility.SetDirty(material);
        }
    }

    private static Texture2D ConfigureNormal(string path)
    {
        var importer = AssetImporter.GetAtPath(path) as TextureImporter;
        if (importer != null)
        {
            importer.textureType = TextureImporterType.NormalMap;
            importer.sRGBTexture = false;
            importer.SaveAndReimport();
        }
        return AssetDatabase.LoadAssetAtPath<Texture2D>(path);
    }
}
