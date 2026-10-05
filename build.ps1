<#
Builds the C++ LBM worker into worker_cpp\worker.exe with MSVC.
Needs Visual Studio 2019+ or Build Tools with the "Desktop development with C++" workload.

    powershell -ExecutionPolicy Bypass -File build.ps1
#>
$ErrorActionPreference = "Stop"

$installer = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer"
$vswhere = Join-Path $installer "vswhere.exe"
if (-not (Test-Path $vswhere)) {
    throw "vswhere.exe not found. Install Visual Studio (or Build Tools) with the 'Desktop development with C++' workload."
}
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw "No Visual Studio installation with the MSVC x64 toolset was found." }
$vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"

$src = Join-Path $PSScriptRoot "worker_cpp"
$obj = Join-Path $src "build"
New-Item -ItemType Directory -Force $obj | Out-Null

# vcvars64.bat calls vswhere itself and expects to find it on PATH
$env:PATH = "$installer;$env:PATH"
cmd /c "`"$vcvars`" >nul && cl /nologo /O2 /openmp /std:c++17 /EHsc `"$src\main.cpp`" /Fo`"$obj\main.obj`" /Fe`"$src\worker.exe`""
if ($LASTEXITCODE -ne 0) { throw "Build failed." }
Write-Host "Built $src\worker.exe"
