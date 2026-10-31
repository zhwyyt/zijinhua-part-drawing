// Bounded native primitive calibration, not the independent layout solver.
using System;
using System.Collections.Generic;
using D=Tekla.Structures.Drawing;
using G=Tekla.Structures.Geometry3d;

public class PageViewControl { public double[] region; public string sourceDigest; }
internal static class NativeViewControl {
    internal static object Apply(D.SinglePartDrawing drawing,PageViewControl control) {
        var views=new List<D.View>();var e=drawing.GetSheet().GetAllViews();
        while(e.MoveNext()) {var v=e.Current as D.View;if(v!=null)views.Add(v);}
        if(views.Count!=1||views[0].ViewType.ToString()!="FrontView")throw new InvalidOperationException("Expected one native FrontView");
        var view=views[0];var region=control.region;var attempts=new List<object>();
        foreach(double scale in new[]{1d,2d,2.5d,5d,10d,20d,50d}) {
            if(!view.Select())throw new InvalidOperationException("View refresh failed");
            var attributes=view.Attributes;attributes.Scale=scale;attributes.FixedViewPlacing=true;
            attributes.Shortening.CutParts=false;view.Attributes=attributes;
            if(!view.Modify()||!view.Select())throw new InvalidOperationException("View scale update failed");
            bool fits=view.Width<=region[2]-region[0]&&view.Height<=region[3]-region[1];
            attempts.Add(new {scale,view.Width,view.Height,fits});
            if(!fits)continue;
            view.Origin=new G.Point((region[0]+region[2]-view.Width)/2-view.FrameOrigin.X,
                                   (region[1]+region[3]-view.Height)/2-view.FrameOrigin.Y,0);
            if(!view.Modify()||!view.Select())throw new InvalidOperationException("Fixed view placement failed");
            double x=view.Origin.X+view.FrameOrigin.X,y=view.Origin.Y+view.FrameOrigin.Y;
            if(x<region[0]-1e-6||y<region[1]-1e-6||x+view.Width>region[2]+1e-6||y+view.Height>region[3]+1e-6)
                throw new InvalidOperationException("Native view escaped calibration region");
            return new {attempts,region,scale,origin=new[]{view.Origin.X,view.Origin.Y},
                box=new[]{x,y,x+view.Width,y+view.Height},control.sourceDigest};
        }
        throw new InvalidOperationException("No allowed scale fits calibration region");
    }
}
