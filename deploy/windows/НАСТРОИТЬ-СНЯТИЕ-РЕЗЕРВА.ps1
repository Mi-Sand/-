#Requires -Version 5.1
<#
.SYNOPSIS
    Настроить снятие резерва с неподтверждённых заказов.

.DESCRIPTION
    Создаёт задание планировщика Windows, которое раз в час отменяет
    заказы с витрины, не подтверждённые за сутки, и возвращает товар
    покупателям.

    Зачем это нужно. Товар при оформлении заказа не списывается, а
    резервируется: он на складе, но обещан покупателю. Если покупатель
    передумал или ошибся номером, заказ так и остаётся новым, а товар
    висит в резерве и на витрине не показывается.

    Права администратора не нужны: задание создаётся от имени текущего
    пользователя.

.PARAMETER Hours
    Через сколько часов снимать резерв. По умолчанию берётся из
    настроек системы (SHOP_ORDER_LIMITS, обычно сутки).

.PARAMETER Remove
    Убрать задание.

.EXAMPLE
    .\НАСТРОИТЬ-СНЯТИЕ-РЕЗЕРВА.ps1
    .\НАСТРОИТЬ-СНЯТИЕ-РЕЗЕРВА.ps1 -Hours 48
    .\НАСТРОИТЬ-СНЯТИЕ-РЕЗЕРВА.ps1 -Remove
#>
[CmdletBinding()]
param(
    [int]$Hours = 0,
    [switch]$Remove
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$TaskName = 'Складской учёт ЛЕКО - снятие резерва с заказов'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$VenvPython = Join-Path $ProjectRoot 'venv\Scripts\python.exe'

function Fail([string]$Text) {
    Write-Host ''
    Write-Host "ОШИБКА: $Text" -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '  Складская система ООО «ЛЕКО»' -ForegroundColor White
Write-Host '  Снятие резерва с неподтверждённых заказов' -ForegroundColor White
Write-Host '  ---------------------------------------------------------'

if ($Remove) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host '    Задание убрано. Резерв больше не снимается сам.' -ForegroundColor Yellow
        Write-Host '    Снять вручную: venv\Scripts\python.exe manage.py expireorders'
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
Write-Host '  Пробный запуск (ничего не меняя)...' -ForegroundColor Cyan
Push-Location $ProjectRoot
try {
    & $VenvPython 'manage.py' 'expireorders' '--dry-run'
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($code -ne 0) {
    Fail 'пробный запуск не удался. Задание не создано — сначала разберитесь с причиной выше.'
}

$arguments = "-NoProfile -ExecutionPolicy Bypass -Command `"& '$VenvPython' manage.py expireorders --quiet"
if ($Hours -gt 0) { $arguments += " --hours $Hours" }
$arguments += '"'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument $arguments -WorkingDirectory $ProjectRoot
# Раз в час: сутки ожидания и так заданы самой командой, а частая
# проверка лишь сокращает время, на которое товар задерживается в
# резерве после срока.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description `
        'Отмена неподтверждённых заказов витрины и возврат товара покупателям' `
        -Force | Out-Null
} catch {
    Fail "не удалось создать задание: $($_.Exception.Message)"
}

Write-Host ''
Write-Host '  Готово. Задание создано и работает раз в час.' -ForegroundColor Green
Write-Host "  Имя задания: $TaskName"
Write-Host ''
Write-Host '  Посмотреть, что было бы отменено сейчас:'
Write-Host '      venv\Scripts\python.exe manage.py expireorders --dry-run'
Write-Host ''
exit 0
