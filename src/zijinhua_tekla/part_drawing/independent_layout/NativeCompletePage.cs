// Called only immediately after insertion and registration of a new owned trial page.
using System;
using System.Collections.Generic;
using System.Reflection;
using D=Tekla.Structures.Drawing;

internal static class NativeCompletePage {
    static int Id(D.DrawingObject obj) {
        var id=typeof(D.DatabaseObject).GetProperty("Identifier",BindingFlags.Instance|BindingFlags.NonPublic);
        return ((Tekla.Structures.Identifier)id.GetValue(obj,null)).ID;
    }
    internal static object ClearNewPage(D.SinglePartDrawing drawing) {
        var remove=new List<D.DrawingObject>();var receipts=new List<object>();int parts=0,views=0;
        var leaders=new List<D.DrawingObject>();var markChildren=new HashSet<int>();
        var ve=drawing.GetSheet().GetAllViews();
        while(ve.MoveNext()) {
            var view=ve.Current as D.View;if(view==null)continue;views++;
            var oe=view.GetAllObjects();
            while(oe.MoveNext()) {
                var o=oe.Current;var part=o as D.Part;
                if(part!=null) {
                    if(part.ModelIdentifier.ID!=drawing.PartIdentifier.ID)throw new InvalidOperationException("Unexpected drawing part");
                    parts++;continue;
                }
                if(o is D.StraightDimensionSet||o is D.Mark) {
                    remove.Add(o);var mark=o as D.Mark;
                    if(mark!=null) {
                        var children=mark.GetObjects();
                        while(children.MoveNext()) {
                            if(!(children.Current is D.LeaderLine))throw new InvalidOperationException("Unexpected mark child");
                            markChildren.Add(Id(children.Current));
                        }
                    }
                }
                else if(o is D.LeaderLine)leaders.Add(o);
                else if(!(o is D.StraightDimension))throw new InvalidOperationException("Unexpected auto object: "+o.GetType().FullName);
            }
        }
        if(views!=1||parts!=1)throw new InvalidOperationException("Complete trial requires exactly one target view/part");
        if(leaders.Count!=markChildren.Count||leaders.Exists(o=>!markChildren.Contains(Id(o))))
            throw new InvalidOperationException("Unrelated leader on new page");
        foreach(var leader in leaders)receipts.Add(new {objectId=Id(leader),deletedThroughParent=true,
            data=NativePageCapture.Props(leader,"StartPoint","EndPoint","ElbowPoints")});
        foreach(var o in remove) {
            receipts.Add(new {objectId=Id(o),data=NativePageCapture.Props(o,"Attributes","Distance","InsertionPoint")});
            if(!o.Delete())throw new InvalidOperationException("New page automatic annotation deletion failed");
        }
        drawing.CommitChanges();return receipts;
    }
}
