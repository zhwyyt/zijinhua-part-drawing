// New calibration pages only. Existing drawings are never opened for modification.
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
using D=Tekla.Structures.Drawing;
using M=Tekla.Structures.Model;
using G=Tekla.Structures.Geometry3d;

public class PageSample { public string key,font,text; public double height,angle; public double[] at; }
public class PageTemplate : BundleTemplate {
    public int sheet,boundTableId; public double[] size; public PageSample[] samples;
    public string boundLayoutName,bindingDigest;
    public BundleFile[] boundDependencies;
    public PageViewControl viewControl;
    public PagePrimitive[] primitives;
}
public class PageJob {
    public string runId,modelPath,ownership,ownershipSha256,guid,position,modified,sourceEnvelopeDigest,assetResumeDirectory;
    public int partId;
    public PageTemplate[] templates;
}
public static class NativePageProbe {
    const string Owner="NATIVE-PLATE-R1";
    static readonly JavaScriptSerializer Json=new JavaScriptSerializer {MaxJsonLength=Int32.MaxValue,RecursionLimit=100};
    static void Need(bool value,string message) { if(!value)throw new InvalidOperationException(message); }
    static string Hash(byte[] bytes) { using(var h=SHA256.Create())return BitConverter.ToString(h.ComputeHash(bytes)).Replace("-","").ToLowerInvariant(); }
    static string Full(string path) { return Path.GetFullPath(path).TrimEnd('\\','/'); }
    static void Write(string path,object value) { File.WriteAllText(path,Json.Serialize(value),new UTF8Encoding(false)); }
    static Dictionary<string,string> Inventory(string path) {
        Need(Directory.GetDirectories(path).Length==0,"Nested attributes unsupported");
        return Directory.GetFiles(path).OrderBy(p=>p).ToDictionary(Path.GetFileName,p=>Hash(File.ReadAllBytes(p)));
    }
    static bool Same(Dictionary<string,string> a,Dictionary<string,string> b) { return a.Count==b.Count&&a.All(p=>b.ContainsKey(p.Key)&&b[p.Key]==p.Value); }
    static void CheckPart(M.Part part,PageJob job) {
        Need(part.Select(),"Part refresh failed");string position="";
        Need(part.GetReportProperty("PART_POS",ref position)&&position==job.position,"Part position changed");
        Need(part.Identifier.GUID.ToString()==job.guid&&part.ModificationTime.HasValue&&
            part.ModificationTime.Value.ToUniversalTime()==DateTime.Parse(job.modified).ToUniversalTime(),"Part source changed");
    }
    static void CheckActive(D.DrawingHandler h,D.SinglePartDrawing expected) {
        var active=h.GetActiveDrawing() as D.SinglePartDrawing;
        Need(active!=null&&active.Select()&&active.Name==Owner&&active.PartIdentifier.ID==expected.PartIdentifier.ID&&
            active.SheetNumber==expected.SheetNumber&&TargetDrawingLookup.ObjectId(active)==TargetDrawingLookup.ObjectId(expected),"Active page changed");
        Need(!active.IsLocked&&!active.IsFrozen&&!active.IsIssued,"Test page locked/frozen/issued");
    }
    static void Close(D.DrawingHandler h,D.SinglePartDrawing expected,bool saved) {
        CheckActive(h,expected);Need(h.CloseActiveDrawing(saved),"Close test page failed");
    }
    static object Samples(D.SinglePartDrawing drawing,PageTemplate template) {
        var rows=new List<object>();var e=drawing.GetSheet().GetObjects(new[]{typeof(D.Text)});
        while(e.MoveNext()) {
            var t=e.Current as D.Text;if(t==null)continue;string key="";t.GetUserProperty("IL_SAMPLE",ref key);
            if(String.IsNullOrEmpty(key))continue;
            Need(t.Select(),"Sample refresh failed");var box=t.GetAxisAlignedBoundingBox();
            rows.Add(new {key,objectId=TargetDrawingObjectId(t),text=t.TextString,font=t.Attributes.Font.Name,
                height=t.Attributes.Font.Height,angle=t.Attributes.Angle,at=new[]{t.InsertionPoint.X,t.InsertionPoint.Y},
                box=new[]{box.MinPoint.X,box.MinPoint.Y,box.MaxPoint.X,box.MaxPoint.Y},
                fixedPlacement=t.Attributes.PlacingAttributes.IsFixed});
        }
        Need(rows.Count==template.samples.Length,"Text sample count mismatch");return rows;
    }
    static int TargetDrawingObjectId(D.DrawingObject obj) {
        var p=typeof(D.DatabaseObject).GetProperty("Identifier",BindingFlags.Instance|BindingFlags.NonPublic);
        Need(p!=null,"Object identity unavailable");return ((Tekla.Structures.Identifier)p.GetValue(obj,null)).ID;
    }
    static object LayoutIdentity(D.SinglePartDrawing drawing) {
        var layout=drawing.Layout;var values=new Dictionary<string,object>();
        foreach(var name in new[]{"InternalLayout","InternalTableLayoutId"}) {
            var p=typeof(D.LayoutAttributes).GetProperty(name,BindingFlags.Instance|BindingFlags.Public|BindingFlags.NonPublic);
            values[name]=p==null?null:p.GetValue(layout,null);
        }
        return new {drawing.Mark,drawing.Name,drawing.SheetNumber,layout=TemplateLoadProbe.Layout(layout),internalIdentity=values};
    }
    static object ContentFacts(M.Model model,M.Part part) {
        var values=new Dictionary<string,object>();
        foreach(string key in new[]{"PART_POS","PROFILE","MATERIAL","ASSEMBLY.ASSEMBLY_POS","PROJECT.NAME","PROJECT.NUMBER","USERDEFINED.USER_FIELD_1","USERDEFINED.USER_FIELD_2","USERDEFINED.USER_FIELD_3","CONTENTTYPE","PROFILE_TYPE"}) {
            string value="";bool available=part.GetReportProperty(key,ref value);values[key]=new {available,value};
        }
        foreach(string key in new[]{"LENGTH","WEIGHT_NET","WEIGHT_GROSS","PROFILE.WIDTH","PROFILE.HEIGHT","MATERIAL.PROFILE_DENSITY"}) {
            double value=0;bool available=part.GetReportProperty(key,ref value);values[key]=new {available,value};
        }
        int number=0;bool numberAvailable=part.GetReportProperty("NUMBER",ref number);
        values["NUMBER"]=new {available=numberAvailable,value=number,readType="INTEGER"};
        var project=model.GetProjectInfo();
        return new {partReports=values,project=NativePageCapture.Props(project,"Name","ProjectNumber","Builder","Object","Description")};
    }
    static void Register(PageJob job,string output,Dictionary<string,object> record,ref string expectedHash) {
        byte[] before=File.ReadAllBytes(job.ownership);Need(Hash(before)==expectedHash,"Ownership registry changed");
        var registry=Json.Deserialize<Dictionary<string,object>>(Encoding.UTF8.GetString(before).TrimStart('\uFEFF'));
        Need((string)registry["owner"]==Owner&&Full((string)registry["modelPath"])==Full(job.modelPath),"Ownership model mismatch");
        var records=new List<object>();
        // DeserializeObject's lists may be object[] or ArrayList; preserve every unrelated entry.
        if(registry.ContainsKey("independentLayoutTests"))foreach(var item in (System.Collections.IEnumerable)registry["independentLayoutTests"])records.Add(item);
        records.RemoveAll(item=> {
            var r=(Dictionary<string,object>)item;
            return Convert.ToString(r["runId"])==job.runId&&Convert.ToString(r["templateId"])==Convert.ToString(record["templateId"]);
        });
        records.Add(record);registry["independentLayoutTests"]=records;
        string pending=job.ownership+"."+job.runId+".pending";
        byte[] bytes=Encoding.UTF8.GetBytes(Json.Serialize(registry));
        using(var stream=new FileStream(pending,FileMode.CreateNew,FileAccess.Write,FileShare.None)) {stream.Write(bytes,0,bytes.Length);stream.Flush(true);}
        Need(Hash(File.ReadAllBytes(job.ownership))==expectedHash,"Concurrent ownership update");
        File.Replace(pending,job.ownership,null);expectedHash=Hash(bytes);
        Write(Path.Combine(output,"ownership-after.json"),registry);
    }
    public static int Main(string[] args) {
        try {
            Need(args.Length==4&&new[]{"--preflight","--create","--inspect","--export-pdf"}.Contains(args[0]),"Usage: --preflight|--create|--inspect|--export-pdf job.json new-output sdk");
            var job=Json.Deserialize<PageJob>(File.ReadAllText(args[1],Encoding.UTF8));
            Need(Regex.IsMatch(job.runId??"",@"\A[a-z0-9-]{1,60}\z"),"Invalid run ID");
            Need(job.partId>0&&job.templates!=null&&job.templates.Length==3&&job.templates.Select(t=>t.templateId).OrderBy(s=>s).SequenceEqual(new[]{"零件图10","零件图20","零件图50"}),"All three templates required");
            Need(!Directory.Exists(args[2])&&!File.Exists(args[2]),"Output exists");
            foreach(var t in job.templates) {
                Need(Regex.IsMatch(t.alias??"",@"\AIL_[0-9a-f]{24}\z"),"Invalid alias");
                Need(t.sheet>0&&t.samples!=null&&t.samples.Length>0&&t.samples.Select(s=>s.key).Distinct().Count()==t.samples.Length,"Invalid samples/sheet");
                foreach(var sample in t.samples)Need(sample.height>0&&!Double.IsNaN(sample.height)&&!Double.IsInfinity(sample.height)&&!Double.IsNaN(sample.angle)&&!Double.IsInfinity(sample.angle)&&sample.at.Length==2&&sample.at.All(v=>!Double.IsNaN(v)&&!Double.IsInfinity(v))&&!String.IsNullOrWhiteSpace(sample.font)&&!String.IsNullOrWhiteSpace(sample.text),"Invalid text sample");
                NativePrimitiveProbe.Validate(t.primitives);
                foreach(var f in t.files) {
                    Need(Regex.IsMatch(f.name??"",@"\A"+t.alias+@"(?:_r[0-9]+)?\.[a-z.]+\z"),"Invalid asset name");
                    Need(Hash(File.ReadAllBytes(f.source))==f.sha256,"Asset changed");
                    if(f.name.EndsWith(".wd"))Need(!Regex.IsMatch(Encoding.ASCII.GetString(File.ReadAllBytes(f.source)),@"(?m)^wdraw_dial\."),"Legacy .wd rejected");
                }
                if(!String.IsNullOrEmpty(t.boundLayoutName)) {
                    Need(t.boundTableId>0&&!String.IsNullOrEmpty(t.boundLayoutName)&&t.boundDependencies!=null,"Missing bound layout");
                    foreach(var f in t.boundDependencies)Need(Hash(File.ReadAllBytes(f.source))==f.sha256,"Bound dependency changed");
                }
                if(t.viewControl!=null) {
                    var r=t.viewControl.region;
                    Need(r!=null&&r.Length==4&&r.All(v=>!Double.IsNaN(v)&&!Double.IsInfinity(v))&&
                        r[0]>=0&&r[1]>=0&&r[0]<r[2]&&r[1]<r[3]&&r[2]<=t.size[0]&&r[3]<=t.size[1]&&
                        t.viewControl.sourceDigest==job.sourceEnvelopeDigest,"Invalid admitted-source view control");
                }
            }
            string sdk=Full(args[3]);AppDomain.CurrentDomain.AssemblyResolve+=(s,e)=>{string p=Path.Combine(sdk,new AssemblyName(e.Name).Name+".dll");return File.Exists(p)?Assembly.LoadFrom(p):null;};
            if(String.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("SESSIONNAME")))Environment.SetEnvironmentVariable("SESSIONNAME","Console");
            return Run(args,job);
        }catch(Exception e){Console.Error.WriteLine(e);return 2;}
    }
    [MethodImpl(MethodImplOptions.NoInlining)]
    static int Run(string[] args,PageJob job) {
        var model=new M.Model();var h=new D.DrawingHandler();
        Need(model.GetConnectionStatus()&&h.GetConnectionStatus(),"Tekla connection unavailable");
        Need(Full(model.GetInfo().ModelPath)==Full(job.modelPath),"Wrong model");
        var part=model.SelectModelObject(new Tekla.Structures.Identifier(job.partId)) as M.Part;Need(part!=null,"Part missing");CheckPart(part,job);
        string output=Full(args[2]),attributes=Path.Combine(Full(job.modelPath),"attributes");Directory.CreateDirectory(output);
        var before=TemplateLoadProbe.State(model,h,part);var current=Inventory(attributes);
        var original=new Dictionary<string,string>(current);var resumed=new HashSet<string>();
        if(!String.IsNullOrEmpty(job.assetResumeDirectory)) {
            Need(Directory.GetFiles(job.assetResumeDirectory,"*-created.json").Length==0,"Prior page receipt exists; cannot restart creation");
            var old=Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(job.assetResumeDirectory,"preflight.json")));
            original=Json.ConvertToType<Dictionary<string,string>>(old["original"]);
            var assets=Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(job.assetResumeDirectory,"assets.json")));
            // Exact model, inventory and bytes allow reuse of an interrupted installation.
            // The manifest's run remains recorded; page ownership uses the new job's run.
            Need(Full((string)assets["modelPath"])==Full(job.modelPath),"Resume assets belong to another model");
            var expectedResume=new Dictionary<string,string>(original);
            foreach(var f in Json.ConvertToType<BundleFile[]>(assets["files"])) {
                Need(job.templates.SelectMany(t=>t.files).Any(p=>p.name==f.name&&p.sha256==f.sha256),"Resume asset differs from job");
                expectedResume.Add(f.name,f.sha256);resumed.Add(f.name);
            }
            Need(Same(expectedResume,current),"Resume directory changed");
        }
        var registry=Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(job.ownership,Encoding.UTF8));
        Need((string)registry["owner"]==Owner&&Full((string)registry["modelPath"])==Full(job.modelPath),"Registry identity mismatch");
        Need(Hash(File.ReadAllBytes(job.ownership))==job.ownershipSha256,"Ownership changed since job");
        if(args[0]=="--create"&&registry.ContainsKey("independentLayoutTests"))foreach(Dictionary<string,object> record in (System.Collections.IEnumerable)registry["independentLayoutTests"])
            Need(Convert.ToString(record["runId"])!=job.runId,"Run already registered; inspect existing pages, never reinsert");
        bool noActive=h.GetActiveDrawing()==null;
        Write(Path.Combine(output,"preflight.json"),new {before,noActive,original,job.sourceEnvelopeDigest});
        Need(noActive,"ACTIVE_DRAWING_OPEN: user must save/close their drawing before test-page creation");
        if(args[0]=="--export-pdf") {
            var exports=new List<object>();D.SinglePartDrawing active=null;string exportError=null;
            try {
                Write(Path.Combine(output,"content-facts.json"),ContentFacts(model,part));
                foreach(var t in job.templates) {
                    var records=((System.Collections.IEnumerable)registry["independentLayoutTests"]).Cast<Dictionary<string,object>>()
                        .Where(r=>Convert.ToString(r["runId"])==job.runId&&Convert.ToString(r["templateId"])==t.templateId).ToArray();
                    Need(records.Length==1,"Ambiguous registered PDF target");var record=records[0];
                    Need(Convert.ToInt32(record["id"])==job.partId&&Convert.ToString(record["guid"])==job.guid&&Convert.ToInt32(record["sheet"])==t.sheet,"Wrong PDF target");
                    var d=TargetDrawingLookup.Read(job.partId,t.sheet,Convert.ToInt32(record["objectId"]),Convert.ToInt32(record["drawingId"]));
                    Need(d.Name==Owner&&!d.IsLocked&&!d.IsFrozen&&!d.IsIssued&&h.GetActiveDrawing()==null,"PDF target not available");
                    Need(h.SetActiveDrawing(d,true),"Open PDF target failed");active=d;CheckActive(h,d);
                    string prefix=Path.Combine(output,t.templateId);var pageBefore=NativePageCapture.Capture(d);
                    Write(prefix+"-before.json",pageBefore);
                    var exported=NativePdfExport.Export(Full(args[3]),prefix+".pdf");CheckActive(h,d);
                    var pageAfter=NativePageCapture.Capture(d);Write(prefix+"-after.json",pageAfter);
                    bool unchanged=Json.Serialize(pageBefore)==Json.Serialize(pageAfter);Need(unchanged,"PDF export changed page snapshot");
                    Close(h,d,false);active=null;exports.Add(new {t.templateId,exported,pageUnchanged=unchanged});
                    Write(Path.Combine(output,"exports.json"),exports);
                }
            } catch(Exception e){exportError=e.ToString();}
            finally {if(active!=null)try{Close(h,active,false);}catch(Exception e){exportError=(exportError??"")+e;}}
            var stateAfter=TemplateLoadProbe.State(model,h,part);
            bool stateSame=Json.Serialize(before)==Json.Serialize(stateAfter),attributesSame=Same(current,Inventory(attributes)),registrySame=Hash(File.ReadAllBytes(job.ownership))==job.ownershipSha256;
            Write(Path.Combine(output,"result.json"),new {before,after=stateAfter,exports,error=exportError,stateUnchanged=stateSame,attributesUnchanged=attributesSame,registryUnchanged=registrySame});
            if(exportError!=null)Console.Error.WriteLine(exportError);
            return exportError==null&&stateSame&&attributesSame&&registrySame?0:1;
        }
        if(args[0]=="--inspect") {
            Write(Path.Combine(output,"content-facts.json"),ContentFacts(model,part));
            var identities=new List<object>();
            foreach(var section in new[]{"parts","independentLayoutTests"}) {
                if(!registry.ContainsKey(section))continue;
                foreach(Dictionary<string,object> r in (System.Collections.IEnumerable)registry[section]) {
                    if(Convert.ToInt32(r["id"])!=job.partId)continue;
                    var d=TargetDrawingLookup.Read(job.partId,Convert.ToInt32(r["sheet"]),Convert.ToInt32(r["objectId"]),Convert.ToInt32(r["drawingId"]));
                    identities.Add(LayoutIdentity(d));
                }
            }
            Write(Path.Combine(output,"layout-identities.json"),identities);
            var inspected=TemplateLoadProbe.State(model,h,part);
            Need(Json.Serialize(before)==Json.Serialize(inspected)&&Same(current,Inventory(attributes))&&Hash(File.ReadAllBytes(job.ownership))==job.ownershipSha256,"Inspection changed source/attributes/registry");
            Write(Path.Combine(output,"result.json"),new {stateUnchanged=true,attributesUnchanged=true,registryUnchanged=true});return 0;
        }
        if(args[0]=="--preflight") {
            var drawings=h.GetDrawings();
            Write(Path.Combine(output,"catalog-size.json"),new {count=drawings.GetSize()});
            return 0;
        }
        File.Copy(job.ownership,Path.Combine(output,"ownership-before.json"));
        var expected=new Dictionary<string,string>(original);var locks=new List<FileStream>();var installed=new List<BundleFile>();
        var results=new List<object>();string error=null;D.SinglePartDrawing opened=null;string registryHash=job.ownershipSha256;
        try {
            foreach(var name in current.Keys)locks.Add(new FileStream(Path.Combine(attributes,name),FileMode.Open,FileAccess.Read,FileShare.Read));
            Need(Same(current,Inventory(attributes)),"Attributes changed while locking");
            foreach(var f in job.templates.SelectMany(t=>t.files))Need(resumed.Contains(f.name)||!File.Exists(Path.Combine(attributes,f.name)),"Asset already exists");
            foreach(var f in job.templates.SelectMany(t=>t.files)) {
                var bytes=File.ReadAllBytes(f.source);Need(Hash(bytes)==f.sha256,"Prepared asset changed");
                if(!resumed.Contains(f.name)) {
                    using(var stream=new FileStream(Path.Combine(attributes,f.name),FileMode.CreateNew,FileAccess.Write,FileShare.None)){stream.Write(bytes,0,bytes.Length);stream.Flush(true);}
                    locks.Add(new FileStream(Path.Combine(attributes,f.name),FileMode.Open,FileAccess.Read,FileShare.Read));
                }
                installed.Add(f);expected.Add(f.name,f.sha256);Write(Path.Combine(output,"assets.json"),new {runId=job.runId,modelPath=job.modelPath,files=installed,retainedForSavedPages=true});
            }
            Need(job.templates.Select(t=>t.sheet).Distinct().Count()==3,"Duplicate requested sheets");
            // A finite new-page request. Insert may fail on an occupied number; never find/modify that page.
            foreach(var t in job.templates) {
                Need(h.GetActiveDrawing()==null,"Unexpected active drawing");CheckPart(part,job);
                Need(Same(expected,Inventory(attributes)),"Attribute assets changed");
                var loaded=new D.SinglePartDrawing(part.Identifier,t.sheet,t.alias);
                Need(TargetDrawingLookup.ObjectId(loaded)<=0&&TargetDrawingLookup.DrawingId(loaded)<=0,"Constructor unexpectedly bound an existing page");
                Need(loaded.Name==t.templateId&&loaded.Layout.SheetSize.Width==t.size[0]&&loaded.Layout.SheetSize.Height==t.size[1],"Template fallback");
                if(!String.IsNullOrEmpty(t.boundLayoutName)) {
                    foreach(var f in t.boundDependencies)Need(Full(f.source)==Full(Path.Combine(attributes,f.name))&&
                        expected.ContainsKey(f.name)&&expected[f.name]==f.sha256,"Bound model dependency mismatch");
                    var props=(Dictionary<string,object>)Json.DeserializeObject(Json.Serialize(LayoutIdentity(loaded)));
                    var values=(Dictionary<string,object>)props["internalIdentity"];
                    Need(Convert.ToString(values["InternalLayout"])==t.boundLayoutName&&Convert.ToInt32(values["InternalTableLayoutId"])==t.boundTableId,"Bound table layout not resolved");
                    Write(Path.Combine(output,t.templateId+"-loaded-layout.json"),props);
                }
                D.SinglePartDrawing d;Dictionary<string,object> record;
                string prefix=Path.Combine(output,t.templateId);
                d=loaded;d.Name=Owner;d.Title3="INDEPENDENT LAYOUT CALIBRATION - NOT FOR PRODUCTION";
                Write(Path.Combine(output,"phase.json"),new {phase="INSERTING_NEW_PAGE",t.templateId,sheet=d.SheetNumber});
                Need(d.Insert(),"New test-page insertion failed: "+d.QueryReturnValue);
                record=new Dictionary<string,object>{{"runId",job.runId},{"templateId",t.templateId},{"bundleDigest",t.bundleDigest},
                {"alias",t.alias},{"id",job.partId},{"guid",job.guid},{"sheet",d.SheetNumber},{"mark",d.Mark},
                {"objectId",TargetDrawingLookup.ObjectId(d)},{"drawingId",TargetDrawingLookup.DrawingId(d)},{"stage","CREATED"},{"productionEligible",false}};
                Write(prefix+"-created.json",record);Register(job,output,record,ref registryHash);
                Need(h.SetActiveDrawing(d,true),"Open new test page failed");opened=d;CheckActive(h,d);
                Write(prefix+"-template.json",NativePageCapture.Capture(d));
                if(t.viewControl!=null)Write(prefix+"-view-control.json",NativeViewControl.Apply(d,t.viewControl));
                foreach(var sample in t.samples) {
                    var a=new D.Text.TextAttributes();a.Font.Name=sample.font;a.Font.Height=sample.height;a.Font.Color=D.DrawingColors.Black;
                    a.Alignment=D.TextAlignment.Left;a.Frame.Type=D.FrameTypes.None;a.PlacingAttributes.IsFixed=true;a.ArrowHead.Head=D.ArrowheadTypes.NoArrow;
                    a.Angle=sample.angle;
                    var text=new D.Text(d.GetSheet(),new G.Point(sample.at[0],sample.at[1],0),sample.text,new D.PointPlacing(),a);
                    Need(text.Insert(),"Text sample insert failed");Need(text.SetUserProperty("IL_SAMPLE",sample.key),"Sample tag failed");
                    Need(text.SetUserProperty("COD_OWNER",Owner),"Owner tag failed");
                }
                NativePrimitiveProbe.Insert(d,t.primitives);
                d.CommitChanges();CheckActive(h,d);
                Write(prefix+"-before.json",new {page=NativePageCapture.Capture(d),samples=Samples(d,t),primitives=NativePrimitiveProbe.Capture(d,t.primitives)});
                Need(h.SaveActiveDrawing(),"Save test page failed");record["stage"]="SAVED";Register(job,output,record,ref registryHash);
                Close(h,d,true);opened=null;
                for(int pass=1;pass<=2;pass++) {
                    d=TargetDrawingLookup.Read(job.partId,Convert.ToInt32(record["sheet"]),Convert.ToInt32(record["objectId"]),Convert.ToInt32(record["drawingId"]));
                    Need(d.Name==Owner,"Registered page ownership changed");Need(h.SetActiveDrawing(d,true),"Reopen test page failed");opened=d;CheckActive(h,d);
                    Write(prefix+"-reopen"+pass+".json",new {page=NativePageCapture.Capture(d),samples=Samples(d,t),primitives=NativePrimitiveProbe.Capture(d,t.primitives)});
                    Close(h,d,false);opened=null;
                }
                var print=new D.PrintAttributes();print.PrinterInstance="DXF";print.Scale=1;
                Need(h.PrintDrawing(d,print,prefix+".dxf")&&File.Exists(prefix+".dxf"),"Native DXF export failed");
                CheckPart(part,job);Need(Same(expected,Inventory(attributes)),"Attributes changed during page creation");
                record["stage"]="SAVED_REOPENED_EXPORTED";Register(job,output,record,ref registryHash);results.Add(record);
                Write(Path.Combine(output,"progress.json"),results);
            }
        } catch(Exception e){error=e.ToString();}
        finally {
            if(opened!=null)try {Close(h,opened,false);}catch(Exception e){error=(error??"")+e.ToString();}
            foreach(var stream in locks)stream.Dispose();
        }
        var after=TemplateLoadProbe.State(model,h,part);var final=Inventory(attributes);
        var report=new {schemaVersion="NativePageProbe.v1",job.runId,job.partId,job.modelPath,job.sourceEnvelopeDigest,
            before,after,originalFiles=original,finalFiles=final,assets=installed,results,error,
            stateUnchanged=Json.Serialize(before)==Json.Serialize(after),originalFilesUnchanged=original.All(p=>final.ContainsKey(p.Key)&&final[p.Key]==p.Value),
            assetsMatch=Same(expected,final),ownershipBeforeSha256=job.ownershipSha256,ownershipAfterSha256=registryHash,
            executableSha256=Hash(File.ReadAllBytes(Assembly.GetExecutingAssembly().Location))};
        Write(Path.Combine(output,"probe.json"),report);Console.WriteLine(Json.Serialize(new {pages=results.Count,report.stateUnchanged,report.originalFilesUnchanged,error}));
        return error==null&&report.stateUnchanged&&report.assetsMatch?0:1;
    }
}
