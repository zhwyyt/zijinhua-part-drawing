param(
    [string]$SdkDir = 'D:\Program Files\Tekla Structures\2017\nt\bin\plugins',
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [switch]$Bundle,
    [switch]$Page
)
$ErrorActionPreference = 'Stop'
if ($Bundle -and $Page) { throw 'Choose Bundle or Page' }
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Output directory already exists' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$arguments = @('/nologo','/target:exe','/platform:x64','/r:System.Web.Extensions.dll')
foreach ($name in @('Tekla.Structures','Tekla.Structures.Model','Tekla.Structures.Drawing','Tekla.Structures.Geometry3d.Compatibility')) {
    $path = Join-Path $SdkDir ($name + '.dll')
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing SDK assembly: $path" }
    $arguments += "/r:$path"
}
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
$entry = if ($Page) { 'NativePageProbe' } elseif ($Bundle) { 'TemplateBundleProbe' } else { 'TemplateLoadProbe' }
$executable = Join-Path ([IO.Path]::GetFullPath($OutputDirectory)) ($entry + '.exe')
$arguments += "/out:$executable"
$arguments += (Join-Path $PSScriptRoot 'TemplateLoadProbe.cs')
if ($Bundle -or $Page) {
    $arguments += "/main:$entry"
    $arguments += (Join-Path $PSScriptRoot 'TemplateBundleProbe.cs')
}
if ($Page) {
    $arguments += (Join-Path $PSScriptRoot 'NativePageCapture.cs')
    $arguments += (Join-Path $PSScriptRoot 'NativePageProbe.cs')
    $arguments += (Join-Path $PSScriptRoot 'NativeViewControl.cs')
    $arguments += (Join-Path $PSScriptRoot 'NativePdfExport.cs')
    $arguments += (Join-Path $PSScriptRoot 'NativePrimitiveProbe.cs')
    $arguments += (Join-Path $PSScriptRoot '../../../../tools/tekla2017_native_drawing/TargetDrawingLookup.cs')
}
& $compiler @arguments
if ($LASTEXITCODE -ne 0) { throw "C# compile failed: $LASTEXITCODE" }
Write-Output $executable
