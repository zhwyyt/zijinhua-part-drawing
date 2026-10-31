// No explicit native writes. Legacy attribute loads can rewrite files in Tekla;
// reject that format before connecting and monitor every remaining load call.
using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using D = Tekla.Structures.Drawing;
using M = Tekla.Structures.Model;

public class ProbeTemplate {
    public string templateId, attributePath;
    public string[] files;
}
public class ProbeJob {
    public string modelPath;
    public int partId;
    public ProbeTemplate[] templates;
}
public static class TemplateLoadProbe {
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer {MaxJsonLength=Int32.MaxValue};
    static string Hash(byte[] bytes) {
        using(var hash=SHA256.Create()) return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
    }
    static string Full(string path) { return Path.GetFullPath(path).TrimEnd('\\','/'); }
    static void Need(bool value, string message) { if(!value) throw new InvalidOperationException(message); }
    static object Point(Tekla.Structures.Geometry3d.Point p) { return new[]{p.X,p.Y,p.Z}; }
    internal static object State(M.Model model, D.DrawingHandler handler, M.Part part) {
        Need(part.Select(), "Target refresh failed");
        var plane=model.GetWorkPlaneHandler().GetCurrentTransformationPlane();
        var matrix=plane.TransformationMatrixToGlobal;
        var active=handler.GetActiveDrawing();
        object drawing=null;
        if(active!=null) {
            Need(active.Select(), "Active drawing refresh failed");
            var single=active as D.SinglePartDrawing;
            drawing=new {type=active.GetType().FullName, active.Mark, active.Name,
                active.IsLocked,active.IsFrozen,active.IsIssued,
                partId=single==null?0:single.PartIdentifier.ID, sheet=single==null?-1:single.SheetNumber};
        }
        string position=""; Need(part.GetReportProperty("PART_POS",ref position),"PART_POS unavailable");
        return new {modelPath=model.GetInfo().ModelPath, active=drawing,
            part=new {id=part.Identifier.ID,guid=part.Identifier.GUID.ToString(),position,
                modified=part.ModificationTime.HasValue?part.ModificationTime.Value.ToUniversalTime().ToString("o"):null,part.Name,
                profile=part.Profile.ProfileString,material=part.Material.MaterialString},
            plane=new[]{Point(matrix.Transform(new Tekla.Structures.Geometry3d.Point())),
                Point(matrix.Transform(new Tekla.Structures.Geometry3d.Point(1,0,0))),
                Point(matrix.Transform(new Tekla.Structures.Geometry3d.Point(0,1,0))),
                Point(matrix.Transform(new Tekla.Structures.Geometry3d.Point(0,0,1)))}};
    }
    internal static object Layout(D.LayoutAttributes layout) {
        return new {size=new[]{layout.SheetSize.Width,layout.SheetSize.Height},
            mode=layout.SizeDefinitionMode.ToString(),autoSize=layout.AutoSizeOptions.ToString(),
            listHidden=layout.ListHiddenObjectsInTemplates};
    }
    static bool FilesUnchanged(List<Dictionary<string,object>> files) {
        bool unchanged=true;
        foreach(var file in files) {
            file["afterSha256"]=Hash(File.ReadAllBytes((string)file["source"]));
            unchanged &= (string)file["sha256"]==(string)file["afterSha256"];
        }
        return unchanged;
    }
    static string[] DirectoryFiles(string directory) {
        return Directory.GetFiles(directory).Select(Path.GetFileName).OrderBy(p=>p,StringComparer.OrdinalIgnoreCase).ToArray();
    }
    static object Load(string name, int partId, Func<bool> unchanged) {
        var result=new Dictionary<string,object>{{"requested",name},{"drawing",null},
            {"drawingError",null},{"layout",null},{"loadReturned",null},{"layoutError",null}};
        D.SinglePartDrawing detached;
        try {
            // Constructor loads properties in memory; Insert is never called.
            detached=new D.SinglePartDrawing(new Tekla.Structures.Identifier(partId),1,name);
            result["drawing"]=new {detached.Name,detached.Title1,detached.Title2,detached.Title3,layout=Layout(detached.Layout)};
        } catch(Exception e) {
            result["drawingError"]=e.GetBaseException().ToString();
            return result;
        }
        if(!unchanged()) return result;
        try {
            var layout=new D.LayoutAttributes(detached);
            if(!unchanged()) return result;
            result["loadReturned"]=layout.LoadAttributes(name);
            result["layout"]=Layout(layout);
        } catch(Exception e) {
            result["layoutError"]=e.GetBaseException().ToString();
        }
        return result;
    }
    static bool Preflight(ProbeJob job,string output) {
        Need(job.partId>0 && job.templates!=null && job.templates.Length>0,"Empty probe job");
        var risks=new List<object>();
        foreach(var item in job.templates) {
            byte[] bytes=File.ReadAllBytes(item.attributePath);
            if(Regex.IsMatch(Encoding.ASCII.GetString(bytes),@"(?m)^wdraw_dial\."))
                risks.Add(new {item.templateId,item.attributePath,sha256=Hash(bytes),reason="LEGACY_ATTRIBUTE_MIGRATION_RISK"});
        }
        if(risks.Count==0)return true;
        Directory.CreateDirectory(output);
        File.WriteAllText(Path.Combine(output,"preflight.json"),Json.Serialize(new {
            schemaVersion="TemplateLoadPreflight.v1",status="INPUT_BLOCKED",nativeApiStarted=false,risks}),new UTF8Encoding(false));
        Console.WriteLine("Blocked before native API: legacy attributes may be rewritten by Tekla. Use an isolated copy/model.");
        return false;
    }
    public static int Main(string[] args) {
        try {
            Need(args.Length==3,"Usage: job.json new-output-directory sdk-directory");
            string output=Full(args[1]); Need(!Directory.Exists(output)&&!File.Exists(output),"Output exists");
            var job=Json.Deserialize<ProbeJob>(File.ReadAllText(args[0],Encoding.UTF8));
            if(!Preflight(job,output))return 1;
            string sdk=Full(args[2]);
            AppDomain.CurrentDomain.AssemblyResolve+=(s,e)=>{
                var path=Path.Combine(sdk,new AssemblyName(e.Name).Name+".dll");
                return File.Exists(path)?Assembly.LoadFrom(path):null;
            };
            if(String.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("SESSIONNAME")))
                Environment.SetEnvironmentVariable("SESSIONNAME","Console");
            return Run(args,job);
        } catch(Exception e) { Console.Error.WriteLine(e); return 2; }
    }
    [MethodImpl(MethodImplOptions.NoInlining)]
    static int Run(string[] args,ProbeJob job) {
        Need(job.templates.Select(t=>t.templateId).Distinct().Count()==job.templates.Length,"Duplicate template");
        string output=Full(args[1]); Need(!Directory.Exists(output)&&!File.Exists(output),"Output exists");
        var model=new M.Model(); var handler=new D.DrawingHandler();
        Need(model.GetConnectionStatus()&&handler.GetConnectionStatus(),"Tekla connection unavailable");
        Need(String.Equals(Full(model.GetInfo().ModelPath),Full(job.modelPath),StringComparison.OrdinalIgnoreCase),"Wrong model");
        var part=model.SelectModelObject(new Tekla.Structures.Identifier(job.partId)) as M.Part;
        Need(part!=null,"Target part unavailable");
        var before=State(model,handler,part);
        Directory.CreateDirectory(output);
        var entries=new List<object>();
        bool filesUnchanged=true;
        int index=0;
        foreach(var item in job.templates) {
            Need(new[]{"零件图10","零件图20","零件图50"}.Contains(item.templateId),"Unsupported template");
            string attribute=Full(item.attributePath);
            Need(Path.GetExtension(attribute)==".wd","Expected .wd attribute");
            Need(item.files!=null && item.files.Select(Full).Distinct(StringComparer.OrdinalIgnoreCase).Count()==item.files.Length,"Duplicate file");
            Need(item.files.Any(p=>String.Equals(Full(p),attribute,StringComparison.OrdinalIgnoreCase)),"Attribute absent from files");
            var snapshots=new List<Dictionary<string,object>>();
            foreach(var file in item.files) {
                string source=Full(file), relative="snapshot-"+(index++).ToString("D3")+Path.GetExtension(source);
                byte[] bytes=File.ReadAllBytes(source);
                File.WriteAllBytes(Path.Combine(output,relative),bytes);
                snapshots.Add(new Dictionary<string,object>{{"source",source},{"snapshot",relative},{"bytes",bytes.Length},{"sha256",Hash(bytes)}});
            }
            string stem=attribute.Substring(0,attribute.Length-3);
            string directory=Path.GetDirectoryName(attribute);
            string[] directoryBefore=DirectoryFiles(directory);
            Func<bool> unchanged=()=>FilesUnchanged(snapshots)&&directoryBefore.SequenceEqual(DirectoryFiles(directory));
            string missing=Path.Combine(Path.GetDirectoryName(stem),"__independent_missing_"+Guid.NewGuid().ToString("N"));
            Need(!File.Exists(missing+".wd"),"Negative control unexpectedly exists");
            var entry=new Dictionary<string,object>{{"templateId",item.templateId},{"attributePath",attribute},
                {"files",snapshots},{"directoryBefore",directoryBefore}};
            string[] keys={"absolute","byName","repeated","repeatedName","negative","negativeName"};
            string[] names={stem,Path.GetFileName(stem),stem,Path.GetFileName(stem),missing,Path.GetFileName(missing)};
            for(int i=0;i<keys.Length;i++) {
                entry[keys[i]]=Load(names[i],job.partId,unchanged);
                filesUnchanged=unchanged();
                if(!filesUnchanged) { entry["stoppedReason"]="ATTRIBUTE_FILE_CHANGED"; break; }
            }
            entry["directoryAfter"]=DirectoryFiles(directory);
            entries.Add(entry);
            if(!filesUnchanged)break;
        }
        var after=State(model,handler,part);
        var assemblies=new[]{typeof(D.Drawing).Assembly,typeof(M.Model).Assembly,typeof(Tekla.Structures.Identifier).Assembly}
            .Select(a=>new {name=a.FullName,path=a.Location,sha256=Hash(File.ReadAllBytes(a.Location))}).ToArray();
        var report=new {schemaVersion="NativeTemplateLoadProbe.v1",capturedUtc=DateTime.UtcNow.ToString("o"),
            modelPath=model.GetInfo().ModelPath,partId=job.partId,before,after,
            stateUnchanged=Json.Serialize(before)==Json.Serialize(after),mutatingApiCalls=false,filesUnchanged,
            scope="DETACHED_ATTRIBUTES_ONLY",assemblies,
            executableSha256=Hash(File.ReadAllBytes(Assembly.GetExecutingAssembly().Location)),entries};
        File.WriteAllText(Path.Combine(output,"probe.json"),Json.Serialize(report),new UTF8Encoding(false));
        Console.WriteLine("Captured "+entries.Count+" detached template probes; stateUnchanged="+report.stateUnchanged+"; filesUnchanged="+filesUnchanged);
        return report.stateUnchanged&&filesUnchanged?0:1;
    }
}
