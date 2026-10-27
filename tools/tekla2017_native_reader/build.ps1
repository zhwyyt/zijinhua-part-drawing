param(
    [string]$SdkDir = 'D:\Program Files\Tekla Structures\2017\nt\bin\plugins',
    [string]$OutputDirectory = ''
)
$ErrorActionPreference = 'Stop'
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $PSScriptRoot '..\..\outputs\native-reader-build'
}
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$references = @('Tekla.Structures.dll', 'Tekla.Structures.Model.dll', 'Tekla.Structures.Geometry3d.Compatibility.dll')
$arguments = @('/nologo', '/target:exe', '/platform:x64', '/r:System.Web.Extensions.dll')
foreach ($name in $references) {
    $reference = Join-Path $SdkDir $name
    if (-not (Test-Path -LiteralPath $reference)) { throw "Missing SDK assembly: $reference" }
    $arguments += "/r:$reference"
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$executable = Join-Path ([System.IO.Path]::GetFullPath($OutputDirectory)) 'TeklaNativeReader.exe'
$arguments += "/out:$executable"
$arguments += (Join-Path $PSScriptRoot 'Program.cs')
$arguments += (Join-Path $PSScriptRoot 'NcCapture.cs')
& $compiler @arguments
if ($LASTEXITCODE -ne 0) { throw "C# compile failed: $LASTEXITCODE" }
Write-Output $executable
