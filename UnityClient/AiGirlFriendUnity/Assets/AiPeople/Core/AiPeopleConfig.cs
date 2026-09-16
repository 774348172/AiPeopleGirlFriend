using System;
using System.IO;
using UnityEngine;

namespace AiPeople.Core
{
    /// <summary>
    /// 客户端配置：后端端点、存档 ID、角色 ID。
    /// 取值优先级：StreamingAssets/aipeople_config.json 覆盖 &gt; Resources/AiPeopleConfig.asset &gt; 内置默认值。
    /// </summary>
    [CreateAssetMenu(menuName = "AiPeople/Config", fileName = "AiPeopleConfig")]
    public sealed class AiPeopleConfig : ScriptableObject
    {
        [Serializable]
        public sealed class Data
        {
            public string endpoint = "http://127.0.0.1:8767";
            public string saveId = "m1check0001";
            public string characterId = "baiweixi";
            public float requestTimeoutSeconds = 120f;
        }

        public Data data = new Data();

        public static AiPeopleConfig Load()
        {
            AiPeopleConfig source = Resources.Load<AiPeopleConfig>("AiPeopleConfig");
            AiPeopleConfig config = source != null ? Instantiate(source) : CreateInstance<AiPeopleConfig>();
            config.name = "AiPeopleConfig (Runtime)";

            string overridePath = Path.Combine(Application.streamingAssetsPath, "aipeople_config.json");
            if (File.Exists(overridePath))
            {
                try
                {
                    JsonUtility.FromJsonOverwrite(File.ReadAllText(overridePath), config.data);
                }
                catch (Exception exception)
                {
                    Debug.LogWarning("[AiPeople] 配置覆盖文件无效，已忽略：" + exception.Message);
                }
            }

            config.data.endpoint = (config.data.endpoint ?? string.Empty).TrimEnd('/');
            if (config.data.endpoint.Length == 0 || !Uri.TryCreate(config.data.endpoint, UriKind.Absolute, out Uri endpointUri)
                || (endpointUri.Scheme != Uri.UriSchemeHttp && endpointUri.Scheme != Uri.UriSchemeHttps))
            {
                Debug.LogWarning("[AiPeople] endpoint 无效，已恢复默认地址：" + config.data.endpoint);
                config.data.endpoint = new Data().endpoint;
            }

            config.data.requestTimeoutSeconds = Mathf.Clamp(config.data.requestTimeoutSeconds, 5f, 600f);

            config.data.saveId = (config.data.saveId ?? string.Empty).Trim();
            if (!IsValidSaveId(config.data.saveId))
            {
                Debug.LogWarning("[AiPeople] saveId 不合法（需 8-64 位 ASCII 字母数字，可含 _ 和 -），后端会拒绝：" + config.data.saveId);
            }

            if (string.IsNullOrWhiteSpace(config.data.characterId))
            {
                config.data.characterId = "baiweixi";
            }

            return config;
        }

        /// <summary>与后端 SAVE_ID_PATTERN 一致：^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$</summary>
        public static bool IsValidSaveId(string saveId)
        {
            if (string.IsNullOrEmpty(saveId) || saveId.Length < 8 || saveId.Length > 64)
            {
                return false;
            }

            for (int i = 0; i < saveId.Length; i++)
            {
                char c = saveId[i];
                bool asciiLetterOrDigit = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9');
                if (i == 0)
                {
                    if (!asciiLetterOrDigit)
                    {
                        return false;
                    }
                }
                else if (!asciiLetterOrDigit && c != '_' && c != '-')
                {
                    return false;
                }
            }

            return true;
        }
    }
}
