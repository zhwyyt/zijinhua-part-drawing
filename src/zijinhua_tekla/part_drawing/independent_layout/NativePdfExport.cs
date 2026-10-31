// Use the installed Tekla file exporter without dialog or printer spool submission.
using System;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Text;

internal static class NativePdfExport {
    internal static object Export(string sdk,string output) {
        string executable=Path.Combine(Directory.GetParent(sdk).FullName,"applications/Tekla/Model/DPMPrinter/DPMPrinterCommand.exe");
        if(!File.Exists(executable)||File.Exists(output))throw new InvalidOperationException("PDF exporter missing or output exists");
        // Explicit local settings avoid the default WinAnsi export losing Chinese glyphs.
        string settings=output+".settings.xml";
        string xml="<?xml version=\"1.0\" encoding=\"utf-8\"?><PdfPrintOptions Version=\"1.2\"><Options>"+
            "<PrintTarget>PDF</PrintTarget><PrinterName/><PDFAndPlotFileLocation/><EmbedFonts>true</EmbedFonts>"+
            "<OpenFolderWhenFinished>false</OpenFolderWhenFinished><OpenFileWhenFinished>false</OpenFileWhenFinished>"+
            "<OutputToSingleFile>false</OutputToSingleFile><SinglePDFFileName/><PlotFileExtension>plt</PlotFileExtension>"+
            "<PlotFilePrefix/><PlotFileSuffix/><ScalingMethod>Auto</ScalingMethod><ScaleFactor>1</ScaleFactor>"+
            "<CenterDrawingOnPaper>true</CenterDrawingOnPaper><PrintOnMultipleSheets>false</PrintOnMultipleSheets>"+
            "<PaperSize>A3</PaperSize><Orientation>Landscape</Orientation><ColorMode>BlackAndWhite</ColorMode>"+
            "<NumberOfCopies>1</NumberOfCopies><LineThicknesses/></Options></PdfPrintOptions>";
        using(var stream=new FileStream(settings,FileMode.CreateNew,FileAccess.Write,FileShare.None)) {
            byte[] bytes=Encoding.UTF8.GetBytes(xml);stream.Write(bytes,0,bytes.Length);
        }
        string args="printer:pdf printActive:true settingsFile:\""+settings+"\" paper:A3 scalingMethod:Auto orientation:Landscape out:\""+output+"\"";
        var start=new ProcessStartInfo(executable,args) {UseShellExecute=false,CreateNoWindow=true,
            WorkingDirectory=Path.GetDirectoryName(executable),RedirectStandardOutput=true,RedirectStandardError=true};
        var log=new StringBuilder();var errors=new StringBuilder();
        using(var process=new Process()) {
            process.StartInfo=start;
            process.OutputDataReceived+=(s,e)=>{if(e.Data!=null)lock(log)log.AppendLine(e.Data);};
            process.ErrorDataReceived+=(s,e)=>{if(e.Data!=null)lock(errors)errors.AppendLine(e.Data);};
            if(!process.Start())throw new InvalidOperationException("PDF export launch failed");
            process.BeginOutputReadLine();process.BeginErrorReadLine();
            if(!process.WaitForExit(60000)){process.Kill();throw new TimeoutException("Only the PDF export helper was stopped after 60 seconds");}
            process.WaitForExit();
            if(process.ExitCode!=0||!File.Exists(output))throw new InvalidOperationException("PDF export failed: "+log+errors);
            using(var sha=SHA256.Create())return new {executable,arguments=args,exitCode=process.ExitCode,
                executableSha256=BitConverter.ToString(sha.ComputeHash(File.ReadAllBytes(executable))).Replace("-","").ToLowerInvariant(),
                settingsSha256=BitConverter.ToString(sha.ComputeHash(File.ReadAllBytes(settings))).Replace("-","").ToLowerInvariant(),
                pdfSha256=BitConverter.ToString(sha.ComputeHash(File.ReadAllBytes(output))).Replace("-","").ToLowerInvariant(),stdout=log.ToString(),stderr=errors.ToString()};
        }
    }
}
