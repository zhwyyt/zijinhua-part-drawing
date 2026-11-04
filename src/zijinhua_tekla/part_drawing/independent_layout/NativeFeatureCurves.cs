// Source-derived analytic curves on newly inserted and registered complete pages.
using System;
using System.Linq;
using System.Collections.Generic;
using System.Reflection;
using D=Tekla.Structures.Drawing;
using G=Tekla.Structures.Geometry3d;

public class PageCurve {
    public string key,feature,kind;
    public double[] start,end,center;
    public double radius;
    public bool centerLine;
}
internal static class NativeFeatureCurves {
    static void Need(bool ok,string message) {if(!ok)throw new InvalidOperationException(message);}
    static int Id(D.DrawingObject o) {
        return ((Tekla.Structures.Identifier)typeof(D.DatabaseObject).GetProperty("Identifier",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(o,null)).ID;
    }
    static bool Point(double[] p) {return p!=null&&p.Length==3&&p.All(v=>!Double.IsNaN(v)&&!Double.IsInfinity(v));}
    internal static void Validate(PageCurve[] curves) {
        if(curves==null)return;
        Need(curves.Length>0&&curves.Select(c=>c.key).Distinct().Count()==curves.Length,"Empty/duplicate source curves");
        foreach(var c in curves) {
            Need(!String.IsNullOrWhiteSpace(c.key)&&!String.IsNullOrWhiteSpace(c.feature),"Missing curve source");
            Need(c.kind=="line"||c.kind=="arc"||c.kind=="circle","Unsupported curve");
            Need(c.kind=="circle"?Point(c.center)&&c.radius>0&&!Double.IsInfinity(c.radius):Point(c.start)&&Point(c.end),"Invalid curve points");
            Need(c.kind!="arc"||Point(c.center),"Arc center missing");
            Need(!c.centerLine||c.kind=="line","Center axis must be a line");
        }
    }
    internal static void Insert(D.SinglePartDrawing drawing,PageCurve[] curves) {
        if(curves==null)return;
        D.View view=null;D.Part target=null;var ve=drawing.GetSheet().GetAllViews();
        while(ve.MoveNext()) {
            var v=ve.Current as D.View;if(v==null)continue;Need(view==null,"Multiple feature views");view=v;
            var oe=v.GetAllObjects();
            while(oe.MoveNext()) {
                var p=oe.Current as D.Part;
                if(p!=null) {Need(target==null&&p.ModelIdentifier.ID==drawing.PartIdentifier.ID,"Wrong feature part");target=p;}
                var bolt=oe.Current as D.Bolt;
                if(bolt!=null) {
                    var a=bolt.Attributes;a.Representation=D.Bolt.Representation.Symbol;a.Color=D.DrawingColors.Invisible;
                    a.SymbolContainsHole=false;a.SymbolContainsAxis=false;bolt.Attributes=a;
                    bolt.Hideable.HideFromDrawingView();Need(bolt.Modify(),"Suppress bolt hardware failed");
                }
            }
        }
        Need(target!=null&&view!=null,"Feature target missing");
        var attributes=target.Attributes;attributes.Representation=D.Part.Representation.Exact;
        attributes.DrawReferenceLine=false;attributes.DrawCenterLine=false;attributes.DrawOrientationMark=false;
        attributes.DrawConnectingSideMarks=false;attributes.DrawPopMarks=false;attributes.DrawHiddenLines=false;attributes.DrawOwnHiddenLines=false;
        attributes.VisibleLines.Color=D.DrawingColors.Invisible;attributes.HiddenLines.Color=D.DrawingColors.Invisible;
        attributes.FaceHatch.Name="None";attributes.FaceHatch.DrawBackgroundColor=false;
        attributes.SectionFaceHatch.Name="None";attributes.SectionFaceHatch.DrawBackgroundColor=false;
        target.Attributes=attributes;Need(target.Modify(),"Suppress duplicate part graphics failed");
        var matrix=G.MatrixFactory.ToCoordinateSystem(view.DisplayCoordinateSystem);
        Func<double[],G.Point> point=p=>{var q=matrix.Transform(new G.Point(p[0],p[1],p[2]));return new G.Point(q.X,q.Y,0);};
        foreach(var c in curves) {
            D.DrawingObject obj;
            if(c.kind=="line") {
                var line=new D.Line(view,point(c.start),point(c.end));line.Attributes.Line.Color=D.DrawingColors.Black;
                line.Attributes.Line.Type=c.centerLine?D.LineTypes.DashDot:D.LineTypes.SolidLine;
                line.Attributes.Arrowhead.Head=D.ArrowheadTypes.NoArrow;obj=line;
            } else if(c.kind=="circle") {
                var circle=new D.Circle(view,point(c.center),c.radius);circle.Attributes.Line.Color=D.DrawingColors.Black;
                circle.Attributes.Line.Type=D.LineTypes.SolidLine;obj=circle;
            } else {
                var arc=new D.Arc(view,point(c.start),point(c.end),point(c.center));arc.Attributes.Line.Color=D.DrawingColors.Black;
                arc.Attributes.Line.Type=D.LineTypes.SolidLine;obj=arc;
            }
            Need(obj.Insert()&&obj.SetUserProperty("IL_CURVE",c.key)&&obj.SetUserProperty("IL_FEATURE",c.feature)&&
                 obj.SetUserProperty("COD_OWNER","NATIVE-PLATE-R1"),"Feature curve insert failed");
        }
    }
    internal static object Capture(D.SinglePartDrawing drawing,PageCurve[] curves) {
        var rows=new List<object>();if(curves==null)return rows;
        var ve=drawing.GetSheet().GetAllViews();
        while(ve.MoveNext()) {
            var v=ve.Current as D.View;if(v==null)continue;var oe=v.GetObjects();
            while(oe.MoveNext()) {
                var o=oe.Current;string key="",feature="";o.GetUserProperty("IL_CURVE",ref key);
                if(String.IsNullOrEmpty(key))continue;
                Need(o.Select(),"Curve refresh failed");o.GetUserProperty("IL_FEATURE",ref feature);
                rows.Add(new {key,feature,objectId=Id(o),data=NativePageCapture.Props(o,"StartPoint","EndPoint","CenterPoint","Radius","Attributes")});
            }
        }
        Need(rows.Count==curves.Length,"Curve capture count mismatch");return rows;
    }
}
