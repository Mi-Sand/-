#Requires -Version 5.1
<#
.SYNOPSIS
    Установка складской системы и проверка всех её составных частей.

.DESCRIPTION
    Одна кнопка вместо девяти шагов из руководства. Делает по порядку:

        1. находит Python и проверяет, что он годится;
        2. создаёт окружение venv;
        3. ставит библиотеки;
        4. заводит .env с собственным ключом и адресами этого компьютера;
        5. создаёт базу (миграции);
        6. заводит администратора, если сотрудников ещё нет;
        7. собирает статику;
        8. создаёт папки media, logs, backups;
        9. проверяет все части и говорит, что осталось сделать.

    Повторный запуск безопасен: готовое не переделывается. Уже
    заведённый .env не перезаписывается — там пароли и настройки почты,
    а установщик о них ничего не знает.

    Ключ -Check выполняет только девятый шаг: ничего не ставит, а
    проверяет, всё ли на месте. Это же делает кнопка ПРОВЕРИТЬ.bat.

.PARAMETER Check
    Только проверка. Ничего не устанавливать и не менять.

.PARAMETER NoInput
    Ничего не спрашивать. Администратор тогда не заводится — об этом
    будет сказано в итогах.

.PARAMETER Admin
    Имя администратора, чтобы не вводить его руками.

.PARAMETER Password
    Пароль администратора. Без него будет запрошен при установке.

.EXAMPLE
    .\УСТАНОВИТЬ.ps1
    .\УСТАНОВИТЬ.ps1 -Check
    .\УСТАНОВИТЬ.ps1 -Admin sklad -Password "..." -NoInput
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$NoInput,
    [string]$Admin = '',
    [string]$Password = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

# Пути. Скрипт лежит в deploy\windows, программа — двумя уровнями выше.
$ProjectRoot = (Resolve-Path (Join-Path (Join-Path $PSScriptRoot '..') '..')).Path
$VenvDir = Join-Path $ProjectRoot 'venv'
# На Windows исполняемые файлы окружения лежат в Scripts, на прочих
# системах — в bin. Разница нужна не ради других систем, а ради того,
# чтобы установку можно было прогнать целиком при проверке.
$IsWindowsHost = $true
try { $IsWindowsHost = [System.Environment]::OSVersion.Platform -eq 'Win32NT' } catch { }
$BinDir = if ($IsWindowsHost) { 'Scripts' } else { 'bin' }
$Exe = if ($IsWindowsHost) { '.exe' } else { '' }
$VenvPython = Join-Path $VenvDir (Join-Path $BinDir "python$Exe")
$EnvFile = Join-Path $ProjectRoot '.env'
$EnvSample = Join-Path $ProjectRoot '.env.example'

$script:Problems = @()
$script:Notes = @()

function Write-Head([string]$Text) {
    Write-Host ''
    Write-Host "=> $Text" -ForegroundColor Cyan
}
function Write-Ok([string]$Text)   { Write-Host "   $Text" -ForegroundColor Green }
function Write-Note([string]$Text) { Write-Host "   $Text" -ForegroundColor DarkGray }
function Write-Warn([string]$Text) {
    # Просто предупреждение на экране. В итоги оно не попадает: там
    # должны быть советы, что делать, а не пересказ уже показанного.
    # Первый вариант складывал в итоги всё подряд, и список из десятка
    # строк вида «пропущено: нет окружения» никто не стал бы читать.
    Write-Host "   $Text" -ForegroundColor Yellow
}
function Add-Note([string]$Text) {
    if ($script:Notes -notcontains $Text) { $script:Notes += $Text }
}
function Add-Problem([string]$Text) {
    if ($script:Problems -notcontains $Text) { $script:Problems += $Text }
}
function Stop-WithError([string]$Text) {
    Write-Host ''
    Write-Host "ОШИБКА: $Text" -ForegroundColor Red
    Write-Host ''
    Write-Host 'Установка не завершена. Исправьте и запустите снова.' -ForegroundColor Red
    Write-Host ''
    exit 1
}

Write-Host ''
Write-Host '  Складская система ООО Фирма «ЛЕКО»' -ForegroundColor White
Write-Host $(if ($Check) { '  Проверка установки' } else { '  Установка' }) -ForegroundColor White
Write-Host '  ---------------------------------------------------------'
Write-Note "Папка программы: $ProjectRoot"

# --- 1. Python --------------------------------------------------------------
Write-Head 'Python'

function Find-Python {
    <#
        Ищем годный Python: 3.10 и новее. Проверяем не только наличие
        команды, но и версию — на складском компьютере нередко стоит
        древний 3.8, на котором проект не запустится, а сообщение об
        этом появилось бы много позже и в непонятном виде.
    #>
    $candidates = @()
    foreach ($name in @('python', 'python3', 'py')) {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        if ($found) { $candidates += $found.Source }
    }
    foreach ($path in $candidates) {
        try {
            $version = & $path -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        } catch { continue }
        if (-not $version) { continue }
        $parts = "$version".Trim().Split('.')
        if ($parts.Count -lt 2) { continue }
        if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10) {
            return @{ Path = $path; Version = "$version".Trim() }
        }
    }
    return $null
}

if (Test-Path $VenvPython) {
    $inVenv = & $VenvPython -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    Write-Ok "окружение уже есть, Python $inVenv"
} else {
    $python = Find-Python
    if (-not $python) {
        Stop-WithError @'
не найден Python 3.10 или новее.

Скачайте с https://www.python.org/downloads/ и при установке
обязательно отметьте «Add python.exe to PATH» на первом экране.
Затем закройте это окно, откройте заново и повторите.
'@
    }
    Write-Ok "найден Python $($python.Version): $($python.Path)"
}

# --- 2. Окружение -----------------------------------------------------------
Write-Head 'Рабочее окружение'

if (Test-Path $VenvPython) {
    Write-Ok 'venv на месте'
} elseif ($Check) {
    Add-Problem 'система ещё не установлена'
    Write-Warn 'нет окружения venv'
} else {
    Write-Note 'создаю venv, это занимает до минуты...'
    & $python.Path -m venv $VenvDir
    if (-not (Test-Path $VenvPython)) {
        Stop-WithError 'не удалось создать окружение venv. Проверьте права на папку программы.'
    }
    Write-Ok 'окружение создано'
}

# --- 3. Библиотеки ----------------------------------------------------------
Write-Head 'Библиотеки'

function Test-Libraries {
    if (-not (Test-Path $VenvPython)) { return $false }
    # Проверяем не по списку файлов, а попыткой ввоза: библиотека может
    # лежать на диске и всё равно не работать (не та разрядность, битая
    # установка). Django тянет за собой остальное.
    & $VenvPython -c "import django, rest_framework, openpyxl, PIL, whitenoise" 2>$null
    return ($LASTEXITCODE -eq 0)
}

if (Test-Libraries) {
    $djangoVersion = & $VenvPython -c "import django; print(django.get_version())" 2>$null
    Write-Ok "на месте, Django $djangoVersion"
} elseif ($Check) {
    Add-Problem 'система ещё не установлена'
    Write-Warn 'библиотеки не установлены'
} else {
    Write-Note 'ставлю библиотеки, это самый долгий шаг...'
    & $VenvPython -m pip install --upgrade pip --quiet
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot 'requirements.txt') --quiet
    if (-not (Test-Libraries)) {
        Stop-WithError @'
не удалось поставить библиотеки.

Чаще всего причина — нет связи с интернетом или мешает антивирус.
Проверьте подключение и запустите установку снова: уже сделанное
переделываться не будет.
'@
    }
    Write-Ok 'библиотеки поставлены'
}

# --- 4. Настройки .env ------------------------------------------------------
Write-Head 'Файл настроек .env'

function New-SecretKey {
    # Ключ подписывает сессии и формы. Свой у каждой установки: с общим
    # ключом чужой человек может подделать вход.
    $bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ([Convert]::ToBase64String($bytes) -replace '[^A-Za-z0-9]', '').Substring(0, 50)
}

function Get-LocalAddresses {
    $list = @('localhost', '127.0.0.1')
    try {
        Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
            ForEach-Object { $list += $_.IPAddress }
    } catch {
        # Не Windows или команда недоступна — обойдёмся своим именем.
        try { $list += [System.Net.Dns]::GetHostName() } catch { }
    }
    return ($list | Select-Object -Unique)
}

if (Test-Path $EnvFile) {
    Write-Ok '.env уже есть — не трогаю'
    Write-Note 'там пароли и настройки почты; установщик их не знает и не переписывает'
} elseif ($Check) {
    Add-Problem 'нет файла настроек .env'
    Write-Warn 'файла .env нет'
} else {
    if (-not (Test-Path $EnvSample)) {
        Stop-WithError "не найден образец настроек: $EnvSample"
    }
    $text = Get-Content $EnvSample -Raw -Encoding UTF8
    $addresses = (Get-LocalAddresses) -join ','
    $text = $text -replace '(?m)^SECRET_KEY=.*$', "SECRET_KEY=$(New-SecretKey)"
    $text = $text -replace '(?m)^DEBUG=.*$', 'DEBUG=False'
    $text = $text -replace '(?m)^ALLOWED_HOSTS=.*$', "ALLOWED_HOSTS=$addresses"
    [IO.File]::WriteAllText($EnvFile, $text,
        (New-Object System.Text.UTF8Encoding($false)))
    Write-Ok '.env создан: свой ключ, отладочный режим выключен'
    Write-Note "адреса: $addresses"
}

# --- 5. База данных ---------------------------------------------------------
Write-Head 'База данных'

function Invoke-Manage([string[]]$Arguments) {
    Push-Location $ProjectRoot
    try {
        $prevEA = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $output = & $VenvPython '-X' 'utf8' 'manage.py' @Arguments 2>&1
        $code = $LASTEXITCODE
        $ErrorActionPreference = $prevEA
    } finally {
        Pop-Location
    }
    return @{ Code = $code; Text = ($output | Out-String) }
}

if (-not (Test-Path $VenvPython)) {
    Write-Warn 'пропущено: нет окружения'
} elseif ($Check) {
    $result = Invoke-Manage @('migrate', '--check')
    if ($result.Code -eq 0) {
        Write-Ok 'база создана, изменений не ждёт'
    } else {
        Add-Problem 'база не создана или не применены миграции'
        Write-Warn 'нужны миграции: manage.py migrate'
    }
} else {
    $result = Invoke-Manage @('migrate', '--noinput')
    if ($result.Code -ne 0) {
        Write-Host $result.Text
        Stop-WithError 'не удалось создать базу данных (см. вывод выше).'
    }
    Write-Ok 'база готова'
}

# --- 6. Администратор -------------------------------------------------------
Write-Head 'Учётная запись администратора'

function Get-UserCount {
    $result = Invoke-Manage @('shell', '-c',
        'from django.contrib.auth import get_user_model; print(get_user_model().objects.count())')
    if ($result.Code -ne 0) { return -1 }
    $line = ($result.Text -split "`n" | Where-Object { $_ -match '^\s*\d+\s*$' } | Select-Object -Last 1)
    if ($null -eq $line) { return -1 }
    return [int]$line.Trim()
}

if (-not (Test-Path $VenvPython)) {
    Write-Warn 'пропущено: нет окружения'
} else {
    $count = Get-UserCount
    if ($count -gt 0) {
        Write-Ok "сотрудников заведено: $count"
    } elseif ($Check) {
        Add-Problem 'не заведён ни один сотрудник — войти будет некому'
        Write-Warn 'сотрудников нет'
    } elseif ($NoInput -and -not $Admin) {
        Write-Warn 'администратор не заведён (запуск без вопросов)'
        Write-Note 'заведите позже: venv\Scripts\python.exe manage.py createsuperuser'
    } else {
        $name = $Admin
        if (-not $name) {
            $name = Read-Host '   Имя администратора (латиницей, например sklad)'
        }
        $secret = $Password
        if (-not $secret) {
            $secure = Read-Host '   Пароль (не отображается)' -AsSecureString
            $secret = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
        }
        if (-not $name -or -not $secret) {
            Write-Warn 'администратор не заведён: имя или пароль не введены'
        } else {
            # Пароль передаём переменной окружения, а не в командной
            # строке: список запущенных команд виден другим, и пароль
            # попал бы в него целиком.
            $env:DJANGO_SUPERUSER_PASSWORD = $secret
            $result = Invoke-Manage @('createsuperuser', '--noinput',
                                      '--username', $name, '--email', '')
            Remove-Item Env:\DJANGO_SUPERUSER_PASSWORD -ErrorAction SilentlyContinue
            if ($result.Code -eq 0) {
                Write-Ok "администратор «$name» заведён"
            } else {
                Write-Host $result.Text
                Write-Warn 'не удалось завести администратора (см. вывод выше)'
            }
        }
    }
}

# --- 7. Статика -------------------------------------------------------------
Write-Head 'Оформление страниц'

$StaticDir = Join-Path $ProjectRoot 'staticfiles'
if (-not (Test-Path $VenvPython)) {
    Write-Warn 'пропущено: нет окружения'
} elseif ($Check) {
    if (Test-Path $StaticDir) {
        $count = (Get-ChildItem $StaticDir -Recurse -File -ErrorAction SilentlyContinue).Count
        Write-Ok "собрано файлов: $count"
    } else {
        Write-Warn 'статика не собрана — страницы откроются без оформления, если DEBUG=False'
        Add-Note 'соберите оформление: venv\Scripts\python.exe manage.py collectstatic --noinput'
    }
} else {
    $result = Invoke-Manage @('collectstatic', '--noinput')
    if ($result.Code -eq 0) {
        Write-Ok 'оформление собрано'
    } else {
        Write-Warn 'не удалось собрать оформление (страницы откроются без стилей)'
    }
}

# --- 8. Папки ---------------------------------------------------------------
Write-Head 'Рабочие папки'

foreach ($name in @('media', 'logs', 'backups')) {
    $path = Join-Path $ProjectRoot $name
    if (Test-Path $path) {
        Write-Ok "$name — на месте"
    } elseif ($Check) {
        Write-Warn "$name — нет"
    } else {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
        Write-Ok "$name — создана"
    }
}

# --- 9. Проверка всех частей ------------------------------------------------
Write-Head 'Проверка составных частей'

if (Test-Path $VenvPython) {
    $result = Invoke-Manage @('checksetup')
    Write-Host $result.Text
    if ($result.Code -ne 0) {
        Add-Problem 'проверка установки нашла мешающее работе — см. вывод выше'
    }
} else {
    Write-Warn 'пропущено: нет окружения'
}

Write-Head 'Что настроено вокруг системы'

# Ночное снятие копий — то, без чего однажды теряют весь учёт.
$hasBackupTask = $false
try {
    $hasBackupTask = $null -ne (Get-ScheduledTask -TaskName '*клад*коп*' -ErrorAction SilentlyContinue)
} catch { }
if ($hasBackupTask) {
    Write-Ok 'ночное снятие копий — задание есть'
} else {
    Write-Warn 'ночное снятие копий не настроено'
    Add-Note 'заведите ночные копии: deploy\windows\НАСТРОИТЬ-КОПИИ.ps1 -To D:\Копии\Склад'
}

# Порт 8000: занят ли кем-то ещё
try {
    $busy = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue
    if ($busy) {
        Write-Warn 'порт 8000 уже занят другой программой'
        Add-Note 'освободите порт 8000 или запускайте систему на другом порту'
    } else {
        Write-Ok 'порт 8000 свободен'
    }
} catch { }

# nginx и сертификат — только если за них уже брались
$certPath = Join-Path $ProjectRoot 'deploy\nginx\sklad.crt'
if (Test-Path $certPath) {
    $head = Get-Content $certPath -TotalCount 1 -ErrorAction SilentlyContinue
    if ($head -like '-----BEGIN*') {
        Write-Ok 'сертификат для HTTPS на месте'
    } else {
        Write-Warn 'файл сертификата испорчен — выпустите заново (СДЕЛАТЬ-СЕРТИФИКАТ.ps1)'
    }
} else {
    Write-Note 'HTTPS не настроен — для сети предприятия это допустимо (см. HTTPS.md)'
}

# --- Итоги ------------------------------------------------------------------
Write-Host ''
Write-Host '  ---------------------------------------------------------'
if ($Check -and $script:Problems -contains 'система ещё не установлена') {
    Write-Host '  Система на этом компьютере ещё не установлена.' -ForegroundColor Red
    Write-Host ''
    Write-Host '  Запустите УСТАНОВИТЬ.bat — двойным щелчком из этой же папки.'
    Write-Host ''
    exit 1
}
if ($script:Problems.Count -gt 0) {
    Write-Host '  Мешает работе:' -ForegroundColor Red
    foreach ($item in $script:Problems) { Write-Host "    - $item" -ForegroundColor Red }
}
if ($script:Notes.Count -gt 0) {
    Write-Host ''
    Write-Host '  Стоит сделать:' -ForegroundColor Yellow
    foreach ($item in $script:Notes) { Write-Host "    - $item" -ForegroundColor Yellow }
}

Write-Host ''
if ($script:Problems.Count -eq 0) {
    # «Всё на месте» — только про то, что мешает работе. Замечания
    # проверки установки (нет склада, нет копий, не заполнены
    # реквизиты) работать не мешают, но и умалчивать о них нельзя:
    # рядом стояла бы бодрая надпись и список из трёх пунктов.
    Write-Host $(if ($Check) { '  Мешающего работе не найдено.' }
                 else { '  Установка завершена.' }) -ForegroundColor Green
    Write-Host '  Замечания проверки, если они есть, перечислены выше.' `
        -ForegroundColor DarkGray
} else {
    Write-Host '  Установка завершена не полностью.' -ForegroundColor Red
}

if (-not $Check) {
    Write-Host ''
    Write-Host '  Запуск системы:'
    Write-Host "    cd $ProjectRoot"
    Write-Host '    venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000'
    Write-Host ''
    Write-Host '  Открыть в браузере: http://127.0.0.1:8000'
    Write-Host '  Порядок целиком — в WINDOWS_SETUP.md, что осталось до'
    Write-Host '  внедрения — в ВНЕДРЕНИЕ.md.'
}
Write-Host ''

exit $(if ($script:Problems.Count -gt 0) { 1 } else { 0 })
