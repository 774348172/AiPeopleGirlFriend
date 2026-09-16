using UnityEngine;

namespace AiPeople.Core
{
    /// <summary>
    /// M1 开发期字体：优先使用系统已安装的 CJK 字体（动态字体，Windows 开发机可用）。
    /// 发布前必须替换为随包授权的 CJK 字体资产（见 README 限制说明）。
    /// </summary>
    public static class FontLibrary
    {
        private static Font _cached;

        private static readonly string[] Candidates =
        {
            "Microsoft YaHei",
            "微软雅黑",
            "SimHei",
            "黑体",
            "SimSun",
            "宋体",
            "Noto Sans CJK SC",
        };

        public static Font Get()
        {
            if (_cached != null)
            {
                return _cached;
            }

            _cached = Resources.Load<Font>("AiPeopleFont");
            if (_cached != null) return _cached;

            string[] installed = Font.GetOSInstalledFontNames();
            if (installed != null)
            {
                foreach (string candidate in Candidates)
                {
                    if (System.Array.IndexOf(installed, candidate) < 0)
                    {
                        continue;
                    }

                    _cached = Font.CreateDynamicFontFromOSFont(candidate, 24);
                    if (_cached != null)
                    {
                        return _cached;
                    }
                }
            }

            _cached = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            return _cached;
        }
    }
}
