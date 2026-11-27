// Stream-Clipper-Setup.exe: installs Stream Clipper on a Windows PC that has nothing on it yet.
//
// It needs no Python and no Git, and asks for no administrator rights (one exception: on a PC without Microsoft's Visual C++
// runtime, setup.ps1 runs Microsoft's installer for it and Windows asks to allow that). Everything else goes into two folders the person chooses:
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
            try { ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072 | (SecurityProtocolType)12288; }   // TLS 1.2 and 1.3
            catch { try { ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072; } catch { } }             // an older .NET knows 1.2 only
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
        volatile bool stopping;        // the person chose Stop: end what is running and say nothing more
        volatile Process running;      // the program a step is waiting for right now

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
            bool dataGiven = false;
            foreach (string a in args) if (a.ToLowerInvariant() == "/data") dataGiven = true;
            string had = SavedData(folder);
            if (had != null && !dataGiven) dataDir = had;
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
            choose.Controls.Add(Caption("Where the program goes (about 2 GB with Python and its parts)", 26, 4));
            folderBox = Box(folder, 26, 28); choose.Controls.Add(folderBox);
            browse1 = Btn("Choose...", 520, 27, 94); browse1.Click += delegate { Pick(folderBox); }; choose.Controls.Add(browse1);
            choose.Controls.Add(Caption("Where your clips, downloads and models go (pick the drive with the most free space; 5 GB or more)", 26, 76));
            dataBox = Box(dataDir, 26, 100); choose.Controls.Add(dataBox);
            browse2 = Btn("Choose...", 520, 99, 94); browse2.Click += delegate { Pick(dataBox); }; choose.Controls.Add(browse2);
            Label what = new Label { Location = new Point(26, 150), Size = new Size(590, 190), ForeColor = Color.FromArgb(144, 150, 160),
                Text = "What happens when you press Install:\r\n\r\n" +
                       "  1.  A small tool called uv is fetched; it installs Python for this program only.\r\n" +
                       "  2.  The program is fetched from its page on GitHub.\r\n" +
                       "  3.  Python and the program's parts are installed (the longest step).\r\n" +
                       "  4.  The video tools and models are downloaded (about 300 MB).\r\n" +
                       "  5.  A shortcut is put on your desktop.\r\n\r\n" +
                       "Nothing else is installed outside these two folders. If Windows lacks Microsoft's Visual C++ runtime (captions need it), " +
                       "Windows asks once to let Microsoft's own installer add it. It needs the internet and takes 5 to 20 minutes." };
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
                if (worker == null || !worker.IsBusy) return;
                if (MessageBox.Show(this, "The install is still running. Stop it?\r\n\r\nYou can run this installer again later: it carries on where it stopped.", "Install Stream Clipper",
                                    MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.No) { e.Cancel = true; return; }
                // Stop means stop: the download or the program a step started is ended with everything it started.
                // Left running, it would go on downloading with no window, and the next run would collide with it.
                stopping = true;
                KillRunning();
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
        // The data folder an installed copy already uses (from its config.local.yaml), or null.
        static string SavedData(string programFolder)
        {
            try
            {
                string app = Path.Combine(programFolder, "app"), f = Path.Combine(app, "config.local.yaml");
                if (!File.Exists(f)) return null;
                foreach (string line in File.ReadAllLines(f))
                {
                    Match m = Regex.Match(line, "^\\s*data_dir\\s*:\\s*(.+?)\\s*$");
                    if (!m.Success) continue;
                    string v = m.Groups[1].Value.Trim('"', '\'');
                    if (v.Length == 0) return null;
                    return Path.GetFullPath(Path.IsPathRooted(v) ? v : Path.Combine(app, v));
                }
            }
            catch { }
            return null;
        }
        static bool SameFolder(string a, string b)
        {
            return string.Equals(a.TrimEnd('\\', '/'), b.TrimEnd('\\', '/'), StringComparison.OrdinalIgnoreCase);
        }
        void KillRunning()
        {
            Process p = running;
            if (p == null) return;
            try
            {
                ProcessStartInfo k = new ProcessStartInfo("taskkill", "/PID " + p.Id + " /T /F") { UseShellExecute = false, CreateNoWindow = true };
                using (Process t = Process.Start(k)) t.WaitForExit(15000);
            }
            catch { try { p.Kill(); } catch { } }
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
            string kept = SavedData(root);
            if (kept != null && !SameFolder(kept, data))
            {   // the installed copy reads its data folder from its own settings: a second folder would only confuse it
                dataBox.Text = kept;
                Say("The copy in this program folder already keeps its clips in " + kept + ". That folder is used again; press Install once more.", true); return;
            }
            try
            {
                Directory.CreateDirectory(root); Directory.CreateDirectory(data);
                DriveInfo d = new DriveInfo(Path.GetPathRoot(data));
                if (d.AvailableFreeSpace < 5L * 1024 * 1024 * 1024)
                {
                    Say("The drive " + d.Name + " has less than 5 GB free. Choose a folder on another drive for your clips, or free some space.", true); return;
                }
                DriveInfo pd = new DriveInfo(Path.GetPathRoot(root));
                if (!string.Equals(pd.Name, d.Name, StringComparison.OrdinalIgnoreCase) && pd.AvailableFreeSpace < 3L * 1024 * 1024 * 1024)
                {
                    Say("The drive " + pd.Name + " has less than 3 GB free, and the program with Python needs about 2 GB there. Choose another program folder, or free some space.", true); return;
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
            // "Already there" must mean whole: a file left half-written by a cut-off run does not count.
            Add("Fetch uv, the tool that installs Python", delegate { return File.Exists(Uv) && Answers(Uv, "--version"); }, GetUv);
            Add("Make sure Git is there (for the Update button)", delegate { return (File.Exists(OwnGit) && Answers(OwnGit, "--version")) || (!portableGit && OnPath("git.exe") != null); }, GetGit);
            // never "already there": a copy that is already here is brought up to date instead (see UpdateProgram), so that running
            // the installer again over an old copy repairs it with the newest files, not with the old ones
            Add("Fetch the program", delegate { return false; }, GetProgram);
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

        bool ProgramWhole()
        {
            if (!Directory.Exists(Path.Combine(App, ".git"))) return false;
            foreach (string f in new string[] { "pyproject.toml", "uv.lock", "setup.ps1", "Stream-Clipper.cmd", Path.Combine("src", "clipper", "cli.py") })
                if (!File.Exists(Path.Combine(App, f))) return false;
            return true;
        }

        // Whether a program starts and ends well when asked for its version (a half-unpacked one does not).
        bool Answers(string exe, string args)
        {
            try
            {
                ProcessStartInfo si = new ProcessStartInfo(exe, args) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
                using (Process p = Process.Start(si))
                {
                    p.StandardOutput.ReadToEnd();
                    if (!p.WaitForExit(20000)) { try { p.Kill(); } catch { } return false; }
                    return p.ExitCode == 0;
                }
            }
            catch { return false; }
        }

        // Removes a folder with everything in it. Git marks its own files read-only, and Windows refuses to delete those until the mark is taken off.
        static void Wipe(string dir)
        {
            if (!Directory.Exists(dir)) return;
            foreach (string f in Directory.GetFiles(dir, "*", SearchOption.AllDirectories))
            {
                try { File.SetAttributes(f, FileAttributes.Normal); } catch { }
            }
            Directory.Delete(dir, true);
        }

        void OnEnd(Exception error)
        {
            if (stopping || IsDisposed) return;   // the person closed the window: nothing more to show
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
            bool link = false;
            try { link = File.Exists(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Stream Clipper.lnk")); } catch { }
            string rest = "In the program, click Set up in the menu for the last steps (captions, and signing in to Claude or ChatGPT if you want them). It shows a warning sign while something is missing.";
            sub.Text = test ? "Test run: no shortcut was made and the program is not started." :
                link ? "A shortcut called Stream Clipper is on your desktop. " + rest :
                       "Windows did not let a shortcut be put on your desktop. Start the program with Stream-Clipper.cmd in " + App + ". " + rest;
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
            string zip = Path.Combine(root, "tools", "uv.zip"), dir = Path.Combine(root, "tools", "uv"), part = dir + ".part";
            Directory.CreateDirectory(Path.Combine(root, "tools"));
            Download(UvZip, zip, "uv");
            Wipe(part);
            Unzip(zip, part);
            File.Delete(zip);
            string got = Path.Combine(part, "uv.exe");
            if (!File.Exists(got))
            {   // some releases keep the files in a folder of their own
                foreach (string f in Directory.GetFiles(part, "uv.exe", SearchOption.AllDirectories)) { File.Copy(f, got, true); break; }
            }
            if (!File.Exists(got)) throw new Exception("uv.exe was not in the download from " + UvZip);
            Wipe(dir);
            Directory.Move(part, dir);
        }

        void GetGit()
        {
            Line("Asking GitHub for the newest portable Git...");
            string url = null;
            try
            {
                string json;
                using (WebClient c = Client()) json = c.DownloadString(GitLatest);
                Match m = Regex.Match(json, "\"browser_download_url\"\\s*:\\s*\"(https://[^\"]*/MinGit-[0-9.]+-64-bit\\.zip)\"");
                if (m.Success) url = m.Groups[1].Value;
            }
            catch (Exception e)
            {   // GitHub answers this question only 60 times an hour for one internet address, and a campus or a phone network shares one
                Line("GitHub did not answer that question (" + Deepest(e) + "); asking another way...");
            }
            if (url == null) url = GitByTag();
            if (url == null) throw new Exception("GitHub would not say where the newest portable Git is (it is refusing for a while). Wait an hour and press Try again, " +
                                                 "or install Git from https://git-scm.com first: this step is then skipped");
            string zip = Path.Combine(root, "tools", "git.zip"), dir = Path.Combine(root, "tools", "git"), part = dir + ".part";
            Download(url, zip, "portable Git");
            Wipe(part);
            Unzip(zip, part);
            File.Delete(zip);
            if (!File.Exists(Path.Combine(part, "cmd", "git.exe"))) throw new Exception("git.exe was not in the download " + url);
            Wipe(dir);
            Directory.Move(part, dir);
        }

        // The newest portable Git without GitHub's question-and-answer service: the "latest" page forwards to the
        // release's own page, whose name holds the version, and the download's name is made from that.
        string GitByTag()
        {
            try
            {
                HttpWebRequest q = (HttpWebRequest)WebRequest.Create("https://github.com/git-for-windows/git/releases/latest");
                q.AllowAutoRedirect = false; q.UserAgent = Agent; q.Timeout = 30000;
                string to;
                using (HttpWebResponse r = (HttpWebResponse)q.GetResponse()) to = r.Headers["Location"] ?? "";
                Match m = Regex.Match(to, "/releases/tag/(v([0-9]+\\.[0-9]+\\.[0-9]+)\\.windows\\.([0-9]+))$");
                if (!m.Success) return null;
                string ver = m.Groups[2].Value + (m.Groups[3].Value == "1" ? "" : "." + m.Groups[3].Value);
                return "https://github.com/git-for-windows/git/releases/download/" + m.Groups[1].Value + "/MinGit-" + ver + "-64-bit.zip";
            }
            catch (Exception e) { Line("That did not work either: " + Deepest(e)); return null; }
        }

        string Git { get { return File.Exists(OwnGit) ? OwnGit : (OnPath("git.exe") ?? "git"); } }

        void GetProgram()
        {
            if (ProgramWhole()) { UpdateProgram(); return; }   // a whole copy is not fetched again, only brought up to date
            string[] keep = { "config.local.yaml", ".env" };   // a person's own settings and keys survive a fetch that is done again
            if (Directory.Exists(App))
            {
                if (Directory.GetFileSystemEntries(App).Length > 0 && !Directory.Exists(Path.Combine(App, ".git")))
                    throw new Exception("The folder " + App + " already holds something that is not Stream Clipper. Choose another program folder, or empty that one");
                // a fetch that was cut off (a whole copy was dealt with above): it is taken again from the start
                foreach (string k in keep)
                {
                    string f = Path.Combine(App, k), aside = Path.Combine(root, k + ".kept");
                    if (File.Exists(f)) File.Copy(f, aside, true);
                }
                Wipe(App);
            }
            string args = "clone --progress " + (source == Repo ? "--depth 50 " : "") + Q(source) + " " + Q(App);
            Exec(Git, args, root, "fetching the program");
            foreach (string k in keep)
            {
                string aside = Path.Combine(root, k + ".kept");
                if (File.Exists(aside)) { File.Copy(aside, Path.Combine(App, k), true); File.Delete(aside); }
            }
        }

        // The copy that is already here is brought up to date the way the program's own Update button does it: the newest version is
        // asked for and taken only when that is a plain step forward (a fast-forward). Nothing of the person's is touched: their
        // settings, their keys and every file Git does not know stay where they are. When it cannot be done (no internet, files of the
        // program changed on this PC, a copy that went its own way) the reason is written down and the install carries on with the copy as it is.
        void UpdateProgram()
        {
            Line("The program is already here: bringing it up to date...");
            string said, bad, old, now, origin, count, changed;
            if (AskGit("rev-parse HEAD", 60, false, out old, out bad) != 0) { NotUpdated("Git could not read this copy (" + Why(bad, old) + ")"); return; }
            if (AskGit("remote get-url origin", 60, false, out origin, out bad) != 0) { NotUpdated("this copy has no address to fetch new versions from"); return; }
            string place = origin.ToLowerInvariant().Contains("github.com") ? "GitHub" : origin.Trim();   // with /source it is a folder on this PC
            Status("Asking " + place + " for the newest version ...");
            int code = AskGit("fetch --quiet origin", 300, true, out said, out bad);   // quiet: only what goes wrong is printed
            Status("");
            if (code != 0)
            {   // a repair without the internet must still work: the copy that is here is whole
                Line(place + " could not be reached (" + Why(bad, said) + "), so the program was not brought up to date. The copy that is here is used as it is. " +
                     "When the internet works again, run this installer once more or press Update in the program.");
                return;
            }
            if (AskGit("rev-parse --verify --quiet refs/remotes/origin/main", 60, false, out now, out bad) != 0) { NotUpdated(place + " has no version called main"); return; }
            old = old.Trim(); now = now.Trim();
            if (now == old) { Line("Already the newest version."); return; }
            if (AskGit("rev-list --count " + old + ".." + now, 60, false, out count, out bad) != 0)
            { NotUpdated("Git could not compare this copy with the newest version (" + Why(bad, count) + ")"); return; }
            count = count.Trim();
            if (count == "0") { Line("Already the newest version (this copy has changes of its own on top of it)."); return; }
            code = AskGit("merge-base --is-ancestor " + old + " " + now, 60, false, out said, out bad);
            if (code == 1) { NotUpdated("this copy and " + place + " have gone separate ways"); return; }
            if (code != 0) { NotUpdated("Git could not compare this copy with the newest version (" + Why(bad, said) + ")"); return; }
            if (AskGit("status --porcelain --untracked-files=no", 120, false, out changed, out bad) != 0)
            { NotUpdated("Git could not say whether files of the program were changed (" + Why(bad, changed) + ")"); return; }
            if (changed.Trim().Length > 0) { NotUpdated("files of the program were changed on this computer (" + Names(changed) + ")"); return; }
            Status("Taking the new version ...");
            // fast-forward only: Git itself refuses, and changes nothing, if a file of the person's would be written over
            code = AskGit("merge --ff-only --no-stat origin/main", 0, true, out said, out bad);
            Status("");
            if (code != 0) { NotUpdated("Git would not take the new version (" + Why(bad, said) + ")"); return; }
            Line("Updated: " + count + " change(s) brought in.");
        }

        // Not a failure of the install: the copy that is here is whole and is used as it is. One plain line says why and what can be done.
        void NotUpdated(string why)
        {
            Line("The copy that is here could not be brought up to date: " + why + ". It is used as it is. " +
                 "To get the newest version, run this installer again and choose a new, empty program folder (the same data folder can be kept).");
        }

        // Asks Git one thing about the copy in the app folder and waits for the answer. Unlike Exec, a "no" from Git does not stop the
        // install: the exit code is handed back with what Git printed (``said``) and what it complained about (``bad``), and the caller decides.
        // ``seconds``: how long to wait before giving up (0: as long as it takes). ``show``: whether Git's lines also go into the log box.
        int AskGit(string args, int seconds, bool show, out string said, out string bad)
        {
            ProcessStartInfo si = new ProcessStartInfo(Git, args)
            {
                WorkingDirectory = App, UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            // the same two settings Exec gives Git: the program's own Git first, and never a question nobody can answer
            si.EnvironmentVariables["PATH"] = (File.Exists(OwnGit) ? Path.Combine(root, "tools", "git", "cmd") + ";" : "") + Environment.GetEnvironmentVariable("PATH");
            si.EnvironmentVariables["GIT_TERMINAL_PROMPT"] = "0";
            StringBuilder o = new StringBuilder(), e = new StringBuilder();
            bool late = false; int code;
            using (Process p = new Process { StartInfo = si })
            {
                p.OutputDataReceived += delegate(object s, DataReceivedEventArgs d)
                {
                    if (d.Data == null) return;
                    lock (o) o.Append(d.Data).Append('\n');
                    if (show && d.Data.Trim().Length > 0) Line(d.Data.TrimEnd());
                };
                p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs d)
                {
                    if (d.Data == null) return;
                    lock (e) e.Append(d.Data).Append('\n');
                    if (show && d.Data.Trim().Length > 0) Line(d.Data.TrimEnd());
                };
                try { p.Start(); }
                catch (Exception x) { said = ""; bad = "Git could not be started: " + x.Message; return -1; }
                running = p;
                p.BeginOutputReadLine(); p.BeginErrorReadLine();
                DateTime began = DateTime.Now;
                while (!p.WaitForExit(500))
                {
                    // a connection that goes silent must not hold the install up for ever
                    if (seconds > 0 && !late && (DateTime.Now - began).TotalSeconds > seconds) { late = true; KillRunning(); }
                }
                p.WaitForExit();   // lets the last lines arrive
                running = null;
                code = p.ExitCode;
            }
            if (stopping) throw new Exception("stopped");
            lock (o) said = o.ToString().TrimEnd();
            lock (e) bad = e.ToString().TrimEnd();
            if (late) { bad = "no answer within " + (seconds >= 120 ? (seconds / 60) + " minutes" : seconds + " seconds"); return -1; }
            return code;
        }

        // Git's reason in one line: its first "fatal:" or "error:" line, or else the last thing it printed.
        static string Why(string bad, string said)
        {
            string last = "";
            foreach (string l in (bad + "\n" + said).Split('\n'))
            {
                string t = l.Trim();
                if (t.Length == 0) continue;
                if (t.StartsWith("fatal:") || t.StartsWith("error:")) { last = t.Substring(6).Trim(); break; }
                last = t;
            }
            last = last.TrimEnd(':', '.');
            if (last.Length > 300) last = last.Substring(0, 300);
            return last.Length == 0 ? "no reason given" : last;
        }

        // The first few names from Git's list of changed files (each line is two letters, a space and the name), for a message.
        static string Names(string list)
        {
            List<string> names = new List<string>();
            foreach (string l in list.Split('\n'))
                if (l.TrimEnd().Length > 3) names.Add(l.TrimEnd().Substring(3).Trim('"'));
            string s = string.Join(", ", names.GetRange(0, Math.Min(3, names.Count)).ToArray());
            return names.Count > 3 ? s + " and " + (names.Count - 3) + " more" : s;
        }

        void SyncParts()
        {
            Line("This is the longest step: Python and the program's parts are downloaded (several hundred MB).");
            // NVIDIA's libraries (about 1 GB), once chosen, stay: a plain sync would remove them and captions would fall back to the processor
            bool gpu = Directory.Exists(Path.Combine(App, ".venv", "Lib", "site-packages", "nvidia", "cublas"));
            Exec(Uv, "sync --frozen" + (gpu ? " --extra gpu" : ""), App, "installing Python and the program's parts");
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
            Exception fault = null; bool done = false; int last = -1; bool stalled = false;
            DateTime heard = DateTime.Now;
            using (WebClient c = Client())
            {
                c.DownloadProgressChanged += delegate(object s, DownloadProgressChangedEventArgs e)
                {
                    heard = DateTime.Now;
                    int pct = e.TotalBytesToReceive > 0 ? (int)(e.BytesReceived * 100 / e.TotalBytesToReceive) : 0;
                    if (pct != last) { last = pct; Status("Downloading " + what + ": " + (e.BytesReceived / 1048576) + " MB" + (e.TotalBytesToReceive > 0 ? " of " + (e.TotalBytesToReceive / 1048576) + " MB" : "")); StepProgress(pct * 10); }
                };
                c.DownloadFileCompleted += delegate(object s, AsyncCompletedEventArgs e) { fault = e.Error; done = true; };
                c.DownloadFileAsync(new Uri(url), to + ".part");
                while (!done)
                {
                    System.Threading.Thread.Sleep(120);
                    if (stopping) c.CancelAsync();
                    // a connection that goes silent would otherwise leave "12 MB of 20 MB" on the screen for ever
                    else if (!stalled && (DateTime.Now - heard).TotalSeconds > 90) { stalled = true; c.CancelAsync(); }
                }
            }
            if (stopping) { try { File.Delete(to + ".part"); } catch { } throw new Exception("stopped"); }
            if (stalled) { try { File.Delete(to + ".part"); } catch { } throw new Exception("downloading " + what + " timed out: nothing arrived for 90 seconds"); }
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
                    if (!target.StartsWith(Path.GetFullPath(dir).TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase)) continue;   // never outside the folder (also not a neighbour whose name starts the same)
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
            // uv keeps Python and its download store inside the program folder, not in the user's profile on C:
            // (Stream-Clipper.cmd sets the same two, so the program and its Update button use the same Python)
            si.EnvironmentVariables["UV_PYTHON_INSTALL_DIR"] = Path.Combine(root, "tools", "python");
            si.EnvironmentVariables["UV_CACHE_DIR"] = Path.Combine(root, "tools", "uv-cache");
            Directory.CreateDirectory(Path.Combine(root, "tools", "python")); Directory.CreateDirectory(Path.Combine(root, "tools", "uv-cache"));
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
                running = p;
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
                running = null;
                if (stopping) throw new Exception("stopped");
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
            if (low.Contains("already holds something") || low.Contains("would not say where the newest portable git"))
                return m + ".";
            if (low.Contains("already says the data folder"))
                return "This copy already keeps its clips in another folder than the one chosen here. Close this installer, start it again, and keep the folder it suggests for your clips.";
            if (low.Contains("(403)") || low.Contains("(429)") || low.Contains("rate limit"))
                return "GitHub is refusing for a while: too many requests came from this internet connection. Wait an hour, then press Try again.";
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
