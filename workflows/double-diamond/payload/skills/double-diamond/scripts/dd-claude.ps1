<#
.SYNOPSIS
Starts Claude Code as the double-diamond planner. The only supported Claude Code entry point.

.DESCRIPTION
Checks that python3, claude and the installed write guard are reachable, proves the guard denies a write
outside the runs folder,
creates the runs folder, writes a per-launch settings file that turns off auto memory and hides the
user CLAUDE.md and rules, then runs `claude --agent dd-planner` (or dd-planner-high with -High) with
the runs folder added. DD_LAUNCHER and CLAUDE_CODE_DISABLE_AUTO_MEMORY are set for the child only.

.EXAMPLE
pwsh -File dd-claude.ps1
pwsh -File dd-claude.ps1 -High --model opus
#>
param(
  [switch]$High
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Stop-Launch([string]$Message) {
  [Console]::Error.WriteLine("dd-claude: $Message")
  exit 1
}

# 1. Tools
foreach ($tool in 'python3', 'claude') {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
    Stop-Launch "'$tool' was not found on PATH. Install it and start a new shell."
  }
}

# 2. Guard reachability: the hooks run python3 "$HOME/.claude/skills/double-diamond/scripts/guard_write.py".
$homeDir = if ($env:HOME) { $env:HOME } else { $env:USERPROFILE }
if (-not $homeDir) { Stop-Launch 'Neither HOME nor USERPROFILE is set, so the write guard cannot be located.' }
$guard = Join-Path $homeDir '.claude/skills/double-diamond/scripts/guard_write.py'
if (-not (Test-Path -LiteralPath $guard -PathType Leaf)) {
  Stop-Launch "The write guard is missing: $guard. The hooks run it from `$HOME/.claude/skills/double-diamond/scripts. Install the Claude Code package (or set HOME to the folder that holds .claude) and try again."
}
# The Bash guard accepts only printable ASCII without $ % ^ | ` or ", so the plan script path must qualify too.
$planScript = Join-Path (Split-Path -Parent $guard) 'dd_plan.py'
if ($planScript -match '[^\x20-\x7e]|[`$%^|"]') {
  Stop-Launch "The plan script path contains characters the Bash guard refuses (non-ASCII or one of `$ % ^ | `` `"): $planScript. Install Claude Code under a plain-ASCII home folder (or set HOME accordingly)."
}

# 3. Guard self-test: the guard must deny a write outside the runs folder, or the Claude Code hooks would let writes through.
$probe = '{"session_id":"dd-selftest","tool_input":{"file_path":"/dd-selftest/outside.txt"}}'
$verdict = try { "$($probe | & python3 $guard plan 2>$null)" } catch { '' }
if ($verdict -notmatch '"permissionDecision":\s*"deny"') {
  Stop-Launch 'the write guard did not run with python3, the Claude Code hooks would let writes through; on Windows `python3` may be the Microsoft Store stub (Settings > Apps > Advanced app settings > App execution aliases), so install a real python3 and try again'
}

# 4. Runs root
$ddPlan = Join-Path $PSScriptRoot 'dd_plan.py'
$runsRoot = "$(& python3 $ddPlan 'runs-root')".Trim()
if ($LASTEXITCODE -ne 0 -or -not $runsRoot) { Stop-Launch "Could not read the runs folder from $ddPlan." }
New-Item -ItemType Directory -Force -Path $runsRoot | Out-Null

# 5. Per-launch settings: no auto memory, user CLAUDE.md and user rules folder hidden (both slash styles, as Claude Code matches literally).
$configDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $homeDir '.claude' }
$userMd = Join-Path $configDir 'CLAUDE.md'
$userRules = "$configDir/rules/**"
$excludes = @(($userMd -replace '\', '/'), ($userMd -replace '/', '\'), ($userRules -replace '\', '/'), ($userRules -replace '/', '\'))
$settings = Join-Path $runsRoot "dd-launch-$PID.settings.json"
$json = @{ autoMemoryEnabled = $false; claudeMdExcludes = $excludes } | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText($settings, $json, (New-Object System.Text.UTF8Encoding($false)))

# 6. Run claude with the launcher variables set for this process tree only, then restore.
$names = 'DD_LAUNCHER', 'CLAUDE_CODE_DISABLE_AUTO_MEMORY'
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$code = 1
try {
  foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, '1', 'Process') }
  $agent = if ($High) { 'dd-planner-high' } else { 'dd-planner' }
  $claudeArgs = @('--agent', $agent, '--add-dir', $runsRoot, '--settings', $settings) + @($args)
  & claude @claudeArgs
  $code = $LASTEXITCODE
}
finally {
  foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
  Remove-Item -LiteralPath $settings -Force -ErrorAction SilentlyContinue
}
exit $code
