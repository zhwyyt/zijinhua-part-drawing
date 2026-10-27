using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Collections;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;
using D = Tekla.Structures.Drawing;

public class TemplateEvidence {
    public string schemaVersion="nativeTemplateEvidence.v1", modelPath, capturedUtc, mark, attributeFile, signature;
    public int partId, sheetNumber;
    public double[] sheetSize;
    public object paper;
}

internal static class TemplateCapture {
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer {MaxJsonLength=Int32.MaxValue,RecursionLimit=100};
    static object Data(object value,int depth) {
        if(value==null)return null;
        var type=value.GetType();
        if(type.IsEnum)return value.ToString();
        if(type.IsPrimitive||value is string||value is decimal)return value;
        if(depth>20)throw new InvalidOperationException("Template presentation nesting unsupported");
        var enumerable=value as IEnumerable;
        if(enumerable!=null&&type.FullName!="Tekla.Structures.DrawingPresentationModel.View") {
            var items=new List<object>();foreach(var item in enumerable)items.Add(Data(item,depth+1));return items;
        }
        var result=new Dictionary<string,object>{{"type",type.FullName}};
        foreach(var property in type.GetProperties(BindingFlags.Public|BindingFlags.Instance).OrderBy(p=>p.Name)) {
            if(!property.CanRead||property.GetIndexParameters().Length>0)continue;
            // Drawing IDs are not geometry; regenerating a template may replace them.
            if(property.Name=="Id")continue;
            if((type.Namespace??"").StartsWith("Tekla.Common")&&!(property.PropertyType.IsPrimitive||property.PropertyType.IsEnum||property.PropertyType==typeof(string)))continue;
            result[property.Name]=Data(property.GetValue(value,null),depth+1);
        }
        foreach(var field in type.GetFields(BindingFlags.Public|BindingFlags.Instance))
            if(field.FieldType.IsPrimitive)result[field.Name]=field.GetValue(value);
        return result;
    }
    public static TemplateEvidence Capture(D.SinglePartDrawing drawing,string modelPath,string attributeFile) {
        if(!drawing.Select())throw new InvalidOperationException("Template drawing metadata refresh failed");
        var handler=new D.DrawingHandler();var active=handler.GetActiveDrawing() as D.SinglePartDrawing;
        if(active==null||active.PartIdentifier.ID!=drawing.PartIdentifier.ID||active.SheetNumber!=drawing.SheetNumber)
            throw new InvalidOperationException("Template capture requires the explicit active target");
        var origins=new List<double[]>();var ve=drawing.GetSheet().GetAllViews();
        while(ve.MoveNext()) {
            var view=ve.Current as D.View;if(view==null)continue;
            if(!view.Select())throw new InvalidOperationException("Template view refresh failed");
            origins.Add(new[]{view.Origin.X,view.Origin.Y});
        }
        // Drawing.dll may resolve from the GAC. Use the host's configured SDK resolver.
        var assembly=Assembly.Load("Tekla.Structures.DrawingPresentationModelInterface");
        var connectionType=assembly.GetType("Tekla.Structures.DrawingPresentationModelInterface.Connection",true);
        var contract=assembly.GetType("Tekla.Structures.DrawingPresentationModelInterface.IService",true);
        using(var connection=(IDisposable)Activator.CreateInstance(connectionType)) {
            var service=connectionType.GetProperty("Service").GetValue(connection,null);
            var presentation=contract.GetMethod("GetCurrentPresentationModel").Invoke(service,null) as IEnumerable;
            if(presentation==null)throw new InvalidOperationException("Template presentation unavailable");
            var paper=new List<object>();int matched=0;
            foreach(var item in presentation) {
                var position=item.GetType().GetProperty("Position").GetValue(item,null);
                var coordinates=(Dictionary<string,object>)Data(position,0);
                double x=Convert.ToDouble(coordinates["X"]),y=Convert.ToDouble(coordinates["Y"]);
                int matches=origins.Count(o=>Math.Abs(o[0]-x)<1e-6&&Math.Abs(o[1]-y)<1e-6);
                if(matches==1) {matched++;continue;}
                if(matches!=0||Math.Abs(x)>1e-6||Math.Abs(y)>1e-6)throw new InvalidOperationException("Ambiguous template paper/view coordinates");
                paper.Add(Data(item,0));
            }
            if(paper.Count!=1||matched!=origins.Count)throw new InvalidOperationException("Template paper layer is not uniquely identified");
            var encoded=Encoding.UTF8.GetBytes(Json.Serialize(paper[0]));string signature;
            using(var hash=SHA256.Create())signature=BitConverter.ToString(hash.ComputeHash(encoded)).Replace("-","").ToLowerInvariant();
            return new TemplateEvidence {modelPath=modelPath,capturedUtc=DateTime.UtcNow.ToString("o"),
                partId=drawing.PartIdentifier.ID,sheetNumber=drawing.SheetNumber,mark=drawing.Mark,
                attributeFile=attributeFile,signature=signature,paper=paper[0],
                sheetSize=new[]{drawing.Layout.SheetSize.Width,drawing.Layout.SheetSize.Height}};
        }
    }
}
