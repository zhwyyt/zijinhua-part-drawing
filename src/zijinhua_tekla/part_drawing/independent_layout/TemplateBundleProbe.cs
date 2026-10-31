// Temporary, uniquely named attribute files only. Never insert/modify/save/close a drawing.
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

public class BundleFile { public string name,source,sha256; }
public class BundleTemplate { public string templateId,alias,bundleDigest; public BundleFile[] files; }
public class BundleJob { public string modelPath; public int partId; public BundleTemplate[] templates; }
public static class TemplateBundleProbe {
    static readonly JavaScriptSerializer Json=new JavaScriptSerializer {MaxJsonLength=Int32.MaxValue};
    static void Need(bool test,string message) { if(!test)throw new InvalidOperationException(message); }
    static string Full(string path) { return Path.GetFullPath(path).TrimEnd('\\','/'); }
    static string Hash(byte[] bytes) {
        using(var hash=SHA256.Create())return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();
    }
    static void Write(string path,object value) { File.WriteAllText(path,Json.Serialize(value),new UTF8Encoding(false)); }
    static Dictionary<string,string> Inventory(string directory) {
        Need(Directory.GetDirectories(directory).Length==0,"Nested attribute directories unsupported");
        return Directory.GetFiles(directory).OrderBy(p=>p,StringComparer.OrdinalIgnoreCase)
            .ToDictionary(Path.GetFileName,p=>Hash(File.ReadAllBytes(p)),StringComparer.OrdinalIgnoreCase);
    }
    static bool Same(Dictionary<string,string> a,Dictionary<string,string> b) {
        return a.Count==b.Count && a.All(pair=>b.ContainsKey(pair.Key)&&b[pair.Key]==pair.Value);
    }
    static object Load(string alias,int partId) {
        var result=new Dictionary<string,object>{{"requested",alias},{"drawing",null},{"drawingError",null}};
        try {
            var detached=new D.SinglePartDrawing(new Tekla.Structures.Identifier(partId),1,alias);
            result["drawing"]=new {detached.Name,detached.Title1,detached.Title2,detached.Title3,
                layout=TemplateLoadProbe.Layout(detached.Layout)};
        } catch(Exception e) { result["drawingError"]=e.GetBaseException().ToString(); }
        return result;
    }
    public static int Main(string[] args) {
        try {
            Need(args.Length==3,"Usage: job.json new-output-directory sdk-directory");
            var job=Json.Deserialize<BundleJob>(File.ReadAllText(args[0],Encoding.UTF8));
            Need(job.partId>0 && job.templates!=null && job.templates.Length==3,"Require all three templates");
            Need(job.templates.Select(t=>t.templateId).OrderBy(s=>s).SequenceEqual(
                new[]{"零件图10","零件图20","零件图50"}),"Require unique 10/20/50");
            Need(!Directory.Exists(args[1])&&!File.Exists(args[1]),"Output exists");
            var names=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach(var t in job.templates) {
                Need(Regex.IsMatch(t.alias??"",@"\AIL_[0-9a-f]{24}\z"),"Invalid alias");
                Need(Regex.IsMatch(t.bundleDigest??"",@"\A[0-9a-f]{64}\z"),"Missing bundle digest");
                Need(t.files!=null && t.files.Any(f=>f.name==t.alias+".wd"),"Primary absent");
                foreach(var f in t.files) {
                    Need(Regex.IsMatch(f.name??"",@"\A"+t.alias+@"(?:_r[0-9]+)?\.(?:wd(?:\.more|\.copt|\.filter|\.npfilter)?|vi(?:\.copt|\.filter|\.npfilter)?|lay|wdcd|dim)\z"),"Invalid staged filename");
                    Need(names.Add(f.name),"Duplicate destination");
                    byte[] bytes=File.ReadAllBytes(f.source);
                    Need(Hash(bytes)==f.sha256,"Prepared bytes changed: "+f.name);
                    if(f.name.EndsWith(".wd"))Need(!Regex.IsMatch(Encoding.ASCII.GetString(bytes),@"(?m)^wdraw_dial\."),"Legacy format rejected");
                }
            }
            string sdk=Full(args[2]);
            AppDomain.CurrentDomain.AssemblyResolve+=(s,e)=>{
                string path=Path.Combine(sdk,new AssemblyName(e.Name).Name+".dll");
                return File.Exists(path)?Assembly.LoadFrom(path):null;
            };
            if(String.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("SESSIONNAME")))Environment.SetEnvironmentVariable("SESSIONNAME","Console");
            return Run(args,job);
        } catch(Exception e) { Console.Error.WriteLine(e); return 2; }
    }
    [MethodImpl(MethodImplOptions.NoInlining)]
    static int Run(string[] args,BundleJob job) {
        var model=new M.Model(); var handler=new D.DrawingHandler();
        Need(model.GetConnectionStatus()&&handler.GetConnectionStatus(),"Tekla connection unavailable");
        Need(String.Equals(Full(model.GetInfo().ModelPath),Full(job.modelPath),StringComparison.OrdinalIgnoreCase),"Wrong model");
        var part=model.SelectModelObject(new Tekla.Structures.Identifier(job.partId)) as M.Part;
        Need(part!=null,"Target part unavailable");
        string output=Full(args[1]),directory=Path.Combine(Full(job.modelPath),"attributes");
        var before=TemplateLoadProbe.State(model,handler,part);
        Directory.CreateDirectory(output);
        Directory.CreateDirectory(Path.Combine(output,"originals"));
        var original=Inventory(directory);
        var expected=new Dictionary<string,string>(original,StringComparer.OrdinalIgnoreCase);
        var locks=new List<FileStream>(); var owned=new List<BundleFile>();
        var entries=job.templates.Select(t=>new Dictionary<string,object>{{"templateId",t.templateId},
            {"alias",t.alias},{"bundleDigest",t.bundleDigest}}).ToArray();
        var events=new List<object>(); var cleanupErrors=new List<string>();
        string error=null;
        Action<string> check=phase=>{
            var actual=Inventory(directory);
            var state=TemplateLoadProbe.State(model,handler,part);
            bool filesMatch=Same(expected,actual),stateMatch=Json.Serialize(before)==Json.Serialize(state);
            events.Add(new {phase,filesMatch,stateMatch,files=actual,state});
            Write(Path.Combine(output,"journal.json"),new {original,owned,events,cleanupErrors});
            Need(filesMatch&&stateMatch,"State/files changed at "+phase);
        };
        try {
            foreach(var name in original.Keys) {
                string source=Path.Combine(directory,name);
                Need((File.GetAttributes(source)&FileAttributes.ReparsePoint)==0,"Reparse file unsupported");
                locks.Add(new FileStream(source,FileMode.Open,FileAccess.Read,FileShare.Read));
                byte[] bytes=File.ReadAllBytes(source);
                Need(Hash(bytes)==original[name],"Original changed while locking");
                File.WriteAllBytes(Path.Combine(output,"originals",name),bytes);
            }
            foreach(var f in job.templates.SelectMany(t=>t.files))Need(!File.Exists(Path.Combine(directory,f.name)),"Alias already exists");
            check("locked");
            for(int i=0;i<entries.Length;i++) {
                entries[i]["absentBefore"]=Load(job.templates[i].alias,job.partId);
                check("absentBefore:"+job.templates[i].alias);
            }
            foreach(var f in job.templates.SelectMany(t=>t.files)) {
                byte[] bytes=File.ReadAllBytes(f.source); Need(Hash(bytes)==f.sha256,"Prepared file changed");
                // Record intent before creation. Only successful CreateNew becomes owned.
                Write(Path.Combine(output,"install-intent.json"),new {file=f,owned});
                using(var stream=new FileStream(Path.Combine(directory,f.name),FileMode.CreateNew,FileAccess.Write,FileShare.None)) {
                    owned.Add(f); stream.Write(bytes,0,bytes.Length); stream.Flush(true);
                }
                expected.Add(f.name,f.sha256);
                Write(Path.Combine(output,"journal.json"),new {original,owned,events,cleanupErrors});
            }
            check("installed");
            for(int i=0;i<entries.Length;i++) {
                entries[i]["first"]=Load(job.templates[i].alias,job.partId);
                check("first:"+job.templates[i].alias);
                entries[i]["second"]=Load(job.templates[i].alias,job.partId);
                check("second:"+job.templates[i].alias);
            }
        } catch(Exception e) { error=e.ToString(); }
        finally {
            foreach(var f in owned) {
                try {
                    string path=Path.Combine(directory,f.name);
                    Need(File.Exists(path)&&Hash(File.ReadAllBytes(path))==f.sha256,"Owned alias changed/missing; retained: "+f.name);
                    File.Delete(path); expected.Remove(f.name);
                } catch(Exception e) { cleanupErrors.Add(e.Message); }
            }
            try {
                check("cleaned");
                if(error==null && cleanupErrors.Count==0) {
                    for(int i=0;i<entries.Length;i++) {
                        entries[i]["absentAfter"]=Load(job.templates[i].alias,job.partId);
                        check("absentAfter:"+job.templates[i].alias);
                    }
                }
            } catch(Exception e) { error=(error??"")+e.ToString(); }
            foreach(var stream in locks)stream.Dispose();
        }
        var after=TemplateLoadProbe.State(model,handler,part);
        var finalFiles=Inventory(directory);
        var assemblies=new[]{typeof(D.Drawing).Assembly,typeof(M.Model).Assembly,typeof(Tekla.Structures.Identifier).Assembly}
            .Select(a=>new {name=a.FullName,path=a.Location,sha256=Hash(File.ReadAllBytes(a.Location))}).ToArray();
        var report=new {schemaVersion="NativeTemplateBundleProbe.v1",scope="DETACHED_ISOLATED_ATTRIBUTES_ONLY",
            capturedUtc=DateTime.UtcNow.ToString("o"),modelPath=job.modelPath,job.partId,before,after,
            mutatingDrawingApiCalls=false,originalFiles=original,finalFiles,entries,events,cleanupErrors,error,
            filesRestored=Same(original,finalFiles),stateUnchanged=Json.Serialize(before)==Json.Serialize(after),
            assemblies,executableSha256=Hash(File.ReadAllBytes(Assembly.GetExecutingAssembly().Location))};
        Write(Path.Combine(output,"probe.json"),report);
        Console.WriteLine("Captured isolated template probes; filesRestored="+report.filesRestored+"; stateUnchanged="+report.stateUnchanged+"; error="+error);
        return error==null&&cleanupErrors.Count==0&&report.filesRestored&&report.stateUnchanged?0:1;
    }
}
