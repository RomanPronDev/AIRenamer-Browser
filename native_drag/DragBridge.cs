// SPDX-License-Identifier: GPL-3.0-only
// A persistent STA drag source. No hooks, overlay, timer, or browser injection.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class DragBridge {
    [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    static extern uint DragQueryFileW(IntPtr drop, uint index, StringBuilder buffer, uint capacity);
    [DllImport("ole32.dll")] static extern void ReleaseStgMedium(ref STGMEDIUM medium);
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    static readonly object OutputLock = new object();
    static Control source;
    static bool dragging;

    static void Reply(object message) {
        lock (OutputLock) { Console.Out.WriteLine(Json.Serialize(message)); Console.Out.Flush(); }
    }
    static DataObject FileData(string path) {
        var data = new DataObject();
        data.SetData(DataFormats.FileDrop, true, new string[] { path });
        return data;
    }
    static string[] NativePaths(DataObject data) {
        var format = new FORMATETC { cfFormat = 15, dwAspect = DVASPECT.DVASPECT_CONTENT,
            lindex = -1, tymed = TYMED.TYMED_HGLOBAL, ptd = IntPtr.Zero };
        var native = (System.Runtime.InteropServices.ComTypes.IDataObject)data;
        STGMEDIUM medium;
        native.GetData(ref format, out medium);
        try {
            uint count = DragQueryFileW(medium.unionmember, UInt32.MaxValue, null, 0);
            var paths = new string[count];
            for (uint i = 0; i < count; i++) {
                uint size = DragQueryFileW(medium.unionmember, i, null, 0) + 1;
                var name = new StringBuilder((int)size);
                DragQueryFileW(medium.unionmember, i, name, size);
                paths[i] = name.ToString();
            }
            return paths;
        } finally { ReleaseStgMedium(ref medium); }
    }
    static void Command(Dictionary<string, object> message) {
        string id = Convert.ToString(message["id"]);
        string action = Convert.ToString(message["action"]);
        bool began = false;
        try {
            if (action == "ping") { Reply(new { id, ok = true, ready = true }); return; }
            if (dragging) throw new InvalidOperationException("A native drag is already active.");
            if (action == "probe") {
                ShowProbe(Path.GetFullPath(Convert.ToString(message["path"])));
                Reply(new { id, ok = true, opened = true }); return;
            }
            string path = Path.GetFullPath(Convert.ToString(message["path"]));
            if (!File.Exists(path)) throw new FileNotFoundException("The local file no longer exists.");
            if (action == "inspect") {
                var data = FileData(path);
                Reply(new { id, ok = true, format = DataFormats.FileDrop,
                    paths = data.GetData(DataFormats.FileDrop),
                    cfHdrop = 15, nativePaths = NativePaths(data),
                    apartment = Thread.CurrentThread.GetApartmentState().ToString() });
                return;
            }
            if (action != "begin") throw new InvalidOperationException("Unknown drag command.");
            long now = (long)(DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalMilliseconds;
            if (now > Convert.ToInt64(message["expires"]))
                throw new InvalidOperationException("Native drag request expired. Hover the file, then try again.");
            // Never start a drag after a quick release or a delayed browser request.
            if ((GetAsyncKeyState(1) & 0x8000) == 0) {
                Reply(new { id, ok = false, error = "Mouse button was released before native drag started. Try a slower drag." });
                return;
            }
            dragging = true;
            began = true;
            Reply(new { id, ok = true, started = true });
            var effect = source.DoDragDrop(FileData(path), DragDropEffects.Copy);
            Reply(new { id, phase = "finished", effect = effect.ToString(), ok = true });
        } catch (Exception error) {
            if (began) Reply(new { id, phase = "finished", ok = false, error = error.Message });
            else Reply(new { id, ok = false, error = error.Message });
        } finally { if (began) dragging = false; }
    }
    static void ReadCommands() {
        try {
            string line;
            while ((line = Console.ReadLine()) != null) {
                var message = Json.Deserialize<Dictionary<string, object>>(line);
                source.BeginInvoke(new Action(() => Command(message)));
            }
        } catch (Exception error) { Console.Error.WriteLine(error.Message); }
        finally {
            // Closing Chrome/native messaging must not leave an idle helper behind.
            Environment.Exit(0);
        }
    }
    static Form ShowProbe(string report) {
        var form = new Form { Text = "AIRenamer native drop probe", AllowDrop = true,
            ClientSize = new Size(460, 210), StartPosition = FormStartPosition.CenterScreen };
        var label = new Label { Text = "Drop a file from the Chrome panel here.\n\nOnly native FileDrop paths count as success.",
            Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter, AllowDrop = true };
        DragEventHandler enter = (sender, args) => {
            args.Effect = args.Data.GetDataPresent(DataFormats.FileDrop) ? DragDropEffects.Copy : DragDropEffects.None;
        };
        DragEventHandler drop = (sender, args) => {
            var paths = args.Data.GetData(DataFormats.FileDrop) as string[];
            var result = new { paths, formats = args.Data.GetFormats(false), utc = DateTime.UtcNow.ToString("o") };
            File.AppendAllText(report, Json.Serialize(result) + Environment.NewLine);
            label.Text = paths == null ? "No native paths received." : String.Join(Environment.NewLine, paths);
        };
        form.DragEnter += enter; label.DragEnter += enter;
        form.DragDrop += drop; label.DragDrop += drop;
        form.Controls.Add(label); form.Show(); return form;
    }
    [STAThread] static void Main(string[] args) {
        Console.InputEncoding = new System.Text.UTF8Encoding(false);
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        if (args.Length == 2 && args[0] == "--probe") { Application.Run(ShowProbe(Path.GetFullPath(args[1]))); return; }
        Application.OleRequired();
        source = new Control();
        var handle = source.Handle; // Message-only use: the control is never shown.
        Reply(new { id = "ready", ok = true, ready = true });
        var reader = new Thread(ReadCommands) { IsBackground = true };
        reader.Start(); Application.Run();
    }
}
