[CmdletBinding()]
param([switch]$CheckOnly, [string]$CodexPath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Use the installer to verify the expected Git source, refresh it, and install.
# A different source named shadow fails explicitly; it is never silently replaced.
$installer = Join-Path $PSScriptRoot 'install-shadow.ps1'
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw 'Missing install-shadow.ps1. Download the complete release package.' }
$arguments = @{ CheckOnly = $CheckOnly }
if ($CodexPath) { $arguments.CodexPath = $CodexPath }
& $installer @arguments
