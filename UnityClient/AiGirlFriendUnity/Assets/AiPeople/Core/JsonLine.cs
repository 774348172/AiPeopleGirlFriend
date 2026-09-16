namespace AiPeople.Core
{
    /// <summary>
    /// 从单行 JSON 中提取字符串字段的最小工具（只覆盖本协议信封所需）。
    /// 不替代完整 JSON 解析器；解析失败时返回 false，由调用方降级处理。
    /// </summary>
    public static class JsonLine
    {
        public static bool TryGetString(string json, string key, out string value)
        {
            value = null;
            if (string.IsNullOrEmpty(json))
            {
                return false;
            }

            string pattern = "\"" + key + "\"";
            int keyIndex = json.IndexOf(pattern, System.StringComparison.Ordinal);
            if (keyIndex < 0)
            {
                return false;
            }

            int colon = json.IndexOf(':', keyIndex + pattern.Length);
            if (colon < 0)
            {
                return false;
            }

            int index = colon + 1;
            while (index < json.Length && (json[index] == ' ' || json[index] == '\t'))
            {
                index++;
            }

            if (index >= json.Length || json[index] != '"')
            {
                return false;
            }

            index++;
            var builder = new System.Text.StringBuilder();
            while (index < json.Length)
            {
                char c = json[index];
                if (c == '\\' && index + 1 < json.Length)
                {
                    char escaped = json[index + 1];
                    switch (escaped)
                    {
                        case '"': builder.Append('"'); break;
                        case '\\': builder.Append('\\'); break;
                        case '/': builder.Append('/'); break;
                        case 'b': builder.Append('\b'); break;
                        case 'f': builder.Append('\f'); break;
                        case 'n': builder.Append('\n'); break;
                        case 'r': builder.Append('\r'); break;
                        case 't': builder.Append('\t'); break;
                        case 'u':
                            if (index + 5 < json.Length &&
                                ushort.TryParse(
                                    json.Substring(index + 2, 4),
                                    System.Globalization.NumberStyles.HexNumber,
                                    System.Globalization.CultureInfo.InvariantCulture,
                                    out ushort code))
                            {
                                builder.Append((char)code);
                                index += 4;
                            }
                            break;
                        default:
                            builder.Append(escaped);
                            break;
                    }

                    index += 2;
                    continue;
                }

                if (c == '"')
                {
                    break;
                }

                builder.Append(c);
                index++;
            }

            value = builder.ToString();
            return true;
        }
    }
}
