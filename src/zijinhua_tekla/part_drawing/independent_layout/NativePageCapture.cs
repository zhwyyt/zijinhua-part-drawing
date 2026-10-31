// Read-only SDK and presentation capture for explicitly owned test pages.
using System;
using System.Linq;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using D=Tekla.Structures.Drawing;

internal static class NativePageCapture {
    internal static object Data(object value,int depth) {
        if(value==null)return null;
        var t=value.GetType();
        if(t.IsEnum||value is Guid)return value.ToString();
        if(t.IsPrimitive||value is string||value is decimal)return value;
        if(value is DateTime)return ((DateTime)value).ToUniversalTime().ToString("o");
        if(depth>16)return new {unavailable="depth limit",type=t.FullName};
        var en=value as IEnumerable;
        if(en!=null && t.FullName!="Tekla.Structures.DrawingPresentationModel.View") {
            var items=new List<object>();foreach(var item in en) {if(items.Count>=100000)throw new Exception("enumeration limit");items.Add(Data(item,depth+1));}return items;
        }
        var result=new Dictionary<string,object>{{"type",t.FullName}};
        foreach(var p in t.GetProperties(BindingFlags.Instance|BindingFlags.Public).GroupBy(x=>x.Name).Select(g=>g.OrderBy(x=>x.DeclaringType==t?0:1).First()).OrderBy(x=>x.Name)) {
            if(!p.CanRead||p.GetIndexParameters().Length>0)continue;
            if((t.Namespace??"").StartsWith("Tekla.Common") && !(p.PropertyType.IsPrimitive||p.PropertyType.IsEnum||p.PropertyType==typeof(string)))continue;
            try {result[p.Name]=Data(p.GetValue(value,null),depth+1);}catch(Exception e){result[p.Name]=new {unavailable=e.GetBaseException().Message};}
        }
        foreach(var f in t.GetFields(BindingFlags.Instance|BindingFlags.Public))if(f.FieldType.IsPrimitive)result[f.Name]=f.GetValue(value);
        var plain=t.GetMethod("GetUnformattedString",Type.EmptyTypes);
        if(plain!=null)try{result["unformatted"]=plain.Invoke(value,null);}catch(Exception e){result["unformattedError"]=e.GetBaseException().Message;}
        return result;
    }
    internal static object Props(object value,params string[] names) {
        var result=new Dictionary<string,object>{{"type",value.GetType().FullName}};
        foreach(var name in names){var p=value.GetType().GetProperties().Where(x=>x.Name==name).OrderBy(x=>x.DeclaringType==value.GetType()?0:1).FirstOrDefault();if(p!=null)try{result[name]=Data(p.GetValue(value,null),0);}catch(Exception e){result[name]=new{unavailable=e.GetBaseException().Message};}}
        return result;
    }
    static object ObjectInfo(D.DrawingObject obj,int index) {
        bool selected=obj.Select();
        var r=new Dictionary<string,object>{{"index",index},{"refreshed",selected},{"data",Props(obj,"Identifier","ModelIdentifier","Attributes","Hideable","Distance","StartPoint","EndPoint","UpDirection","Value","InsertionPoint","Placing","TextString","TextWidth","TextHeight","CenterPoint","Radius")}};
        var box=obj.GetType().GetMethod("GetAxisAlignedBoundingBox",Type.EmptyTypes);
        if(box!=null)try{r["boundingBox"]=Data(box.Invoke(obj,null),0);}catch(Exception e){r["boundingBoxError"]=e.GetBaseException().Message;}
        var set=obj as D.StraightDimensionSet;
        if(set!=null){var children=new List<object>();var ce=set.GetObjects();while(ce.MoveNext()){var child=ce.Current as D.StraightDimension;if(child!=null){child.Select();children.Add(Props(child,"StartPoint","EndPoint","UpDirection","Value","Attributes"));}}r["children"]=children;}
        return r;
    }
    internal static object Capture(D.SinglePartDrawing drawing) {
        if(!drawing.Select())throw new InvalidOperationException("Drawing refresh failed");
        var views=new List<object>();var ve=drawing.GetSheet().GetAllViews();
        while(ve.MoveNext()) {
            var v=ve.Current as D.View;if(v==null)continue;
            if(!v.Select())throw new InvalidOperationException("View refresh failed");
            var objects=new List<object>();var oe=v.GetAllObjects();
            while(oe.MoveNext())objects.Add(ObjectInfo(oe.Current,objects.Count+1));
            views.Add(new {view=Props(v,"Identifier","Name","ViewType","Attributes","Origin","FrameOrigin","Width","Height","DisplayCoordinateSystem","ViewCoordinateSystem","RestrictionBox"),objects});
        }
        var sheetObjects=new List<object>();var se=drawing.GetSheet().GetObjects();
        while(se.MoveNext()) {
            if(se.Current is D.View)continue;
            sheetObjects.Add(ObjectInfo(se.Current,sheetObjects.Count+1));
        }
        var a=Assembly.Load("Tekla.Structures.DrawingPresentationModelInterface");
        var ct=a.GetType("Tekla.Structures.DrawingPresentationModelInterface.Connection",true);
        var contract=a.GetType("Tekla.Structures.DrawingPresentationModelInterface.IService",true);
        object presentation;
        using(var connection=(IDisposable)Activator.CreateInstance(ct)) {
            var service=ct.GetProperty("Service").GetValue(connection,null);
            presentation=Data(contract.GetMethod("GetCurrentPresentationModel").Invoke(service,null),0);
        }
        return new {drawing=Props(drawing,"Mark","Name","Title1","Title2","Title3","SheetNumber","PartIdentifier","Layout","IsLocked","IsFrozen","IsIssued"),views,sheetObjects,presentation};
    }
}
