using System;

namespace AiPeople.Core
{
    /// <summary>
    /// 后端已提交状态的最新本地视图（显示用缓存，不是事实源）。
    /// 数据来源：POST /api/status。
    /// </summary>
    public sealed class GameStateModel
    {
        public StatusResponse Latest { get; private set; }
        public bool HasData => Latest != null;
        public string LastError { get; private set; }

        /// <summary>状态被更新或出错时触发（主线程）。</summary>
        public event Action Changed;

        public void Apply(StatusResponse response)
        {
            Latest = response;
            LastError = null;
            Changed?.Invoke();
        }

        public void ReportError(string error)
        {
            LastError = error;
            Changed?.Invoke();
        }
    }
}
