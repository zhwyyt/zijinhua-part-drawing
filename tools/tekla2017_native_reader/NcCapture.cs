using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using Tekla.Structures.Model;
using ModelOperation = Tekla.Structures.Model.Operations.Operation;

internal static partial class NativeReader
{
    private static object FileEvidence(string path)
    {
        var bytes = File.ReadAllBytes(path);
        using (var sha = SHA256.Create())
            return Obj("name", Path.GetFileName(path), "sha256", BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(),
                "bytesBase64", Convert.ToBase64String(bytes), "lastWriteUtc", File.GetLastWriteTimeUtc(path).ToString("o"));
    }

    private static List<int> SelectedIds(Tekla.Structures.Model.UI.ModelObjectSelector selector, ArrayList objects)
    {
        var ids = new List<int>();
        var selected = selector.GetSelectedObjects();
        while (selected.MoveNext())
        {
            var item = selected.Current as ModelObject;
            if (item == null) throw new InvalidOperationException("Selected object unavailable");
            ids.Add(item.Identifier.ID);
            if (objects != null) objects.Add(item);
        }
        return ids;
    }

    // Explicit opt-in export: never number parts, edit settings or write drawing/model objects.
    [MethodImpl(MethodImplOptions.NoInlining)]
    private static int CaptureNc(Dictionary<string, string> options)
    {
        if (!options.ContainsKey("ids") || !options.ContainsKey("nc-settings-file"))
            throw new ArgumentException("--capture-nc requires --ids and --nc-settings-file");
        string settings = Path.GetFullPath(options["nc-settings-file"]);
        if (!File.Exists(settings) || !String.Equals(Path.GetExtension(settings), ".ncfs", StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("Existing .ncfs settings file required");
        string destination = Path.GetFullPath(options["out"]);
        if (Directory.Exists(destination) || File.Exists(destination)) throw new IOException("Choose a new output directory");
        var model = new Model();
        if (!model.GetConnectionStatus()) throw new InvalidOperationException("Model disconnected");
        var ids = options["ids"].Split(',').Select(Int32.Parse).ToArray();
        if (ids.Length == 0 || ids.Any(id => id <= 0) || ids.Distinct().Count() != ids.Length)
            throw new ArgumentException("Unique positive part IDs required");
        Directory.CreateDirectory(destination);
        string template = Path.GetFileNameWithoutExtension(settings);
        var root = Obj("schemaVersion", "teklaNcCapture.v1", "startedAtUtc", DateTime.UtcNow.ToString("o"),
            "requestedIds", ids, "templateName", template, "settingsPath", settings,
            "settingsBinding", "UNCONFIRMED", "settingsBefore", FileEvidence(settings));
        var readOptions = new Dictionary<string, string>(options);
        string beforePath = Path.Combine(destination, "before.json");
        readOptions["out"] = beforePath;
        int beforeCode = Read(readOptions);
        root["before"] = FileEvidence(beforePath);
        root["beforeExitCode"] = beforeCode;
        string ncDir = Path.Combine(destination, "nc");
        Directory.CreateDirectory(ncDir);
        var selector = new Tekla.Structures.Model.UI.ModelObjectSelector();
        var originalObjects = new ArrayList();
        var originalIds = SelectedIds(selector, originalObjects);
        root["selectionBefore"] = originalIds;
        root["apiReturn"] = false;
        var parts = new ArrayList();
        var numbering = new List<object>();
        bool attemptedSelection = false;
        bool restored = false;
        try
        {
            if (beforeCode != 0) throw new InvalidOperationException("Pre-export evidence incomplete");
            foreach (int id in ids)
            {
                var part = model.SelectModelObject(new Tekla.Structures.Identifier(id)) as Part;
                if (part == null) throw new InvalidOperationException("Part missing: " + id);
                bool current = ModelOperation.IsNumberingUpToDate(part);
                numbering.Add(Obj("id", id, "upToDate", current));
                if (!current) throw new InvalidOperationException("Numbering not current; no renumbering attempted: " + id);
                parts.Add(part);
            }
            attemptedSelection = true;
            if (!selector.Select(parts)) throw new InvalidOperationException("Temporary API selection failed");
            var selected = SelectedIds(selector, null);
            root["selectionForExport"] = selected;
            if (!ids.OrderBy(id => id).SequenceEqual(selected.OrderBy(id => id)))
                throw new InvalidOperationException("Selected IDs differ from requested IDs");
            string log = Path.Combine(model.GetInfo().ModelPath, "dstv_nc.log");
            root["exportLogBefore"] = Capture("exportLogBefore", () => File.Exists(log) ? FileEvidence(log) : null);
            root["exportStartedAtUtc"] = DateTime.UtcNow.ToString("o");
            root["apiReturn"] = ModelOperation.CreateNCFilesFromSelected(template, ncDir + Path.DirectorySeparatorChar);
            root["exportCompletedAtUtc"] = DateTime.UtcNow.ToString("o");
            root["exportLog"] = Capture("exportLog", () => FileEvidence(log));
        }
        catch (Exception exception) { root["captureError"] = exception.ToString(); }
        finally
        {
            try
            {
                bool restoreReturn = !attemptedSelection || selector.Select(originalObjects);
                var afterIds = SelectedIds(selector, null);
                root["selectionAfter"] = afterIds;
                restored = restoreReturn && originalIds.OrderBy(id => id).SequenceEqual(afterIds.OrderBy(id => id));
            }
            catch (Exception exception) { root["restoreError"] = exception.ToString(); }
            root["selectionRestored"] = restored;
        }
        root["numbering"] = numbering;
        root["files"] = Directory.GetFiles(ncDir).OrderBy(path => path, StringComparer.Ordinal).Select(FileEvidence).ToArray();
        root["settingsAfter"] = Capture("settingsAfter", () => FileEvidence(settings));
        string afterPath = Path.Combine(destination, "after.json");
        readOptions["out"] = afterPath;
        int afterCode = Read(readOptions);
        root["after"] = FileEvidence(afterPath);
        root["afterExitCode"] = afterCode;
        bool complete = beforeCode == 0 && afterCode == 0 && (bool)root["apiReturn"] && restored
            && !root.ContainsKey("captureError") && Directory.GetFiles(ncDir, "*.nc1").Length > 0
            && root.ContainsKey("exportLog") && (string)((Dictionary<string, object>)root["exportLog"])["status"] == "READ";
        root["captureStatus"] = complete ? "CAPTURED" : "PARTIAL";
        root["completedAtUtc"] = DateTime.UtcNow.ToString("o");
        Save(Path.Combine(destination, "capture.json"), root);
        Console.WriteLine("NC capture=" + root["captureStatus"] + "; " + destination);
        return complete ? 0 : 1;
    }
}
