param(
  [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
$ModkitRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if ($env:UCS_MODKIT_BUILDTOOLS) {
  $BuildtoolsRoot = Resolve-Path $env:UCS_MODKIT_BUILDTOOLS
} else {
  $BuildtoolsRoot = Resolve-Path (Join-Path $ModkitRoot "..\ucs-modkit-buildtools")
}
$Script = Join-Path $BuildtoolsRoot "build_windows_release.ps1"
if (!(Test-Path $Script)) {
  throw "Buildtools script not found: $Script"
}

& $Script -PythonExe $PythonExe -ModkitRoot $ModkitRoot
