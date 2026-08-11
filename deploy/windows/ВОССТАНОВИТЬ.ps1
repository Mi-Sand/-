#Requires -Version 5.1
<#
.SYNOPSIS
    Восстановить складскую систему из резервной копии.

.DESCRIPTION
    Обёртка над `python manage.py restore`. Находит папку программы и
    окружение, проверяет, что система не запущена, и разворачивает копию.

    Команда сама выбирает самую свежую копию, проверяет её до подмены,
    откладывает нынешнюю базу в сторону и в конце говорит, сколько
    записей вернулось.

    Восстанавливать при работающей системе нельзя: часть данных лежит в
    памяти запущенного сервера, и он допишет их поверх развёрнутой копии.

.PARAMETER From
    Папка с конкретной копией. По умолчанию берётся самая свежая.

.PARAMETER In
    Где искать копии, если они лежат не в папке backups —
    например, D:\Копии\Склад.

.PARAMETER NoMedia
    Не возвращать фотографии товаров, только базу.

.PARAMETER Yes
    Не спрашивать подтверждения.

.EXAMPLE
    .\ВОССТАНОВИТЬ.ps1
    .\ВОССТАНОВИТЬ.ps1 -In D:\Копии\Склад
    .\ВОССТАНОВИТЬ.ps1 -From D:\Копии\Склад\2026-08-11_23-30-00
#>
[CmdletBinding()]
param(
    [string]$From,
    [string]$In,
    [switch]$NoMedia,
    [switch]$Yes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

# Скрипт лежит в deploy\windows, программа — двумя уровнями выше
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$VenvPython = Join-Path $ProjectRoot 'venv\Scripts\python.exe'
$ManagePy = Join-Path $ProjectRoot 'manage.py'

function Fail([string]$Text) {
    Write-Host ''
    Write-Host "ОШИБКА: $Text" -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '  Складская система ООО «ЛЕКО»' -ForegroundColor White
Write-Host '  Восстановление из резервной копии' -ForegroundColor White
Write-Host '  ---------------------------------------------------------'

if (-not (Test-Path $ManagePy)) {
    Fail "не найден $ManagePy. Скрипт должен лежать в папке программы, в deploy\windows."
}
if (-not (Test-Path $VenvPython)) {
    Fail "не найдено окружение $VenvPython. Установка не завершена — см. WINDOWS_SETUP.md."
}

# Работающий сервер держит базу открытой и допишет в неё своё поверх
# развёрнутой копии. Порт 8000 занят — почти наверняка это он.
try {
    $busy = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction Stop
} catch {
    $busy = $null
}
if ($busy) {
    Write-Host ''
    Write-Host '  ВНИМАНИЕ: похоже, система сейчас работает (порт 8000 занят).' -ForegroundColor Yellow
    Write-Host '  Закройте окно с запущенным сервером и повторите:' -ForegroundColor Yellow
    Write-Host '  восстановление на ходу оставит в базе смесь старого с новым.' -ForegroundColor Yellow
    Write-Host ''
    $answer = Read-Host '  Продолжить всё равно? (напишите ДА)'
    if ($answer -cne 'ДА') {
        Write-Host '  Отменено — ничего не тронуто.' -ForegroundColor Green
        exit 0
    }
}

$arguments = @('manage.py', 'restore')
if ($From)    { $arguments += @('--from', $From) }
if ($In)      { $arguments += @('--in', $In) }
if ($NoMedia) { $arguments += '--no-media' }
if ($Yes)     { $arguments += '--yes' }

Push-Location $ProjectRoot
try {
    & $VenvPython @arguments
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($code -ne 0) {
    Fail 'восстановление не выполнено. Причина указана выше.'
}

exit 0
