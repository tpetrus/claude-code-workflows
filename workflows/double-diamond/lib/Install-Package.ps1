# Shared install logic for the double-diamond package. Dot-sourced by install.ps1.
# Every installed file is listed in MANIFEST.json with its SHA-256, so install and
# uninstall are exact: nothing outside the manifest is ever written or removed.
Set-StrictMode -Version Latest

function Get-FileSha256([string]$Path) {
  # .NET instead of Get-FileHash: Windows PowerShell 5.1's Get-FileHash honors -WhatIf and returns nothing.
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try { return ([System.BitConverter]::ToString($sha.ComputeHash([System.IO.File]::ReadAllBytes($Path))) -replace '-', '').ToLowerInvariant() }
  finally { $sha.Dispose() }
}

function Get-PackageManifest([string]$Root) {
  $path = Join-Path $Root 'MANIFEST.json'
  if (-not (Test-Path -LiteralPath $path)) { throw "MANIFEST.json not found in $Root" }
  $manifest = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
  if ($manifest.kind -ne 'double-diamond-package') { throw "$path is not a double-diamond package manifest" }
  return $manifest
}

# Refuses to install a payload that does not match its manifest (corrupt or hand-edited package).
function Test-PackagePayload($Manifest, [string]$Root) {
  $problems = foreach ($file in $Manifest.files) {
    $source = Join-Path (Join-Path $Root 'payload') $file.path
    if (-not (Test-Path -LiteralPath $source)) { "missing:  $($file.path)" }
    elseif ((Get-FileSha256 $source) -ne $file.sha256) { "modified: $($file.path)" }
  }
  if ($problems) { throw "Package payload does not match MANIFEST.json:`n  $($problems -join "`n  ")" }
}

# Plans every copy first and stops before writing anything if a target differs (unless -Force).
function Install-PackageFiles {
  param($Manifest, [string]$Root, [string]$Target, [switch]$Force, $Cmdlet)
  $plan = foreach ($file in $Manifest.files) {
    $destination = Join-Path $Target $file.path
    $state = if (-not (Test-Path -LiteralPath $destination)) { 'new' }
      elseif ((Get-FileSha256 $destination) -eq $file.sha256) { 'same' }
      else { 'differs' }
    [pscustomobject]@{ Source = Join-Path (Join-Path $Root 'payload') $file.path; Destination = $destination; State = $state }
  }
  $conflicts = @($plan | Where-Object State -eq 'differs')
  if ($conflicts.Count -gt 0 -and -not $Force) {
    throw "These files already exist with different content. Nothing was changed. Re-run with -Force to overwrite:`n  $(($conflicts.Destination) -join "`n  ")"
  }
  foreach ($item in $plan) {
    if ($item.State -eq 'same') { Write-Host "up to date  $($item.Destination)"; continue }
    if ($Cmdlet.ShouldProcess($item.Destination, 'Install file')) {
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $item.Destination) | Out-Null
      [System.IO.File]::WriteAllBytes($Cmdlet.GetUnresolvedProviderPathFromPSPath($item.Destination), [System.IO.File]::ReadAllBytes($item.Source))
      Write-Host ('{0,-11} {1}' -f $(if ($item.State -eq 'new') { 'installed' } else { 'replaced' }), $item.Destination)
    }
  }
}

# Removes only files whose content is unchanged (or all of them with -Force), then prunes
# the named directories if they are left empty.
function Uninstall-PackageFiles {
  param($Manifest, [string]$Target, [switch]$Force, $Cmdlet, [string[]]$PruneDirs = @())
  foreach ($file in $Manifest.files) {
    $destination = Join-Path $Target $file.path
    if (-not (Test-Path -LiteralPath $destination)) { continue }
    if ((Get-FileSha256 $destination) -ne [string]$file.sha256 -and -not $Force) { Write-Warning "kept (locally modified, use -Force to remove): $destination"; continue }
    if ($Cmdlet.ShouldProcess($destination, 'Remove file')) { Remove-Item -LiteralPath $destination -Force; Write-Host "removed    $destination" }
  }
  foreach ($relative in $PruneDirs) {
    $dir = Join-Path $Target $relative
    if (-not (Test-Path -LiteralPath $dir)) { continue }
    $dirs = @(Get-ChildItem -LiteralPath $dir -Directory -Recurse -Force | Sort-Object { $_.FullName.Length } -Descending) + @(Get-Item -LiteralPath $dir -Force)
    foreach ($d in $dirs) {
      if (@(Get-ChildItem -LiteralPath $d.FullName -Force).Count -eq 0 -and $Cmdlet.ShouldProcess($d.FullName, 'Remove empty directory')) { Remove-Item -LiteralPath $d.FullName -Force }
    }
  }
}
