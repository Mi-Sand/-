#Requires -Version 5.1
<#
.SYNOPSIS
    Настроить ежедневную сводку на почту.

.DESCRIPTION
    Создаёт задание планировщика Windows, которое раз в сутки
    отправляет ответственному сводку: что требует внимания и что
    происходило за сутки.

    Зачем это нужно. Раньше письма уходили на каждое событие — остаток
    упал ниже минимума, инвентаризация нашла недостачу. При десятке
    материалов это десяток писем в день, и ящик перестают читать. Тогда
    предупреждения формально есть, а фактически их никто не видит.

    Перед созданием задания проверьте, что почта вообще настроена:
        venv\Scripts\python.exe manage.py testmail

    Права администратора не нужны: задание создаётся от имени текущего
    пользователя.

.PARAMETER At
    Во сколько отправлять сводку. По умолчанию 08:00 — чтобы она ждала
    в ящике к началу рабочего дня.

.PARAMETER To
    Кому отправлять. По умолчанию — адрес из настроек
    (WAREHOUSE_MANAGER_EMAIL в .env).

.PARAMETER Remove
    Убрать задание.

.EXAMPLE
    .\НАСТРОИТЬ-СВОДКУ.ps1
    .\НАСТРОИТЬ-СВОДКУ.ps1 -At 07:30 -To nachalnik@example.ru
    .\НАСТРОИТЬ-СВОДКУ.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$At = '08:00',
    [string]$To,
    [switch]$Remove
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$TaskName = 'Складской учёт ЛЕКО - сводка на почту'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$VenvPython = Join-Path $ProjectRoot 'venv\Scripts\python.exe'

function Fail([string]$Text) {
    Write-Host ''
    Write-Host "ОШИБКА: $Text" -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '  Складская система ООО «ЛЕКО»' -ForegroundColor White
Write-Host '  Ежедневная сводка на почту' -ForegroundColor White
Write-Host '  ---------------------------------------------------------'

if ($Remove) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host '    Задание убрано. Сводка больше не приходит.' -ForegroundColor Yellow
        Write-Host '    Посмотреть вручную: venv\Scripts\python.exe manage.py dailysummary --dry-run'
    } else {
        Write-Host '    Задания и не было — убирать нечего.'
    }
    Write-Host ''
    exit 0
}

if (-not (Test-Path $VenvPython)) {
    Fail "не найдено окружение $VenvPython. Установка не завершена — см. WINDOWS_SETUP.md."
}

# Пробный запуск вхолостую: если команда не работает, задание создавать
# незачем — оно будет молча падать каждый час.
Write-Host ''
Write-Host '  Пробный сбор сводки (письмо не отправляется)...' -ForegroundColor Cyan
Push-Location $ProjectRoot
try {
    & $VenvPython 'manage.py' 'dailysummary' '--dry-run'
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($code -ne 0) {
    Fail 'сводка не собирается. Задание не создано — сначала разберитесь с причиной выше.'
}

try {
    $time = [datetime]::ParseExact($At, 'HH:mm', $null)
} catch {
    Fail "время «$At» непонятно. Формат: ЧЧ:ММ, например 08:00."
}

$arguments = "-NoProfile -ExecutionPolicy Bypass -Command `"& '$VenvPython' manage.py dailysummary --quiet"
if ($To) { $arguments += " --to $To" }
$arguments += '"'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument $arguments -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description `
        'Ежедневная сводка складского учёта ООО «ЛЕКО» на почту' `
        -Force | Out-Null
} catch {
    Fail "не удалось создать задание: $($_.Exception.Message)"
}

Write-Host ''
Write-Host "  Готово. Сводка будет приходить ежедневно в $At." -ForegroundColor Green
Write-Host "  Имя задания: $TaskName"
Write-Host ''
Write-Host '  Посмотреть сводку прямо сейчас, не отправляя письма:'
Write-Host '      venv\Scripts\python.exe manage.py dailysummary --dry-run'
Write-Host ''
Write-Host '  Убедиться, что письма вообще доходят:'
Write-Host '      venv\Scripts\python.exe manage.py testmail'
Write-Host ''
exit 0
