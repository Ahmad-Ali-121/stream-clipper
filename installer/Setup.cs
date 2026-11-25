// Stream-Clipper-Setup.exe: installs Stream Clipper on a Windows PC that has nothing on it yet.
//
// It needs no Python, no Git and no administrator rights. Everything goes into two folders the person chooses:
//   <program folder>\tools\uv     the small tool that installs Python and the program's parts (from its maker, Astral)
//   <program folder>\tools\git    portable Git, so that the program's own Update button works (only if this PC has no Git)
//   <program folder>\app          the program itself, taken from its public page on GitHub
//   <data folder>                 clips, downloads and models
// Then it runs the program's own setup step (video tools and models) and puts a shortcut on the desktop.
// Signing in to Claude or ChatGPT is not done here: the program's own "Set up" page does that on first start.
//
// Built with the C# compiler that is part of Windows (see build.cmd), so nothing has to be installed to build it.
// Switches for trying it out:  /test  (no desktop shortcut, do not start the program)   /source <folder or address>
//                              /portablegit  (fetch portable Git even if this PC has Git)   /folder <program folder>  /data <data folder>  /go
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;

namespace StreamClipperSetup
{
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072 | (SecurityProtocolType)12288;   // TLS 1.2 and 1.3
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new SetupForm(args));
        }
    }

    class Step
    {
        public string Title; public Func<bool> Done; public Action Run; public Label Mark; public Label Text;
    }

    class SetupForm : Form
    {
        const string Repo = "https://github.com/Ahmad-Ali-121/stream-clipper.git";
        const string UvZip = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip";
        const string GitLatest = "https://api.github.com/repos/git-for-windows/git/releases/latest";
        const string Agent = "stream-clipper-setup";

        readonly bool test, portableGit, autoGo;
        string source = Repo;
        TextBox folderBox, dataBox, log;
        Button goBtn, closeBtn, browse1, browse2;
        ProgressBar bar;
        Label status, head, sub;
        Panel choose, work;
        CheckBox startBox;
        readonly List<Step> steps = new List<Step>();
        BackgroundWorker worker;
        string root, data;
        bool finished;

        public SetupForm(string[] args)
        {
            string folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "StreamClipper");
            string dataDir = DefaultData();
            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i].ToLowerInvariant();
                if (a == "/test") test = true;
                else if (a == "/portablegit") portableGit = true;
                else if (a == "/go") autoGo = true;
                else if (a == "/source" && i + 1 < args.Length) source = args[++i];
                else if (a == "/folder" && i + 1 < args.Length) folder = args[++i];
                else if (a == "/data" && i + 1 < args.Length) dataDir = args[++i];
            }
            Text = "Install Stream Clipper";
            Font = new Font("Segoe UI", 9.5f);
            ClientSize = new Size(640, 520);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(22, 24, 27);
            ForeColor = Color.FromArgb(236, 235, 231);
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

            head = new Label { Text = "Install Stream Clipper", Font = new Font("Segoe UI Semibold", 17f), AutoSize = true, Location = new Point(24, 18) };
            sub = new Label { Text = "Turns long streams into short clips. This installs everything it needs: you do not need Python or anything else first.",
                              Location = new Point(26, 58), Size = new Size(590, 40), ForeColor = Color.FromArgb(144, 150, 160) };
            Controls.Add(head); Controls.Add(sub);

            // ---- screen 1: where to put it ----
            choose = new Panel { Location = new Point(0, 104), Size = new Size(640, 340) };
            choose.Controls.Add(Caption("Where the program goes (about 1.5 GB with its parts)", 26, 4));
            folderBox = Box(folder, 26, 28); choose.Controls.Add(folderBox);
            browse1 = Btn("Choose...", 520, 27, 94); browse1.Click += delegate { Pick(folderBox); }; choose.Controls.Add(browse1);
            choose.Controls.Add(Caption("Where your clips, downloads and models go (pick the drive with the most free space; 5 GB or more)", 26, 76));
            dataBox = Box(dataDir, 26, 100); choose.Controls.Add(dataBox);
            browse2 = Btn("Choose...", 520, 99, 94); browse2.Click += delegate { Pick(dataBox); }; choose.Controls.Add(browse2);
            Label what = new Label { Location = new Point(26, 150), Size = new Size(590, 170), ForeColor = Color.FromArgb(144, 150, 160),
                Text = "What happens when you press Install:\r\n\r\n" +
                       "  1.  A small tool called uv is fetched; it installs Python for this program only.\r\n" +
                       "  2.  The program is fetched from its page on GitHub.\r\n" +
                       "  3.  Python and the program's parts are installed (the longest step).\r\n" +
                       "  4.  The video tools and models are downloaded (about 300 MB).\r\n" +
                       "  5.  A shortcut is put on your desktop.\r\n\r\n" +
                       "Nothing is installed outside these two folders, and no administrator rights are needed. It needs the internet and takes 5 to 20 minutes." };
            choose.Controls.Add(what);
            Controls.Add(choose);

            // ---- screen 2: the work ----
            work = new Panel { Location = new Point(0, 104), Size = new Size(640, 340), Visible = false };
            Controls.Add(work);

            status = new Label { Location = new Point(26, 452), Size = new Size(380, 44), ForeColor = Color.FromArgb(144, 150, 160) };
            Controls.Add(status);
            goBtn = Btn("Install", 510, 468, 104); goBtn.BackColor = Color.FromArgb(255, 90, 43); goBtn.ForeColor = Color.FromArgb(26, 10, 4);
            goBtn.Click += delegate { OnGo(); }; Controls.Add(goBtn);
            closeBtn = Btn("Close", 400, 468, 100); closeBtn.Click += delegate { Close(); }; Controls.Add(closeBtn);
            AcceptButton = goBtn;
            FormClosing += delegate(object s, FormClosingEventArgs e)
            {
                if (worker != null && worker.IsBusy &&
                    MessageBox.Show(this, "The install is still running. Stop it?\r\n\r\nYou can run this installer again later: it carries on where it stopped.", "Install Stream Clipper",
                                    MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.No) e.Cancel = true;
            };
            if (autoGo) Shown += delegate { OnGo(); };
        }

        // ---------- small helpers for the window ----------
        Label Caption(string t, int x, int y) { return new Label { Text = t, AutoSize = true, Location = new Point(x, y), ForeColor = Color.FromArgb(144, 150, 160) }; }
        TextBox Box(string t, int x, int y)
        {
            return new TextBox { Text = t, Location = new Point(x, y), Size = new Size(484, 26), BackColor = Color.FromArgb(15, 16, 18), ForeColor = Color.FromArgb(236, 235, 231), BorderStyle = BorderStyle.FixedSingle };
        }
        Button Btn(string t, int x, int y, int w)
        {
            Button b = new Button { Text = t, Location = new Point(x, y), Size = new Size(w, 30), FlatStyle = FlatStyle.Flat, BackColor = Color.FromArgb(29, 32, 36), ForeColor = Color.FromArgb(236, 235, 231) };
            b.FlatAppearance.BorderColor = Color.FromArgb(54, 58, 65);
            return b;
        }
        void Pick(TextBox box)
        {
            using (FolderBrowserDialog d = new FolderBrowserDialog())
            {
                d.Description = "Choose a folder";
                try { if (Directory.Exists(box.Text)) d.SelectedPath = box.Text; } catch { }
                if (d.ShowDialog(this) == DialogResult.OK) box.Text = d.SelectedPath;
            }
        }
        static string DefaultData()
        {
            // the fixed drive with the most free space; on a PC with only C:, a folder beside the program
            DriveInfo best = null;
            foreach (DriveInfo d in DriveInfo.GetDrives())
            {
                try { if (d.DriveType == DriveType.Fixed && d.IsReady && (best == null || d.AvailableFreeSpace > best.AvailableFreeSpace)) best = d; } catch { }
            }
            string sys = Path.GetPathRoot(Environment.GetFolderPath(Environment.SpecialFolder.System));
            if (best == null || string.Equals(best.Name, sys, StringComparison.OrdinalIgnoreCase))
                return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "StreamClipper", "data");
            return Path.Combine(best.Name, "clipper-data");
        }

        // ---------- starting ----------
        void OnGo()
        {
            if (finished) { if (startBox != null && startBox.Checked) StartProgram(); Close(); return; }
            if (worker != null && worker.IsBusy) return;
            try
            {
                root = Path.GetFullPath(folderBox.Text.Trim().Trim('"'));
                data = Path.GetFullPath(dataBox.Text.Trim().Trim('"'));
            }
            catch (Exception e) { Say("That is not a folder name Windows accepts: " + e.Message, true); return; }
            if (root.Length < 4 || data.Length < 4) { Say("Choose a folder, not a whole drive.", true); return; }
            try
            {
                Directory.CreateDirectory(root); Directory.CreateDirectory(data);
                DriveInfo d = new DriveInfo(Path.GetPathRoot(data));
                if (d.AvailableFreeSpace < 5L * 1024 * 1024 * 1024)
                {
                    Say("The drive " + d.Name + " has less than 5 GB free. Choose a folder on another drive for your clips, or free some space.", true); return;
                }
            }
            catch (Exception e) { Say("That folder cannot be used: " + e.Message, true); return; }
            if (steps.Count == 0) BuildSteps();
            choose.Visible = false; work.Visible = true; goBtn.Enabled = false; goBtn.Text = "Installing..."; closeBtn.Text = "Stop";
            head.Text = "Installing Stream Clipper"; sub.Text = "Keep this window open. You can use the computer meanwhile.";
            Say("", false);
            worker = new BackgroundWorker();
            worker.DoWork += delegate { RunSteps(); };
            worker.RunWorkerCompleted += delegate(object s, RunWorkerCompletedEventArgs e) { OnEnd(e.Error); };
            worker.RunWorkerAsync();
        }

        string App { get { return Path.Combine(root, "app"); } }
        string Uv { get { return Path.Combine(root, "tools", "uv", "uv.exe"); } }
        string OwnGit { get { return Path.Combine(root, "tools", "git", "cmd", "git.exe"); } }

        void BuildSteps()
        {
            Add("Fetch uv, the tool that installs Python", delegate { return File.Exists(Uv); }, GetUv);
            Add("Make sure Git is there (for the Update button)", delegate { return File.Exists(OwnGit) || (!portableGit && OnPath("git.exe") != null); }, GetGit);
            Add("Fetch the program", delegate { return File.Exists(Path.Combine(App, "pyproject.toml")) && Directory.Exists(Path.Combine(App, ".git")); }, GetProgram);
            Add("Install Python and the program's parts", delegate { return false; }, SyncParts);
            Add("Download the video tools and models", delegate { return false; }, RunSetup);
            int y = 4;
            foreach (Step s in steps)
            {
                s.Mark = new Label { Text = "•", Location = new Point(26, y), Size = new Size(22, 22), ForeColor = Color.FromArgb(102, 107, 116), Font = new Font("Segoe UI Semibold", 10.5f) };
                s.Text = new Label { Text = s.Title, Location = new Point(50, y + 1), Size = new Size(560, 22), ForeColor = Color.FromArgb(144, 150, 160) };
                work.Controls.Add(s.Mark); work.Controls.Add(s.Text); y += 24;
            }
            bar = new ProgressBar { Location = new Point(26, y + 8), Size = new Size(588, 8), Style = ProgressBarStyle.Continuous, Maximum = 1000 };
            work.Controls.Add(bar);
            log = new TextBox { Location = new Point(26, y + 26), Size = new Size(588, 340 - y - 34), Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical,
                                BackColor = Color.FromArgb(15, 16, 18), ForeColor = Color.FromArgb(144, 150, 160), BorderStyle = BorderStyle.FixedSingle, Font = new Font("Consolas", 8.5f) };
            work.Controls.Add(log);
        }
        void Add(string title, Func<bool> done, Action run) { steps.Add(new Step { Title = title, Done = done, Run = run }); }

        void RunSteps()
        {
            for (int i = 0; i < steps.Count; i++)
            {
                Step s = steps[i];
                Mark(s, "▸", Color.FromArgb(255, 90, 43), Color.FromArgb(236, 235, 231));
                Progress(i, 0);
                if (s.Done()) Line(s.Title + ": already there.");
                else { Line(""); Line("== " + s.Title); s.Run(); }
                Mark(s, "✓", Color.FromArgb(63, 178, 127), Color.FromArgb(144, 150, 160));
                Progress(i + 1, 0);
            }
        }

        void OnEnd(Exception error)
        {
            closeBtn.Text = "Close";
            if (error != null)
            {
                foreach (Step s in steps) if (s.Mark.Text == "▸") Mark(s, "✕", Color.FromArgb(234, 84, 85), Color.FromArgb(236, 235, 231));
                Line(""); Line("STOPPED: " + error.Message);
                if (autoGo && test) { Environment.ExitCode = 1; Close(); return; }
                head.Text = "The install stopped";
                sub.Text = "Nothing is broken: what was installed so far is kept. Fix what the message says and press Try again. The full log is in " + Path.Combine(root, "install-log.txt");
                Say(Plain(error.Message), true);
                goBtn.Enabled = true; goBtn.Text = "Try again";
                return;
            }
            finished = true;
            head.Text = "Stream Clipper is installed";
            sub.Text = test ? "Test run: no shortcut was made and the program is not started." :
                "A shortcut called Stream Clipper is on your desktop. The first time it starts, its Set up page takes you through the last steps (captions, and signing in to Claude or ChatGPT if you want them).";
            Say("", false);
            if (!test)
            {
                startBox = new CheckBox { Text = "Start Stream Clipper now", Checked = true, Location = new Point(28, 458), AutoSize = true };
                Controls.Add(startBox); startBox.BringToFront();
            }
            goBtn.Enabled = true; goBtn.Text = "Finish"; closeBtn.Visible = false;
            if (autoGo && test) Close();
        }

        // ---------- the steps ----------
        void GetUv()
        {
            string zip = Path.Combine(root, "tools", "uv.zip"), dir = Path.Combine(root, "tools", "uv");
            Directory.CreateDirectory(dir);
            Download(UvZip, zip, "uv");
            Unzip(zip, dir);
            File.Delete(zip);
            if (!File.Exists(Uv))
            {   // some releases keep the files in a folder of their own
                foreach (string f in Directory.GetFiles(dir, "uv.exe", SearchOption.AllDirectories)) { File.Copy(f, Uv, true); break; }
            }
            if (!File.Exists(Uv)) throw new Exception("uv.exe was not in the download from " + UvZip);
        }

        void GetGit()
        {
            Line("Asking GitHub for the newest portable Git...");
            string json;
            using (WebClient c = Client()) json = c.DownloadString(GitLatest);
            Match m = Regex.Match(json, "\"browser_download_url\"\\s*:\\s*\"(https://[^\"]*/MinGit-[0-9.]+-64-bit\\.zip)\"");
            if (!m.Success) throw new Exception("GitHub's list of Git downloads has no 64-bit portable Git today. Install Git from https://git-scm.com and run this installer again");
            string zip = Path.Combine(root, "tools", "git.zip"), dir = Path.Combine(root, "tools", "git");
            Download(m.Groups[1].Value, zip, "portable Git");
            if (Directory.Exists(dir)) Directory.Delete(dir, true);
            Unzip(zip, dir);
            File.Delete(zip);
            if (!File.Exists(OwnGit)) throw new Exception("git.exe was not in the download " + m.Groups[1].Value);
        }

        string Git { get { return File.Exists(OwnGit) ? OwnGit : (OnPath("git.exe") ?? "git"); } }

        void GetProgram()
        {
            if (Directory.Exists(App))
            {
                if (Directory.GetFileSystemEntries(App).Length > 0 && !Directory.Exists(Path.Combine(App, ".git")))
                    throw new Exception("The folder " + App + " already holds something that is not Stream Clipper. Choose another program folder, or empty that one");
                if (!File.Exists(Path.Combine(App, "pyproject.toml"))) Directory.Delete(App, true);   // a fetch that was cut off
            }
            string args = "clone --progress " + (source == Repo ? "--depth 50 " : "") + Q(source) + " " + Q(App);
            Exec(Git, args, root, "fetching the program");
        }

        void SyncParts()
        {
            Line("This is the longest step: Python and the program's parts are downloaded (several hundred MB).");
            Exec(Uv, "sync --frozen", App, "installing Python and the program's parts");
        }

        void RunSetup()
        {
            string ps = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe");
            string args = "-NoProfile -ExecutionPolicy Bypass -File " + Q(Path.Combine(App, "setup.ps1")) + " -DataDir " + Q(data) + (test ? " -NoShortcut" : "");
            Exec(ps, args, App, "downloading the video tools and models", Path.Combine(data, "downloads"));
        }

        void StartProgram()
        {
            try { Process.Start(new ProcessStartInfo { FileName = Path.Combine(App, "Stream-Clipper.cmd"), WorkingDirectory = App, UseShellExecute = true }); }
            catch (Exception e) { MessageBox.Show(this, "The program could not be started from here: " + e.Message + "\r\n\r\nUse the Stream Clipper shortcut on your desktop.", "Install Stream Clipper"); }
        }

        // ---------- downloading, unpacking, running ----------
        WebClient Client() { WebClient c = new WebClient(); c.Headers.Add("User-Agent", Agent); return c; }

        void Download(string url, string to, string what)
        {
            Line("Downloading " + what + " from " + new Uri(url).Host + " ...");
            Exception fault = null; bool done = false; int last = -1;
            using (WebClient c = Client())
            {
                c.DownloadProgressChanged += delegate(object s, DownloadProgressChangedEventArgs e)
                {
                    int pct = e.TotalBytesToReceive > 0 ? (int)(e.BytesReceived * 100 / e.TotalBytesToReceive) : 0;
                    if (pct != last) { last = pct; Status("Downloading " + what + ": " + (e.BytesReceived / 1048576) + " MB" + (e.TotalBytesToReceive > 0 ? " of " + (e.TotalBytesToReceive / 1048576) + " MB" : "")); StepProgress(pct * 10); }
                };
                c.DownloadFileCompleted += delegate(object s, AsyncCompletedEventArgs e) { fault = e.Error; done = true; };
                c.DownloadFileAsync(new Uri(url), to + ".part");
                while (!done) { System.Threading.Thread.Sleep(120); if (worker.CancellationPending) { c.CancelAsync(); } }
            }
            if (fault != null) { try { File.Delete(to + ".part"); } catch { } throw new Exception("downloading " + what + " failed: " + Deepest(fault)); }
            if (File.Exists(to)) File.Delete(to);
            File.Move(to + ".part", to);
            Status("");
        }

        void Unzip(string zip, string dir)
        {
            Line("Unpacking...");
            using (ZipArchive a = ZipFile.OpenRead(zip))
            {
                foreach (ZipArchiveEntry e in a.Entries)
                {
                    string target = Path.GetFullPath(Path.Combine(dir, e.FullName));
                    if (!target.StartsWith(Path.GetFullPath(dir), StringComparison.OrdinalIgnoreCase)) continue;   // never outside the folder
                    if (e.FullName.EndsWith("/") || e.FullName.EndsWith("\\")) { Directory.CreateDirectory(target); continue; }
                    Directory.CreateDirectory(Path.GetDirectoryName(target));
                    e.ExtractToFile(target, true);
                }
            }
        }

        void Exec(string exe, string args, string cwd, string what) { Exec(exe, args, cwd, what, null); }

        // ``watch``: a folder that fills up while the step downloads; its size is shown so a slow download does not look like a hang
        void Exec(string exe, string args, string cwd, string what, string watch)
        {
            ProcessStartInfo si = new ProcessStartInfo(exe, args)
            {
                WorkingDirectory = cwd, UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            // the program's own uv and Git come first, so nothing that happens to be on this PC is used by mistake
            string path = Path.Combine(root, "tools", "uv") + ";" + (File.Exists(OwnGit) ? Path.Combine(root, "tools", "git", "cmd") + ";" : "") + Environment.GetEnvironmentVariable("PATH");
            si.EnvironmentVariables["PATH"] = path;
            si.EnvironmentVariables["GIT_TERMINAL_PROMPT"] = "0";
            Status(Cap(what) + " ...");
            List<string> tail = new List<string>();
            using (Process p = new Process { StartInfo = si })
            {
                DataReceivedEventHandler take = delegate(object s, DataReceivedEventArgs e)
                {
                    if (string.IsNullOrWhiteSpace(e.Data)) return;
                    lock (tail) { tail.Add(e.Data.Trim()); if (tail.Count > 12) tail.RemoveAt(0); }
                    Line(e.Data.TrimEnd());
                };
                p.OutputDataReceived += take; p.ErrorDataReceived += take;
                try { p.Start(); }
                catch (Exception e) { throw new Exception(what + " could not be started (" + Path.GetFileName(exe) + "): " + e.Message); }
                p.BeginOutputReadLine(); p.BeginErrorReadLine();
                DateTime began = DateTime.Now;
                while (!p.WaitForExit(3000))
                {
                    int mins = (int)(DateTime.Now - began).TotalMinutes;
                    string so = mins > 0 ? " (" + mins + " min so far)" : "";
                    if (watch != null) { long mb = FolderMB(watch); if (mb > 0) so = ": " + mb + " MB downloaded" + so; }
                    Status(Cap(what) + so + " ...");
                }
                p.WaitForExit();   // lets the last lines arrive
                if (p.ExitCode != 0)
                {
                    string why; lock (tail) why = string.Join(" | ", tail.ToArray());
                    if (why.Length > 500) why = why.Substring(why.Length - 500);
                    throw new Exception(what + " failed (exit code " + p.ExitCode + "): " + why);
                }
            }
            Status("");
        }

        static long FolderMB(string dir)
        {
            long n = 0;
            try { foreach (string f in Directory.GetFiles(dir, "*", SearchOption.AllDirectories)) n += new FileInfo(f).Length; } catch { }
            return n / 1048576;
        }

        static string OnPath(string name)
        {
            foreach (string d in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
            {
                try { if (d.Trim().Length > 0 && File.Exists(Path.Combine(d.Trim(), name))) return Path.Combine(d.Trim(), name); } catch { }
            }
            return null;
        }
        static string Q(string s) { return "\"" + s.TrimEnd('\\') + "\""; }
        static string Cap(string s) { return s.Length == 0 ? s : char.ToUpper(s[0]) + s.Substring(1); }
        static string Deepest(Exception e) { while (e.InnerException != null) e = e.InnerException; return e.Message; }

        // What went wrong, in words a person can act on. The technical text stays in the log box above.
        static string Plain(string m)
        {
            string low = m.ToLowerInvariant();
            if (low.Contains("remote name could not be resolved") || low.Contains("unable to connect") || low.Contains("timed out") || low.Contains("could not resolve host") ||
                low.Contains("connection was closed") || low.Contains("network") || low.Contains("failed to fetch") || low.Contains("error sending request"))
                return "The internet connection dropped, or the site did not answer. Check that the internet works, then press Try again: it carries on where it stopped.";
            if (low.Contains("not enough space") || low.Contains("no space left") || low.Contains("disk is full"))
                return "The disk is full. Free some space (or run the installer again and choose another drive), then press Try again.";
            if (low.Contains("access") && low.Contains("denied") || low.Contains("being used by another process"))
                return "Windows would not let a file be written: it is open in another program, or an antivirus is holding it. Close other copies of Stream Clipper, wait a minute, then press Try again.";
            if (low.Contains("already holds something"))
                return m + ".";
            return "A step did not work. The last lines in the box above say why. Press Try again; if it stops at the same place, copy those lines and send them to whoever gave you this installer.";
        }

        // ---------- talking to the window from the working thread ----------
        void Ui(Action a) { try { if (IsHandleCreated && !IsDisposed) BeginInvoke(a); } catch { } }
        void Line(string t)
        {
            // also kept in a file beside the program, so a person can send it to whoever helps them
            try { lock (steps) File.AppendAllText(Path.Combine(root, "install-log.txt"), DateTime.Now.ToString("HH:mm:ss") + " " + t + "\r\n", Encoding.UTF8); } catch { }
            Ui(delegate { if (log != null) { log.AppendText(t + "\r\n"); } });
        }
        void Status(string t) { Ui(delegate { status.ForeColor = Color.FromArgb(144, 150, 160); status.Text = t; }); }
        void Say(string t, bool bad) { status.ForeColor = bad ? Color.FromArgb(241, 154, 155) : Color.FromArgb(144, 150, 160); status.Text = t; }
        void Mark(Step s, string mark, Color c, Color text) { Ui(delegate { s.Mark.Text = mark; s.Mark.ForeColor = c; s.Text.ForeColor = text; }); }
        int at;
        void Progress(int stepIndex, int within) { at = stepIndex; Ui(delegate { bar.Value = Math.Min(1000, stepIndex * 1000 / steps.Count + within / steps.Count); }); }
        void StepProgress(int thousandths) { int i = at; Ui(delegate { bar.Value = Math.Min(1000, i * 1000 / steps.Count + thousandths / steps.Count); }); }
    }
}
