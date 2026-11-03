// Sheet or source-bound model-view capability samples; newly registered test pages only.
using System;
using System.Linq;
using System.Reflection;
using System.Collections.Generic;
using D=Tekla.Structures.Drawing;
using G=Tekla.Structures.Geometry3d;

public class PagePrimitive {
    public string key,kind,font,text,shortDimension,textPlacing;
    public string space,sourceDigest;
    public Dictionary<string,string> anchorRefs;
    public double height,distance;
    public double? minimalDistance,searchMargin,arrowHeight,arrowWidth;
    public double[] start,end,up,at,tip;
    public double[][] elbows;
    public double[][] points;
    public string[] pointRefs;
    public int? decimals;
    public PageDimensionExaggeration exaggeration;
}
public class PageDimensionExaggeration {
    public string direction,origin;
    public double width,height;
    public double? position;
}
internal static class NativePrimitiveProbe {
    static void Need(bool ok,string message) {if(!ok)throw new InvalidOperationException(message);}
    static bool Finite(double n) {return !Double.IsNaN(n)&&!Double.IsInfinity(n);}
    static bool Point(double[] p,int size) {return p!=null&&p.Length==size&&p.All(Finite);}
    static G.Point P(double[] p) {return new G.Point(p[0],p[1],p.Length==3?p[2]:0);}
    static int Id(D.DrawingObject obj) {
        var p=typeof(D.DatabaseObject).GetProperty("Identifier",BindingFlags.Instance|BindingFlags.NonPublic);
        Need(p!=null,"Primitive ID unavailable");return ((Tekla.Structures.Identifier)p.GetValue(obj,null)).ID;
    }
    internal static void Validate(PagePrimitive[] samples) {
        if(samples==null)return;
        Need(samples.Length>0&&samples.Select(s=>s.key).Distinct().Count()==samples.Length,"Duplicate/empty primitive samples");
        foreach(var s in samples) {
            Need(s.space==null||s.space=="MODEL_GLOBAL","Unknown primitive coordinate space");
            int size=s.space==null?2:3;
            if(size==3)Need(!String.IsNullOrWhiteSpace(s.sourceDigest)&&s.anchorRefs!=null&&
                s.anchorRefs.Keys.OrderBy(k=>k).SequenceEqual(s.kind=="dimension"?new[]{"end","start"}:s.kind=="dimensionGroup"?new string[0]:s.kind=="label"?new string[0]:new[]{"tip"})&&
                s.anchorRefs.Values.All(v=>!String.IsNullOrWhiteSpace(v)),"Missing model anchor binding");
            Need(!String.IsNullOrWhiteSpace(s.key)&&!String.IsNullOrWhiteSpace(s.font)&&Finite(s.height)&&s.height>0,"Invalid primitive style");
            Need(s.arrowHeight.HasValue==s.arrowWidth.HasValue&&(!s.arrowHeight.HasValue||
                (Finite(s.arrowHeight.Value)&&s.arrowHeight.Value>0&&s.arrowHeight.Value==s.arrowWidth.Value)),"Only positive equal arrow dimensions are calibrated");
            if(s.kind=="dimension"||s.kind=="dimensionGroup") {
                bool group=s.kind=="dimensionGroup";
                if(group)Need(size==3&&s.points!=null&&s.points.Length>=2&&s.points.All(p=>Point(p,size))&&
                    s.pointRefs!=null&&s.pointRefs.Length==s.points.Length&&s.pointRefs.Distinct().Count()==s.pointRefs.Length&&
                    (s.decimals==0||s.decimals==1),"Invalid complete dimension group");
                Need((group||(Point(s.start,size)&&Point(s.end,size)&&!s.start.SequenceEqual(s.end)))&&Point(s.up,size)&&
                    Math.Abs(s.up.Sum(v=>v*v)-1)<1e-9&&
                    (group||Math.Abs(Enumerable.Range(0,size).Sum(i=>(s.end[i]-s.start[i])*s.up[i]))<1e-6)&&
                    Finite(s.distance)&&s.distance>0&&(s.shortDimension=="Inside"||s.shortDimension=="Outside"),"Invalid dimension sample");
                Need(s.textPlacing==null||s.textPlacing=="AboveDimensionLine"||s.textPlacing=="OnDimensionLine","Invalid dimension text placing");
                Need(s.minimalDistance.HasValue==s.searchMargin.HasValue&&(!s.minimalDistance.HasValue||
                    (Finite(s.minimalDistance.Value)&&s.minimalDistance.Value>=0&&Finite(s.searchMargin.Value)&&s.searchMargin.Value>=0)),"Invalid dimension placing distances");
                var x=s.exaggeration;
                if(x!=null)Need(new[]{"LeftDown","RightUp","Both"}.Contains(x.direction)&&
                    new[]{"NearDimensionLine","NearMeasuredObject"}.Contains(x.origin)&&
                    Finite(x.width)&&x.width>0&&Finite(x.height)&&x.height>0&&x.position.HasValue&&Finite(x.position.Value)&&x.position.Value>=0,"Invalid dimension exaggeration");
            } else {
                Need(s.textPlacing==null&&!s.minimalDistance.HasValue&&!s.searchMargin.HasValue&&s.exaggeration==null,"Dimension controls on leader");
                Need((s.kind=="label"&&size==3&&Point(s.at,size)||s.kind=="leader"&&Point(s.at,size)&&Point(s.tip,size)&&!s.at.SequenceEqual(s.tip)&&
                    s.elbows!=null&&s.elbows.All(p=>Point(p,size)))&&!String.IsNullOrWhiteSpace(s.text),"Invalid text sample");
            }
        }
    }
    internal static void Insert(D.SinglePartDrawing drawing,PagePrimitive[] samples) {
        if(samples==null)return;
        D.Part target=null;int targetCount=0;var views=drawing.GetSheet().GetAllViews();
        while(views.MoveNext()) {
            var view=views.Current as D.View;if(view==null)continue;var objects=view.GetAllObjects();
            while(objects.MoveNext()) {
                var part=objects.Current as D.Part;
                if(part!=null&&part.ModelIdentifier.ID==drawing.PartIdentifier.ID){target=part;targetCount++;}
            }
        }
        Need(targetCount==1,"Primitive study requires one target part view");
        foreach(var s in samples) {
            var view=target.GetView() as D.View;bool inModel=s.space=="MODEL_GLOBAL";
            Need(!inModel||(view!=null&&view.Select()&&!view.Attributes.Shortening.CutParts),"Invalid model target view");
            D.ViewBase container=inModel?(D.ViewBase)view:drawing.GetSheet();
            var matrix=inModel?G.MatrixFactory.ToCoordinateSystem(view.DisplayCoordinateSystem):null;
            Func<double[],G.Point> point=p=>{var q=inModel?matrix.Transform(P(p)):P(p);return new G.Point(q.X,q.Y,0);};
            D.DrawingObject obj;
            if(s.kind=="dimension"||s.kind=="dimensionGroup") {
                var a=new D.StraightDimensionSet.StraightDimensionSetAttributes(target);
                a.Arrowhead.Head=D.ArrowheadTypes.FilledArrow;a.Arrowhead.Height=s.arrowHeight??s.height*.8;a.Arrowhead.Width=s.arrowWidth??s.height*.8;
                a.Color=D.DrawingColors.Black;a.Text.Font.Color=D.DrawingColors.Black;a.Text.Font.Height=s.height;a.Text.Font.Name=s.font;
                a.Placing.Placing=D.DimensionSetBaseAttributes.Placings.Fixed;
                if(s.textPlacing!=null)a.Text.TextPlacing=(D.DimensionSetBaseAttributes.DimensionTextPlacings)Enum.Parse(typeof(D.DimensionSetBaseAttributes.DimensionTextPlacings),s.textPlacing);
                if(s.minimalDistance.HasValue) {
                    a.Placing.Distance.MinimalDistance=s.minimalDistance.Value;
                    a.Placing.Distance.SearchMargin=s.searchMargin.Value;
                }
                if(s.exaggeration!=null) {
                    var x=s.exaggeration;
                    a.Exaggeration=new D.DimensionSetBaseAttributes.DimensionExaggerationAttributes(true,
                        (D.DimensionSetBaseAttributes.ExaggerationDirections)Enum.Parse(typeof(D.DimensionSetBaseAttributes.ExaggerationDirections),x.direction),
                        (D.DimensionSetBaseAttributes.ExaggerationOrigins)Enum.Parse(typeof(D.DimensionSetBaseAttributes.ExaggerationOrigins),x.origin),
                        x.width,x.position.Value,x.height);
                }
                a.DimensionType=D.DimensionSetBaseAttributes.DimensionTypes.Relative;
                a.ShortDimension=(D.DimensionSetBaseAttributes.ShortDimensionTypes)Enum.Parse(typeof(D.DimensionSetBaseAttributes.ShortDimensionTypes),s.shortDimension);
                a.CombinedDimension.Format=D.DimensionSetBaseAttributes.CombineFormats.Off;
                a.Format.Precision=D.DimensionSetBaseAttributes.DimensionValuePrecisions.OnePerTen;
                a.Format.Format=s.decimals==0?D.DimensionSetBaseAttributes.DimensionValueFormats.NoDecimals:D.DimensionSetBaseAttributes.DimensionValueFormats.OneOptionalDecimal;
                a.Format.Unit=D.DimensionSetBaseAttributes.DimensionValueUnits.Millimeter;
                var points=new D.PointList();
                foreach(var p in s.kind=="dimensionGroup"?s.points:new[]{s.start,s.end})points.Add(point(p));
                var up=point(s.up);var zero=point(inModel?new[]{0d,0d,0d}:new[]{0d,0d});
                obj=new D.StraightDimensionSetHandler().CreateDimensionSet(container,points,new G.Vector(up.X-zero.X,up.Y-zero.Y,0),
                    s.distance*(inModel?view.Attributes.Scale:1),a);
                Need(obj!=null,"Dimension probe insert failed: "+s.key);
            } else {
                var a=new D.Text.TextAttributes();a.Font.Name=s.font;a.Font.Height=s.height;a.Font.Color=D.DrawingColors.Black;
                a.Alignment=D.TextAlignment.Left;a.Frame.Type=D.FrameTypes.None;a.PlacingAttributes.IsFixed=true;
                a.ArrowHead.Head=D.ArrowheadTypes.FilledArrow;a.ArrowHead.Height=s.arrowHeight??s.height*.8;a.ArrowHead.Width=s.arrowWidth??s.height*.8;
                a.PreferredPlacing=s.kind=="label"?D.PreferredTextPlacingTypes.PointPlacingType():D.PreferredTextPlacingTypes.LeaderLinePlacingType();
                if(s.kind=="label")a.ArrowHead.Head=D.ArrowheadTypes.NoArrow;
                var text=s.kind=="label"?new D.Text(container,point(s.at),s.text,new D.PointPlacing(),a):
                    new D.Text(container,point(s.at),s.text,new D.LeaderLinePlacing(point(s.tip)),a);
                Need(text.Insert(),"Leader text insert failed: "+s.key);obj=text;
                var children=text.GetObjects();int count=0;
                while(children.MoveNext()) {
                    var line=children.Current as D.LeaderLine;if(line==null)continue;count++;
                    Need(line.Select(),"Leader refresh failed");
                    if(s.elbows.Length>0) {
                        line.ElbowPoints.Clear();foreach(var p in s.elbows)line.ElbowPoints.Add(point(p));
                        Need(line.Modify(),"New test leader elbow control failed");
                    }
                }
                Need(count==(s.kind=="label"?0:1),"Unexpected native leader child count");
            }
            Need(obj.SetUserProperty("IL_PRIMITIVE",s.key)&&obj.SetUserProperty("COD_OWNER","NATIVE-PLATE-R1"),"Primitive tag failed");
        }
    }
    internal static object Capture(D.SinglePartDrawing drawing,PagePrimitive[] samples) {
        var rows=new List<object>();if(samples==null)return rows;
        var objects=new List<D.DrawingObject>();var e=drawing.GetSheet().GetObjects();while(e.MoveNext())objects.Add(e.Current);
        if(samples.Any(s=>s.space=="MODEL_GLOBAL")) {
            var ve=drawing.GetSheet().GetAllViews();while(ve.MoveNext()){var v=ve.Current as D.View;if(v==null)continue;
                var oe=v.GetObjects();while(oe.MoveNext())objects.Add(oe.Current);}
        }
        var keys=new HashSet<string>();
        foreach(var obj in objects) {
            string key="";obj.GetUserProperty("IL_PRIMITIVE",ref key);if(String.IsNullOrEmpty(key))continue;
            Need(keys.Add(key)&&samples.Any(s=>s.key==key)&&obj.Select(),"Unknown/duplicate primitive");
            var children=new List<object>();var set=obj as D.StraightDimensionSet;var text=obj as D.Text;
            Need(set!=null||text!=null,"Unexpected primitive type");
            var ce=set!=null?set.GetObjects():text.GetObjects();
            while(ce.MoveNext()) {
                var child=ce.Current;Need(child.Select(),"Primitive child refresh failed");
                children.Add(new {objectId=Id(child),data=NativePageCapture.Props(child,"StartPoint","EndPoint","UpDirection","Value","Attributes","ElbowPoints","LeaderLineType")});
            }
            var row=new Dictionary<string,object>{{"key",key},{"objectId",Id(obj)},
                {"data",NativePageCapture.Props(obj,"Attributes","StartPoint","EndPoint","UpDirection","Distance","TextString","InsertionPoint","Placing")},{"children",children}};
            if(samples.Single(s=>s.key==key).space=="MODEL_GLOBAL") {
                var v=obj.GetView() as D.View;Need(v!=null&&v.Select(),"Model primitive lost view");
                var parts=new List<D.Part>();var pe=v.GetObjects();while(pe.MoveNext()){var part=pe.Current as D.Part;if(part!=null)parts.Add(part);}
                Need(parts.Count==1&&parts[0].ModelIdentifier.ID==drawing.PartIdentifier.ID,"Ambiguous model-view target");
                row["viewBinding"]=new {viewId=Id(v),targetObjectId=Id(parts[0]),modelPartId=parts[0].ModelIdentifier.ID,
                    view=NativePageCapture.Props(v,"Origin","Attributes","DisplayCoordinateSystem","ViewCoordinateSystem")};
            }
            rows.Add(row);
        }
        Need(keys.Count==samples.Length,"Missing primitive samples");return rows;
    }
}
