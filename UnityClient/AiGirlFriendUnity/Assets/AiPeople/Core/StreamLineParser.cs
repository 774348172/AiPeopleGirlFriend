using System;
using System.Text;

namespace AiPeople.Core
{
    /// <summary>
    /// NDJSON 行切分器：跨网络分包含半行累计，按 \n 切分并容忍 \r。
    /// 注意：NDJSON 的换行只会出现在行边界；字符串内的换行在后端序列化时已转义为 \n。
    /// </summary>
    public sealed class StreamLineParser
    {
        private readonly StringBuilder _buffer = new StringBuilder(2048);

        public void Push(string chunk, Action<string> onLine)
        {
            if (string.IsNullOrEmpty(chunk))
            {
                return;
            }

            _buffer.Append(chunk);
            Drain(onLine);
        }

        public void FlushPending(Action<string> onLine)
        {
            Drain(onLine);
            string rest = _buffer.ToString().Trim();
            _buffer.Length = 0;
            if (rest.Length > 0)
            {
                onLine(rest);
            }
        }

        private void Drain(Action<string> onLine)
        {
            while (true)
            {
                int newline = IndexOfNewline(_buffer);
                if (newline < 0)
                {
                    return;
                }

                string line = _buffer.ToString(0, newline).Trim();
                _buffer.Remove(0, newline + 1);
                if (line.Length > 0)
                {
                    onLine(line);
                }
            }
        }

        private static int IndexOfNewline(StringBuilder builder)
        {
            for (int i = 0; i < builder.Length; i++)
            {
                if (builder[i] == '\n')
                {
                    return i;
                }
            }

            return -1;
        }
    }
}
