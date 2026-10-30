using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using Tekla.Structures.Drawing;

public static class QueryActiveDrawing
{
    public static int Main()
    {
        var handler = new DrawingHandler();
        if (!handler.GetConnectionStatus()) { Console.WriteLine("DRAWING_CONNECTION=DISCONNECTED"); return 2; }
        var drawing = handler.GetActiveDrawing();
        if (drawing == null) { Console.WriteLine("ACTIVE_DRAWING=NONE"); return 1; }
        Console.WriteLine("ACTIVE_DRAWING=YES");
        Console.WriteLine("TYPE=" + drawing.GetType().FullName);
        Console.WriteLine("MARK=" + drawing.Mark);
        Console.WriteLine("NAME=" + drawing.Name);
        Console.WriteLine("SHEET_NUMBER=" + drawing.SheetNumber.ToString(CultureInfo.InvariantCulture));
        Console.WriteLine("PART_ID=" + drawing.PartIdentifier.ID.ToString(CultureInfo.InvariantCulture));
        Console.WriteLine("SHEET_WIDTH=" + drawing.Layout.SheetSize.Width.ToString("R", CultureInfo.InvariantCulture));
        Console.WriteLine("SHEET_HEIGHT=" + drawing.Layout.SheetSize.Height.ToString("R", CultureInfo.InvariantCulture));
        var views = drawing.GetSheet().GetAllViews();
        int count = 0;
        while (views.MoveNext())
        {
            var view = views.Current as View;
            if (view == null) continue;
            count++;
            Console.WriteLine("VIEW_" + count + "_TYPE=" + view.GetType().FullName);
            Console.WriteLine("VIEW_" + count + "_SCALE=" + view.Attributes.Scale.ToString("R", CultureInfo.InvariantCulture));
            Console.WriteLine("VIEW_" + count + "_WIDTH=" + view.Width.ToString("R", CultureInfo.InvariantCulture));
            Console.WriteLine("VIEW_" + count + "_HEIGHT=" + view.Height.ToString("R", CultureInfo.InvariantCulture));
        }
        Console.WriteLine("VIEW_COUNT=" + count.ToString(CultureInfo.InvariantCulture));
        return 0;
    }
}
