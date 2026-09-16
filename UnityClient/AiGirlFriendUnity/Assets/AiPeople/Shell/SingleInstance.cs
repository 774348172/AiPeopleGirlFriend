using System;
using System.Threading;

namespace AiPeople.Shell
{
    /// <summary>单实例互斥（主进程）：已有实例运行时返回 false，由调用方唤出既有实例后自行退出。</summary>
    public static class SingleInstance
    {
        private static Mutex _mutex;

        public static bool TryAcquire(string name)
        {
            try
            {
                _mutex = new Mutex(true, name, out bool createdNew);
                if (!createdNew)
                {
                    _mutex.Dispose();
                    _mutex = null;
                    return false;
                }

                return true;
            }
            catch (Exception)
            {
                // 互斥体异常时放行，避免挡住启动
                return true;
            }
        }
    }
}
