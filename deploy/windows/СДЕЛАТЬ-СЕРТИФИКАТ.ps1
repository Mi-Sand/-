#Requires -Version 5.1
<#
.SYNOPSIS
    Выпустить сертификат для работы системы по HTTPS.

.DESCRIPTION
    Создаёт собственный сертификат сроком на два года и раскладывает его
    в три файла рядом с настройкой nginx:

        sklad.crt  — сертификат для nginx
        sklad.key  — закрытый ключ для nginx (никому не показывать)
        sklad-для-сотрудников.crt — тот же сертификат для установки на
                                    рабочие места

    Зачем последний. Сертификат выпущен предприятием, а не признанным
    удостоверяющим центром, и браузер про такой честно говорит: «этому
    сайту доверять нельзя». Каждый день нажимать «всё равно продолжить»
    — плохая привычка: однажды так же нажмут на настоящую подмену.
    Поэтому сертификат один раз ставят на рабочие места, и предупреждение
    пропадает.

    Купленный сертификат тут не нужен и чаще всего невозможен: их выдают
    на имена в интернете, а sklad.leko.local существует только внутри
    предприятия.

.PARAMETER Name
    Имя, по которому сотрудники открывают систему. По умолчанию —
    sklad.leko.local. Оно должно совпадать с адресом в браузере: иначе
    браузер ругается уже не на доверие, а на несовпадение имени.

.PARAMETER Address
    Адрес компьютера в сети, например 192.168.1.50. Добавляется в
    сертификат, чтобы систему можно было открыть и по адресу.

.PARAMETER Years
    Срок сертификата. По умолчанию 2 года.

.EXAMPLE
    .\СДЕЛАТЬ-СЕРТИФИКАТ.ps1
    .\СДЕЛАТЬ-СЕРТИФИКАТ.ps1 -Name sklad.leko.local -Address 192.168.1.50
#>
[CmdletBinding()]
param(
    [string]$Name = 'sklad.leko.local',
    [string]$Address,
    [int]$Years = 2
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Target = Join-Path $ProjectRoot 'deploy\nginx'

function Fail([string]$Text) {
    Write-Host ''
    Write-Host "ОШИБКА: $Text" -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '  Складская система ООО «ЛЕКО»' -ForegroundColor White
Write-Host '  Сертификат для работы по HTTPS' -ForegroundColor White
Write-Host '  ---------------------------------------------------------'

if (-not (Test-Path $Target)) {
    New-Item -ItemType Directory -Path $Target -Force | Out-Null
}

$names = @($Name)
if ($Address) { $names += $Address }
Write-Host ''
Write-Host "  Имя в сертификате: $($names -join ', ')"
Write-Host "  Срок: $Years г."

try {
    $certificate = New-SelfSignedCertificate `
        -DnsName $names `
        -CertStoreLocation 'Cert:\LocalMachine\My' `
        -FriendlyName "Складской учёт ООО ЛЕКО ($Name)" `
        -NotAfter (Get-Date).AddYears($Years) `
        -KeyExportPolicy Exportable `
        -KeyLength 2048 `
        -KeyAlgorithm RSA `
        -HashAlgorithm SHA256
} catch {
    Fail @"
не удалось выпустить сертификат: $($_.Exception.Message)

Скорее всего не хватает прав. Запустите PowerShell от имени
администратора и повторите.
"@
}

# Пароль нужен только чтобы вынуть ключ из хранилища Windows: файл с
# ним тут же превращается в пару crt/key и удаляется.
$password = [System.Guid]::NewGuid().ToString()
$securePassword = ConvertTo-SecureString -String $password -Force -AsPlainText
$pfxPath = Join-Path $Target 'sklad-временный.pfx'

Export-PfxCertificate -Cert $certificate -FilePath $pfxPath `
    -Password $securePassword | Out-Null

# Сертификат для сотрудников: только открытая часть, ключа в нём нет
$publicPath = Join-Path $Target 'sklad-для-сотрудников.crt'
Export-Certificate -Cert $certificate -FilePath $publicPath -Type CERT | Out-Null

# nginx понимает PEM, а Windows выдаёт PFX. Перекладываем openssl —
# он идёт вместе с nginx для Windows.
$openssl = Get-Command openssl -ErrorAction SilentlyContinue
if (-not $openssl) {
    Write-Host ''
    Write-Host '  ВНИМАНИЕ: не найдена программа openssl.' -ForegroundColor Yellow
    Write-Host '  Сертификат выпущен и лежит здесь:' -ForegroundColor Yellow
    Write-Host "      $pfxPath"
    Write-Host '  Чтобы получить из него файлы для nginx, выполните на любом'
    Write-Host '  компьютере с openssl:'
    Write-Host "      openssl pkcs12 -in sklad-временный.pfx -clcerts -nokeys -out sklad.crt"
    Write-Host "      openssl pkcs12 -in sklad-временный.pfx -nocerts -nodes -out sklad.key"
    Write-Host "  Пароль от файла: $password"
    Write-Host ''
    exit 0
}

$crtPath = Join-Path $Target 'sklad.crt'
$keyPath = Join-Path $Target 'sklad.key'
& openssl pkcs12 -in $pfxPath -clcerts -nokeys -out $crtPath -passin "pass:$password" 2>$null
& openssl pkcs12 -in $pfxPath -nocerts -nodes -out $keyPath -passin "pass:$password" 2>$null
Remove-Item $pfxPath -Force

if (-not (Test-Path $crtPath) -or -not (Test-Path $keyPath)) {
    Fail 'не удалось получить файлы для nginx. Проверьте, что openssl работает.'
}

Write-Host ''
Write-Host '  Готово. Файлы:' -ForegroundColor Green
Write-Host "    $crtPath        — сертификат для nginx"
Write-Host "    $keyPath        — ключ для nginx (никому не показывать)"
Write-Host "    $publicPath  — поставить на рабочие места"
Write-Host ''
Write-Host '  Дальше:'
Write-Host '    1. Пропишите пути к sklad.crt и sklad.key в настройке nginx'
Write-Host '       (образец — deploy\nginx\warehouse-https.conf).'
Write-Host '    2. Поставьте sklad-для-сотрудников.crt на рабочие места:'
Write-Host '       двойное нажатие -> Установить сертификат -> Локальный компьютер'
Write-Host '       -> Поместить в: Доверенные корневые центры сертификации.'
Write-Host '    3. Впишите в .env строки USE_HTTPS=True и CSRF_TRUSTED_ORIGINS.'
Write-Host '    4. Проверьте: venv\Scripts\python.exe manage.py checksetup'
Write-Host ''
Write-Host '  Порядок целиком — в HTTPS.md.'
Write-Host ''
exit 0
