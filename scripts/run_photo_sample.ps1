[CmdletBinding()]
param(
    [ValidateSet("photo", "photo-compact", "classic")]
    [string]$Preset = "photo-compact",
    [int]$TradeCount = 96,
    [int]$EventCount = 120,
    [int]$EquityCount = 700,
    [double]$StartCash = 20000
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Path $PSScriptRoot -Parent
$python = "python"
$artifact = Join-Path $root "artifacts/photo_sample.json"
$outDir = Join-Path $root "outputs/photo_sample"

$scriptArgs = @(
    $python,
    (Join-Path $root "scripts/generate_photo_sample.py")
)

$call = @(
    "--render",
    "--overwrite",
    "--trade-count", "$TradeCount",
    "--event-count", "$EventCount",
    "--equity-count", "$EquityCount",
    "--start-cash", "$StartCash",
    "--photo-preset", $Preset,
    "--output", $artifact,
    "--output-dir", $outDir
)

Write-Host "[RRKAL RenderKit] generating photo sample..."
$py = $scriptArgs[0]
$pyScript = $scriptArgs[1]
& $py $pyScript @call

$report = Join-Path $outDir "report.html"
if (Test-Path $report) {
    Write-Host "[RRKAL RenderKit] done: $report"
} else {
    Write-Warning "[RRKAL RenderKit] report not found: $report"
}
