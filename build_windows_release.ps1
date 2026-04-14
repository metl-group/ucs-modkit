param(
  [string]$PythonExe = "python",
  [string]$ModkitRoot = "",
  [ValidateSet("auto", "onefile", "onedir")]
  [string]$Layout = "auto",
  [ValidateSet("standard", "lobotomized")]
  [string]$Profile = "standard"
)

$ErrorActionPreference = "Stop"
$BuildtoolsRoot = Resolve-Path $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($ModkitRoot)) {
  if ($env:UCS_MODKIT_ROOT) {
    $ModkitRoot = $env:UCS_MODKIT_ROOT
  } else {
    $ModkitRoot = Join-Path $BuildtoolsRoot "..\ucs-modkit"
  }
}
$ModkitRoot = (Resolve-Path $ModkitRoot).Path
$Builder = Join-Path $BuildtoolsRoot "build_pyinstaller.py"

Write-Host "Using Python   :" $PythonExe
Write-Host "Modkit root    :" $ModkitRoot
Write-Host "GUI layout     :" $Layout
Write-Host "Profile        :" $Profile
& $PythonExe $Builder --target windows --layout $Layout --profile $Profile --zip --modkit-root $ModkitRoot

$Suffix = ""
if ($Profile -eq "lobotomized") {
  $Suffix = "-lobotomized"
}
$ReleaseDir = Join-Path $ModkitRoot ("dist\UCS-Modkit-windows" + $Suffix)
$ZipPath = Join-Path $ModkitRoot ("dist\UCS-Modkit-windows" + $Suffix + ".zip")

Write-Host "Release folder :" $ReleaseDir
Write-Host "Zip archive    :" $ZipPath
Write-Host "Done."
