param(
    [string]$PythonPath,
    [string]$IsccPath,
    [switch]$SkipInstaller,
    [string]$BuildTag = '',
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$sourceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$outputRoot = [System.IO.Path]::GetFullPath($(if ($OutputDirectory) { $OutputDirectory } else { Join-Path $sourceRoot '.build' }))
if ($BuildTag -and $BuildTag -notmatch '^[A-Za-z0-9_-]{1,64}$') { throw 'INVALID_BUILD_TAG' }
$buildSuffix = if ($BuildTag) { '-' + $BuildTag } else { '' }
$distRoot = Join-Path $outputRoot ('_build' + $buildSuffix)
$stageRoot = Join-Path $distRoot 'GitHubRadar'
$expectedPrefix = $outputRoot.TrimEnd('\') + '\'
if (-not $stageRoot.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'BUILD_PATH_OUTSIDE_OUTPUTS'
}

if (-not $PythonPath) {
    $PythonPath = Join-Path $sourceRoot '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "PYTHON_WITH_PYINSTALLER_MISSING: $PythonPath"
}

$assets = Join-Path $sourceRoot 'github_radar\web_assets'
$entrypoint = Join-Path $sourceRoot 'packaging\entrypoint.py'
$translationRuntime = Join-Path $sourceRoot '.venv\Lib\site-packages'
$translationModels = if ($env:STARTRAIL_MODEL_DIR) { $env:STARTRAIL_MODEL_DIR } else { Join-Path $sourceRoot '.tools\translation-models' }
if (-not (Test-Path -LiteralPath (Join-Path $translationModels 'manifest.json'))) {
    throw 'TRANSLATION_NOT_PREPARED: run packaging/prepare_translation.ps1'
}
$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = $translationRuntime
try {
& $PythonPath -m PyInstaller --noconfirm --clean --onedir --noconsole `
    --name GitHubRadar --icon "$assets\startrail.ico" --contents-directory AppFiles `
    --paths $sourceRoot --paths $translationRuntime `
    --collect-all ctranslate2 --collect-all sentencepiece --copy-metadata ctranslate2 `
    --copy-metadata sentencepiece --copy-metadata numpy --copy-metadata pyyaml `
    --add-data "$translationModels;github_radar/translation_models" `
    --add-data "$assets;github_radar/web_assets" `
    --add-data "$(Join-Path $sourceRoot 'github_radar\public_config.json');github_radar" `
    --distpath $distRoot `
    --workpath (Join-Path $outputRoot ('_pyinstaller_work' + $buildSuffix)) `
    --specpath (Join-Path $outputRoot ('_pyinstaller_spec' + $buildSuffix)) $entrypoint
if ($LASTEXITCODE -ne 0) {
    throw "PYINSTALLER_FAILED: $LASTEXITCODE"
}
} finally { $env:PYTHONPATH = $previousPythonPath }

# The app only exposes CPU translation. The upstream wheel also carries an
# unused cuDNN loader; it must not become part of this CPU redistribution.
$gpuLoader = [System.IO.Path]::GetFullPath((Join-Path $stageRoot 'AppFiles\ctranslate2\cudnn64_9.dll'))
if (-not $gpuLoader.StartsWith($stageRoot.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'GPU_LOADER_OUTSIDE_STAGE'
}
if (Test-Path -LiteralPath $gpuLoader -PathType Leaf) {
    Remove-Item -LiteralPath $gpuLoader -Force
}
& $PythonPath (Join-Path $sourceRoot 'scripts\collect_licenses.py') `
    --output (Join-Path $stageRoot 'AppFiles\licenses') --models $translationModels
if ($LASTEXITCODE -ne 0) { throw 'REDISTRIBUTION_NOTICES_FAILED' }

Copy-Item -LiteralPath (Join-Path $stageRoot 'GitHubRadar.exe') `
    -Destination (Join-Path $stageRoot 'Uninstall.exe') -Force
New-Item -ItemType Directory -Path (Join-Path $stageRoot 'UserData') -Force | Out-Null
Set-Content -LiteralPath (Join-Path $stageRoot 'UserData\.github-radar-data') `
    -Value 'GitHub Radar personal data v1' -Encoding utf8
Copy-Item -LiteralPath (Join-Path $sourceRoot '使用说明.txt') -Destination $stageRoot -Force

& (Join-Path $sourceRoot 'tests\windows_delivery_check.ps1') -StagingPath $stageRoot
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
    throw "STAGING_CHECK_FAILED: $LASTEXITCODE"
}

if ($SkipInstaller) {
    Write-Output "STAGING_READY: $stageRoot"
    return
}

if (-not $IsccPath) {
    $candidates = @(
        (Join-Path $sourceRoot '.tools\InnoSetup6\ISCC.exe'),
        (Join-Path $sourceRoot '.tools\Tools.InnoSetup.6.7.3\tools\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe')
    )
    $IsccPath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
    throw 'INNO_COMPILER_MISSING: provide -IsccPath'
}

$iss = Join-Path $sourceRoot 'packaging\GitHubRadar.iss'
& $IsccPath "/DSourcePath=$stageRoot" "/DOutputPath=$outputRoot" $iss
if ($LASTEXITCODE -ne 0) {
    throw "INNO_COMPILATION_FAILED: $LASTEXITCODE"
}
$setup = Join-Path $outputRoot 'StarTrail_Setup.exe'
if (-not (Test-Path -LiteralPath $setup -PathType Leaf)) {
    throw "SETUP_MISSING: $setup"
}
Write-Output "SETUP_READY: $setup"
