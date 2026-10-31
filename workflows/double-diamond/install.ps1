<#
.SYNOPSIS
Installs (or removes) the double-diamond planning workflow for Claude Code.

.DESCRIPTION
Copies payload/ into your Claude Code directory (default ~/.claude):
  agents/dd-planner.md, dd-planner-high.md, dd-scout.md, dd-ideator.md, dd-critic.md
  skills/double-diamond/   (SKILL.md, references, assets, scripts incl. the write guard and the dd-claude launchers)
The package is self-contained: the skill folder is a real copy, never a link.
Every file is verified against MANIFEST.json first. The installer is idempotent and stops
before writing anything if a target file differs, unless you pass -Force.
It does not edit settings.json. Start planning with the dd-claude launcher it prints.

.EXAMPLE
pwsh -NoProfile -File install.ps1 -WhatIf
pwsh -NoProfile -File install.ps1
pwsh -NoProfile -File install.ps1 -Uninstall
#>
[CmdletBinding(SupportsShouldProcess)]
param(
  [string]$ClaudeHome = (Join-Path $HOME '.claude'),
  [switch]$Force,
  [switch]$Uninstall
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\Install-Package.ps1')

$manifest = Get-PackageManifest $PSScriptRoot
$skillDir = Join-Path $ClaudeHome 'skills\double-diamond'

# A linked skill folder (junction or symbolic link) would make install and uninstall act on the link's target.
function Get-LinkedSkillDir {
  $item = Get-Item -LiteralPath $skillDir -Force -ErrorAction SilentlyContinue
  if ($null -ne $item -and $item.LinkType -in 'Junction', 'SymbolicLink') { return $item }
  return $null
}

if ($Uninstall) {
  if ($null -ne (Get-LinkedSkillDir)) {
    # Remove the link only, never its target.
    if ($PSCmdlet.ShouldProcess($skillDir, 'Remove linked skill folder (target is untouched)')) { [System.IO.Directory]::Delete($skillDir) }
  }
  Uninstall-PackageFiles -Manifest $manifest -Target $ClaudeHome -Force:$Force -Cmdlet $PSCmdlet -PruneDirs 'skills\double-diamond'
  Write-Host 'double-diamond removed from Claude Code. Run folders in the temp directory are swept automatically after 72 hours.'
  return
}

Test-PackagePayload $manifest $PSScriptRoot
# python3 must exist and run: on Windows the Microsoft Store alias stub exits non-zero with "was not found".
$pythonUsable = $false
if (Get-Command python3 -ErrorAction SilentlyContinue) {
  try {
    $pythonOutput = (& python3 --version 2>&1 | Out-String)
    $pythonUsable = ($LASTEXITCODE -eq 0) -and ($pythonOutput -notmatch 'was not found')
  } catch { $pythonUsable = $false }
}
if (-not $pythonUsable) { Write-Warning 'Python 3.9+ as python3 on PATH is required for the plan script, the write-guard hooks and the launcher. python3 is missing or is the Microsoft Store alias stub; install Python or disable the alias.' }
if ([System.IO.Path]::GetFullPath($ClaudeHome) -ne [System.IO.Path]::GetFullPath((Join-Path $HOME '.claude'))) {
  Write-Warning "The agents' write-guard hooks call `$HOME/.claude/skills/double-diamond/scripts/guard_write.py. Installing elsewhere ($ClaudeHome) leaves those hooks pointing at the default location."
}

# Never write through a linked skill folder; replace the link only on request.
if ($null -ne (Get-LinkedSkillDir)) {
  if (-not $Force) { throw "$skillDir is a link (junction or symbolic link). Re-run with -Force to replace the link with this self-contained copy; its target is never touched." }
  if ($PSCmdlet.ShouldProcess($skillDir, 'Remove linked skill folder (target is untouched)')) { [System.IO.Directory]::Delete($skillDir) }
}

Install-PackageFiles -Manifest $manifest -Root $PSScriptRoot -Target $ClaudeHome -Force:$Force -Cmdlet $PSCmdlet

Write-Host @"

double-diamond $($manifest.version) installed into $ClaudeHome

Next steps:
  1. Start planning with the launcher (the only supported entry point):
       pwsh -File ~/.claude/skills/double-diamond/scripts/dd-claude.ps1           lead on opus (medium effort)
       pwsh -File ~/.claude/skills/double-diamond/scripts/dd-claude.ps1 -High     R3+ plans (high effort)
       ~/.claude/skills/double-diamond/scripts/dd-claude.sh [--high]              macOS / Linux
     The launcher self-tests the guard with python3 (Python 3.9+ on PATH), turns on the write guards and isolates memory and CLAUDE.md for the run.
  2. A plain /double-diamond session has no write guards and is unsupported.
  3. Nothing to add to settings.json: the launcher grants access to the run folder itself.
  4. The old ~/.config/double-diamond folder is no longer used and can be deleted.
"@
