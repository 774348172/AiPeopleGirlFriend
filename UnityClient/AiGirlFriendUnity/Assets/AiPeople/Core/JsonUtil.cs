using System.Text;

namespace AiPeople.Core
{
    /// <summary>最小 JSON 字符串工具（无第三方依赖，供游戏世界 HTTP 服务使用）。</summary>
    public static class JsonUtil
    {
        public static string Escape(string value)
        {
            if (string.IsNullOrEmpty(value))
            {
                return string.Empty;
            }

            var builder = new StringBuilder(value.Length + 8);
            foreach (char c in value)
            {
                switch (c)
                {
                    case '"': builder.Append("\\\""); break;
                    case '\\': builder.Append("\\\\"); break;
                    case '\b': builder.Append("\\b"); break;
                    case '\f': builder.Append("\\f"); break;
                    case '\n': builder.Append("\\n"); break;
                    case '\r': builder.Append("\\r"); break;
                    case '\t': builder.Append("\\t"); break;
                    default:
                        if (c < 0x20)
                        {
                            builder.Append("\\u").Append(((int)c).ToString("x4"));
                        }
                        else
                        {
                            builder.Append(c);
                        }
                        break;
                }
            }

            return builder.ToString();
        }

        public static string Str(string value)
        {
            return "\"" + Escape(value) + "\"";
        }

        /// <summary>从 json 中 quoteIndex 位置的引号开始读取一个字符串（处理转义）。</summary>
        public static bool TryReadStringAt(string json, int quoteIndex, out string value, out int nextIndex)
        {
            value = null;
            nextIndex = quoteIndex;
            if (string.IsNullOrEmpty(json) || quoteIndex < 0 || quoteIndex >= json.Length || json[quoteIndex] != '"')
            {
                return false;
            }

            var builder = new StringBuilder();
            int i = quoteIndex + 1;
            while (i < json.Length)
            {
                char c = json[i];
                if (c == '\\' && i + 1 < json.Length)
                {
                    char escaped = json[i + 1];
                    switch (escaped)
                    {
                        case '"': builder.Append('"'); break;
                        case '\\': builder.Append('\\'); break;
                        case '/': builder.Append('/'); break;
                        case 'n': builder.Append('\n'); break;
                        case 'r': builder.Append('\r'); break;
                        case 't': builder.Append('\t'); break;
                        case 'b': builder.Append('\b'); break;
                        case 'f': builder.Append('\f'); break;
                        case 'u':
                            if (i + 5 < json.Length
                                && ushort.TryParse(
                                    json.Substring(i + 2, 4),
                                    System.Globalization.NumberStyles.HexNumber,
                                    System.Globalization.CultureInfo.InvariantCulture,
                                    out ushort code))
                            {
                                builder.Append((char)code);
                                i += 4;
                            }
                            break;
                        default: builder.Append(escaped); break;
                    }

                    i += 2;
                    continue;
                }

                if (c == '"')
                {
                    value = builder.ToString();
                    nextIndex = i + 1;
                    return true;
                }

                builder.Append(c);
                i++;
            }

            return false;
        }

        /// <summary>解析扁平 JSON 对象（值为字符串/数字/布尔）为字典；不支持嵌套结构。</summary>
        public static System.Collections.Generic.Dictionary<string, string> ParseFlatObject(string body)
        {
            var result = new System.Collections.Generic.Dictionary<string, string>();
            if (string.IsNullOrEmpty(body))
            {
                return result;
            }

            int i = 0;
            while (i < body.Length)
            {
                int keyQuote = body.IndexOf('"', i);
                if (keyQuote < 0 || !TryReadStringAt(body, keyQuote, out string key, out int afterKey))
                {
                    break;
                }

                int colon = body.IndexOf(':', afterKey);
                if (colon < 0)
                {
                    break;
                }

                int valueIndex = colon + 1;
                while (valueIndex < body.Length && char.IsWhiteSpace(body[valueIndex]))
                {
                    valueIndex++;
                }

                if (valueIndex >= body.Length)
                {
                    break;
                }

                if (body[valueIndex] == '"')
                {
                    if (!TryReadStringAt(body, valueIndex, out string value, out int afterValue))
                    {
                        break;
                    }

                    result[key] = value;
                    i = afterValue;
                }
                else
                {
                    int end = valueIndex;
                    while (end < body.Length && body[end] != ',' && body[end] != '}')
                    {
                        end++;
                    }

                    result[key] = body.Substring(valueIndex, end - valueIndex).Trim();
                    i = end;
                }
            }

            return result;
        }
    }
}
