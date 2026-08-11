#Requires -Version 5.1
<#
.SYNOPSIS
    Сделать настройку nginx под эту установку.

.DESCRIPTION
    Берёт образец deploy\nginx\warehouse-https.conf и подставляет в него
    настоящие пути и имя, а не те, что написаны для примера. Получается
    файл warehouse-https.local.conf рядом с образцом — его и указывают
    nginx.

    Зачем отдельный файл. Править образец прямо в папке программы
    нельзя: он под git, и правка останавливала бы обновление. Рабочая
    настройка под git не числится, поэтому обновления её не касаются, а
    она — их.

    Пути берутся от расположения этого скрипта, так что диск и папка
    могут быть любыми.

.PARAMETER Name
    Имя, по которому сотрудники открывают систему.

.PARAMETER Address
    Адрес компьютера в сети. Если указан, систему можно открыть и по
    нему — и он же попадает в server_name.

.PARAMETER Port
    Порт приложения. По умолчанию 8000.

.EXAMPLE
    .\НАСТРОИТЬ-NGINX.ps1 -Name sklad.leko.local -Address 192.168.1.63
#>
[CmdletBinding()]
param(
    [string]$Name = 'sklad.leko.local',
    [string]$Address,
    [int]$Port = 8000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$NginxDir = Join-Path $ProjectRoot 'deploy\nginx'
$Sample = Join-Path $NginxDir 'warehouse-https.conf'
$Result = Join-Path $NginxDir 'warehouse-https.local.conf'

function Fail([string]$Text) {
    Write-Host ''
    Write-Host "ОШИБКА: $Text" -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '  Складская система ООО «ЛЕКО»' -ForegroundColor White
Write-Host '  Настройка nginx под эту установку' -ForegroundColor White
Write-Host '  ---------------------------------------------------------'

if (-not (Test-Path $Sample)) {
    Fail "не найден образец настройки: $Sample"
}

# nginx понимает только прямую косую черту: обратная в его настройке
# означает продолжение строки, и путь с ней читается неверно.
function Slashes([string]$Path) { return $Path -replace '\\', '/' }

$serverNames = @($Name)
if ($Address) { $serverNames += $Address }

$text = Get-Content $Sample -Raw -Encoding UTF8
$text = $text -replace 'server_name sklad\.leko\.local;',
                       "server_name $($serverNames -join ' ');"
$text = $text -replace 'C:/warehouse_project/deploy/nginx/',
                       (Slashes ($NginxDir + '\'))
$text = $text -replace 'C:/warehouse_project/staticfiles/',
                       (Slashes ((Join-Path $ProjectRoot 'staticfiles') + '\'))
$text = $text -replace 'C:/warehouse_project/media/',
                       (Slashes ((Join-Path $ProjectRoot 'media') + '\'))
$text = $text -replace 'proxy_pass http://127\.0\.0\.1:8000;',
                       "proxy_pass http://127.0.0.1:$Port;"

$header = @"
# Настройка сделана скриптом НАСТРОИТЬ-NGINX.ps1 $(Get-Date -Format 'dd.MM.yyyy HH:mm').
# Править её можно, но при следующем запуске скрипта правка пропадёт.
# Образец, из которого она сделана, — warehouse-https.conf рядом.

"@
[IO.File]::WriteAllText($Result, $header + $text,
    (New-Object System.Text.UTF8Encoding($false)))

Write-Host ''
Write-Host '  Готово:' -ForegroundColor Green
Write-Host "    $Result"
Write-Host ''
Write-Host '  Что в неё вписано:'
Write-Host "    имя системы:  $($serverNames -join ', ')"
Write-Host "    сертификат:   $(Slashes (Join-Path $NginxDir 'sklad.crt'))"
Write-Host "    приложение:   127.0.0.1:$Port"
Write-Host ''

# Предупреждаем о том, чего ещё нет: nginx скажет об этом сам, но
# невнятно, и разбираться придётся уже с его сообщением.
$missing = @()
foreach ($file in @('sklad.crt', 'sklad.key')) {
    if (-not (Test-Path (Join-Path $NginxDir $file))) { $missing += $file }
}
if (-not (Test-Path (Join-Path $ProjectRoot 'staticfiles'))) {
    $missing += 'папка staticfiles'
}
if ($missing.Count -gt 0) {
    Write-Host '  Ещё не хватает:' -ForegroundColor Yellow
    foreach ($item in $missing) { Write-Host "    $item" }
    Write-Host ''
    Write-Host '  Сертификат делает СДЕЛАТЬ-СЕРТИФИКАТ.ps1, папку staticfiles —'
    Write-Host '  команда: venv\Scripts\python.exe manage.py collectstatic --noinput'
    Write-Host ''
}

Write-Host '  Дальше — проверить настройку (подставьте свою папку nginx):'
Write-Host "    cd D:\nginx-1.30.4"
Write-Host "    .\nginx.exe -t -c $Result"
Write-Host ''
exit 0
