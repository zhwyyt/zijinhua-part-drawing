// Called only immediately after insertion and registration of a new owned trial page.
using System;
using System.Collections.Generic;
using System.Reflection;
using D=Tekla.Structures.Drawing;

internal static class NativeCompletePage {
    internal static object ClearNewPage(D.SinglePartDrawing drawing) {
        var remove=new List<D.DrawingObject>();var receipts=new List<object>();int parts=0,views=0;
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
                if(o is D.StraightDimensionSet||o is D.Mark)remove.Add(o);
                else if(!(o is D.StraightDimension))throw new InvalidOperationException("Unexpected auto object: "+o.GetType().FullName);
            }
        }
        if(views!=1||parts!=1)throw new InvalidOperationException("Complete trial requires exactly one target view/part");
        foreach(var o in remove) {
            var id=typeof(D.DatabaseObject).GetProperty("Identifier",BindingFlags.Instance|BindingFlags.NonPublic);
            receipts.Add(new {objectId=((Tekla.Structures.Identifier)id.GetValue(o,null)).ID,data=NativePageCapture.Props(o,"Attributes","Distance","InsertionPoint")});
            if(!o.Delete())throw new InvalidOperationException("New page automatic annotation deletion failed");
        }
        drawing.CommitChanges();return receipts;
    }
}
