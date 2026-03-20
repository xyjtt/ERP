param(
    [Parameter(Mandatory = $true)]
    [string]$File,

    [Parameter(Mandatory = $true)]
    [string]$ShopName,

    [string]$OperatorName = "系统",
    [string]$Platform = "京喜",
    [string]$TargetCode = "txcj",
    [string]$StatDate = "",
    [switch]$ValidateOnly,
    [switch]$SkipLogin,
    [string]$DbConfig = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$arguments = @(
    "main.py",
    "--source", "excel",
    "--file", $File,
    "--default-platform", $Platform,
    "--default-shop-name", $ShopName,
    "--default-operator-name", $OperatorName,
    "--discontinued-target-code", $TargetCode
)

if ($StatDate) {
    $arguments += @("--expected-stat-date", $StatDate)
}

if ($ValidateOnly) {
    $arguments += "--validate-only"
}

if ($SkipLogin) {
    $arguments += "--skip-login"
}

if ($DbConfig) {
    $arguments += @("--db-config", $DbConfig)
}

python @arguments
