#Requires -Version 5.1
<#
.SYNOPSIS
    Снять резервную копию складской системы.

.DESCRIPTION
    Обёртка над `python manage.py backup`: находит папку программы и
    окружение, запускает копирование и возвращает код завершения.
    Пригодна и для ручного запуска, и для задания в планировщике.

    Всю работу делает сама команда backup: снимает копию базы средством
    SQLite, проверяет её на читаемость, сверяет число записей с базой,
    копирует фотографии и настройки, удаляет копии старше срока.

.PARAMETER To
    Куда складывать копии. По умолчанию — папка backups в программе.

    Стоит указать другой диск или сетевую папку: копия рядом с базой
    не спасёт от отказа диска, а это и есть главный случай, ради
    которого копии делают.

.PARAMETER Keep
    Сколько дней хранить копии. По умолчанию 30. Самая свежая не
    удаляется никогда.

.PARAMETER NoMedia
    Не копировать фотографии товаров. Имеет смысл для ежедневных копий:
    фотографии занимают больше всего места, а меняются редко.

.PARAMETER Quiet
    Выводить только ошибки. Для запуска по расписанию.

.EXAMPLE
    .\backup.ps1
    .\backup.ps1 -To D:\Копии\Склад -Keep 60
    .\backup.ps1 -NoMedia -Quiet
#>
[CmdletBinding()]
param(
    [string]$To,
    [int]$Keep = 30,
    [switch]$NoMedia,
    [switch]$Quiet
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

if (-not (Test-Path $ManagePy)) {
    Fail "не найден $ManagePy. Скрипт должен лежать в папке программы, в deploy\windows."
}
if (-not (Test-Path $VenvPython)) {
    Fail "не найдено окружение $VenvPython. Установка не завершена — см. WINDOWS_SETUP.md."
}

$arguments = @('manage.py', 'backup', '--keep', $Keep)
if ($To)      { $arguments += @('--to', $To) }
if ($NoMedia) { $arguments += '--no-media' }
if ($Quiet)   { $arguments += '--quiet' }

Push-Location $ProjectRoot
try {
    & $VenvPython @arguments
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($code -ne 0) {
    Fail 'копия не снята. Причина указана выше.'
}

exit 0
