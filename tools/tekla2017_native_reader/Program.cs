using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text;
using System.Web.Script.Serialization;
using Tekla.Structures.Geometry3d;
using Tekla.Structures.Model;
using Tekla.Structures.Solid;

// R1 reads evidence. It deliberately does not infer arcs, planes, holes or fabrication approval.
internal static partial class NativeReader
{
    private static readonly List<object> Errors = new List<object>();
    private static readonly JavaScriptSerializer Strings = new JavaScriptSerializer { MaxJsonLength = Int32.MaxValue };

    private static Dictionary<string, object> Obj(params object[] values)
    {
        var result = new Dictionary<string, object>();
        for (int i = 0; i < values.Length; i += 2) result.Add((string)values[i], values[i + 1]);
        return result;
    }

    private static int Main(string[] args)
    {
        try
        {
            var options = Parse(args);
            string sdk = options.ContainsKey("sdk-dir") ? options["sdk-dir"] :
                @"D:\Program Files\Tekla Structures\2017\nt\bin\plugins";
            AppDomain.CurrentDomain.AssemblyResolve += (sender, eventArgs) => {
                var path = Path.Combine(sdk, new AssemblyName(eventArgs.Name).Name + ".dll");
                return File.Exists(path) ? System.Reflection.Assembly.LoadFrom(path) : null;
            };
            if (options.ContainsKey("self-test")) return SelfTest();
            if (!options.ContainsKey("out")) throw new ArgumentException("--out is required");
            if (String.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("SESSIONNAME")))
                Environment.SetEnvironmentVariable("SESSIONNAME", "Console");
            return options.ContainsKey("capture-nc") ? CaptureNc(options) : Read(options);
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine(exception.GetType().Name + ": " + exception.Message);
            return 2;
        }
    }

    private static Dictionary<string, string> Parse(string[] args)
    {
        var options = new Dictionary<string, string>();
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--self-test") { options.Add("self-test", "true"); continue; }
            if (args[i] == "--capture-nc") { options.Add("capture-nc", "true"); continue; }
            if (args[i] != "--out" && args[i] != "--ids" && args[i] != "--sdk-dir" && args[i] != "--nc-settings-file")
                throw new ArgumentException("Unknown argument: " + args[i]);
            if (++i >= args.Length) throw new ArgumentException("Missing argument value");
            options.Add(args[i - 1].Substring(2), args[i]);
        }
        return options;
    }

    private static object Value(string path, object value)
    {
        return Obj("status", "READ", "source", path, "value", value);
    }

    private static object Unknown(string path, string reason)
    {
        return Obj("status", "UNAVAILABLE", "source", path, "value", null, "reason", reason);
    }

    private static object Failure(string path, Exception exception, object partial = null)
    {
        var actual = exception is TargetInvocationException && exception.InnerException != null
            ? exception.InnerException : exception;
        Errors.Add(Obj("source", path, "type", actual.GetType().Name, "message", actual.Message));
        return Obj("status", "ERROR", "source", path, "value", partial,
            "error", Obj("type", actual.GetType().Name, "message", actual.Message));
    }

    private static object Capture(string path, Func<object> read)
    {
        try { return Value(path, read()); }
        catch (Exception exception) { return Failure(path, exception); }
    }

    private static object Collection(string path, Func<IEnumerator> open, Func<object, int, object> read)
    {
        var values = new List<object>();
        try
        {
            var enumerator = open();
            if (enumerator == null) throw new InvalidOperationException("API returned a null enumerator");
            while (enumerator.MoveNext())
            {
                int index = values.Count;
                try { values.Add(read(enumerator.Current, index)); }
                catch (Exception exception) { values.Add(Failure(path + "/" + index, exception)); }
            }
            return Value(path, values);
        }
        catch (Exception exception) { return Failure(path, exception, values); }
    }

    private static double[] Point(Point point) { return new[] { point.X, point.Y, point.Z }; }

    private static object Frame(CoordinateSystem frame)
    {
        return Obj("origin", Point(frame.Origin), "axisX", Point(frame.AxisX), "axisY", Point(frame.AxisY));
    }

    private static object PlaneState(TransformationPlane plane)
    {
        var matrix = plane.TransformationMatrixToGlobal;
        var origin = matrix.Transform(new Point(0, 0, 0));
        return Obj("origin", Point(origin),
            "axisX", Point(matrix.Transform(new Point(1, 0, 0)) - origin),
            "axisY", Point(matrix.Transform(new Point(0, 1, 0)) - origin),
            "axisZ", Point(matrix.Transform(new Point(0, 0, 1)) - origin));
    }

    private static object Property(object item, string name, string path)
    {
        if (item == null) return Unknown(path, "parent object is null");
        var property = item.GetType().GetProperty(name, BindingFlags.Public | BindingFlags.Instance);
        if (property == null || !property.CanRead) return Unknown(path, "property is not exposed by installed SDK");
        return Capture(path, () => {
            var value = property.GetValue(item, null);
            if (value == null) return null;
            if (value.GetType().IsEnum) return Obj("name", value.ToString(), "code", Convert.ToInt64(value));
            if (value is string || value is bool || value is double || value is int) return value;
            throw new NotSupportedException("Not a scalar property: " + value.GetType().FullName);
        });
    }

    private static object Properties(object item, string path, params string[] names)
    {
        var values = new Dictionary<string, object>();
        foreach (var name in names) values.Add(name, Property(item, name, path + "/" + name));
        return values;
    }

    private static object Report(Part part, string name, bool numeric, string path)
    {
        try
        {
            if (numeric)
            {
                double number = 0;
                return part.GetReportProperty(name, ref number) ? Value(path, number) : Unknown(path, "GetReportProperty returned false");
            }
            string text = null;
            return part.GetReportProperty(name, ref text) ? Value(path, text) : Unknown(path, "GetReportProperty returned false");
        }
        catch (Exception exception) { return Failure(path, exception); }
    }

    private static object Solid(Solid solid, string path)
    {
        return Obj("bounds", Capture(path + "/bounds", () => Obj("min", Point(solid.MinimumPoint), "max", Point(solid.MaximumPoint))),
            "edges", Collection(path + "/edges", () => solid.GetEdgeEnumerator(), (value, index) => {
                var edge = (Edge)value;
                return Obj("id", "E" + index, "apiType", edge.Type.ToString(), "apiTypeCode", (int)edge.Type,
                    "start", Point(edge.StartPoint), "end", Point(edge.EndPoint),
                    "curveParameters", Unknown(path + "/edges/" + index, "reader R1 has no proven native curve parameters"));
            }),
            "faces", Collection(path + "/faces", () => solid.GetFaceEnumerator(), (value, index) => {
                var face = (Face)value;
                string facePath = path + "/faces/" + index;
                return Obj("id", "F" + index, "normal", Capture(facePath + "/normal", () => Point(face.Normal)),
                    "originPartId", Capture(facePath + "/originPartId", () => face.OriginPartId.ID),
                    "surfaceType", Unknown(facePath + "/surfaceType", "surface class not exposed by this reader"),
                    "loops", Collection(facePath + "/loops", () => face.GetLoopEnumerator(), (loopValue, loopIndex) => {
                        var loop = (Loop)loopValue;
                        string loopPath = facePath + "/loops/" + loopIndex;
                        return Obj("id", "L" + loopIndex, "vertices", Collection(loopPath + "/vertices",
                            () => loop.GetVertexEnumerator(), (point, pointIndex) => Point((Point)point)));
                    }));
            }));
    }

    private static object Reference(ModelObject value)
    {
        return value == null ? null : Obj("id", value.Identifier.ID, "runtimeType", value.GetType().FullName);
    }

    private static object BoltAxisIntersections(BoltGroup bolt, Part target)
    {
        if (!bolt.Bolt || bolt.BoltStandard == "STUD" || (bolt.SlottedHoleX == 0 && bolt.SlottedHoleY == 0))
            return new object[0];
        var hardware = bolt.GetSolid(true);
        var material = target.GetSolid();
        var cs = bolt.GetCoordinateSystem();
        var axis = Vector.Cross(cs.AxisX, cs.AxisY).GetNormal();
        var corners = new List<Point>();
        foreach (double x in new[] {hardware.MinimumPoint.X, hardware.MaximumPoint.X})
            foreach (double y in new[] {hardware.MinimumPoint.Y, hardware.MaximumPoint.Y})
                foreach (double z in new[] {hardware.MinimumPoint.Z, hardware.MaximumPoint.Z}) corners.Add(new Point(x,y,z));
        var rows = new List<object>();
        int index = 0;
        foreach (Point p in bolt.BoltPositions)
        {
            var distances = corners.Select(q => (q.X-p.X)*axis.X+(q.Y-p.Y)*axis.Y+(q.Z-p.Z)*axis.Z).ToArray();
            double lo = distances.Min()-1, hi = distances.Max()+1;
            var start = new Point(p.X+lo*axis.X,p.Y+lo*axis.Y,p.Z+lo*axis.Z);
            var end = new Point(p.X+hi*axis.X,p.Y+hi*axis.Y,p.Z+hi*axis.Z);
            rows.Add(Obj("index", index++, "targetPartId", target.Identifier.ID,
                "queryStart", Point(start), "queryEnd", Point(end),
                "hardwarePoints", hardware.Intersect(start,end).Cast<Point>().Select(Point).ToArray(),
                "targetPoints", material.Intersect(start,end).Cast<Point>().Select(Point).ToArray()));
        }
        return rows;
    }

    private static object Bolt(BoltGroup bolt, Part target, string path)
    {
        return Obj("id", bolt.Identifier.ID, "runtimeType", bolt.GetType().FullName,
            "properties", Properties(bolt, path + "/properties", "BoltStandard", "BoltType", "BoltSize", "Tolerance",
                "SlottedHoleX", "SlottedHoleY", "HoleType", "RotateSlots", "Bolt", "Hole1", "Hole2", "Hole3", "Hole4", "Hole5",
                "Length", "CutLength", "ExtraLength"),
            "coordinateSystem", Capture(path + "/coordinateSystem", () => Frame(bolt.GetCoordinateSystem())),
            "positions", Collection(path + "/positions", () => bolt.BoltPositions.GetEnumerator(), (point, index) => Point((Point)point)),
            "firstPosition", Capture(path + "/firstPosition", () => Point(bolt.FirstPosition)),
            "secondPosition", Capture(path + "/secondPosition", () => Point(bolt.SecondPosition)),
            "targets", Obj("partToBoltTo", Capture(path + "/targets/partToBoltTo", () => Reference(bolt.PartToBoltTo)),
                "partToBeBolted", Capture(path + "/targets/partToBeBolted", () => Reference(bolt.PartToBeBolted)),
                "otherParts", Collection(path + "/targets/otherParts", () => bolt.GetOtherPartsToBolt().GetEnumerator(), (part, index) => Reference((ModelObject)part))),
            "solidWithHighAccuracy", Capture(path + "/solidWithHighAccuracy", () => Solid(bolt.GetSolid(true), path + "/solidWithHighAccuracy")),
            "axisIntersections", Capture(path + "/axisIntersections", () => BoltAxisIntersections(bolt,target)));
    }

    private static object Contour(Part part, string path)
    {
        Contour contour = part is ContourPlate ? ((ContourPlate)part).Contour : part is PolyBeam ? ((PolyBeam)part).Contour : null;
        if (contour == null) return Unknown(path, "part has no supported modeling-contour accessor");
        return Collection(path, () => contour.ContourPoints.GetEnumerator(), (value, index) => {
            var point = (ContourPoint)value;
            return Obj("point", Point(point), "chamfer", Properties(point.Chamfer, path + "/" + index + "/chamfer", "Type", "X", "Y", "DZ1", "DZ2"));
        });
    }

    private static object Operation(ModelObject item, string path)
    {
        var result = Obj("id", item.Identifier.ID, "runtimeType", item.GetType().FullName);
        var boolean = item as BooleanPart;
        if (boolean != null)
        {
            result["type"] = Property(boolean, "Type", path + "/type");
            result["operativePart"] = Capture(path + "/operativePart", () => Reference(boolean.OperativePart));
            result["operativeGeometry"] = Capture(path + "/operativeGeometry", () => {
                var operative = boolean.OperativePart;
                if (operative == null) throw new InvalidOperationException("operative part is null");
                return Obj("id", operative.Identifier.ID, "runtimeType", operative.GetType().FullName,
                    "coordinateSystem", Capture(path + "/operativeGeometry/coordinateSystem", () => Frame(operative.GetCoordinateSystem())),
                    "profile", Capture(path + "/operativeGeometry/profile", () => operative.Profile.ProfileString),
                    "rawSolid", Capture(path + "/operativeGeometry/rawSolid", () => Solid(operative.GetSolid(Tekla.Structures.Model.Solid.SolidCreationTypeEnum.RAW), path + "/operativeGeometry/rawSolid")),
                    "modelingContour", Capture(path + "/operativeGeometry/modelingContour", () => Contour(operative, path + "/operativeGeometry/modelingContour")));
            });
            result["operativeSolid"] = Capture(path + "/operativeSolid", () => {
                if (boolean.OperativePart == null) throw new InvalidOperationException("operative part is null");
                return Solid(boolean.OperativePart.GetSolid(), path + "/operativeSolid");
            });
        }
        var fitting = item as Fitting;
        if (fitting != null)
            result["plane"] = Capture(path + "/plane", () => Obj("origin", Point(fitting.Plane.Origin),
                "axisX", Point(fitting.Plane.AxisX), "axisY", Point(fitting.Plane.AxisY)));
        var cutPlane = item as CutPlane;
        if (cutPlane != null)
            result["plane"] = Capture(path + "/plane", () => Obj("origin", Point(cutPlane.Plane.Origin),
                "axisX", Point(cutPlane.Plane.AxisX), "axisY", Point(cutPlane.Plane.AxisY)));
        if (boolean == null && fitting == null && cutPlane == null)
            result["detail"] = Unknown(path, "operation type retained; no R1 detail reader");
        return result;
    }

    private static object ModificationTime(Part part)
    {
        var time = part.ModificationTime;
        return time.HasValue ? Obj("value", time.Value.ToString("o", CultureInfo.InvariantCulture),
            "kind", time.Value.Kind.ToString()) : null;
    }

    private static object PartEvidence(Model model, Part part, string path)
    {
        int startErrors = Errors.Count;
        var reports = new Dictionary<string, object>();
        foreach (var name in new[] { "PART_POS", "PART_POSITION" }) reports.Add(name, Report(part, name, false, path + "/reports/" + name));
        foreach (var name in new[] { "PLATE_THICKNESS", "PROFILE.WIDTH", "WIDTH", "DIM_A" })
            reports.Add(name, Report(part, name, true, path + "/reports/" + name));
        var result = Obj("id", part.Identifier.ID, "runtimeType", part.GetType().FullName,
            "guid", Capture(path + "/guid", () => model.GetGUIDByIdentifier(part.Identifier)),
            "modificationTime", Capture(path + "/modificationTime", () => ModificationTime(part)),
            "name", Capture(path + "/name", () => part.Name),
            "profile", Capture(path + "/profile", () => part.Profile.ProfileString),
            "material", Capture(path + "/material", () => part.Material.MaterialString),
            "reports", reports,
            "coordinateSystem", Capture(path + "/coordinateSystem", () => Frame(part.GetCoordinateSystem())),
            "dstvCoordinateSystem", Capture(path + "/dstvCoordinateSystem", () => Frame(part.GetDSTVCoordinateSystem())),
            "modelingContour", Capture(path + "/modelingContourRead", () => Contour(part, path + "/modelingContour")),
            "rawSolid", Capture(path + "/rawSolid", () => Solid(part.GetSolid(Tekla.Structures.Model.Solid.SolidCreationTypeEnum.RAW), path + "/rawSolid")),
            "finalSolid", Capture(path + "/finalSolid", () => Solid(part.GetSolid(), path + "/finalSolid")),
            "boltGroups", Collection(path + "/boltGroups", () => part.GetBolts(), (value, index) => Bolt((BoltGroup)value, part, path + "/boltGroups/" + index)),
            "operations", Collection(path + "/operations", () => part.GetBooleans(), (value, index) => Operation((ModelObject)value, path + "/operations/" + index)),
            "children", Collection(path + "/children", () => part.GetChildren(), (value, index) => Reference((ModelObject)value)));
        result["identityAfterRead"] = Capture(path + "/identityAfterRead", () => {
            var refreshed = model.SelectModelObject(part.Identifier) as Part;
            if (refreshed == null) throw new InvalidOperationException("Part disappeared during read");
            return Obj("guid", model.GetGUIDByIdentifier(refreshed.Identifier), "modificationTime", ModificationTime(refreshed));
        });
        result["readStatus"] = Errors.Count == startErrors ? "COMPLETE" : "PARTIAL";
        return result;
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static int Read(Dictionary<string, string> options)
    {
        Errors.Clear();
        string destination = Path.GetFullPath(options["out"]);
        if (File.Exists(destination)) throw new IOException("Evidence file already exists; choose a new --out path");
        var root = Obj("schemaVersion", "teklaNativeEvidence.v1", "readerVersion", "native-reader.r2b.11",
            "startedAtUtc", DateTime.UtcNow.ToString("o"), "coordinateSpace", "GLOBAL",
            "selectionSource", options.ContainsKey("ids") ? "EXPLICIT_IDS" : "MODEL_SELECTION",
            "sdkVersion", typeof(Part).Assembly.GetName().Version.ToString());
        var model = new Model();
        if (!model.GetConnectionStatus())
        {
            root["connectionStatus"] = "DISCONNECTED";
            root["parts"] = new object[0];
            root["errors"] = new[] { Obj("source", "model", "type", "DISCONNECTED", "message", "Tekla model connection unavailable") };
            Save(destination, root);
            return 2;
        }
        root["connectionStatus"] = "CONNECTED";
        root["model"] = Capture("model", () => Obj("name", model.GetInfo().ModelName, "path", model.GetInfo().ModelPath));
        var handler = model.GetWorkPlaneHandler();
        var original = handler.GetCurrentTransformationPlane();
        root["workPlaneBefore"] = Capture("workPlaneBefore", () => PlaneState(original));
        var parts = new List<object>();
        var selection = new List<object>();
        try
        {
            if (!handler.SetCurrentTransformationPlane(new TransformationPlane())) throw new InvalidOperationException("cannot set global work plane");
            var ids = new List<int>();
            if (options.ContainsKey("ids"))
            {
                foreach (var token in options["ids"].Split(','))
                {
                    int id;
                    if (!Int32.TryParse(token, out id) || id <= 0) throw new ArgumentException("--ids must contain positive numeric IDs");
                    if (!ids.Contains(id)) ids.Add(id);
                }
            }
            else
            {
                var selected = new Tekla.Structures.Model.UI.ModelObjectSelector().GetSelectedObjects();
                while (selected.MoveNext())
                {
                    var value = selected.Current as ModelObject;
                    if (value == null) throw new InvalidOperationException("null selected model object");
                    if (!ids.Contains(value.Identifier.ID)) ids.Add(value.Identifier.ID);
                }
            }
            if (ids.Count == 0) throw new InvalidOperationException("model selection is empty");
            foreach (var id in ids)
            {
                string path = "parts/" + id;
                try
                {
                    var value = model.SelectModelObject(new Tekla.Structures.Identifier(id));
                    var selectedItem = Obj("requestedId", id, "resolved", Reference(value));
                    selection.Add(selectedItem);
                    if (value != null && !(value is Part) && !options.ContainsKey("ids"))
                    {
                        selectedItem["disposition"] = "SKIPPED_NON_PART";
                        continue; // Related bolt groups are read through each selected part.
                    }
                    if (!(value is Part)) throw new InvalidOperationException("ID is missing or is not a Part: " + id);
                    parts.Add(PartEvidence(model, (Part)value, path));
                }
                catch (Exception exception) { parts.Add(Obj("id", id, "readStatus", "ERROR", "detail", Failure(path, exception))); }
            }
            if (parts.Count == 0) throw new InvalidOperationException("No parts in model selection; non-part selections are retained in selection evidence");
        }
        catch (Exception exception) { Failure("read", exception); }
        finally
        {
            root["workPlaneRestore"] = Capture("workPlaneRestore", () => {
                if (!handler.SetCurrentTransformationPlane(original)) throw new InvalidOperationException("work plane restoration failed");
                return PlaneState(handler.GetCurrentTransformationPlane());
            });
        }
        root["selection"] = selection;
        root["parts"] = parts;
        root["errors"] = Errors;
        root["completedAtUtc"] = DateTime.UtcNow.ToString("o");
        root["readStatus"] = Errors.Count == 0 ? "COMPLETE" : "PARTIAL";
        Save(destination, root);
        Console.WriteLine("Read parts=" + parts.Count + "; errors=" + Errors.Count + "; " + destination);
        return Errors.Count == 0 ? 0 : 1;
    }

    private static void Save(string path, object value)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path));
        using (var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write))
        using (var writer = new StreamWriter(stream, new UTF8Encoding(false))) WriteJson(writer, value);
    }

    private static void WriteJson(TextWriter writer, object value)
    {
        if (value == null) { writer.Write("null"); return; }
        if (value is string) { writer.Write(Strings.Serialize((string)value)); return; }
        if (value is bool) { writer.Write((bool)value ? "true" : "false"); return; }
        if (value is double)
        {
            double number = (double)value;
            if (Double.IsNaN(number) || Double.IsInfinity(number)) throw new InvalidDataException("nonfinite API coordinate");
            writer.Write(number.ToString("R", CultureInfo.InvariantCulture)); return;
        }
        if (value is int || value is long) { writer.Write(Convert.ToString(value, CultureInfo.InvariantCulture)); return; }
        var dictionary = value as IDictionary<string, object>;
        if (dictionary != null)
        {
            writer.Write('{'); bool comma = false;
            foreach (var pair in dictionary.OrderBy(pair => pair.Key, StringComparer.Ordinal))
            {
                if (comma) writer.Write(','); comma = true;
                WriteJson(writer, pair.Key); writer.Write(':'); WriteJson(writer, pair.Value);
            }
            writer.Write('}'); return;
        }
        var sequence = value as IEnumerable;
        if (sequence != null)
        {
            writer.Write('['); bool comma = false;
            foreach (var item in sequence) { if (comma) writer.Write(','); comma = true; WriteJson(writer, item); }
            writer.Write(']'); return;
        }
        throw new NotSupportedException("Unsupported JSON value: " + value.GetType().FullName);
    }

    private static int SelfTest()
    {
        foreach (double value in new[] { 0.1, 123456789.12345679, 1.0000000000000002, -0.0000000123456789 })
        {
            var writer = new StringWriter(CultureInfo.InvariantCulture); WriteJson(writer, value);
            double restored = Double.Parse(writer.ToString(), CultureInfo.InvariantCulture);
            if (BitConverter.DoubleToInt64Bits(value) != BitConverter.DoubleToInt64Bits(restored))
                throw new InvalidDataException("double round-trip failed");
        }
        var scalar = new StringWriter(); WriteJson(scalar, Obj("name", "中文\"\\\n", "missing", null, "false", false));
        var parsed = Strings.Deserialize<Dictionary<string, object>>(scalar.ToString());
        if ((string)parsed["name"] != "中文\"\\\n" || parsed["missing"] != null || (bool)parsed["false"])
            throw new InvalidDataException("JSON string/null/bool round-trip failed");
        string large = new string('A', 3 * 1024 * 1024);
        var bundleString = new StringWriter(); WriteJson(bundleString, large);
        if (Strings.Deserialize<string>(bundleString.ToString()) != large)
            throw new InvalidDataException("Large capture string round-trip failed");
        Console.WriteLine("PASS: double precision, JSON and 3 MiB capture string round-trip; no model connection attempted.");
        return 0;
    }
}
