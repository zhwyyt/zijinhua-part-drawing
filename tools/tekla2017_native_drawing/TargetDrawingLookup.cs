using System;
using System.Reflection;
using Tekla.Structures;
using D = Tekla.Structures.Drawing;

// Tekla 2017 has no public lookup by drawing database ID. Only construct/read
// an in-memory handle here; public Select performs the read, never a mutation.
internal static class TargetDrawingLookup {
    static PropertyInfo Property(string name) {
        var property=typeof(D.DatabaseObject).GetProperty(name,BindingFlags.Instance|BindingFlags.NonPublic);
        if(property==null)throw new InvalidOperationException("Tekla 2017 drawing identity unavailable: "+name);
        return property;
    }
    public static int ObjectId(D.Drawing drawing) { return ((Identifier)Property("Identifier").GetValue(drawing,null)).ID; }
    public static int DrawingId(D.Drawing drawing) { return ((Identifier)Property("DrawingIdentifier").GetValue(drawing,null)).ID; }
    public static D.SinglePartDrawing Read(int partId,int sheet,int objectId,int drawingId) {
        if(objectId<=0&&drawingId<=0)throw new InvalidOperationException("Registered drawing database identity required");
        var drawing=new D.SinglePartDrawing(new Identifier(partId),sheet);
        Property("Identifier").SetValue(drawing,new Identifier(objectId),null);
        Property("DrawingIdentifier").SetValue(drawing,new Identifier(drawingId),null);
        if(!drawing.Select())throw new InvalidOperationException("Registered drawing lookup failed; no catalog fallback");
        if(drawing.PartIdentifier.ID!=partId||drawing.SheetNumber!=sheet)
            throw new InvalidOperationException("Registered drawing identity points to another target");
        return drawing;
    }
}
