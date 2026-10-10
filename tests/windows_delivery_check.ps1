param(
    [string]$StagingPath = (Join-Path $PSScriptRoot '..\.build\_build\GitHubRadar'),
    [switch]$KeepEvidence
)

$ErrorActionPreference = 'Stop'
$stage = [System.IO.Path]::GetFullPath($StagingPath)
if (-not (Test-Path -LiteralPath $stage -PathType Container)) {
    throw "STAGING_MISSING: $stage"
}

$expected = @('AppFiles', 'GitHubRadar.exe', 'Uninstall.exe', 'UserData', '使用说明.md')
$actual = @(Get-ChildItem -LiteralPath $stage -Force | ForEach-Object Name | Sort-Object)
$missing = @($expected | Where-Object { $_ -notin $actual })
$extra = @($actual | Where-Object { $_ -notin $expected })
if ($missing.Count -or $extra.Count) {
    throw "WRONG_TOP_LEVEL: missing=$($missing -join ',') extra=$($extra -join ',')"
}

foreach ($asset in @('index.html', 'app.css', 'app.js', 'i18n.js', 'translation.js', 'history_following.js')) {
    $path = Join-Path $stage "AppFiles\github_radar\web_assets\$asset"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "WEB_ASSET_MISSING: $path"
    }
}

$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$unicodeRoot = Join-Path $tempRoot ("GitHub Radar 验收 " + [guid]::NewGuid().ToString('N'))
if (-not $unicodeRoot.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'TEST_PATH_OUTSIDE_TEMP'
}

try {
    New-Item -ItemType Directory -Path $unicodeRoot | Out-Null
    Get-ChildItem -LiteralPath $stage -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $unicodeRoot -Recurse
    }
    $exe = Join-Path $unicodeRoot 'GitHubRadar.exe'
    $process = Start-Process -FilePath $exe -ArgumentList '--help' -WorkingDirectory $unicodeRoot -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "EXE_FAILED_IN_UNICODE_PATH: $($process.ExitCode)"
    }
    $keyword = Start-Process -FilePath $exe -ArgumentList '--add-keyword=验收测试' `
        -WorkingDirectory $unicodeRoot -WindowStyle Hidden -Wait -PassThru
    if ($keyword.ExitCode -ne 0 -or
        -not (Test-Path -LiteralPath (Join-Path $unicodeRoot 'UserData\radar.db') -PathType Leaf)) {
        throw "PACKAGED_DATA_WRITE_FAILED: $($keyword.ExitCode)"
    }
    Write-Output "STAGING_OK: $unicodeRoot"
}
finally {
    $resolved = [System.IO.Path]::GetFullPath($unicodeRoot)
    if (-not $KeepEvidence -and $resolved.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath $resolved)) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
