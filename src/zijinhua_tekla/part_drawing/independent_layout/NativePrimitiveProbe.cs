// Synthetic sheet-space capability samples; invoked only on newly registered test pages.
using System;
using System.Linq;
using System.Reflection;
using System.Collections.Generic;
using D=Tekla.Structures.Drawing;
using G=Tekla.Structures.Geometry3d;

public class PagePrimitive {
    public string key,kind,font,text,shortDimension;
    public double height,distance;
    public double[] start,end,up,at,tip;
    public double[][] elbows;
}
internal static class NativePrimitiveProbe {
    static void Need(bool ok,string message) {if(!ok)throw new InvalidOperationException(message);}
    static bool Finite(double n) {return !Double.IsNaN(n)&&!Double.IsInfinity(n);}
    static bool Point(double[] p) {return p!=null&&p.Length==2&&p.All(Finite);}
    static G.Point P(double[] p) {return new G.Point(p[0],p[1],0);}
    static int Id(D.DrawingObject obj) {
        var p=typeof(D.DatabaseObject).GetProperty("Identifier",BindingFlags.Instance|BindingFlags.NonPublic);
        Need(p!=null,"Primitive ID unavailable");return ((Tekla.Structures.Identifier)p.GetValue(obj,null)).ID;
    }
    internal static void Validate(PagePrimitive[] samples) {
        if(samples==null)return;
        Need(samples.Length>0&&samples.Select(s=>s.key).Distinct().Count()==samples.Length,"Duplicate/empty primitive samples");
        foreach(var s in samples) {
            Need(!String.IsNullOrWhiteSpace(s.key)&&!String.IsNullOrWhiteSpace(s.font)&&Finite(s.height)&&s.height>0,"Invalid primitive style");
            if(s.kind=="dimension") {
                Need(Point(s.start)&&Point(s.end)&&!s.start.SequenceEqual(s.end)&&Point(s.up)&&
                    Math.Abs(s.up[0]*s.up[0]+s.up[1]*s.up[1]-1)<1e-9&&
                    Math.Abs((s.end[0]-s.start[0])*s.up[0]+(s.end[1]-s.start[1])*s.up[1])<1e-6&&
                    Finite(s.distance)&&s.distance>0&&(s.shortDimension=="Inside"||s.shortDimension=="Outside"),"Invalid dimension sample");
            } else {
                Need(s.kind=="leader"&&Point(s.at)&&Point(s.tip)&&!s.at.SequenceEqual(s.tip)&&
                    !String.IsNullOrWhiteSpace(s.text)&&s.elbows!=null&&s.elbows.All(Point),"Invalid leader sample");
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
            D.DrawingObject obj;
            if(s.kind=="dimension") {
                var a=new D.StraightDimensionSet.StraightDimensionSetAttributes(target);
                a.Arrowhead.Head=D.ArrowheadTypes.FilledArrow;a.Arrowhead.Height=s.height*.8;a.Arrowhead.Width=s.height*.8;
                a.Color=D.DrawingColors.Black;a.Text.Font.Color=D.DrawingColors.Black;a.Text.Font.Height=s.height;a.Text.Font.Name=s.font;
                a.Placing.Placing=D.DimensionSetBaseAttributes.Placings.Fixed;
                a.DimensionType=D.DimensionSetBaseAttributes.DimensionTypes.Relative;
                a.ShortDimension=(D.DimensionSetBaseAttributes.ShortDimensionTypes)Enum.Parse(typeof(D.DimensionSetBaseAttributes.ShortDimensionTypes),s.shortDimension);
                a.CombinedDimension.Format=D.DimensionSetBaseAttributes.CombineFormats.Off;
                a.Format.Precision=D.DimensionSetBaseAttributes.DimensionValuePrecisions.OnePerTen;
                a.Format.Format=D.DimensionSetBaseAttributes.DimensionValueFormats.OneOptionalDecimal;
                a.Format.Unit=D.DimensionSetBaseAttributes.DimensionValueUnits.Millimeter;
                var points=new D.PointList();points.Add(P(s.start));points.Add(P(s.end));
                obj=new D.StraightDimensionSetHandler().CreateDimensionSet(drawing.GetSheet(),points,new G.Vector(s.up[0],s.up[1],0),s.distance,a);
                Need(obj!=null,"Dimension probe insert failed: "+s.key);
            } else {
                var a=new D.Text.TextAttributes();a.Font.Name=s.font;a.Font.Height=s.height;a.Font.Color=D.DrawingColors.Black;
                a.Alignment=D.TextAlignment.Left;a.Frame.Type=D.FrameTypes.None;a.PlacingAttributes.IsFixed=true;
                a.ArrowHead.Head=D.ArrowheadTypes.FilledArrow;a.ArrowHead.Height=s.height*.8;a.ArrowHead.Width=s.height*.8;
                a.PreferredPlacing=D.PreferredTextPlacingTypes.LeaderLinePlacingType();
                var text=new D.Text(drawing.GetSheet(),P(s.at),s.text,new D.LeaderLinePlacing(P(s.tip)),a);
                Need(text.Insert(),"Leader text insert failed: "+s.key);obj=text;
                var children=text.GetObjects();int count=0;
                while(children.MoveNext()) {
                    var line=children.Current as D.LeaderLine;if(line==null)continue;count++;
                    Need(line.Select(),"Leader refresh failed");
                    if(s.elbows.Length>0) {
                        line.ElbowPoints.Clear();foreach(var p in s.elbows)line.ElbowPoints.Add(P(p));
                        Need(line.Modify(),"New test leader elbow control failed");
                    }
                }
                Need(count==1,"Expected one native leader child");
            }
            Need(obj.SetUserProperty("IL_PRIMITIVE",s.key)&&obj.SetUserProperty("COD_OWNER","NATIVE-PLATE-R1"),"Primitive tag failed");
        }
    }
    internal static object Capture(D.SinglePartDrawing drawing,PagePrimitive[] samples) {
        var rows=new List<object>();if(samples==null)return rows;
        var e=drawing.GetSheet().GetObjects();var keys=new HashSet<string>();
        while(e.MoveNext()) {
            var obj=e.Current;string key="";obj.GetUserProperty("IL_PRIMITIVE",ref key);if(String.IsNullOrEmpty(key))continue;
            Need(keys.Add(key)&&samples.Any(s=>s.key==key)&&obj.Select(),"Unknown/duplicate primitive");
            var children=new List<object>();var set=obj as D.StraightDimensionSet;var text=obj as D.Text;
            Need(set!=null||text!=null,"Unexpected primitive type");
            var ce=set!=null?set.GetObjects():text.GetObjects();
            while(ce.MoveNext()) {
                var child=ce.Current;Need(child.Select(),"Primitive child refresh failed");
                children.Add(new {objectId=Id(child),data=NativePageCapture.Props(child,"StartPoint","EndPoint","UpDirection","Value","Attributes","ElbowPoints","LeaderLineType")});
            }
            rows.Add(new {key,objectId=Id(obj),data=NativePageCapture.Props(obj,"Attributes","StartPoint","EndPoint","UpDirection","Distance","TextString","InsertionPoint","Placing"),children});
        }
        Need(keys.Count==samples.Length,"Missing primitive samples");return rows;
    }
}
