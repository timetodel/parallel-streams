#Requires -Version 7
<#
Разворачивает канал согласования между вкладками в проект: сторожа, профиль, переходник.

Зачем отдельный установщик. Сам канал лежит в папке скилла, но работать он начинает только от трёх
вещей ВНЕ её: записей в настройках проекта, раздела в профиле и короткого переходника в `scripts/`.
Собирать это руками в каждом новом проекте — сверка с чужим файлом настроек и четыре места, где
легко ошибиться, причём ошибка в любом из них выглядит одинаково безобидно: «соседу нечего
сказать». Поэтому одна команда и отчёт, в котором названо КАЖДОЕ действие.

Что делает установка:
  1. настройки  `<проект>/.claude/settings.json` — три сторожа: доставка находок, подсказка при
     правке плана волны и отказ фиксировать работу из необъявленного потока;
  2. профиль    `<проект>/.parallel-streams.md`  — разделы `## Coordination` и `## Plans`;
  3. переходник `<проект>/scripts/wave-board.ps1` — чтобы команда запуска везде была одинаково
     короткой: `pwsh scripts/wave-board.ps1 ...`.

Чего установщик не делает НИКОГДА: не трогает чужие записи в настройках, не меняет ни байта в уже
написанном профиле и не переписывает чужой файл на месте переходника. Всё, что он делать не стал,
названо в отчёте — молчаливых пропусков здесь нет, иначе канал окажется наполовину подключённым, а
выглядеть это будет как тишина на доске.

Оговорка про файл настроек: чужие записи и их порядок целы, но собирает файл обратно сериализатор, и
раскладку (отступы, переносы) он ставит свою. Файл, написанный в другом стиле, после первой установки
окажется в диффе целиком. Установщик про это ГОВОРИТ — сверяется с прежним текстом заранее и печатает
предупреждение ровно тогда, когда переписывает стиль.

Повторный прогон безопасен: он не плодит дублей, а переехавшую папку скилла — или запись прежней
формы — подхватывает правкой уже стоящих записей. Разбор — в комментарии к Set-Guards.

Режимы: Install (по умолчанию), Uninstall (снять), Check (показать состояние).
#>

[CmdletBinding()]
param(
    [ValidateSet('Install', 'Uninstall', 'Check')]
    [string]$Mode = 'Install',

    # Корень проекта, куда ставим. По умолчанию — корень репозитория, из которого запущен скрипт.
    [string]$ProjectRoot,

    # Чем запускать сторож фиксации (см. Get-ClaimGuardShell). Bash — форма bash, выбор команды:
    # каждый следующий прогон её сохраняет. PowerShell — назад к PowerShell. Auto — та форма, что уже
    # стоит в настройках проекта, а без неё — PowerShell.
    [ValidateSet('Auto', 'Bash', 'PowerShell')]
    [string]$HookShell = 'Auto'
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ‼️ Снимаем git-переменные окружения ДО первого обращения к git: с заданным снаружи GIT_DIR корнем
# проекта окажется чужой репозиторий, и установка уедет туда молча. Разбор ловушки — в самом файле.
. (Join-Path $PSScriptRoot 'lib/git-env-clean.ps1')

# Папка скилла, от которой считаются все пути в настройках и в переходнике.
$CoordDir = (Resolve-Path -LiteralPath $PSScriptRoot).Path

# Признак «запись наша»: команда ведёт в НАШ файл сторожа, лежащий в папке сторожей канала. Ни одной
# половины признака не хватает. По одной папке своим сочли бы чужой хук, случайно положенный в папку
# с таким же именем, — и снятие унесло бы его вместе с нашими. По одному имени файла не узнали бы
# СВОЮ прежнюю запись после переезда скилла, и рядом со старой (мёртвой) легла бы вторая.
# Признак держится в каждой форме, какую установщик когда-либо писал, — `$PWD/<путь>`, полный путь и
# нынешняя, ищущая в двух местах, — и под обоими именами, какие носил файл сторожа, поэтому запись
# прежней формы переписывается на месте, а не получает вторую рядом.
$OurHookFiles = [ordered]@{
    deliver = 'deliver-wave-board.ps1'
    nudge   = 'nudge-wave-plan-edit.ps1'
    claim   = 'guard-claim-before-publish.ps1'
}

# Имена, которые файлы сторожей носили до 1.19.0. Их сменили, когда команда стала искать копию в двух
# местах: под старыми именами запасной путь находил бы и СТАРУЮ копию в проекте, где началась сессия, —
# копию, не знающую, что такое «не наш проект», — и запускал бы её из любой папки, в том числе в чужом
# репозитории. Новое имя находится только в копии, которая знает, где стоит. Старые имена остаются
# здесь навсегда: записи под ними узнаются и переписываются на новые имена, а снятие их убирает.
# (В папке скилла под старыми именами лежат переходные пересыльщики, в `hooks/`, — для настроек, ещё
# не переписанных.)
$FormerHookFiles = [ordered]@{
    deliver = 'wave-board-deliver.ps1'
    nudge   = 'pretooluse-wave-board-nudge.ps1'
    claim   = 'pretooluse-claim-before-publish.ps1'
}
$OurMark = 'coordination/hooks/(' +
    ((@($OurHookFiles.Values) + @($FormerHookFiles.Values) |
        ForEach-Object { [regex]::Escape($_) }) -join '|') + ')'

# Строка, по которой узнаётся наш переходник: чужой файл с тем же именем не трогаем.
$BridgeMark = 'переехал в папку скилла'

$ProfileName = '.parallel-streams.md'

function Report {
    param([string]$Text)
    # ‼️ Пишем в консоль, а не в поток вывода: отчёт печатают функции, у которых есть и возвращаемое
    # значение, и вызов из присваивания. Через поток вывода такой отчёт молча уехал бы в переменную.
    Write-Host $Text
}

function Deny {
    param([string]$Text)
    # Отказ читает человек: `throw` завернул бы текст в рамку исключения с путём и номером строки.
    [Console]::Error.WriteLine($Text)
    exit 1
}

function Get-SlashPath {
    param([string]$Path)
    return ($Path -replace '\\', '/')
}

function Test-SameText {
    param([string]$Left, [string]$Right)
    # Сравнение содержимого файла — посимвольное: обычное -eq в PowerShell не различает регистр, и
    # переехавший путь, отличающийся только регистром буквы, сошёл бы за «ничего не изменилось».
    return [string]::Equals($Left, $Right, [System.StringComparison]::Ordinal)
}

function Resolve-Root {
    param([string]$Given)
    if ($Given) {
        if (-not (Test-Path -LiteralPath $Given -PathType Container)) {
            Deny "Папки нет: $Given"
        }
        return (Resolve-Path -LiteralPath $Given).Path
    }
    $top = ''
    try { $top = (& git rev-parse --show-toplevel 2>$null) } catch { $top = '' }
    if ($top) { return (Resolve-Path -LiteralPath ([string]$top).Trim()).Path }
    Report "Это не репозиторий git — ставлю в текущую папку: $($PWD.Path)"
    return $PWD.Path
}

function Get-InsidePath {
    param([string]$Root, [string]$Target)
    # Путь от корня проекта, если цель лежит ВНУТРИ него, иначе пусто. Внутренний вид нужен затем,
    # чтобы настройки пережили переезд папки проекта и одинаково работали в каждом рабочем дереве.
    $rel = Get-SlashPath ([System.IO.Path]::GetRelativePath($Root, $Target))
    if ([System.IO.Path]::IsPathRooted($rel)) { return '' }
    if ($rel -eq '.' -or $rel -eq '..' -or $rel.StartsWith('../')) { return '' }
    return $rel
}

# ─── Разметка профиля ────────────────────────────────────────────────────────────────────────────

function Get-MarkdownSection {
    param([string]$Text, [string]$Name)
    # Раздел `## Имя` целиком, без хвостовых пустых строк. Пусто — раздела нет.
    #
    # Границей раздела считается заголовок и второго уровня, и ПЕРВОГО: `# Другая часть` стоит в
    # разметке выше, и, обрывая раздел только на `##`, мы утащили бы в него весь остаток документа —
    # вместе с чужими путями в обратных кавычках, из которых потом читается папка планов.
    # Подзаголовки (`### ...`) границей не считаются: они часть раздела.
    #
    # Имя сравнивается БЕЗ учёта регистра (в PowerShell `-eq` для строк так и работает): профиль
    # пишет человек, и `## plans` у него — тот же раздел планов, что `## Plans`. Так же его читает
    # сторож-подсказка, иначе профиль с другим регистром дал бы подключённого, но немого сторожа.
    if (-not $Text) { return '' }
    $found = $false
    $out = [System.Collections.Generic.List[string]]::new()
    foreach ($line in ($Text -split "`r?`n")) {
        if ($line -match '^(#{1,2})\s+(.+?)\s*$') {
            if ($found) { break }
            if ($Matches[1].Length -eq 2 -and $Matches[2] -eq $Name) { $found = $true; $out.Add($line) }
            continue
        }
        if ($found) { $out.Add($line) }
    }
    if (-not $found) { return '' }
    while ($out.Count -gt 0 -and -not $out[$out.Count - 1].Trim()) { $out.RemoveAt($out.Count - 1) }
    return ($out -join "`n")
}

function Get-PlansFolder {
    param([string]$ProfileText)
    # Папку планов профиль называет в разделе `## Plans`, обратными кавычками — как путь. Берём
    # первое подходящее: раздел человек пишет словами, и в кавычки там попадает и имя файла, и
    # команда. Пусто — папка не объявлена, и сторож-подсказка не подключается вовсе.
    $section = Get-MarkdownSection -Text $ProfileText -Name 'Plans'
    if (-not $section) { return '' }
    foreach ($found in [regex]::Matches($section, '`([^`]+)`')) {
        $value = Get-SlashPath $found.Groups[1].Value.Trim()
        $value = $value -replace '^\./', ''
        if ($value -match '\s') { continue }
        # Место под заполнение папкой не считаем: в заготовках проекта незаполненное помечается
        # угловыми скобками (`<номер потока>`), и принять их за имя папки значит подключить сторожа
        # к папке, которой нет, — вместо честного «папка не названа».
        if ($value -match '[<>]') { continue }
        # Имя файла папкой планов не бывает: `.parallel-streams.md`, `README.md` и прочее.
        if ($value -notmatch '/$' -and $value -match '\.[A-Za-z0-9]{1,5}$') { continue }
        if (-not $value.EndsWith('/')) { $value += '/' }
        return $value
    }
    return ''
}

# ─── Чтение и запись файлов ──────────────────────────────────────────────────────────────────────

function Read-TextFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    return [System.IO.File]::ReadAllText($Path, [System.Text.UTF8Encoding]::new($false))
}

function Save-TextFile {
    param([string]$Path, [string]$Text)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Text, [System.Text.UTF8Encoding]::new($false))
}

function ConvertTo-FileJson {
    param($Data, [string]$Original)
    # ‼️ Сборка настроек обратно в текст. Три вещи, каждая из которых портит чужой файл молча:
    #   • разбор идёт БЕЗ -AsHashtable (см. Read-Settings): словарь теряет порядок ключей, и файл
    #     после сохранения перетасовывается целиком — чужие записи выглядят переписанными;
    #   • кириллица не должна уехать в \uXXXX: в файле есть русские подписи сторожей, их читает
    #     человек. Разворачиваем обратно всё, кроме того, что в JSON обязано быть экранированным;
    #   • переводы строк и хвостовой перевод берём у прежнего файла: иначе безобидный прогон
    #     переписывает КАЖДУЮ строку и тонет в разнице при слиянии.
    #
    # ‼️ Разворачиваем по РУНАМ обратных косых, а не по одиночному совпадению `\uXXXX`. Чужое
    # значение `путь C:\u0041pps\bin` записано в файле как `C:\\u0041pps\\bin`, и наивный поиск
    # принял бы ВТОРУЮ косую за начало escape-последовательности: в файл уехало бы `C:\Apps` —
    # незаконный escape, после которого файл настроек не разбирается ЦЕЛИКОМ, а с ним молча
    # отключаются ВСЕ хуки проекта, не только наши. Нечётное число косых перед `u` — последняя
    # открывает escape; чётное — все они парные, и перед нами обычный текст, который трогать нельзя.
    $text = $Data | ConvertTo-Json -Depth 20
    $text = [regex]::Replace($text, '(\\+)u([0-9a-fA-F]{4})', {
            param($found)
            $slashes = $found.Groups[1].Value
            if ($slashes.Length % 2 -eq 0) { return $found.Value }
            $code = [Convert]::ToInt32($found.Groups[2].Value, 16)
            if ($code -lt 0x20 -or $code -eq 0x22 -or $code -eq 0x5C) { return $found.Value }
            # Половинка суррогатной пары (эмодзи) сама по себе уже не символ — оставляем как есть.
            if ($code -ge 0xD800 -and $code -le 0xDFFF) { return $found.Value }
            return $slashes.Substring(0, $slashes.Length - 1) + [string][char]$code
        })
    $text = $text -replace "`r`n", "`n"
    $useCrlf = $Original -and ($Original -match "`r`n")
    if ($useCrlf) { $text = $text -replace "`n", "`r`n" }
    if (-not $Original -or $Original.EndsWith("`n")) {
        $text += $(if ($useCrlf) { "`r`n" } else { "`n" })
    }
    return $text
}

function Read-Settings {
    param([string]$Path)
    $raw = Read-TextFile -Path $Path
    if (-not $raw -or -not $raw.Trim()) {
        return [pscustomobject]@{ Data = $null; Raw = $raw }
    }
    $data = $null
    try {
        $data = $raw | ConvertFrom-Json
    } catch {
        Deny "Настройки проекта не разбираются как JSON: $Path. Почините файл и повторите."
    }
    return [pscustomobject]@{ Data = $data; Raw = $raw }
}

function New-Settings {
    return [pscustomobject][ordered]@{
        '$schema' = 'https://json.schemastore.org/claude-code-settings.json'
        hooks     = [pscustomobject]@{}
    }
}

# ─── Работа со свойствами разобранного JSON ──────────────────────────────────────────────────────

function Get-Prop {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    $prop = $Object.PSObject.Properties[$Name]
    if (-not $prop) { return $null }
    return $prop.Value
}

function Set-Prop {
    param($Object, [string]$Name, $Value)
    $prop = $Object.PSObject.Properties[$Name]
    if ($prop) {
        $prop.Value = $Value
    } else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

# ─── Опознание записей сторожей ──────────────────────────────────────────────────────────────────

function Test-OurHook {
    param($Hook)
    $command = Get-SlashPath ([string](Get-Prop $Hook 'command'))
    return $command -match $OurMark
}

function Get-HookWhat {
    param($Hook)
    # Как запись называется в отчёте — одними и теми же словами и при установке, и при показе.
    switch (Get-HookKind $Hook) {
        'deliver' { return 'доставка находок' }
        'nudge' { return 'подсказка при правке плана волны' }
        'claim' { return 'отказ фиксировать работу из необъявленного потока' }
        default { return 'запись канала' }
    }
}

function Get-HookKind {
    param($Hook)
    $command = Get-SlashPath ([string](Get-Prop $Hook 'command'))
    # По любому из двух имён: запись под старым — тот же сторож, её переписывают на месте.
    foreach ($kind in $OurHookFiles.Keys) {
        foreach ($name in @($OurHookFiles[$kind], $FormerHookFiles[$kind])) {
            if ($command -match ('coordination/hooks/' + [regex]::Escape($name))) { return $kind }
        }
    }
    return 'other'
}

function Get-HookMark {
    param($Hook)
    # Различитель внутри вида: у доставки это стадия, у подсказки — инструмент из условия. По нему
    # повторный прогон узнаёт СВОЮ прежнюю запись и правит её, а не кладёт рядом вторую такую же.
    $kind = Get-HookKind $Hook
    if ($kind -eq 'deliver') {
        $stage = [regex]::Match([string](Get-Prop $Hook 'command'), '-Stage\s+(\w+)')
        if ($stage.Success) { return "deliver:$($stage.Groups[1].Value)" }
        return 'deliver:'
    }
    if ($kind -eq 'nudge') {
        $tool = [regex]::Match([string](Get-Prop $Hook 'if'), '^\s*(\w+)\s*\(')
        if ($tool.Success) { return "nudge:$($tool.Groups[1].Value)" }
        return 'nudge:'
    }
    # Запись у сторожа фиксации ровно одна, и различать копии по условию не по чему: сам вид записи
    # и есть различитель.
    if ($kind -eq 'claim') { return 'claim:' }
    return 'other'
}

function New-HookRecord {
    param($Wanted)
    # Порядок полей ровно тот, что в уже написанных руками настройках: так запись читается глазами
    # одинаково, где бы она ни стояла. `onFailure` — последним: правка записи прежней формы
    # дописывает недостающее поле в конец, и обе дороги должны давать один и тот же файл.
    $record = [ordered]@{ type = 'command'; shell = $Wanted.Shell; command = $Wanted.Command }
    if ($Wanted.If) { $record['if'] = $Wanted.If }
    $record['timeout'] = $Wanted.Timeout
    if ($Wanted.Status) { $record['statusMessage'] = $Wanted.Status }
    if ($Wanted.Failure) { $record['onFailure'] = $Wanted.Failure }
    return [pscustomobject]$record
}

function Update-HookRecord {
    param($Hook, $Wanted)
    # Правим НА МЕСТЕ: существующие поля сохраняют свой порядок, дописывается только недостающее.
    Set-Prop $Hook 'type' 'command'
    Set-Prop $Hook 'shell' $Wanted.Shell
    Set-Prop $Hook 'command' $Wanted.Command
    if ($Wanted.If) { Set-Prop $Hook 'if' $Wanted.If }
    Set-Prop $Hook 'timeout' $Wanted.Timeout
    if ($Wanted.Status) { Set-Prop $Hook 'statusMessage' $Wanted.Status }
    if ($Wanted.Failure) { Set-Prop $Hook 'onFailure' $Wanted.Failure }
}

function Get-EventEntries {
    param($Settings, [string]$EventName)
    $node = Get-Prop $Settings 'hooks'
    if (-not $node) { return @() }
    $entries = Get-Prop $node $EventName
    if (-not $entries) { return @() }
    return @($entries)
}

function Set-EventEntries {
    param($Settings, [string]$EventName, $Entries)
    $node = Get-Prop $Settings 'hooks'
    if (-not $node) {
        $node = [pscustomobject]@{}
        Set-Prop $Settings 'hooks' $node
    }
    $kept = @($Entries)
    if ($kept.Count -eq 0) {
        # Опустевшее событие убираем целиком: пустой массив читают глазами и принимают за
        # подключённого сторожа.
        if ($node.PSObject.Properties[$EventName]) { $node.PSObject.Properties.Remove($EventName) }
        return
    }
    Set-Prop $node $EventName ([object[]]$kept)
}

function Get-Buckets {
    param($Settings, [string]$EventName)
    # Записи события вместе со своими списками хуков — в изменяемом виде.
    $buckets = [System.Collections.Generic.List[object]]::new()
    foreach ($entry in (Get-EventEntries -Settings $Settings -EventName $EventName)) {
        $list = [System.Collections.Generic.List[object]]::new()
        foreach ($hook in @(Get-Prop $entry 'hooks')) { if ($hook) { $list.Add($hook) } }
        $buckets.Add([pscustomobject]@{ Entry = $entry; Hooks = $list })
    }
    # Запятая обязательна: без неё PowerShell разворачивает список в отдельные значения, и на том
    # конце вместо изменяемого списка окажется обычный массив.
    return , $buckets
}

function Save-Buckets {
    param($Settings, [string]$EventName, $Buckets)
    $entries = [System.Collections.Generic.List[object]]::new()
    foreach ($bucket in $Buckets) {
        if ($bucket.Hooks.Count -eq 0) { continue }
        Set-Prop $bucket.Entry 'hooks' ([object[]]$bucket.Hooks)
        $entries.Add($bucket.Entry)
    }
    Set-EventEntries -Settings $Settings -EventName $EventName -Entries $entries
}

function Find-Bucket {
    param($Buckets, [string]$Matcher)
    foreach ($bucket in $Buckets) {
        $own = [string](Get-Prop $bucket.Entry 'matcher')
        if (-not $Matcher) {
            if (-not $own) { return $bucket }
            continue
        }
        if (-not $own) { continue }
        # `Write|Edit` и `Edit|Write` — один и тот же отбор: сравниваем составом, а не строкой.
        $mine = @($Matcher -split '\|' | ForEach-Object { $_.Trim() } | Sort-Object)
        $theirs = @($own -split '\|' | ForEach-Object { $_.Trim() } | Sort-Object)
        if (($mine -join '|') -eq ($theirs -join '|')) { return $bucket }
    }
    return $null
}

function New-Bucket {
    param($Buckets, [string]$Matcher)
    $entry = if ($Matcher) {
        [pscustomobject][ordered]@{ matcher = $Matcher; hooks = @() }
    } else {
        [pscustomobject][ordered]@{ hooks = @() }
    }
    $bucket = [pscustomobject]@{
        Entry = $entry
        Hooks = [System.Collections.Generic.List[object]]::new()
    }
    $Buckets.Add($bucket)
    return $bucket
}

function Get-OurEvents {
    param($Settings)
    # События, на которых УЖЕ стоят наши записи. Обходить только желаемые события мало: пропала
    # папка планов или переименовали раздел профиля — событие правки файлов в желаемое не попадёт
    # вовсе, прежняя запись сторожа-подсказки останется в настройках и будет указывать в мёртвое
    # место. Отчёт при этом говорил «не подключён», а показ состояния — «подключён»: врал отчёт.
    $node = Get-Prop $Settings 'hooks'
    if (-not $node) { return @() }
    $names = [System.Collections.Generic.List[string]]::new()
    foreach ($eventName in @($node.PSObject.Properties.Name)) {
        foreach ($bucket in (Get-Buckets -Settings $Settings -EventName $eventName)) {
            foreach ($hook in $bucket.Hooks) {
                if (Test-OurHook $hook) { $names.Add($eventName); break }
            }
        }
    }
    return @($names | Select-Object -Unique)
}

function Set-Guards {
    param($Settings, $Wanted)
    # Подключение сторожей. Идемпотентность держится на трёх правилах:
    #   • «наша» запись узнаётся по тому, что её команда ведёт в наш файл сторожа, — значит
    #     переехавший скилл правит СВОЮ прежнюю запись, а не заводит вторую;
    #   • внутри вида запись узнаётся по различителю (стадия у доставки, инструмент у подсказки),
    #     поэтому изменившееся условие тоже правится на месте;
    #   • всё, что осталось нашим и лишним, убирается — повторный прогон не копит дубли.
    # Чужие записи не читаются дальше поля команды и не двигаются ни при каком исходе.
    $events = @(@($Wanted | ForEach-Object { $_.Event }) + (Get-OurEvents -Settings $Settings)) |
        Select-Object -Unique
    foreach ($eventName in $events) {
        $here = @($Wanted | Where-Object { $_.Event -eq $eventName })
        $buckets = Get-Buckets -Settings $Settings -EventName $eventName

        $ours = [System.Collections.Generic.List[object]]::new()
        foreach ($bucket in $buckets) {
            foreach ($hook in $bucket.Hooks) {
                if (-not (Test-OurHook $hook)) { continue }
                $ours.Add([pscustomobject]@{
                        Bucket = $bucket
                        Hook   = $hook
                        Kind   = (Get-HookKind $hook)
                        Mark   = (Get-HookMark $hook)
                        Used   = $false
                    })
            }
        }

        foreach ($want in $here) {
            $hit = $ours | Where-Object { -not $_.Used -and $_.Mark -eq $want.Mark } | Select-Object -First 1
            if (-not $hit) {
                # Различитель не сошёлся, а вид тот же — это наша запись с устаревшим условием или
                # путём. Её надо поправить, иначе рядом ляжет вторая и сторож заговорит дважды.
                $hit = $ours | Where-Object { -not $_.Used -and $_.Kind -eq $want.Kind } | Select-Object -First 1
            }
            if ($hit) {
                $hit.Used = $true
                $before = ($hit.Hook | ConvertTo-Json -Depth 5 -Compress)
                Update-HookRecord -Hook $hit.Hook -Wanted $want
                $after = ($hit.Hook | ConvertTo-Json -Depth 5 -Compress)
                if (Test-SameText -Left $before -Right $after) {
                    Report "  уже подключено: $($want.Title)"
                } else {
                    Report "  поправлена запись (путь или условие устарели): $($want.Title)"
                }
                continue
            }
            $bucket = Find-Bucket -Buckets $buckets -Matcher $want.Matcher
            if (-not $bucket) { $bucket = New-Bucket -Buckets $buckets -Matcher $want.Matcher }
            $bucket.Hooks.Add((New-HookRecord -Wanted $want))
            Report "  подключено: $($want.Title)"
        }

        # Всё наше, что не сошлось ни с одним желаемым, — лишнее, и вида это не касается: запись
        # сторожа-подсказки после пропавшей папки планов не «другого вида», а просто больше не
        # нужна. Оставленная, она указывала бы в мёртвое место столько, сколько живёт проект.
        foreach ($extra in $ours) {
            if ($extra.Used) { continue }
            $extra.Bucket.Hooks.Remove($extra.Hook) | Out-Null
            Report "  снята наша прежняя запись, больше не нужная: $(Get-HookWhat $extra.Hook) (событие $eventName)"
        }

        Save-Buckets -Settings $Settings -EventName $eventName -Buckets $buckets
    }
}

function Remove-Guards {
    param($Settings)
    $node = Get-Prop $Settings 'hooks'
    if (-not $node) { return }
    foreach ($eventName in @($node.PSObject.Properties.Name)) {
        $buckets = Get-Buckets -Settings $Settings -EventName $eventName
        $removed = 0
        foreach ($bucket in $buckets) {
            foreach ($hook in @($bucket.Hooks)) {
                if (-not (Test-OurHook $hook)) { continue }
                $bucket.Hooks.Remove($hook) | Out-Null
                $removed++
            }
        }
        if ($removed -eq 0) { continue }
        Save-Buckets -Settings $Settings -EventName $eventName -Buckets $buckets
        Report "  снято записей на событии ${eventName}: $removed"
    }
}

# ─── Переходник ──────────────────────────────────────────────────────────────────────────────────

function Get-BridgeText {
    param([string]$Root)
    $target = Join-Path $CoordDir 'wave-board.ps1'
    $inside = Get-InsidePath -Root $Root -Target $CoordDir
    if ($inside) {
        $where = "$inside/"
        $rel = Get-SlashPath ([System.IO.Path]::GetRelativePath((Join-Path $Root 'scripts'), $target))
        $call = "`$target = Join-Path `$PSScriptRoot '$rel'"
    } else {
        # Скилл лежит вне проекта — относительного пути до него не построить, пишем полный.
        $where = (Get-SlashPath $CoordDir) + '/'
        $call = "`$target = '$(Get-SlashPath $target)'"
    }
    $lines = @(
        '#Requires -Version 7'
        '<#'
        "Переходник: инструмент $BridgeMark (``$where``),"
        'здесь только вызов — чтобы команда запуска во всех проектах со скиллом была одинаково короткой:'
        '`pwsh scripts/wave-board.ps1 ...`. Логика и правки — только в скрипте скилла, не здесь.'
        '#>'
        ''
        $call
        '& $target @args'
        'exit $LASTEXITCODE'
    )
    return (($lines -join "`n") + "`n")
}

function Get-BridgeTarget {
    param([string]$Text)
    # Строка вызова — единственное, что в переходнике важно на деле. Сравнивать целиком годится для
    # решения «переписать», но не для рапорта: подправленная шапка заготовки — не повод пугать
    # человека тем, что переходник ведёт не туда.
    if (-not $Text) { return '' }
    $found = [regex]::Match($Text, '(?m)^\$target\s*=.*$')
    if ($found.Success) { return $found.Value.Trim() }
    return ''
}

# ─── Состояние проекта ───────────────────────────────────────────────────────────────────────────

function Get-State {
    param([string]$Root)
    $profilePath = Join-Path $Root $ProfileName
    $bridgePath = Join-Path $Root 'scripts/wave-board.ps1'
    $profileText = Read-TextFile -Path $profilePath
    $bridgeText = Read-TextFile -Path $bridgePath
    $plans = Get-PlansFolder -ProfileText $profileText
    return [pscustomobject]@{
        SettingsPath = (Join-Path $Root '.claude/settings.json')
        ProfilePath  = $profilePath
        BridgePath   = $bridgePath
        ProfileText  = $profileText
        BridgeText   = $bridgeText
        BridgeIsOurs = [bool]($bridgeText -and ($bridgeText -match [regex]::Escape($BridgeMark)))
        Plans        = $plans
        PlansExists  = [bool]($plans -and (Test-Path -LiteralPath (Join-Path $Root $plans) -PathType Container))
    }
}

function Get-HookCommand {
    param([string]$Inside, [string]$Name)
    # Как записывается путь к сторожу. Внутри проекта — от проекта, никогда не полностью: тогда
    # настройки переживают переезд папки и одинаково работают в каждом рабочем дереве. Но НЕ от голой
    # рабочей папки (`$PWD/...`), как писалось раньше: хук запускается в ТЕКУЩЕЙ папке вкладки, и
    # сессия, шагнувшая в подпапку или в другую папку, своего сторожа уже не находила — хук падал с
    # неблокирующей ошибкой, а фиксация, которую он должен был остановить, проходила молча.
    #
    # Поэтому команда ищет копию дважды: в папке, где стоит вкладка (корень рабочего дерева со своей
    # копией — запускается она, версия этого самого дерева), иначе в проекте, где началась сессия
    # (`CLAUDE_PROJECT_DIR`, его среда задаёт каждому хуку). Если запущенная копия оказалась вне
    # своего проекта, она сама молчит (`Test-ServedFolder` в библиотеке). Имена файлов, записанные
    # здесь, есть только у копии с этой проверкой (см. `$FormerHookFiles`): в дереве со старой копией
    # команда не находит ничего и не запускается, как и прежняя форма.
    #
    # Снаружи проекта — полным путём: он и так работает из любой папки, другого способа нет.
    if (-not $Inside) { return "& `"$(Get-SlashPath $CoordDir)/hooks/$Name`"" }
    $rel = "$Inside/hooks/$Name"
    return "& `"`$(if (Test-Path -LiteralPath `"`$PWD/$rel`") { `"`$PWD`" } else { `$env:CLAUDE_PROJECT_DIR })/$rel`""
}

function Get-RecordedClaimShell {
    param($Settings)
    # Оболочка записи сторожа фиксации, которая уже стоит в настройках проекта; пусто, если записи нет.
    $node = Get-Prop $Settings 'hooks'
    if (-not $node) { return '' }
    foreach ($eventName in @($node.PSObject.Properties.Name)) {
        foreach ($bucket in (Get-Buckets -Settings $Settings -EventName $eventName)) {
            foreach ($hook in $bucket.Hooks) {
                if ((Test-OurHook $hook) -and (Get-HookKind $hook) -eq 'claim') {
                    return [string](Get-Prop $hook 'shell')
                }
            }
        }
    }
    return ''
}

function Get-ClaimGuardShell {
    param($Settings)
    # Чем запускать сторож фиксации и почему — причина идёт в отчёт.
    #
    # Сторож стоит перед КАЖДЫМ вызовом оболочки, а делает что-то только на публикующей команде (в
    # живом проекте — около 4 % вызовов). В форме PowerShell каждый вызов платит запуск `pwsh` —
    # полсекунды и больше, а под нагрузкой больше секунды, — чтобы на остальных 96 % тут же выйти.
    # В форме bash перед сторожем стоит проверка встроенными средствами Git Bash, без единого
    # нового процесса, и PowerShell стартует только на команде, похожей на публикацию.
    #
    # ‼️ Форма — выбор КОМАНДЫ, а не машины. Настройки общие, а на Windows среда запускает запись
    # формы bash только тем Git Bash, который находит: на машине, где не находит, запись не может
    # запуститься вовсе — сторож фиксации у этого участника не работает, а каждый вызов оболочки
    # показывает ошибку хука. Есть ли Git Bash у каждого участника, ни одной машине не видно, поэтому
    # установщик никогда не выбирает форму bash сам, и прогон на одной машине никогда не переключает
    # форму, выбранную командой:
    #   • Bash / PowerShell — названо и записано;
    #   • Auto — та форма, что у записи сторожа уже стоит в настройках: однажды названную форму
    #     сохраняет каждый следующий прогон, на любой машине; записи ещё нет или она в PowerShell —
    #     PowerShell.
    # Запись сама и есть отметка выбора. Профиль — собственный текст человека, который установщик не
    # правит никогда, а отметка в любом другом месте могла бы разойтись с записью, которая действует.
    switch ($HookShell) {
        'Bash' { return [pscustomobject]@{ Shell = 'bash'; Why = 'названо через -HookShell Bash' } }
        'PowerShell' { return [pscustomobject]@{ Shell = 'powershell'; Why = 'названо через -HookShell PowerShell' } }
    }
    if ((Get-RecordedClaimShell -Settings $Settings) -eq 'bash') {
        return [pscustomobject]@{ Shell = 'bash'; Why = 'эта форма уже стоит в настройках проекта; -HookShell PowerShell возвращает назад' }
    }
    return [pscustomobject]@{ Shell = 'powershell'; Why = 'по умолчанию; -HookShell Bash ставит перед ним проверку дешевле — см. README' }
}

function Get-ClaimFrontCommand {
    param([string]$Inside, [string]$Name)
    # Запись сторожа фиксации в форме bash: проверка перед сторожем, которая работает в процессе Git
    # Bash самого хука и не порождает НИ ОДНОГО нового процесса — только встроенные средства bash, без
    # `$(…)` и без конвейера: в Git Bash каждое из них стоит запуска процесса.
    #
    # По строкам:
    #   1. найти сторожа там же, где его ищет форма PowerShell: копия рабочего дерева, где стоит
    #      вкладка, иначе копия проекта, где началась сессия. Нет ни там, ни там — сообщение и код 1
    #      (видимая ошибка хука, работа идёт дальше), НИКОГДА не 2: код 2 перед вызовом инструмента
    #      значит «отказ», и тогда отказом стал бы каждый вызов оболочки. Копия в папке вкладки названа
    #      путём от этой папки, а не через `$PWD/...`: `$PWD` в Git Bash — это `/d/...`, и до
    #      PowerShell такой путь доходит только через преобразование путей Git Bash, а его человек мог
    #      выключить (MSYS_NO_PATHCONV) — тогда сторож не запустился бы вовсе;
    #   2. запущены не в bash (общие настройки проекта открыли на машине, где хук идёт в sh или zsh) —
    #      сразу отдать вызов сторожу, ничего не читая: стандартный ввод цел, сторож решает как раньше.
    #      Строка отдельная нарочно: sh разбирает команду построчно, и до строк с конструкциями bash
    #      он так не доходит;
    #   3. прочитать вызов целиком в переменную. `read` возвращает 1, дочитав до конца. Любой другой
    #      код значит, что его остановил сырой нулевой байт (переменная bash его не удержит): часть до
    #      этого байта уходит сторожу без суждения, а сторож, не сумев её разобрать, вызов отпускает.
    #      Сырого нулевого байта среда не пишет никогда — JSON несёт его как `\u0000`, а такой вызов
    #      строка 4 отдаёт сторожу целым;
    #   4. отпустить вызов (код 0), если в СЫРОМ JSON нет ни одной из пар `git`…`commit`, `git`…`push`,
    #      `alias.`…`commit`, `alias.`…`push`, `gh`…`pr`…`create`, без учёта регистра. Это
    #      НАДМНОЖЕСТВО образца сторожа: всё, на что сторож отказывает, содержит одну из пар, и слова
    #      идут в том же порядке, в каком стоят в команде, — поля вокруг команды могут только добавить
    #      совпадение, но не отнять. Буквы, записанные в JSON экранированием `\uXXXX`, проверка не
    #      видит, поэтому обратная косая, `u` и четыре шестнадцатеричные цифры где угодно в вызове
    #      отдают его сторожу. Экранированная обратная косая перед таким текстом (путь вроде
    #      `D:\uface`) тоже попадается — лишний запуск PowerShell, но не пропущенный отказ; простое
    #      `\u` (`C:\Users\user`, во многих машинах — в собственных путях вызова) — нет. Отличить
    #      экранированную косую точно можно, только убрав сначала все пары косых, а bash делает это
    #      за время, растущее как квадрат длины: вызов с несколькими сотнями тысяч косых шёл больше
    #      минуты, далеко за пределом хука. Отдаётся сторожу и одна буква за пределами ASCII:
    #      заглавная I с точкой, `İ` (U+0130). Сравнение сторожа без учёта регистра принимает её за
    #      `i` во всех культурах, кроме инвариантной, а bash сводит регистр за пределами ASCII только
    #      в локали UTF-8, которой в окружении хука может и не быть, — `git commİt` прошла бы проверку,
    #      а сторож бы на неё отказал. Вызов, где эта буква есть хоть где-то, уходит сторожу (где она
    #      есть в собственных путях машины — уходит каждый вызов: выигрыша там нет, пропущенного
    #      отказа тоже). Во всей базовой многоязычной плоскости Юникода, во всех культурах .NET 11, это
    #      единственный знак, который сравнение приравнивает к букве этих слов; знак кельвина (U+212A)
    #      стоит за `k`, которой нет ни в одном из них, — слову сторожа с `k` он понадобился бы и здесь;
    #   5. остальное — сторожу в PowerShell, с прочитанным вызовом на стандартном вводе.
    #
    # ‼️ В тексте команды нет ни одной пары обратных косых подряд: на Windows среда передаёт команду
    # Git Bash строкой запуска, и при её разборе каждая пара `\\` превращается в одну. Одна косая,
    # нужная проверке, записана как `'\'`; заглавная I с точкой — как `$'\xc4\xb0'` bash (её байты
    # UTF-8), чтобы текст оставался в ASCII.
    if ($Inside) {
        $rel = "$Inside/hooks/$Name"
        $find = "if [ -f `"$rel`" ]; then g=`"$rel`"; " +
            "elif [ -f `"`$CLAUDE_PROJECT_DIR/$rel`" ]; then g=`"`$CLAUDE_PROJECT_DIR/$rel`"; " +
            "else echo `"сторож фиксации $rel не найден ни в текущей папке, ни в папке сессии — поставьте канал заново`" >&2; exit 1; fi"
    } else {
        # Скилл вне проекта — полный путь, в одинарных кавычках: в двойных bash раскрыл бы `$` и
        # обратную кавычку в имени папки.
        $full = "'" + ((Get-SlashPath $CoordDir) + "/hooks/$Name").Replace("'", "'`"'`"'") + "'"
        $find = "if [ -f $full ]; then g=$full; " +
            "else echo `"сторож фиксации не найден: `"$full >&2; exit 1; fi"
    }
    $run = 'exec pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$g"'
    $lines = @(
        $find
        '[ -n "$BASH_VERSION" ] || ' + $run
        'IFS= read -r -d '''' p; r=$?; h=[0-9a-fA-F]'
        'if [ $r -eq 1 ] && [[ $p != *''\''u$h$h$h$h* && $p != *$''\xc4\xb0''* ]]; then shopt -s nocasematch; [[ $p == *git*commit* || $p == *git*push* || $p == *alias.*commit* || $p == *alias.*push* || $p == *gh*pr*create* ]] || exit 0; fi'
        $run + ' <<< "$p"'
    )
    return ($lines -join "`n")
}

function Get-Wanted {
    param([string]$Root, [string]$Plans, [string]$ClaimShell)
    $inside = Get-InsidePath -Root $Root -Target $CoordDir
    $deliver = Get-HookCommand -Inside $inside -Name $OurHookFiles.deliver
    $nudge = Get-HookCommand -Inside $inside -Name $OurHookFiles.nudge
    $beforePublish = if ($ClaimShell -eq 'bash') {
        Get-ClaimFrontCommand -Inside $inside -Name $OurHookFiles.claim
    } else {
        Get-HookCommand -Inside $inside -Name $OurHookFiles.claim
    }

    $wanted = @(
        [pscustomobject]@{
            Event   = 'SessionStart'
            Matcher = ''
            Kind    = 'deliver'
            Mark    = 'deliver:Start'
            Shell   = 'powershell'
            Command = "$deliver -Stage Start"
            Timeout = 20
            Status  = 'Смотрю доску волны'
            If      = ''
            Failure = ''
            Title   = 'доставка находок в начале сессии'
        }
        [pscustomobject]@{
            Event   = 'UserPromptSubmit'
            Matcher = ''
            Kind    = 'deliver'
            Mark    = 'deliver:Prompt'
            Shell   = 'powershell'
            Command = "$deliver -Stage Prompt"
            Timeout = 15
            Status  = ''
            If      = ''
            Failure = ''
            Title   = 'доставка находок перед каждым обращением человека'
        }
        # Сторож фиксации подключается безусловно: в отличие от подсказки, папка планов ему не
        # нужна — поток объявляется и в проекте, где нет ни волн, ни планов, и там канал сам даёт
        # волну.
        #
        # Без условия `if`: команду разбирает сам сторож. Условие пришлось бы расписать на все виды
        # публикующей команды (`git -C … commit`, цепочка команд, `gh pr create`), и каждый
        # пропущенный вид — это сторож, который выглядит подключённым и молчит ровно там, где нужен.
        #
        # `onFailure: continue` — в обеих формах: собственное правило сторожа (его беда никогда не
        # останавливает работу проекта), сказанное и среде. На Windows без Git Bash среда считает
        # упавший или не уложившийся во время хук перед вызовом инструмента ОТКАЗОМ в этом вызове,
        # если запись не говорит `continue` (Claude Code 2.1.325). Без этого поля форма bash там — она
        # не может запуститься — отказывала бы в каждом вызове оболочки в проекте, а форма PowerShell —
        # в каждом, на котором не уложилась во время под нагрузкой. Настройки проекта общие, поэтому
        # такая машина — не обязательно та, где запускали установку. В остальных местах `continue` —
        # и так то, что делает среда.
        [pscustomobject]@{
            Event   = 'PreToolUse'
            Matcher = 'Bash|PowerShell'
            Kind    = 'claim'
            Mark    = 'claim:'
            Shell   = $ClaimShell
            Command = $beforePublish
            Timeout = 15
            Status  = ''
            If      = ''
            Failure = 'continue'
            Title   = 'отказ фиксировать работу из необъявленного потока'
        }
    )
    # Сторож-подсказка держится на папке планов: не зная её, он не отличит план волны от любого
    # другого файла и будет молчать всегда. Нет папки в профиле — записи не заводим вовсе.
    #
    # Условие привязано к корню файловой системы (`//**/<папка>/**`) и ловит папку на любой глубине.
    # Без ведущей косой черты среда сверяет его с ТЕКУЩЕЙ папкой вкладки, и из подпапки оно не
    # срабатывало вовсе; одна косая черта привязала бы его к папке, где началась сессия, и план в любом
    # другом рабочем дереве прошёл бы мимо. Привязанное к корню, оно ловит и одноимённую папку в чужом
    # репозитории, и такую же глубже внутри проекта — обе сторож отличает сам: он говорит только о
    # папке в корне собственного дерева правимого файла, в репозитории, который он обслуживает.
    if (-not $Plans) { return $wanted }
    foreach ($tool in @('Edit', 'Write')) {
        $wanted += [pscustomobject]@{
            Event   = 'PreToolUse'
            Matcher = 'Write|Edit'
            Kind    = 'nudge'
            Mark    = "nudge:$tool"
            Shell   = 'powershell'
            Command = $nudge
            Timeout = 20
            Status  = ''
            If      = "$tool(//**/$Plans**)"
            Failure = ''
            Title   = "подсказка при правке плана волны ($tool, папка $Plans)"
        }
    }
    return $wanted
}

# ─── Режимы ──────────────────────────────────────────────────────────────────────────────────────

function Update-Profile {
    param([string]$Root, $State, $Told)
    # Профиль разбирается ПЕРВЫМ (из него берётся папка планов, без которой не решить, подключать ли
    # сторож-подсказку), а печатается вторым — отчёт идёт в том порядке, в котором про канал думают.
    # Поэтому строки отчёта складываются в $Told, а не печатаются на месте.
    $profileText = $State.ProfileText
    $template = Read-TextFile -Path (Join-Path $CoordDir 'templates/profile.md')
    $extra = Read-TextFile -Path (Join-Path $CoordDir 'templates/profile-coordination.md')
    if (-not $template -or -not $extra) {
        Deny "Не нашлись заготовки профиля — комплект неполон: $CoordDir/templates"
    }
    $blocks = @()
    foreach ($name in @('Coordination', 'Plans')) {
        $block = Get-MarkdownSection -Text $extra -Name $name
        if (-not $block) {
            Deny "В заготовке разделов нет «## $name» — комплект неполон: $CoordDir/templates"
        }
        $blocks += [pscustomobject]@{ Name = $name; Text = $block }
    }

    if (-not $profileText) {
        $profileText = $template.TrimEnd() + "`n`n" + (($blocks | ForEach-Object { $_.Text }) -join "`n`n") + "`n"
        Save-TextFile -Path $State.ProfilePath -Text $profileText
        $Told.Add("  заведён профиль $ProfileName, в нём разделы «## Coordination» и «## Plans»")
        $Told.Add('  ‼️ править под свой проект: команды проверок, ревью и папку планов волн')
    } else {
        $missing = @($blocks | Where-Object { -not (Get-MarkdownSection -Text $profileText -Name $_.Name) })
        if ($missing.Count -eq 0) {
            $Told.Add("  профиль $ProfileName уже описывает канал — не тронут")
        } else {
            # Дописываем только в конец и только недостающее: в уже написанном тексте нельзя менять
            # ни байта, там слова человека про его проект.
            $eol = if ($profileText -match "`r`n") { "`r`n" } else { "`n" }
            $gap = ''
            if (-not $profileText.EndsWith("`n")) { $gap = $eol + $eol }
            elseif (-not $profileText.EndsWith($eol + $eol)) { $gap = $eol }
            $body = (($missing | ForEach-Object { $_.Text }) -join "`n`n") + "`n"
            if ($eol -ne "`n") { $body = $body -replace "`n", $eol }
            $profileText = $profileText + $gap + $body
            Save-TextFile -Path $State.ProfilePath -Text $profileText
            $Told.Add("  дописано в профиль: $(($missing | ForEach-Object { "«## $($_.Name)»" }) -join ', ')")
        }
    }

    $plans = Get-PlansFolder -ProfileText $profileText
    if (-not $plans) {
        $Told.Add('  папка планов волн в разделе «## Plans» не названа — сторож-подсказка не подключён')
        $Told.Add('  (впишите её туда обратными кавычками — строкой вида «Планы волн: `docs/plans/`» — и повторите установку)')
    } elseif (-not (Test-Path -LiteralPath (Join-Path $Root $plans) -PathType Container)) {
        $Told.Add("  папки планов «$plans», названной в профиле, в проекте нет — сторож-подсказка не подключён")
        $Told.Add('  (заведите папку или поправьте раздел «## Plans» и повторите установку)')
        $plans = ''
    } else {
        $Told.Add("  папка планов волн: $plans")
    }
    return $plans
}

function Invoke-Install {
    param([string]$Root)
    $state = Get-State -Root $Root
    $told = [System.Collections.Generic.List[string]]::new()
    $plans = Update-Profile -Root $Root -State $state -Told $told

    Report ''
    Report '1. Сторожа в настройках проекта'
    $settings = Read-Settings -Path $state.SettingsPath
    $data = $settings.Data
    $restyle = $false
    if (-not $data) {
        $data = New-Settings
        Report "  файла настроек не было — завожу $($state.SettingsPath)"
    } else {
        # Обратно в текст файл собирает сериализатор, и раскладку он берёт свою: отступы, переносы,
        # расстановку скобок. Содержание и порядок записей при этом целы, а вот файл в диффе будет
        # виден целиком — про это надо сказать заранее, а не обещать «ничего не тронуто».
        $restyle = -not (Test-SameText `
                -Left (ConvertTo-FileJson -Data $data -Original $settings.Raw) -Right $settings.Raw)
    }
    # Читается до того, как Set-Guards перепишет записи: форма, уже стоящая в настройках, и есть отметка.
    $claimShell = Get-ClaimGuardShell -Settings $data
    Set-Guards -Settings $data -Wanted (Get-Wanted -Root $Root -Plans $plans -ClaimShell $claimShell.Shell)
    if ($claimShell.Shell -eq 'bash') {
        Report "  сторож фиксации — через Git Bash: PowerShell запускается только на команде, похожей на публикацию ($($claimShell.Why))"
        Report '  ‼️ эта форма требует, чтобы у каждого участника на Windows был Git Bash, который находит Claude Code: без него'
        Report '  сторож фиксации у этого участника не работает, а на каждом вызове оболочки он видит ошибку хука'
    } else {
        Report "  сторож фиксации — через PowerShell, он запускается на каждом вызове оболочки ($($claimShell.Why))"
    }
    $text = ConvertTo-FileJson -Data $data -Original $settings.Raw
    if (Test-SameText -Left $text -Right $settings.Raw) {
        Report '  настройки не изменились'
    } else {
        Save-TextFile -Path $state.SettingsPath -Text $text
        if ($restyle) {
            Report '  настройки сохранены; чужие записи и их порядок целы, но ‼️ файл переписан'
            Report '  единым стилем — отступы и раскладка стали такими, какими их пишет установщик'
        } else {
            Report '  настройки сохранены — чужие записи и их порядок не тронуты'
        }
    }

    Report ''
    Report '2. Профиль проекта'
    foreach ($line in $told) { Report $line }

    Report ''
    Report '3. Переходник scripts/wave-board.ps1'
    $bridge = Get-BridgeText -Root $Root
    if (-not $state.BridgeText) {
        Save-TextFile -Path $state.BridgePath -Text $bridge
        Report "  положен переходник: $($state.BridgePath)"
    } elseif (-not $state.BridgeIsOurs) {
        Report '  ‼️ на месте переходника лежит ЧУЖОЙ файл — не тронут'
        Report "  ($($state.BridgePath)); зовите инструмент полным путём или уберите чужой файл сами"
    } elseif (Test-SameText -Left $state.BridgeText -Right $bridge) {
        Report '  переходник уже на месте'
    } else {
        Save-TextFile -Path $state.BridgePath -Text $bridge
        $target = Get-BridgeTarget $bridge
        Report "  переходник обновлён — теперь он зовёт: $target"
    }

    Report ''
    Report 'Готово. Что подключено — `-Mode Check`, снять — `-Mode Uninstall`.'
}

function Invoke-Uninstall {
    param([string]$Root)
    $state = Get-State -Root $Root

    Report ''
    Report '1. Сторожа в настройках проекта'
    $settings = Read-Settings -Path $state.SettingsPath
    if (-not $settings.Data) {
        Report '  настроек проекта нет — снимать нечего'
    } else {
        # Та же оговорка, что при установке: сохранение переписывает файл раскладкой сериализатора.
        $restyle = -not (Test-SameText `
                -Left (ConvertTo-FileJson -Data $settings.Data -Original $settings.Raw) -Right $settings.Raw)
        Remove-Guards -Settings $settings.Data
        $text = ConvertTo-FileJson -Data $settings.Data -Original $settings.Raw
        if (Test-SameText -Left $text -Right $settings.Raw) {
            Report '  наших записей в настройках не было'
        } elseif ($restyle) {
            Save-TextFile -Path $state.SettingsPath -Text $text
            Report '  настройки сохранены; чужие записи целы, но ‼️ файл переписан единым стилем'
        } else {
            Save-TextFile -Path $state.SettingsPath -Text $text
            Report '  настройки сохранены — чужие записи оставлены как были'
        }
    }

    Report ''
    Report '2. Профиль проекта'
    Report "  $ProfileName оставлен как есть — там текст человека про его проект"

    Report ''
    Report '3. Переходник scripts/wave-board.ps1'
    if (-not $state.BridgeText) {
        Report '  переходника не было'
    } elseif (-not $state.BridgeIsOurs) {
        Report '  на месте переходника лежит чужой файл — не тронут'
    } else {
        Remove-Item -LiteralPath $state.BridgePath -Force
        Report "  убран: $($state.BridgePath)"
    }

    Report ''
    Report 'Канал снят. Папка скилла на месте — поставить обратно можно этой же командой.'
}

function Invoke-Check {
    param([string]$Root)
    $state = Get-State -Root $Root

    Report ''
    Report '1. Сторожа в настройках проекта'
    $settings = Read-Settings -Path $state.SettingsPath
    if (-not $settings.Data) {
        Report "  настроек нет вовсе: $($state.SettingsPath)"
    } else {
        $seen = 0
        foreach ($eventName in @('SessionStart', 'UserPromptSubmit', 'PreToolUse')) {
            foreach ($bucket in (Get-Buckets -Settings $settings.Data -EventName $eventName)) {
                foreach ($hook in $bucket.Hooks) {
                    if (-not (Test-OurHook $hook)) { continue }
                    $seen++
                    $what = Get-HookWhat $hook
                    $cond = [string](Get-Prop $hook 'if')
                    $tail = if ($cond) { ", условие $cond" } else { '' }
                    Report "  ${eventName}: $what$tail, оболочка $(Get-Prop $hook 'shell')"
                    Report "    ведёт в: $(Get-Prop $hook 'command')"
                }
            }
        }
        if ($seen -eq 0) { Report '  ни одной нашей записи — канал не подключён' }
    }

    Report ''
    Report '2. Профиль проекта'
    if (-not $state.ProfileText) {
        Report "  профиля нет: $($state.ProfilePath)"
    } else {
        foreach ($name in @('Coordination', 'Plans')) {
            $has = [bool](Get-MarkdownSection -Text $state.ProfileText -Name $name)
            Report "  раздел «## $name»: $(if ($has) { 'есть' } else { 'нет' })"
        }
        if ($state.Plans) {
            $where = if ($state.PlansExists) { 'есть в проекте' } else { '‼️ в проекте не заведена' }
            Report "  папка планов волн: $($state.Plans) — $where"
        } else {
            Report '  папка планов волн не объявлена — сторожу-подсказке не с чем работать'
        }
    }

    Report ''
    Report '3. Переходник scripts/wave-board.ps1'
    if (-not $state.BridgeText) {
        Report "  переходника нет: $($state.BridgePath)"
    } elseif (-not $state.BridgeIsOurs) {
        Report '  на этом месте лежит чужой файл — инструмент зовут полным путём'
    } elseif (Test-SameText -Left (Get-BridgeTarget $state.BridgeText) -Right (Get-BridgeTarget (Get-BridgeText -Root $Root))) {
        Report '  на месте и ведёт в папку скилла'
    } else {
        Report '  ‼️ на месте, но ведёт не туда, где сейчас лежит скилл — нужна установка заново'
        Report "    сейчас: $(Get-BridgeTarget $state.BridgeText)"
    }
    Report ''
}

# ─── Ход ─────────────────────────────────────────────────────────────────────────────────────────

$root = Resolve-Root -Given $ProjectRoot
$title = switch ($Mode) {
    'Install' { 'установка' }
    'Uninstall' { 'снятие' }
    'Check' { 'состояние' }
}
Report "Канал согласования между вкладками — $title"
Report "  проект:       $root"
Report "  папка скилла: $CoordDir"

switch ($Mode) {
    'Install' { Invoke-Install -Root $root }
    'Uninstall' { Invoke-Uninstall -Root $root }
    'Check' { Invoke-Check -Root $root }
}
