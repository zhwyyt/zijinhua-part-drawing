param(
    [string]$SdkDir = 'D:\Program Files\Tekla Structures\2017\nt\bin\plugins',
    [string]$OutputDirectory = ''
)
$ErrorActionPreference = 'Stop'
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $PSScriptRoot '..\..\outputs\native-drawing-build' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$arguments = @('/nologo','/target:exe','/platform:x64','/r:System.Web.Extensions.dll')
foreach ($name in @('Tekla.Structures','Tekla.Structures.Model','Tekla.Structures.Drawing','Tekla.Structures.Geometry3d.Compatibility')) {
    $path = Join-Path $SdkDir ($name + '.dll')
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing SDK assembly: $path" }
    $arguments += "/r:$path"
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$executable = Join-Path ([IO.Path]::GetFullPath($OutputDirectory)) 'TeklaNativeDrawing.exe'
$arguments += "/out:$executable"
$arguments += (Join-Path $PSScriptRoot 'Program.cs')
$arguments += (Join-Path $PSScriptRoot 'TemplateCapture.cs')
$arguments += (Join-Path $PSScriptRoot 'TargetDrawingLookup.cs')
& $compiler @arguments
if ($LASTEXITCODE -ne 0) { throw "C# compile failed: $LASTEXITCODE" }
Write-Output $executable
