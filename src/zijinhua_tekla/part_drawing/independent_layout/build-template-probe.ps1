param(
    [string]$SdkDir = 'D:\Program Files\Tekla Structures\2017\nt\bin\plugins',
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Output directory already exists' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$arguments = @('/nologo','/target:exe','/platform:x64','/r:System.Web.Extensions.dll')
foreach ($name in @('Tekla.Structures','Tekla.Structures.Model','Tekla.Structures.Drawing','Tekla.Structures.Geometry3d.Compatibility')) {
    $path = Join-Path $SdkDir ($name + '.dll')
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing SDK assembly: $path" }
    $arguments += "/r:$path"
}
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
$executable = Join-Path ([IO.Path]::GetFullPath($OutputDirectory)) 'TemplateLoadProbe.exe'
$arguments += "/out:$executable"
$arguments += (Join-Path $PSScriptRoot 'TemplateLoadProbe.cs')
& $compiler @arguments
if ($LASTEXITCODE -ne 0) { throw "C# compile failed: $LASTEXITCODE" }
Write-Output $executable
