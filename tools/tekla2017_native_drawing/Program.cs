using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Web.Script.Serialization;
using Tekla.Structures;
using Tekla.Structures.Geometry3d;
using M = Tekla.Structures.Model;
using D = Tekla.Structures.Drawing;

public class Frame { public double[] origin, axisX, axisY; }
public class Dimension { public string key, axis, role; public double[][] coordinates; public double[] up; public double distance; public bool combine, suppressWitnesses; }
public class Note { public string key, kind, text, feature; public string[] members; public double[] at, tip; public double height; }
public class Curve { public string key, kind, role, feature; public double[] start, end, middle, center; public double radius; }
public class RadiusPlan { public string key, feature; public double[][] points; public double distance; }
public class BoltPlan { public int modelId; public string display; public string[] holes; }
public class DisplayPlan { public int decimals; public string combineFormat,shortDimension; public int combineMinimum; }
public class ViewPlan { public string templateSignature,snapshotDigest; public int templatePartId,templateSheet; public double fontHeight,scale; public double[] availableArea,sheetSize; public bool previewOverflow; }
public class SectionCut { public double[] start,end; public double depthUp,depthDown; }
public class Plan {
    public int id; public string position, status, guid, modification, sourceDigest, planDigest, geometryMode;
    public int ownedSheet,ownedObjectId,ownedDrawingId;
    public double scale; public double[] sheet; public Frame frame; public Dimension[] dimensions; public Note[] notes; public Curve[] curves;
    public RadiusPlan[] radii; public BoltPlan[] bolts; public DisplayPlan display;
    public ViewPlan viewPlan;
    public string schemaVersion,viewId,label; public Plan[] sections; public SectionCut cut; public Frame sourceFrame;
}
public class Job { public string modelPath,attributeFile; public Plan[] parts; }

internal static class NativeDrawing {
    const string Owner = "NATIVE-PLATE-R1";
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = Int32.MaxValue };
    static void Need(bool ok, string message) { if (!ok) throw new InvalidOperationException(message); }
    static bool SeriesScale(double scale) {
        foreach(var item in new[]{1d,2d,2.5d,5d,10d,20d,50d}) if(Math.Abs(item-scale)<1e-9) return true;
        return false;
    }
    static Point P(double[] a) { return new Point(a[0], a[1], a.Length > 2 ? a[2] : 0); }
    static double[] A(Point p) { return p==null?null:new[] { p.X, p.Y, p.Z }; }
    static string Tag(D.DrawingObject o, string name) { string value=""; o.GetUserProperty(name, ref value); return value; }
    static bool Owned(D.DrawingObject o) { return Tag(o,"COD_OWNER") == Owner; }
    static void TagObject(D.DrawingObject o, string key) {
        Need(o.SetUserProperty("COD_OWNER",Owner),"Owner tag failed");
        Need(o.SetUserProperty("COD_KEY",key),"Identity tag failed");
        Need(o.SetUserProperty("COD_ID",Guid.NewGuid().ToString()),"Object identity tag failed");
    }
    static int Main(string[] args) {
        try {
            Need(args.Length>=3,"Usage: --prepare|--write|--audit job.json new-output-directory [sdk-dir]");
            var sdk=args.Length>3?args[3]:@"D:\Program Files\Tekla Structures\2017\nt\bin\plugins";
            AppDomain.CurrentDomain.AssemblyResolve+=(s,e)=> { var f=Path.Combine(sdk,new AssemblyName(e.Name).Name+".dll");return File.Exists(f)?Assembly.LoadFrom(f):null; };
            if (String.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("SESSIONNAME"))) Environment.SetEnvironmentVariable("SESSIONNAME","Console");
            return Run(args);
        } catch(Exception e) { Console.Error.WriteLine(e); return 2; }
    }
    [MethodImpl(MethodImplOptions.NoInlining)]
    static int Run(string[] args) {
        bool write=args[0]=="--write",prepare=args[0]=="--prepare"; Need(write||prepare||args[0]=="--audit","Unknown mode");
        var job=Json.Deserialize<Job>(File.ReadAllText(args[1]));
        Need(job.parts!=null&&job.parts.Length>0,"Empty job");
        string output=Path.GetFullPath(args[2]); Need(!Directory.Exists(output),"Output directory already exists");
        Directory.CreateDirectory(output);
        var model=new M.Model(); var handler=new D.DrawingHandler();
        Need(model.GetConnectionStatus()&&handler.GetConnectionStatus(),"Tekla connection unavailable");
        Need(String.Equals(Path.GetFullPath(model.GetInfo().ModelPath).TrimEnd('\\'),Path.GetFullPath(job.modelPath).TrimEnd('\\'),StringComparison.OrdinalIgnoreCase),"Wrong model");
        Need(handler.GetActiveDrawing()==null,"ACTIVE_DRAWING_OPEN: save/close the user's drawing yourself before running; this tool will not save or close it");
        var plane=model.GetWorkPlaneHandler();var previous=plane.GetCurrentTransformationPlane();
        D.SinglePartDrawing opened=null;
        var results=new List<object>();int failures=0;
        try {
            Need(plane.SetCurrentTransformationPlane(new M.TransformationPlane()),"Global work plane failed");
            foreach(var p in job.parts) {
                try {
                    Need(p.status=="READY","Source/plan not ready");
                    if(!prepare)Need(p.viewPlan!=null&&SeriesScale(p.scale)&&Math.Abs(p.scale-p.viewPlan.scale)<1e-9&&p.sheet.Length==2,"M4 live template view plan required");
                    var part=model.SelectModelObject(new Identifier(p.id)) as M.Part;Need(part!=null,"Target part missing");
                    CheckSource(part,p);
                    Console.WriteLine("BEGIN "+p.id);
                    bool created=p.ownedSheet==0;D.SinglePartDrawing drawing;
                    if(created) {
                        Need(prepare,"Owned drawing not prepared");int sheet=1;
                        drawing=String.IsNullOrWhiteSpace(job.attributeFile)?new D.SinglePartDrawing(part.Identifier,sheet):new D.SinglePartDrawing(part.Identifier,sheet,job.attributeFile);
                        drawing.Name=Owner;drawing.Title1="平板零件图 - 自动验收试验";
                        Need(drawing.Insert(),"Drawing insertion failed: "+drawing.QueryReturnValue);
                        File.WriteAllText(Path.Combine(output,p.id+".created.json"),Json.Serialize(new {
                            id=p.id,guid=p.guid,sheet=drawing.SheetNumber,mark=drawing.Mark,
                            objectId=TargetDrawingLookup.ObjectId(drawing),drawingId=TargetDrawingLookup.DrawingId(drawing)}));
                    } else { drawing=TargetDrawingLookup.Read(p.id,p.ownedSheet,p.ownedObjectId,p.ownedDrawingId); }
                    Need(drawing.Select(),"Drawing ownership reread failed");
                    Need(drawing.Name==Owner&&drawing.PartIdentifier.ID==p.id,"Refusing non-owned or wrong-instance drawing");
                    Need(!drawing.IsLocked&&!drawing.IsIssued&&!drawing.IsFrozen,"Drawing locked/issued/frozen");
                    Need(handler.SetActiveDrawing(drawing,true),"Open target drawing failed");
                    opened=drawing;
                    if(prepare) {
                        if(!created) {var existing=drawing.GetSheet().GetAllViews();while(existing.MoveNext()) {
                            var check=existing.Current as D.View;if(check==null)continue;var annotations=check.GetAllObjects();
                            while(annotations.MoveNext())if(Annotation(annotations.Current))Need(Owned(annotations.Current),"Manual annotation in existing view; refusing preparation");
                        }}
                        var view=PrepareView(drawing,created);int index=0;
                        // Hidden target parts disappear from Tekla material tables.
                        // Keep the model object included; analytic mode suppresses
                        // only its rendering, after the live template is captured.
                        var modelParts=view.GetObjects(new[] {typeof(D.Part)});
                        while(modelParts.MoveNext()) {var linked=modelParts.Current as D.Part;
                            if(linked!=null&&linked.ModelIdentifier.ID==p.id) {linked.Hideable.ShowInDrawingView();Need(linked.Modify(),"Restore target table inclusion failed");}}
                        foreach(var objects in new[]{view.GetAllObjects(),drawing.GetSheet().GetObjects()}) {
                            while(objects.MoveNext())if(Annotation(objects.Current)) {
                                if(created)TagObject(objects.Current,"prepared:"+(index++));
                                else Need(Owned(objects.Current),"Manual annotation in prepared target");
                            }
                        }
                        view.Attributes.Scale=1;Need(view.Modify(),"Prepare fixed 1:1 view failed");
                        drawing.CommitChanges();Need(handler.SaveActiveDrawing(),"Prepare save failed");
                        CloseTarget(handler,drawing,true);opened=null;
                        Need(handler.SetActiveDrawing(drawing,true),"Reopen prepared target failed");opened=drawing;
                        var template=TemplateCapture.Capture(drawing,job.modelPath,job.attributeFile);
                        File.WriteAllText(Path.Combine(output,p.id+".template.json"),Json.Serialize(template));
                        CloseTarget(handler,drawing,false);opened=null;
                        results.Add(new {id=p.id,status="TEMPLATE_PREPARED",mark=template.mark,sheet=drawing.SheetNumber,created=created,
                            objectId=TargetDrawingLookup.ObjectId(drawing),drawingId=TargetDrawingLookup.DrawingId(drawing)});
                        continue;
                    }
                    var templateBefore=TemplateCapture.Capture(drawing,job.modelPath,job.attributeFile);
                    File.WriteAllText(Path.Combine(output,p.id+".template-before.json"),Json.Serialize(templateBefore));
                    Need(templateBefore.signature==p.viewPlan.templateSignature&&p.viewPlan.templatePartId==p.id&&p.viewPlan.templateSheet==drawing.SheetNumber,"Template changed since planning");
                    if(write) { Write(drawing,p,created);CheckSource(part,p);drawing.CommitChanges();Need(handler.SaveActiveDrawing(),"Save failed"); }
                    CloseTarget(handler,drawing,true);opened=null;
                    Need(handler.SetActiveDrawing(drawing,true),"Reopen target failed");
                    opened=drawing;
                    var templateAfter=TemplateCapture.Capture(drawing,job.modelPath,job.attributeFile);
                    File.WriteAllText(Path.Combine(output,p.id+".template-after.json"),Json.Serialize(templateAfter));
                    var report=Read(drawing,p,templateAfter.signature);File.WriteAllText(Path.Combine(output,p.id+".json"),Json.Serialize(report));
                    var scaleLabels=TemplateCapture.ScaleLabels(templateAfter.paper);
                    Need(scaleLabels.All(label=>label==TemplateCapture.ExpectedScaleLabel(p.scale)),"Title scale label differs from the view scale");
                    Need(templateAfter.signature==p.viewPlan.templateSignature,"Template changed after saved reopen");
                    CloseTarget(handler,drawing,false);opened=null;
                    var print=new D.PrintAttributes();print.PrinterInstance="DXF";print.Scale=1;
                    string export=Path.Combine(output,p.id+".dxf");Need(handler.PrintDrawing(drawing,print,export)&&File.Exists(export),"Native DXF export failed");
                    CheckSource(part,p);
                    results.Add(new {id=p.id,status="SAVED_REOPENED_EXPORTED",mark=drawing.Mark,sheet=drawing.SheetNumber,created=created,
                        objectId=TargetDrawingLookup.ObjectId(drawing),drawingId=TargetDrawingLookup.DrawingId(drawing)});
                    Console.WriteLine("SAVED "+p.id+" "+drawing.Mark);
                } catch(Exception e) {
                    failures++;results.Add(new {id=p.id,status="FAILED",error=e.ToString()});Console.WriteLine("FAILED "+p.id+" "+e.Message);
                    if(opened!=null) {CloseTarget(handler,opened,false);opened=null;}
                }
            }
        } finally {
            if(opened!=null)CloseTarget(handler,opened,false);
            Need(plane.SetCurrentTransformationPlane(previous),"Restore work plane failed");
            File.WriteAllText(Path.Combine(output,"batch.json"),Json.Serialize(new {owner=Owner,results=results,originalDrawingRestored=true,workPlaneRestored=true}));
        }
        return failures==0?0:1;
    }
    static void CloseTarget(D.DrawingHandler handler,D.SinglePartDrawing expected,bool saved) {
        var active=handler.GetActiveDrawing() as D.SinglePartDrawing;
        Need(active!=null&&active.Name==Owner&&active.PartIdentifier.ID==expected.PartIdentifier.ID&&active.SheetNumber==expected.SheetNumber,"Active drawing changed; refusing to touch it");
        Need(handler.CloseActiveDrawing(saved),"Close owned target failed");
    }
    static void CheckSource(M.Part part,Plan p) {
        Need(part.Select(),"Reread target failed");string position="";Need(part.GetReportProperty("PART_POS",ref position)&&position==p.position,"Part number changed");
        Need(part.Identifier.GUID.ToString()==p.guid,"Part GUID changed");
        var property=part.GetType().GetProperty("ModificationTime");Need(property!=null,"Modification time unavailable");
        var modified=(DateTime)property.GetValue(part,null);
        Need(modified.ToUniversalTime().Ticks==DateTime.Parse(p.modification,CultureInfo.InvariantCulture,DateTimeStyles.RoundtripKind).ToUniversalTime().Ticks,"Model version changed");
        var cs=part.GetDSTVCoordinateSystem();
        var sourceFrame=p.sourceFrame??p.frame;
        foreach(var pair in new[]{new[]{A(cs.Origin),sourceFrame.origin},new[]{A(cs.AxisX),sourceFrame.axisX},new[]{A(cs.AxisY),sourceFrame.axisY}})
            Need(pair[0].Zip(pair[1],(a,b)=>Math.Abs(a-b)).Max()<1e-6,"Coordinate system changed");
    }
    static D.View SingleView(D.SinglePartDrawing drawing) {
        var views=new List<D.View>();var e=drawing.GetSheet().GetAllViews();while(e.MoveNext())if(e.Current is D.View)views.Add((D.View)e.Current);
        var main=views.Where(v=>v.ViewType==D.View.ViewTypes.FrontView).ToList();
        Need(main.Count==1&&views.All(v=>v==main[0]||Owned(v)&&Tag(v,"COD_KEY").StartsWith("section:")),"Unexpected/unowned view; unsupported template");return main[0];
    }
    internal static D.View PrepareView(D.SinglePartDrawing drawing,bool created) {
        if(!created)return SingleView(drawing);
        var views=new List<D.View>();var e=drawing.GetSheet().GetAllViews();
        while(e.MoveNext())if(e.Current is D.View)views.Add((D.View)e.Current);
        var front=views.Where(v=>v.ViewType==D.View.ViewTypes.FrontView).ToList();
        Need(front.Count==1,"New flat-plate drawing requires exactly one front view");
        // Only views generated by this Insert are removable, never existing user views.
        foreach(var view in views)if(view!=front[0])Need(view.Delete(),"Remove generated secondary view failed");
        return front[0];
    }
    static bool Symbol(Plan p,int id) { return p.bolts!=null&&p.bolts.Any(b=>b.modelId==id&&b.display=="SYMBOL"); }
    static D.DimensionSetBaseAttributes.DimensionValueFormats Decimals(Plan p) {
        return p.display.decimals==0?D.DimensionSetBaseAttributes.DimensionValueFormats.NoDecimals:D.DimensionSetBaseAttributes.DimensionValueFormats.OneOptionalDecimal;
    }
    static bool Annotation(D.DrawingObject o) { return o is D.DimensionSetBase||o is D.Text||o is D.Line||o is D.Arc||o is D.Circle||o is D.MarkBase||o is D.RadiusDimension||o is D.SectionMark; }
    static void Write(D.SinglePartDrawing drawing,Plan p,bool created) {
        Need(drawing.Select()&&drawing.Name==Owner&&drawing.PartIdentifier.ID==p.id,"Write target ownership changed");
        Need(created||drawing.SheetNumber==p.ownedSheet,"Write sheet changed");
        Need(!drawing.IsLocked&&!drawing.IsIssued&&!drawing.IsFrozen,"Write target locked/issued/frozen");
        Need(p.sections==null||p.sections.Length==0||p.schemaVersion=="nativeDrawingPlan.v2","Sections require v2 plan");
        if(p.sections!=null)Need(p.sections.All(s=>s.cut!=null&&s.viewPlan!=null&&s.viewId!=null&&s.viewId.StartsWith("section:"))&&p.sections.Select(s=>s.viewId).Distinct().Count()==p.sections.Length,"Invalid/duplicate section plan");
        if(p.sections!=null)Need(p.sections.All(s=>Math.Abs(s.scale-p.scale)<1e-9&&s.viewPlan!=null&&Math.Abs(s.viewPlan.scale-p.scale)<1e-9),"Section scale differs from the main view");
        var main=SingleView(drawing);var others=new List<D.View>();var ve=drawing.GetSheet().GetAllViews();
        // Preflight EVERY owned view before deleting a previous section or annotation.
        while(ve.MoveNext()) {var view=ve.Current as D.View;if(view==null)continue;
            var items=view.GetAllObjects();while(items.MoveNext())if(Annotation(items.Current))Need(created||Owned(items.Current),"Manual annotation in view; refusing all changes");
            if(view.ViewType!=D.View.ViewTypes.FrontView){Need(Owned(view),"Unowned secondary view");others.Add(view);}
        }
        var sheetItems=drawing.GetSheet().GetObjects();while(sheetItems.MoveNext())if(Annotation(sheetItems.Current))Need(created||Owned(sheetItems.Current),"Manual sheet annotation; refusing all changes");
        foreach(var old in others)Need(old.Delete(),"Remove old owned section failed");
        WriteView(drawing,p,main,created);
        if(p.sections==null||p.sections.Length==0)return;
        Need(p.schemaVersion=="nativeDrawingPlan.v2","Sections require v2 plan");
        foreach(var section in p.sections) {
            Need(section.cut!=null&&section.viewPlan!=null,"Missing section cut/placement");
            var va=new D.View.ViewAttributes();va.Scale=section.scale;va.FixedViewPlacing=true;
            // An explicit owned label carries A-A; suppress inherited template tags.
            foreach(var tag in new[]{va.TagsAttributes.TagA1,va.TagsAttributes.TagA2,va.TagsAttributes.TagA3,va.TagsAttributes.TagA4,va.TagsAttributes.TagA5})tag.TagContent.Clear();
            var ma=new D.SectionMarkBase.SectionMarkAttributes();ma.MarkName=section.label;
            foreach(var tag in new[]{ma.TagsAttributes.TagA1,ma.TagsAttributes.TagA2,ma.TagsAttributes.TagA3,ma.TagsAttributes.TagA4,ma.TagsAttributes.TagA5})tag.TagContent.Clear();
            var nameTag=ma.TagsAttributes.TagA1;
            nameTag.ShowOnSide=D.SectionMarkBase.SectionMarkTagAttributes.TagShowOnSide.ShowOnBothSides;
            nameTag.TagRotation=D.SectionMarkBase.SectionMarkTagAttributes.TagTextRotation.AlwaysHorizontal;
            nameTag.Location=D.TagLocation.AboveLine;
            nameTag.TagContent.Add(new D.TextElement(section.label,new D.FontAttributes {Name="Arial",Height=p.viewPlan.fontHeight,Color=D.DrawingColors.Black}));
            ma.LineColor=D.DrawingColors.Black;ma.SymbolColor=D.DrawingColors.Black;
            ma.LineLength=p.viewPlan.fontHeight;ma.LineLengthOffset=0;ma.LineWidthOffsetLeft=0;ma.LineWidthOffsetRight=0;
            ma.LeftSymbol.Size=p.viewPlan.fontHeight;ma.RightSymbol.Size=p.viewPlan.fontHeight;
            D.View view;D.SectionMark mark;
            Need(D.View.CreateSectionView(main,P(section.cut.start),P(section.cut.end),new Point(),section.cut.depthUp,section.cut.depthDown,va,ma,out view,out mark)&&view!=null&&mark!=null,"Create native section failed");
            TagObject(view,section.viewId);TagObject(mark,"section-mark:"+section.viewId);
            section.id=p.id;section.position=p.position;section.ownedSheet=p.ownedSheet;section.sheet=p.sheet;section.display=p.display;
            section.sourceDigest=p.sourceDigest;section.planDigest=p.planDigest;
            WriteView(drawing,section,view,true);
        }
        PlaceView(main,p.viewPlan);
    }
    static void WriteView(D.SinglePartDrawing drawing,Plan p,D.View view,bool created) {
        Need(drawing.Select(),"Ownership check at write entry failed");
        Need(p.display!=null&&p.radii!=null&&p.bolts!=null,"Plan predates the P6 display contract; rebuild it");
        Need(drawing.Name==Owner&&drawing.PartIdentifier.ID==p.id,"Write scope must be the explicitly owned target trial");
        Need(created||drawing.SheetNumber==p.ownedSheet,"Write sheet differs from verified ownership manifest");
        Need(!drawing.IsLocked&&!drawing.IsIssued&&!drawing.IsFrozen,"Write target locked/issued/frozen");
        if(!created) {
            // Preflight before changing even view placement or model visibility.
            var inspect=view.GetAllObjects();while(inspect.MoveNext())if(Annotation(inspect.Current))Need(Owned(inspect.Current),"Manual/unowned annotation present; refusing all changes");
            var sheetObjects=drawing.GetSheet().GetObjects();while(sheetObjects.MoveNext())if(Annotation(sheetObjects.Current))Need(Owned(sheetObjects.Current),"Manual sheet annotation present; refusing all changes");
        }
        var cs=new CoordinateSystem(P(p.frame.origin),new Vector(P(p.frame.axisX)),new Vector(P(p.frame.axisY)));
        view.DisplayCoordinateSystem=cs;view.Attributes.Scale=p.scale;view.Attributes.FixedViewPlacing=true;
        view.Attributes.Shortening.CutParts=false;view.Attributes.Shortening.CutSkewParts=false;Need(view.Modify(),"View setup failed");
        var remove=new List<D.DrawingObject>();D.Part target=null;var objects=view.GetAllObjects();
        while(objects.MoveNext()) {
            var o=objects.Current;
            var part=o as D.Part;if(part!=null) {
                if(part.ModelIdentifier.ID!=p.id)part.Hideable.HideFromDrawingView();
                else { target=part;var a=part.Attributes;a.Representation=D.Part.Representation.Exact;a.DrawReferenceLine=false;a.DrawCenterLine=false;a.DrawOrientationMark=false;a.DrawConnectingSideMarks=false;a.DrawPopMarks=false;a.DrawHiddenLines=false;a.DrawOwnHiddenLines=false;part.Attributes=a;
                    if(p.geometryMode=="ANALYTIC_NATIVE_CURVES") {
                        a.VisibleLines.Color=D.DrawingColors.Invisible;a.HiddenLines.Color=D.DrawingColors.Invisible;
                        a.FaceHatch.Name="None";a.FaceHatch.DrawBackgroundColor=false;
                        a.SectionFaceHatch.Name="None";a.SectionFaceHatch.DrawBackgroundColor=false;part.Attributes=a;
                    }
                    part.Hideable.ShowInDrawingView();
                }
                Need(part.Modify(),"Part display failed");continue;
            }
            var bolt=o as D.Bolt;if(bolt!=null) {
                var a=bolt.Attributes;
                if(Symbol(p,bolt.ModelIdentifier.ID)) {
                    // Planned symbol: every position is exactly one final bolt hole.
                    a.Representation=D.Bolt.Representation.Symbol2;a.Color=D.DrawingColors.Green;
                    a.SymbolContainsHole=true;a.SymbolContainsAxis=true;bolt.Attributes=a;
                    bolt.Hideable.ShowInDrawingView();Need(bolt.Modify(),"Show bolt symbol failed");continue;
                }
                // Hideable alone retains Solid hardware in the drawing database.
                // Final hole boundaries and center lines are separate owned curves.
                a.Representation=D.Bolt.Representation.Symbol;a.Color=D.DrawingColors.Invisible;
                a.SymbolContainsHole=false;a.SymbolContainsAxis=false;bolt.Attributes=a;
                bolt.Hideable.HideFromDrawingView();Need(bolt.Modify(),"Suppress source bolt graphics failed");continue;
            }
            if(Annotation(o)) { Need(created||Owned(o),"Manual/unowned annotation present; refusing replacement");remove.Add(o); }
        }
        Need(target!=null,"Target drawing part missing");foreach(var o in remove)Need(o.Delete(),"Delete owned annotation failed");
        foreach(var d in p.dimensions) {
            var points=new D.PointList();foreach(var pt in d.coordinates)points.Add(P(pt));
            var a=new D.StraightDimensionSet.StraightDimensionSetAttributes(target);
            a.Arrowhead.Head=D.ArrowheadTypes.FilledArrow;a.Arrowhead.Height=p.viewPlan.fontHeight*.8;a.Arrowhead.Width=p.viewPlan.fontHeight*.8;a.Color=D.DrawingColors.Black;
            a.Text.Font.Color=D.DrawingColors.Black;a.Text.Font.Height=p.viewPlan.fontHeight;a.Text.Font.Name="Arial";
            a.Format.Precision=D.DimensionSetBaseAttributes.DimensionValuePrecisions.OnePerTen;a.Format.Format=Decimals(p);
            a.Placing.Placing=D.DimensionSetBaseAttributes.Placings.Fixed;a.DimensionType=D.DimensionSetBaseAttributes.DimensionTypes.Relative;
            a.ShortDimension=(D.DimensionSetBaseAttributes.ShortDimensionTypes)Enum.Parse(typeof(D.DimensionSetBaseAttributes.ShortDimensionTypes),p.display.shortDimension);
            if(d.suppressWitnesses)a.ExtensionLine=D.DimensionSetBaseAttributes.ExtensionLineTypes.Yes;
            // Tekla itself combines equal consecutive intervals as n@p.
            a.CombinedDimension.MinimumNumberToCombine=p.display.combineMinimum;
            a.CombinedDimension.Format=d.combine?(D.DimensionSetBaseAttributes.CombineFormats)Enum.Parse(typeof(D.DimensionSetBaseAttributes.CombineFormats),p.display.combineFormat):D.DimensionSetBaseAttributes.CombineFormats.Off;
            var dim=new D.StraightDimensionSetHandler().CreateDimensionSet(view,points,new Vector(d.up[0],d.up[1],0),d.distance,a);
            Need(dim!=null,"Dimension insert failed: "+d.key);TagObject(dim,d.key);
        }
        foreach(var r in p.radii) {
            var a=new D.RadiusDimensionAttributes();a.Color=D.DrawingColors.Black;
            a.Arrowhead.Head=D.ArrowheadTypes.FilledArrow;a.Arrowhead.Height=p.viewPlan.fontHeight*.8;a.Arrowhead.Width=p.viewPlan.fontHeight*.8;
            a.Text.Font.Color=D.DrawingColors.Black;a.Text.Font.Height=p.viewPlan.fontHeight;a.Text.Font.Name="Arial";
            a.Format.Precision=D.DimensionSetBaseAttributes.DimensionValuePrecisions.OnePerTen;a.Format.Format=Decimals(p);
            var rd=new D.RadiusDimension(view,P(r.points[0]),P(r.points[1]),P(r.points[2]),r.distance,a);
            Need(rd.Insert(),"Radius dimension insert failed: "+r.key);TagObject(rd,r.key);
        }
        foreach(var n in p.notes) {
            var a=new D.Text.TextAttributes();a.Font.Name="Arial";a.Font.Height=n.height;a.Font.Color=D.DrawingColors.Black;a.Alignment=D.TextAlignment.Center;
            a.Frame.Type=D.FrameTypes.None;a.PlacingAttributes.IsFixed=true;a.ArrowHead.Head=n.tip==null?D.ArrowheadTypes.NoArrow:D.ArrowheadTypes.FilledArrow;
            a.ArrowHead.Height=p.viewPlan.fontHeight*.8;a.ArrowHead.Width=p.viewPlan.fontHeight*.8;a.PreferredPlacing=n.tip==null?D.PreferredTextPlacingTypes.PointPlacingType():D.PreferredTextPlacingTypes.LeaderLinePlacingType();
            var text=n.tip==null?new D.Text(view,P(n.at),n.text,new D.PointPlacing(),a):new D.Text(view,P(n.at),n.text,new D.LeaderLinePlacing(P(n.tip)),a);
            Need(text.Insert(),"Text insert failed: "+n.key);TagObject(text,n.key);
        }
        foreach(var c in p.curves) {
            D.DrawingObject o;
            if(c.kind=="line") {var l=new D.Line(view,P(c.start),P(c.end));l.Attributes.Line.Type=c.role=="center"?D.LineTypes.DashDot:D.LineTypes.SolidLine;l.Attributes.Line.Color=D.DrawingColors.Black;l.Attributes.Arrowhead.Head=D.ArrowheadTypes.NoArrow;o=l;}
            else if(c.kind=="circle") {var circle=new D.Circle(view,P(c.center),c.radius);circle.Attributes.Line.Type=D.LineTypes.SolidLine;circle.Attributes.Line.Color=D.DrawingColors.Black;o=circle;}
            else if(c.kind=="arc") {var arc=new D.Arc(view,P(c.start),P(c.end),P(c.center));arc.Attributes.Line.Type=D.LineTypes.SolidLine;arc.Attributes.Line.Color=D.DrawingColors.Black;o=arc;}
            else throw new Exception("Unknown curve kind");
            Need(o.Insert(),"Curve insert failed: "+c.key);TagObject(o,c.key);
        }
        drawing.Layout.SizeDefinitionMode=D.SizeDefinitionMode.SpecifiedSize;drawing.Layout.SheetSize=new D.Size(p.sheet[0],p.sheet[1]);Need(drawing.Modify(),"Sheet size failed");
        drawing.CommitChanges();Need(view.Select(),"View refresh failed");
        PlaceView(view,p.viewPlan);
    }
    static void PlaceView(D.View view,ViewPlan plan) {
        Need(view.Select(),"Refresh placed view failed");var area=plan.availableArea;
        Need(area!=null&&area.Length==4&&area.All(v=>!Double.IsNaN(v)&&!Double.IsInfinity(v)),"Invalid template free area");
        if(!plan.previewOverflow)Need(view.Width<=area[2]-area[0]&&view.Height<=area[3]-area[1],"Actual view exceeds template free area");
        view.Origin=new Point((area[0]+area[2]-view.Width)/2-view.FrameOrigin.X,(area[1]+area[3]-view.Height)/2-view.FrameOrigin.Y,0);
        Need(view.Modify(),"View placement failed");
    }
    static object Read(D.SinglePartDrawing drawing,Plan p,string templateSignature) {
        var main=SingleView(drawing);
        int viewCount=0;var allViews=drawing.GetSheet().GetAllViews();while(allViews.MoveNext())if(allViews.Current is D.View)viewCount++;
        Need(viewCount==1+(p.sections==null?0:p.sections.Length),"Missing/extra views in saved drawing");
        var result=Json.Deserialize<Dictionary<string,object>>(Json.Serialize(ReadView(drawing,p,main,templateSignature)));
        if(p.sections!=null&&p.sections.Length>0) {
            var sections=new List<object>();var views=new List<D.View>();var e=drawing.GetSheet().GetAllViews();
            while(e.MoveNext())if(e.Current is D.View&&((D.View)e.Current).ViewType!=D.View.ViewTypes.FrontView)views.Add((D.View)e.Current);
            Need(views.Count==p.sections.Length,"Missing/extra section views");
            foreach(var section in p.sections) {
                var matches=views.Where(v=>Owned(v)&&Tag(v,"COD_KEY")==section.viewId).ToList();Need(matches.Count==1,"Section view identity mismatch");
                section.id=p.id;section.sourceDigest=p.sourceDigest;section.planDigest=p.planDigest;
                sections.Add(ReadView(drawing,section,matches[0],templateSignature));
            }
            result["schemaVersion"]="nativeDrawingReadback.v2";result["sections"]=sections;
        }
        return result;
    }
    static object ReadView(D.SinglePartDrawing drawing,Plan p,D.View view,string templateSignature) {
        Need(drawing.Select(),"Drawing readback failed");Need(view.Select(),"View readback failed");
        var rows=new List<object>();var bolts=new List<object>();var modelParts=new List<object>();var inventory=new Dictionary<string,int>();var e=view.GetAllObjects();int visible=0;int targets=0;
        while(e.MoveNext()) {
            var o=e.Current;Need(o.Select(),"Object readback failed");string key=Tag(o,"COD_KEY");
            string type=o.GetType().Name;if(!inventory.ContainsKey(type))inventory[type]=0;inventory[type]++;
            var part=o as D.Part;if(part!=null) {
                var a=part.Attributes;bool suppressed=a.VisibleLines.Color==D.DrawingColors.Invisible;
                if(part.ModelIdentifier.ID==p.id) {targets++;Need(!part.Hideable.IsHidden,"Target excluded from material table");
                    if(p.geometryMode=="ANALYTIC_NATIVE_CURVES")Need(suppressed&&a.HiddenLines.Color==D.DrawingColors.Invisible&&
                        a.FaceHatch.Name=="None"&&!a.FaceHatch.DrawBackgroundColor&&a.SectionFaceHatch.Name=="None"&&!a.SectionFaceHatch.DrawBackgroundColor&&
                        !a.DrawReferenceLine&&!a.DrawCenterLine&&!a.DrawHiddenLines&&!a.DrawOwnHiddenLines&&!a.DrawOrientationMark&&!a.DrawConnectingSideMarks&&!a.DrawPopMarks,
                        "Analytic source rendering not fully suppressed");
                }
                if(!part.Hideable.IsHidden&&!suppressed) {visible++;Need(part.ModelIdentifier.ID==p.id,"Visible neighbour");}
                modelParts.Add(new {modelId=part.ModelIdentifier.ID,hidden=part.Hideable.IsHidden,visibleLineColor=a.VisibleLines.Color.ToString(),
                    hiddenLineColor=a.HiddenLines.Color.ToString(),faceHatch=a.FaceHatch.Name,sectionHatch=a.SectionFaceHatch.Name});continue;
            }
            var bolt=o as D.Bolt;if(bolt!=null) {var a=bolt.Attributes;bolts.Add(new {modelId=bolt.ModelIdentifier.ID,hidden=bolt.Hideable.IsHidden,color=a.Color.ToString(),representation=a.Representation.ToString(),hole=a.SymbolContainsHole,axis=a.SymbolContainsAxis});Need(bolt.Hideable.IsHidden||Symbol(p,bolt.ModelIdentifier.ID),"Source bolt symbol still visible");continue;}
            if(!Annotation(o))continue;Need(Owned(o),"Unowned annotation in readback");
            var sectionMark=o as D.SectionMark;if(sectionMark!=null) {rows.Add(new {kind="section_mark",key=key,id=Tag(o,"COD_ID"),label=sectionMark.Attributes.MarkName,start=A(sectionMark.LeftPoint),end=A(sectionMark.RightPoint)});continue;}
            var dim=o as D.StraightDimensionSet;
            if(dim!=null) {
                var children=new List<object>();Vector up=null;var ce=dim.GetObjects();while(ce.MoveNext()) {var child=ce.Current as D.StraightDimension;if(child==null)continue;Need(child.Select(),"Dimension child read failed");up=child.UpDirection;children.Add(new {start=A(child.StartPoint),end=A(child.EndPoint),text=child.Value.GetUnformattedString()});}
                rows.Add(new {kind="dimension",key=key,id=Tag(o,"COD_ID"),children=children,distance=dim.Distance,up=up==null?null:A(up),arrow=dim.Attributes.Arrowhead.Head.ToString(),textColor=dim.Attributes.Text.Font.Color.ToString(),textHeight=dim.Attributes.Text.Font.Height,combine=dim.Attributes.CombinedDimension.Format.ToString(),extension=dim.Attributes.ExtensionLine.ToString(),shortDimension=dim.Attributes.ShortDimension.ToString()});continue;
            }
            var radius=o as D.RadiusDimension;if(radius!=null) {rows.Add(new {kind="radius_dimension",key=key,id=Tag(o,"COD_ID"),points=new[]{A(radius.ArcPoint1),A(radius.ArcPoint2),A(radius.ArcPoint3)},distance=radius.Distance,arrow=radius.Attributes.Arrowhead.Head.ToString(),textHeight=radius.Attributes.Text.Font.Height,format=radius.Attributes.Format.Format.ToString()});continue;}
            var text=o as D.Text;if(text!=null) {var b=text.GetAxisAlignedBoundingBox();var leader=text.Placing as D.LeaderLinePlacing;rows.Add(new {kind="text",key=key,id=Tag(o,"COD_ID"),text=text.TextString,at=A(text.InsertionPoint),tip=leader==null?null:A(leader.StartPoint),arrow=text.Attributes.ArrowHead.Head.ToString(),height=text.Attributes.Font.Height,boxMin=A(b.MinPoint),boxMax=A(b.MaxPoint)});continue;}
            var line=o as D.Line;if(line!=null) {rows.Add(new {kind="line",key=key,id=Tag(o,"COD_ID"),start=A(line.StartPoint),end=A(line.EndPoint),style=line.Attributes.Line.Type.ToString()});continue;}
            var circle=o as D.Circle;if(circle!=null) {rows.Add(new {kind="circle",key=key,id=Tag(o,"COD_ID"),center=A(circle.CenterPoint),radius=circle.Radius});continue;}
            var arc=o as D.Arc;if(arc!=null) {rows.Add(new {kind="arc",key=key,id=Tag(o,"COD_ID"),start=A(arc.StartPoint),end=A(arc.EndPoint),radius=arc.Radius});continue;}
            throw new Exception("Unexpected annotation in readback: "+o.GetType().Name);
        }
        Need(targets==1&&visible==(p.geometryMode=="ANALYTIC_NATIVE_CURVES"?0:1),"Unexpected target visibility/model association");
        return new {schemaVersion="nativeDrawingReadback.v1",id=p.id,viewId=p.viewId??"main",viewType=view.ViewType.ToString(),mark=drawing.Mark,sheet=drawing.SheetNumber,name=drawing.Name,
            sourceDigest=p.sourceDigest,planDigest=p.planDigest,geometryMode=p.geometryMode,linkedTargetCount=targets,visiblePartCount=visible,stage="SAVED_REOPENED",scale=view.Attributes.Scale,origin=A(view.Origin),
            frameOrigin=A(view.FrameOrigin),width=view.Width,height=view.Height,sheetSize=new[]{drawing.Layout.SheetSize.Width,drawing.Layout.SheetSize.Height},templateSignature=templateSignature,
            objects=rows,sourceBolts=bolts,sourceParts=modelParts,objectInventory=inventory,displayFrame=new {origin=A(view.DisplayCoordinateSystem.Origin),axisX=A(view.DisplayCoordinateSystem.AxisX),axisY=A(view.DisplayCoordinateSystem.AxisY)},
            viewFrame=new {origin=A(view.ViewCoordinateSystem.Origin),axisX=A(view.ViewCoordinateSystem.AxisX),axisY=A(view.ViewCoordinateSystem.AxisY)}};
    }
}
