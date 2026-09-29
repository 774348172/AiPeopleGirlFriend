using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace AiPeople.PortableLauncher
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] arguments)
        {
            LauncherOptions options;
            string optionError;
            if (!LauncherOptions.TryParse(arguments, out options, out optionError))
            {
                ShowError(optionError);
                return 64;
            }

            LauncherPaths paths;
            try
            {
                paths = LauncherPaths.FromRoot(AppDomain.CurrentDomain.BaseDirectory);
            }
            catch (Exception error)
            {
                if (!options.ValidateOnly)
                {
                    ShowError(error.Message);
                }
                return 2;
            }

            if (options.ValidateOnly)
            {
                return paths.MissingRequiredFiles().Count == 0 ? 0 : 2;
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            Mutex instanceMutex = null;
            bool ownsMutex = false;
            try
            {
                instanceMutex = new Mutex(false, paths.MutexName());
                try
                {
                    ownsMutex = instanceMutex.WaitOne(0, false);
                }
                catch (AbandonedMutexException)
                {
                    ownsMutex = true;
                }

                if (!ownsMutex)
                {
                    return HandleDuplicateInstance();
                }

                using (LauncherApplicationContext context = new LauncherApplicationContext(paths, options))
                {
                    Application.Run(context);
                    return context.ExitCode;
                }
            }
            catch (Exception error)
            {
                ShowError(error.ToString());
                return 1;
            }
            finally
            {
                if (ownsMutex && instanceMutex != null)
                {
                    try
                    {
                        instanceMutex.ReleaseMutex();
                    }
                    catch (ApplicationException)
                    {
                    }
                }

                if (instanceMutex != null)
                {
                    instanceMutex.Close();
                }
            }
        }

        private static int HandleDuplicateInstance()
        {
            MessageBox.Show(
                "Another AiPeople launcher already owns the fixed local port 8767.\r\n\r\n"
                + "To protect save data, this copy will not connect to or reuse that service. "
                + "Exit the running copy from its tray icon before starting this one.",
                "AiPeople - Already Running",
                MessageBoxButtons.OK,
                MessageBoxIcon.Warning);
            return 7;
        }

        private static void ShowError(string message)
        {
            MessageBox.Show(
                message,
                "AiPeople - Startup Error",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }
    }

    internal sealed class LauncherApplicationContext : ApplicationContext
    {
        private const int StartupTimeoutMinutes = 15;

        private readonly LauncherPaths paths;
        private readonly LauncherOptions options;
        private readonly StartupForm form;
        private readonly NotifyIcon trayIcon;
        private readonly ToolStripMenuItem openMenuItem;
        private readonly ManualResetEvent stopRequested = new ManualResetEvent(false);
        private readonly object childSync = new object();
        private readonly string shutdownToken;

        private LauncherLog log;
        private Process child;
        private JobObject childJob;
        private bool startupWorkerStarted;
        private bool serviceReady;
        private bool intentionalStop;
        private bool exiting;
        private Thread startupWorkerThread;

        internal LauncherApplicationContext(LauncherPaths paths, LauncherOptions options)
        {
            this.paths = paths;
            this.options = options;
            shutdownToken = ShutdownToken.Create();
            ExitCode = 0;

            Directory.CreateDirectory(paths.LogsRoot);
            log = new LauncherLog(paths.LogsRoot);

            form = new StartupForm(log.Path);
            form.CancelRequested += delegate { RequestExit(true); };
            form.Shown += delegate { StartWorkerOnce(); };

            openMenuItem = new ToolStripMenuItem("Open AiPeople");
            openMenuItem.Enabled = false;
            openMenuItem.Click += delegate { OpenBrowserWithError(); };

            ToolStripMenuItem logMenuItem = new ToolStripMenuItem("Open log");
            logMenuItem.Click += delegate { OpenLogWithError(); };

            ToolStripMenuItem exitMenuItem = new ToolStripMenuItem("Exit");
            exitMenuItem.Click += delegate { RequestExit(true); };

            ContextMenuStrip menu = new ContextMenuStrip();
            menu.Items.Add(openMenuItem);
            menu.Items.Add(logMenuItem);
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(exitMenuItem);

            trayIcon = new NotifyIcon();
            trayIcon.Icon = SystemIcons.Application;
            trayIcon.Text = "AiPeople - starting";
            trayIcon.ContextMenuStrip = menu;
            trayIcon.Visible = true;
            trayIcon.DoubleClick += delegate
            {
                if (serviceReady)
                {
                    OpenBrowserWithError();
                }
                else
                {
                    form.Show();
                    form.Activate();
                }
            };

            form.Show();
        }

        internal int ExitCode { get; private set; }

        private void StartWorkerOnce()
        {
            if (startupWorkerStarted)
            {
                return;
            }

            startupWorkerStarted = true;
            startupWorkerThread = new Thread(StartupWorker);
            startupWorkerThread.IsBackground = true;
            startupWorkerThread.Name = "AiPeople startup monitor";
            startupWorkerThread.Start();
        }

        private void StartupWorker()
        {
            try
            {
                log.Write("launcher", "Root: " + paths.Root);
                IList<string> missing = paths.MissingRequiredFiles();
                if (missing.Count != 0)
                {
                    StringBuilder message = new StringBuilder();
                    message.AppendLine("The portable package is incomplete. Missing required files:");
                    foreach (string path in missing)
                    {
                        message.AppendLine(path);
                    }
                    message.AppendLine();
                    message.Append("Extract the complete package before starting AiPeople.");
                    FailStartup(message.ToString(), 2);
                    return;
                }

                Directory.CreateDirectory(paths.DataRoot);
                if (stopRequested.WaitOne(0))
                {
                    return;
                }

                ReportStatus("Checking local service and port...");
                HealthProbeResult existing = HealthProbe.Probe(1200);
                if (existing.ExpectedReady)
                {
                    FailStartup(
                        "Port 8767 is serving another verified AiPeople process that is not owned by this launcher. "
                        + "To protect save data, this release will not reuse it. Exit the other process first.",
                        3);
                    return;
                }

                string portDetail;
                if (!PortProbe.IsAvailable(LauncherPaths.ServicePort, out portDetail))
                {
                    string conflict = "Port 8767 is already in use by a service that did not pass the AiPeople health identity check. "
                        + "No process was terminated.\r\n\r\nDetails: " + existing.Detail
                        + "\r\nPort: " + portDetail;
                    FailStartup(conflict, 3);
                    return;
                }

                ReportStatus("Starting the local Python runtime...");
                StartPython();
                if (stopRequested.WaitOne(0))
                {
                    return;
                }

                ReportStatus("Loading and verifying the 12B NF4 model. This can take several minutes...");
                DateTime deadline = DateTime.UtcNow.AddMinutes(StartupTimeoutMinutes);
                while (DateTime.UtcNow < deadline && !stopRequested.WaitOne(0))
                {
                    if (HasChildExited())
                    {
                        int code = SafeExitCode();
                        FailStartup(
                            "The Python service exited before it became ready (exit code "
                            + code.ToString(CultureInfo.InvariantCulture) + ").",
                            4);
                        return;
                    }

                    HealthProbeResult health = HealthProbe.Probe(1200);
                    if (health.ExpectedReady)
                    {
                        log.Write("launcher", "Health identity verified: " + health.Detail);
                        ReportReady();
                        WaitForChildExit();
                        return;
                    }

                    if (health.Responded && health.StatusCode == 200)
                    {
                        FailStartup(
                            "The service health endpoint returned an unexpected identity.\r\n\r\n"
                            + health.Detail,
                            5);
                        return;
                    }

                    if (stopRequested.WaitOne(1000))
                    {
                        return;
                    }
                }

                if (!stopRequested.WaitOne(0))
                {
                    FailStartup(
                        "AiPeople did not become ready within "
                        + StartupTimeoutMinutes.ToString(CultureInfo.InvariantCulture)
                        + " minutes. Check the log for asset, CUDA, driver, or memory errors.",
                        6);
                }
            }
            catch (Exception error)
            {
                if (!stopRequested.WaitOne(0))
                {
                    FailStartup("Unexpected launcher failure:\r\n\r\n" + error, 1);
                }
            }
        }

        private void StartPython()
        {
            ProcessStartInfo startInfo = new ProcessStartInfo();
            startInfo.FileName = paths.PythonExe;
            startInfo.Arguments = paths.BuildPythonArguments();
            startInfo.WorkingDirectory = paths.AppRoot;
            startInfo.UseShellExecute = false;
            startInfo.CreateNoWindow = true;
            startInfo.RedirectStandardOutput = true;
            startInfo.RedirectStandardError = true;
            startInfo.StandardOutputEncoding = new UTF8Encoding(false);
            startInfo.StandardErrorEncoding = new UTF8Encoding(false);
            PythonEnvironment.Configure(startInfo, paths, shutdownToken);

            log.Write("launcher", "Python: " + startInfo.FileName);
            log.Write("launcher", "Arguments: " + startInfo.Arguments);
            log.Write("launcher", "Working directory: " + startInfo.WorkingDirectory);

            Process process = new Process();
            process.StartInfo = startInfo;
            LauncherLog processLog = log;
            process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs eventArgs)
            {
                if (eventArgs.Data != null)
                {
                    processLog.Write("stdout", eventArgs.Data);
                }
            };
            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs eventArgs)
            {
                if (eventArgs.Data != null)
                {
                    processLog.Write("stderr", eventArgs.Data);
                }
            };

            JobObject job = null;
            try
            {
                lock (childSync)
                {
                    if (stopRequested.WaitOne(0))
                    {
                        process.Dispose();
                        return;
                    }

                    job = new JobObject();
                    if (!process.Start())
                    {
                        throw new InvalidOperationException("Python did not start.");
                    }
                    job.Assign(process);
                    child = process;
                    childJob = job;
                    job = null;
                }
            }
            catch
            {
                if (job != null)
                {
                    job.Dispose();
                }

                try
                {
                    if (!process.HasExited)
                    {
                        process.Kill();
                    }
                }
                catch
                {
                }

                process.Dispose();
                throw;
            }

            if (stopRequested.WaitOne(0))
            {
                return;
            }

            try
            {
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
            }
            catch
            {
                CleanupChild();
                if (!stopRequested.WaitOne(0))
                {
                    throw;
                }
            }
        }

        private bool HasChildExited()
        {
            lock (childSync)
            {
                return child == null || child.HasExited;
            }
        }

        private int SafeExitCode()
        {
            lock (childSync)
            {
                try
                {
                    return child == null ? -1 : child.ExitCode;
                }
                catch
                {
                    return -1;
                }
            }
        }

        private void WaitForChildExit()
        {
            Process process;
            lock (childSync)
            {
                process = child;
            }

            if (process == null)
            {
                return;
            }

            process.WaitForExit();
            process.WaitForExit();
            int code = SafeExitCode();
            log.Write("launcher", "Python exited with code " + code.ToString(CultureInfo.InvariantCulture) + ".");
            if (!stopRequested.WaitOne(0))
            {
                PostToUi(delegate
                {
                    MessageBox.Show(
                        "The AiPeople service stopped (exit code "
                        + code.ToString(CultureInfo.InvariantCulture) + ").\r\n\r\nLog: " + log.Path,
                        "AiPeople",
                        MessageBoxButtons.OK,
                        code == 0 ? MessageBoxIcon.Information : MessageBoxIcon.Error);
                    ExitCode = code == 0 ? 0 : 4;
                    RequestExit(false);
                });
            }
        }

        private void ReportStatus(string value)
        {
            log.Write("launcher", value);
            PostToUi(delegate { form.SetStatus(value); });
        }

        private void ReportReady()
        {
            PostToUi(delegate
            {
                serviceReady = true;
                openMenuItem.Enabled = true;
                trayIcon.Text = "AiPeople - ready";
                form.Hide();
                trayIcon.ShowBalloonTip(
                    3000,
                    "AiPeople",
                    "The model is ready.",
                    ToolTipIcon.Info);
                if (!options.NoBrowser)
                {
                    OpenBrowserWithError();
                }
            });
        }

        private void FailStartup(string message, int exitCode)
        {
            log.Write("launcher", message);
            PostToUi(delegate
            {
                ExitCode = exitCode;
                CleanupChild();
                MessageBox.Show(
                    message + "\r\n\r\nLog: " + log.Path,
                    "AiPeople - Startup Error",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                RequestExit(false);
            });
        }

        private void OpenBrowserWithError()
        {
            if (!serviceReady)
            {
                form.Show();
                form.Activate();
                return;
            }

            try
            {
                BrowserLauncher.Open();
            }
            catch (Exception error)
            {
                MessageBox.Show(
                    "The browser could not be opened automatically.\r\n\r\n"
                    + HealthProbe.BrowserUri.AbsoluteUri + "\r\n\r\n" + error.Message,
                    "AiPeople",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Warning);
            }
        }

        private void OpenLogWithError()
        {
            try
            {
                ProcessStartInfo startInfo = new ProcessStartInfo();
                startInfo.FileName = log.Path;
                startInfo.UseShellExecute = true;
                Process.Start(startInfo);
            }
            catch (Exception error)
            {
                MessageBox.Show(error.Message, "AiPeople", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        private void RequestExit(bool intentional)
        {
            if (exiting)
            {
                return;
            }

            exiting = true;
            intentionalStop = intentional;
            stopRequested.Set();
            if (intentional && serviceReady && HasOwnedChild())
            {
                BeginGracefulShutdown();
                return;
            }

            FinishExit();
        }

        private bool HasOwnedChild()
        {
            lock (childSync)
            {
                return child != null;
            }
        }

        private void BeginGracefulShutdown()
        {
            openMenuItem.Enabled = false;
            trayIcon.Text = "AiPeople - stopping";
            form.SetStatus("Saving data and stopping the local service...");
            form.Show();
            form.Activate();

            Thread worker = new Thread(GracefulShutdownWorker);
            worker.IsBackground = true;
            worker.Name = "AiPeople graceful shutdown";
            worker.Start();
        }

        private void GracefulShutdownWorker()
        {
            string detail;
            bool accepted = ShutdownProbe.Request(shutdownToken, 2000, out detail);
            log.Write("launcher", detail);

            int waitMilliseconds = accepted ? 15000 : 2500;
            bool exited = WaitForOwnedChild(waitMilliseconds);
            if (exited)
            {
                log.Write("launcher", "Python completed graceful shutdown.");
            }
            else
            {
                log.Write(
                    "launcher",
                    "Graceful shutdown did not complete within "
                    + waitMilliseconds.ToString(CultureInfo.InvariantCulture)
                    + " ms; closing the process job.");
                CleanupChild();
            }

            PostToUi(FinishExit);
        }

        private bool WaitForOwnedChild(int timeoutMilliseconds)
        {
            Process process;
            lock (childSync)
            {
                process = child;
            }

            if (process == null)
            {
                return true;
            }

            try
            {
                if (!process.WaitForExit(timeoutMilliseconds))
                {
                    return false;
                }
                process.WaitForExit();
                return true;
            }
            catch (ObjectDisposedException)
            {
                return true;
            }
            catch (InvalidOperationException)
            {
                return true;
            }
        }

        private void FinishExit()
        {
            CleanupChild();
            trayIcon.Visible = false;
            form.AllowClose();
            form.Close();
            ExitThread();
        }

        private void CleanupChild()
        {
            lock (childSync)
            {
                if (childJob != null)
                {
                    childJob.Dispose();
                    childJob = null;
                }

                if (child != null)
                {
                    try
                    {
                        if (!child.HasExited)
                        {
                            child.Kill();
                        }
                    }
                    catch
                    {
                    }
                }
            }
        }

        private void PostToUi(MethodInvoker action)
        {
            try
            {
                if (!form.IsDisposed && form.IsHandleCreated)
                {
                    form.BeginInvoke(action);
                }
            }
            catch (ObjectDisposedException)
            {
            }
            catch (InvalidOperationException)
            {
            }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                stopRequested.Set();
                CleanupChild();
                Thread worker = startupWorkerThread;
                if (worker != null && worker != Thread.CurrentThread && worker.IsAlive)
                {
                    worker.Join(5000);
                }
                trayIcon.Visible = false;
                trayIcon.Dispose();
                form.Dispose();
                stopRequested.Close();
                if (child != null)
                {
                    child.Dispose();
                    child = null;
                }
                if (log != null)
                {
                    log.Write("launcher", intentionalStop ? "Launcher stopped by user." : "Launcher stopped.");
                    log.Dispose();
                    log = null;
                }
            }
            base.Dispose(disposing);
        }
    }

    internal sealed class StartupForm : Form
    {
        private readonly Label statusLabel;
        private bool closeAllowed;

        internal StartupForm(string logPath)
        {
            Text = "AiPeople";
            Width = 560;
            Height = 205;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = true;
            StartPosition = FormStartPosition.CenterScreen;
            ShowInTaskbar = true;

            Label title = new Label();
            title.AutoSize = true;
            title.Font = new Font(SystemFonts.MessageBoxFont.FontFamily, 12.0f, FontStyle.Bold);
            title.Left = 20;
            title.Top = 18;
            title.Text = "Starting AiPeople";
            Controls.Add(title);

            statusLabel = new Label();
            statusLabel.AutoEllipsis = true;
            statusLabel.Left = 20;
            statusLabel.Top = 52;
            statusLabel.Width = 510;
            statusLabel.Height = 22;
            statusLabel.Text = "Preparing the portable runtime...";
            Controls.Add(statusLabel);

            ProgressBar progress = new ProgressBar();
            progress.Style = ProgressBarStyle.Marquee;
            progress.MarqueeAnimationSpeed = 30;
            progress.Left = 20;
            progress.Top = 78;
            progress.Width = 510;
            progress.Height = 18;
            Controls.Add(progress);

            Label logLabel = new Label();
            logLabel.AutoEllipsis = true;
            logLabel.Left = 20;
            logLabel.Top = 108;
            logLabel.Width = 420;
            logLabel.Height = 36;
            logLabel.Text = "Log: " + logPath;
            Controls.Add(logLabel);

            Button cancel = new Button();
            cancel.Left = 452;
            cancel.Top = 111;
            cancel.Width = 78;
            cancel.Height = 28;
            cancel.Text = "Cancel";
            cancel.Click += delegate { RaiseCancelRequested(); };
            Controls.Add(cancel);
        }

        internal event EventHandler CancelRequested;

        internal void SetStatus(string value)
        {
            statusLabel.Text = value;
        }

        internal void AllowClose()
        {
            closeAllowed = true;
        }

        protected override void OnFormClosing(FormClosingEventArgs eventArgs)
        {
            if (!closeAllowed && eventArgs.CloseReason == CloseReason.UserClosing)
            {
                eventArgs.Cancel = true;
                BeginInvoke(new MethodInvoker(RaiseCancelRequested));
            }
            base.OnFormClosing(eventArgs);
        }

        private void RaiseCancelRequested()
        {
            EventHandler handler = CancelRequested;
            if (handler != null)
            {
                handler(this, EventArgs.Empty);
            }
        }
    }

    internal sealed class LauncherLog : IDisposable
    {
        private readonly object sync = new object();
        private StreamWriter writer;

        internal LauncherLog(string directory)
        {
            Directory.CreateDirectory(directory);
            string fileName = "AiPeople-"
                + DateTime.Now.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture)
                + "-" + Process.GetCurrentProcess().Id.ToString(CultureInfo.InvariantCulture)
                + ".log";
            Path = System.IO.Path.Combine(directory, fileName);
            FileStream stream = new FileStream(
                Path,
                FileMode.CreateNew,
                FileAccess.Write,
                FileShare.ReadWrite);
            writer = new StreamWriter(stream, new UTF8Encoding(false));
            writer.AutoFlush = true;
        }

        internal string Path { get; private set; }

        internal void Write(string source, string value)
        {
            lock (sync)
            {
                if (writer == null)
                {
                    return;
                }

                try
                {
                    writer.WriteLine(
                        "[{0:yyyy-MM-dd HH:mm:ss.fff}] [{1}] {2}",
                        DateTime.Now,
                        source,
                        value);
                }
                catch (ObjectDisposedException)
                {
                }
                catch (IOException)
                {
                }
            }
        }

        public void Dispose()
        {
            lock (sync)
            {
                if (writer != null)
                {
                    writer.Dispose();
                    writer = null;
                }
            }
        }
    }
}
