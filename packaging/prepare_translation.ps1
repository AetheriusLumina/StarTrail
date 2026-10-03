param([string]$PythonPath, [string]$ArchiveDirectory, [string]$ModelsDirectory, [switch]$Offline)
$ErrorActionPreference = 'Stop'
$sourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $PythonPath) { $PythonPath = Join-Path $sourceRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'PYTHON_MISSING: create .venv first' }
$modelArguments = @((Join-Path $sourceRoot 'scripts\prepare_models.py'))
if ($ArchiveDirectory) { $modelArguments += @('--archive-dir', $ArchiveDirectory) }
if ($ModelsDirectory) { $modelArguments += @('--models-dir', $ModelsDirectory) }
if ($Offline) { $modelArguments += '--offline' }
& $PythonPath @modelArguments
if ($LASTEXITCODE -ne 0) { throw 'TRANSLATION_PREPARATION_FAILED' }
