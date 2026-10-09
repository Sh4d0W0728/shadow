[CmdletBinding()]
param(
    [switch]$Local,
    [switch]$CheckOnly,
    [string]$CodexPath
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not $CodexPath) {
    $command = Get-Command codex -ErrorAction SilentlyContinue
    if ($command) { $CodexPath = $command.Source }
    elseif ($env:LOCALAPPDATA -and (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Programs\OpenAI\Codex\bin\codex.exe'))) {
        $CodexPath = Join-Path $env:LOCALAPPDATA 'Programs\OpenAI\Codex\bin\codex.exe'
    }
}
if (-not $CodexPath -or -not (Test-Path -LiteralPath $CodexPath -PathType Leaf)) {
    throw 'Codex CLI was not found. Install/update Codex or pass -CodexPath with its full executable path.'
}
$manifestPath = Join-Path $PSScriptRoot 'shadow\.codex-plugin\plugin.json'
$marketplacePath = Join-Path $PSScriptRoot '.agents\plugins\marketplace.json'
foreach ($requiredPath in @($manifestPath, $marketplacePath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) { throw "Incomplete package: $requiredPath" }
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.name -ne 'shadow') { throw 'Unexpected plugin manifest.' }
if (-not $Local -and -not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git is required for GitHub install/update. Install Git from https://git-scm.com/downloads first.'
}
function Invoke-ShadowCodex {
    param([string[]]$CliArguments)
    & $CodexPath @CliArguments
    if ($LASTEXITCODE -ne 0) { throw "Codex failed (exit $LASTEXITCODE): $($CliArguments -join ' ')" }
}
Write-Host "Package version: $($manifest.version)"
Write-Host "Codex: $CodexPath"
Invoke-ShadowCodex -CliArguments @('--version')
if ($CheckOnly) { Write-Host 'Preflight passed. No installation or network changes made.'; return }
$marketplaceOutput = Invoke-ShadowCodex -CliArguments @('plugin', 'marketplace', 'list', '--json')
$state = ($marketplaceOutput -join [Environment]::NewLine) | ConvertFrom-Json
if (-not $state.PSObject.Properties['marketplaces']) { throw 'Unexpected Codex marketplace list format. Update Codex before retrying.' }
$registered = @($state.marketplaces | Where-Object { $_.name -eq 'shadow' })
if ($registered.Count -gt 1) { throw 'More than one shadow marketplace is configured. Inspect the Codex plugin settings first.' }
if ($registered.Count -eq 1) {
    $entry = $registered[0]
    $originProperty = $entry.PSObject.Properties['marketplaceSource']
    $origin = if ($originProperty) { $originProperty.Value } else { $null }
    if ($Local) {
        $expectedRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd([char[]]'\/')
        $actualRoot = [IO.Path]::GetFullPath([string]$entry.root).TrimEnd([char[]]'\/')
        if ($origin -or $actualRoot -ne $expectedRoot) {
            throw 'The name shadow belongs to another source. Local installation stopped; no source was replaced.'
        }
    } else {
        $acceptedSources = @('https://github.com/Sh4d0W0728/shadow.git', 'https://github.com/Sh4d0W0728/shadow')
        if (-not $origin -or $origin.sourceType -ne 'git' -or $origin.source -notin $acceptedSources) {
            throw 'The name shadow belongs to another/local source. Inspect and remove that marketplace through Codex before choosing GitHub installation. No source was replaced.'
        }
    }
}
if ($Local) {
    Invoke-ShadowCodex -CliArguments @('plugin', 'marketplace', 'add', $PSScriptRoot)
} else {
    Invoke-ShadowCodex -CliArguments @('plugin', 'marketplace', 'add', 'https://github.com/Sh4d0W0728/shadow.git', '--ref', 'main')
    # Repeated add is idempotent but does not fetch. Refresh explicitly.
    Invoke-ShadowCodex -CliArguments @('plugin', 'marketplace', 'upgrade', 'shadow')
}
Invoke-ShadowCodex -CliArguments @('plugin', 'add', 'shadow@shadow')
Write-Host 'shadow installed. Open a new Codex chat and use $shadow.'
if ($Local) { Write-Host 'Local mode: this source does not receive GitHub updates. Keep the extracted directory.' }
else { Write-Host 'Run update-shadow.ps1 to fetch future updates. Existing projects stay outside the plugin cache.' }
