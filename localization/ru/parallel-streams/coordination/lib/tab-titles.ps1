#Requires -Version 7
<#
Название вкладки: какая сессия ведёт поток и как её назвал человек.

Зачем. Владелец держит десятки вкладок и называет их сам («О1-3-1»). Доска знает поток по папке и
ветке, и на вопрос «в какой вкладке ведут поток волна9/3» ответа у неё не было — его искали
перебором журналов всех сессий, минутами. Здесь лежит всё, что связывает поток с вкладкой:

  • чтение названия из журнала сессии — быстро, дочитывая только новый хвост;
  • реестр вкладок — маленькая запись на сессию рядом с реестром заявок;
  • общий вид строки «вкладка О1-3-1» для всех показов.

‼️ Из журнала сессии читаются РОВНО две служебные записи — `custom-title` (имя, данное человеком)
и `ai-title` (придуманное автоматически). Ни разговор, ни что-либо ещё не читается и не
сохраняется: журнал — это вся переписка человека, и набор, поставленный в чужой проект, не вправе
выносить из него больше, чем название.

‼️ Внутренности редактора (его базы, память рабочей области) не читаются вовсе: этот приём уже
отвергнут — он давал ложные срабатывания. Источник один — журнал сессии.

‼️ Частый путь писан скупо на вызовы. Сторож доставки зовёт запись вкладки на КАЖДОМ сообщении
человека, в свежем процессе оболочки, а там первое обращение к каждой функции стоит миллисекунды.
Замер 13.09.2026 (процессорное время, машина под полной нагрузкой): та же работа через цепочку
вспомогательных функций — около 150 мс, одной функцией — около 55 мс. Поэтому постоянные здесь —
переменные, а запись файлов в частом пути сделана на месте, без посредников.

Файл ничего не делает сам и ни от чего в комплекте не зависит: только объявляет функции. Любая
неудача чтения — тихое «не найдено»: сорванное название не повод ронять команду или сторожа.
#>

# Номер сессии идёт в имя файла, поэтому принимаем только безопасный вид: буквы, цифры, дефис и
# подчёркивание. Косая, точка или звёздочка в нём означали бы выход из своей папки или образец поиска
# вместо имени — а номер приходит снаружи (переменная окружения, данные сторожа).
$script:TabSessionIdPattern = '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$'

# Кусок обратного чтения. Мегабайт — это один-два десятка миллисекунд на чтение и разбор, а запись
# названия в живых журналах лежит обычно в 1–30 КБ от конца: чаще всего хватает одного куска.
$script:TabTitleChunkBytes = 1MB

# Потолок обратного чтения при первом обращении к журналу.
#
# Запись названия среда повторяет по ходу разговора (в паре со служебной записью о последнем
# запросе), поэтому от любого места журнала до последней записи названия назад не дальше самого
# большого разрыва между повторами. Замер 13.09.2026 на двенадцати крупнейших живых журналах (до
# 270 МБ): наибольший разрыв — 30,4 МБ (журнал 207 МБ), следом 25,9 и 19,1 МБ. Первая прикидка того
# же дня по трём журналам до 105 МБ давала 9,3 МБ — журналы растут, и разрывы вместе с ними. 64 МБ —
# двойной запас к наибольшему замеру.
#
# Цена ограничена: 64 куска по мегабайту, и только при ПЕРВОМ чтении журнала сессии — дальше читается
# лишь новый хвост. Промах тоже не навсегда: не нашли имени от человека — отдаём автоматическое, если
# оно попалось, иначе «не найдено», а ближайший повтор записи в новом хвосте вернёт имя сам. Молчание
# по журналу дальше потолка честнее, чем чтение двухсот мегабайт.
$script:TabTitleCeilingBytes = 64MB

# Первая строка кэша названия: по ней видно, что файл — наш кэш этого вида. Сменится вид кэша —
# сменится метка, и старые файлы просто перечитаются.
$script:TabTitleCacheMark = 'parallel-streams tab title cache 1'

function Test-SessionId {
    param([string]$Id)
    return [bool]($Id -and $Id -cmatch $script:TabSessionIdPattern)
}

function Get-ClaudeConfigDir {
    # Каталог настроек среды: переменная `CLAUDE_CONFIG_DIR`, если задана, иначе `.claude` в папке
    # пользователя. Проверки подставляют сюда свою временную папку — настоящий каталог они не
    # трогают никогда.
    if ($env:CLAUDE_CONFIG_DIR) { return $env:CLAUDE_CONFIG_DIR }
    $profileDir = [Environment]::GetFolderPath('UserProfile')
    if (-not $profileDir) { $profileDir = $HOME }
    return [System.IO.Path]::Combine($profileDir, '.claude')
}

function Find-SessionTranscript {
    param([string]$SessionId)
    # Журнал сессии — `<каталог настроек>/projects/<папка проекта>/<номер>.jsonl`.
    #
    # ‼️ Папку проекта НЕ вычисляем из пути: формула кодирования нигде не объявлена, соединения
    # папок на этой машине подменяют одну папку другой, а сессия, перешедшая в рабочее дерево,
    # продолжает писать в журнал СТАРТОВОЙ папки. Поэтому ищем номер во всех папках проектов. Нашлось
    # несколько — берём самый крупный: это тот, куда пишут.
    if (-not (Test-SessionId $SessionId)) { return '' }
    $projects = [System.IO.Path]::Combine((Get-ClaudeConfigDir), 'projects')
    $best = ''
    $bestSize = [long]-1
    foreach ($dir in [System.IO.Directory]::EnumerateDirectories($projects)) {
        $info = [System.IO.FileInfo]::new([System.IO.Path]::Combine($dir, "$SessionId.jsonl"))
        if ($info.Exists -and $info.Length -gt $bestSize) {
            $best = $info.FullName
            $bestSize = $info.Length
        }
    }
    return $best
}

function Get-TitleFromLine {
    param([string]$Line, [string]$Type, [string]$Field)
    # Запись названия опознаётся ТОЛЬКО разобранным объектом верхнего уровня с нужным полем `type`.
    #
    # ‼️ Текстовому поиску тут не верим: те же слова лежат и внутри разговора — вывод поиска, чтение
    # журнала, обсуждение формата. Внутри строки кавычки экранированы, но объект с таким `type`
    # может оказаться и вложенным (результат инструмента, разобранный в структуру). Поиск только
    # ОТБИРАЕТ строки-кандидаты, решает разбор.
    #
    # Разбираем средствами .NET, а не оболочки: оболочка превращает строку, похожую на дату, в дату,
    # и вкладка с названием «2026-09-04» (волны здесь зовут датами) потеряла бы имя.
    $doc = $null
    try {
        $doc = [System.Text.Json.JsonDocument]::Parse($Line)
        $root = $doc.RootElement
        if ($root.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) { return $null }
        $kind = [System.Text.Json.JsonElement]::new()
        if (-not $root.TryGetProperty('type', [ref]$kind)) { return $null }
        if ($kind.ValueKind -ne [System.Text.Json.JsonValueKind]::String -or $kind.GetString() -ne $Type) {
            return $null
        }
        $value = [System.Text.Json.JsonElement]::new()
        if (-not $root.TryGetProperty($Field, [ref]$value)) { return $null }
        if ($value.ValueKind -ne [System.Text.Json.JsonValueKind]::String) { return $null }
        return $value.GetString()
    } catch {
        return $null
    } finally {
        if ($doc) { $doc.Dispose() }
    }
}

function Find-LastTitleInText {
    param([string]$Text, [string]$Type, [string]$Field)
    # Последняя запись заданного вида в куске текста, состоящем из ЦЕЛЫХ строк. Идём с конца: нужна
    # именно последняя — переименование дописывает новую запись в конец, и побеждает она.
    $needle = '"' + $Type + '"'
    $at = $Text.LastIndexOf($needle, [System.StringComparison]::Ordinal)
    while ($at -ge 0) {
        $lineStart = if ($at -gt 0) { $Text.LastIndexOf([char]10, $at - 1) + 1 } else { 0 }
        $lineEnd = $Text.IndexOf([char]10, $at)
        if ($lineEnd -lt 0) { $lineEnd = $Text.Length }
        $value = Get-TitleFromLine -Line $Text.Substring($lineStart, $lineEnd - $lineStart) -Type $Type -Field $Field
        if ($null -ne $value) { return $value }
        if ($lineStart -le 0) { break }
        $at = $Text.LastIndexOf($needle, $lineStart - 1, [System.StringComparison]::Ordinal)
    }
    return $null
}

function Read-TitleRecords {
    param([string]$Path, [long]$From, [long]$To)
    # Обратное чтение окна [From, To) кусками: последняя запись имени от человека, последняя
    # автоматическая и смещение сразу за последней ЦЕЛОЙ строкой окна (с него начнётся следующее
    # дочитывание).
    #
    # ‼️ Строка на границе куска не теряется и половинкой не разбирается. Правило одно: в дело идут
    # только строки, у которых виден и перевод строки в конце, и начало. Хвост куска без перевода
    # строки — начало строки, которую дочитает следующий кусок; голова куска до первого перевода
    # строки — конец строки, начавшейся раньше, и она уходит в следующий кусок целиком. Неполная
    # последняя строка файла (её дописывают прямо сейчас) в дело не идёт, и смещение за неё не
    # сдвигается: дочитает следующий вызов.
    #
    # Строка длиннее куска записью названия быть не может (это сотня байт), поэтому такая строка
    # пропускается целиком, а не читается расширением окна.
    $chunk = [long]$script:TabTitleChunkBytes
    $custom = $null
    $auto = $null
    $offset = $null
    $stream = [System.IO.FileStream]::new($Path, 'Open', 'Read', 'ReadWrite, Delete')
    try {
        # Начинается ли окно с начала строки: окно дочитывания начинается с запомненного смещения,
        # окно первого чтения — с потолка, то есть обычно посреди строки.
        $fromIsLineStart = $true
        if ($From -gt 0) {
            $stream.Position = $From - 1
            $fromIsLineStart = ($stream.ReadByte() -eq 10)
        }
        $bufferSize = $To - $From
        if ($bufferSize -gt $chunk) { $bufferSize = $chunk }
        if ($bufferSize -lt 1) { $bufferSize = 1 }
        $buffer = [byte[]]::new([int]$bufferSize)
        $end = $To
        while ($end -gt $From) {
            $pos = $end - $chunk
            if ($pos -lt $From) { $pos = $From }
            $length = [int]($end - $pos)
            $stream.Position = $pos
            $read = 0
            while ($read -lt $length) {
                $got = $stream.Read($buffer, $read, $length - $read)
                if ($got -le 0) { break }
                $read += $got
            }
            # Файл укоротили прямо под чтением — дальше читать нечего, решит следующий вызов.
            if ($read -lt $length) { break }
            # Обычно кусок кончается переводом строки (строки журнала пишутся целиком) — тогда искать
            # его незачем. Поиск по байтам нужен, только если хвост оборван посередине строки.
            $lastBreak = if ($buffer[$length - 1] -eq 10) {
                $length - 1
            } else {
                [Array]::LastIndexOf($buffer, [byte]10, $length - 1, $length)
            }
            if ($lastBreak -lt 0) {
                # Во всём куске ни одного перевода строки: это середина строки длиннее куска.
                $end = $pos
                continue
            }
            if ($null -eq $offset) { $offset = $pos + $lastBreak + 1 }
            $atWindowStart = ($pos -eq $From)
            $startsAtLine = $atWindowStart -and $fromIsLineStart
            # Начало первой целой строки ищем, только если кусок начинается посреди строки.
            $firstBreak = if ($startsAtLine) { -1 } else { [Array]::IndexOf($buffer, [byte]10, 0, $length) }
            $regionStart = $firstBreak + 1
            if ($regionStart -le $lastBreak) {
                # Границы куска стоят на переводах строки, а многобайтовый знак перевода строки не
                # содержит — значит кириллица на границе не рвётся.
                $text = [System.Text.Encoding]::UTF8.GetString($buffer, $regionStart, $lastBreak + 1 - $regionStart)
                if ($null -eq $custom) {
                    $custom = Find-LastTitleInText -Text $text -Type 'custom-title' -Field 'customTitle'
                }
                if ($null -eq $auto) {
                    $auto = Find-LastTitleInText -Text $text -Type 'ai-title' -Field 'aiTitle'
                }
            }
            # Имя от человека нашлось — дальше назад читать незачем: старые записи ему проигрывают.
            # Исключение — имя, стёртое в пустую строку: тогда в дело идёт автоматическое, и его
            # ищем дальше, если оно ещё не попалось.
            if ($null -ne $custom -and ($custom -ne '' -or $null -ne $auto)) { break }
            if ($atWindowStart) { break }
            $next = $pos + $firstBreak + 1
            # Единственный перевод строки стоит в самом конце куска: строка началась раньше куска и
            # длиннее его — пропускаем её целиком, иначе чтение топталось бы на месте.
            $end = if ($next -ge $end) { $pos } else { $next }
        }
    } finally {
        $stream.Dispose()
    }
    return @{ Custom = $custom; Auto = $auto; Offset = $offset }
}

function Get-CleanTitle {
    param([string]$Raw)
    # Название пишет человек или модель, а печатается оно в строку показа. Управляющие знаки и
    # переключатели направления письма убираем (они ломали бы строку и могли бы переставить её
    # слова), пробелы сводим, длину ограничиваем: строке показа нужно узнаваемое имя, а не абзац.
    if (-not $Raw) { return '' }
    $text = $Raw -replace '[\p{Cc}\u200E\u200F\u202A-\u202E\u2066-\u2069]', ' '
    $text = ($text -replace '\s+', ' ').Trim()
    if ($text.Length -gt 80) { $text = $text.Substring(0, 79) + '…' }
    return $text
}

function Get-SessionTitle {
    param([string]$SessionId, [string]$CacheDir)
    # Действующее название вкладки по номеру сессии: `Title` и вид `Kind` — `custom` (имя от
    # человека), `auto` (придуманное автоматически) или пусто (не найдено).
    #
    # Действующее — ПОСЛЕДНЯЯ запись имени от человека; нет её вовсе — последняя автоматическая.
    #
    # ‼️ Сторож доставки зовёт это на КАЖДОМ сообщении человека, а журналы бывают по двести мегабайт.
    # Поэтому кэш на сессию: путь журнала, смещение, до которого он прочитан, и найденные имена.
    # Следующий вызов дочитывает только НОВЫЙ хвост от смещения. Файл стал короче смещения — его
    # переписали, читаем заново. Хвост вырос больше потолка — читаем назад от конца до потолка, а не
    # найдя там ничего, остаёмся при прежнем.
    #
    # Кэш — пять строк простого текста: метка вида, путь журнала, смещение, имя от человека,
    # автоматическое имя (оба уже очищены). Не JSON намеренно: разбор JSON в свежем процессе сторожа
    # стоит десятки миллисекунд на каждом ходу, а пять строк читаются одним обращением к диску. Из
    # журнала в кэш не попадает ничего, кроме этих двух имён.
    #
    # Любая неудача — «не найдено», без исключения наружу.
    if (-not $SessionId -or $SessionId -cnotmatch $script:TabSessionIdPattern) { return @{ Title = ''; Kind = '' } }
    try {
        $cacheFile = if ($CacheDir) { [System.IO.Path]::Combine($CacheDir, "$SessionId.txt") } else { '' }
        $cached = $null
        if ($cacheFile) {
            try { $cached = [System.IO.File]::ReadAllLines($cacheFile) } catch { $cached = $null }
        }
        $known = if ($cached -and $cached.Count -eq 5 -and $cached[0] -eq $script:TabTitleCacheMark) { $cached[2] -as [long] } else { $null }
        $usable = $null -ne $known -and $known -ge 0
        $info = if ($usable) { [System.IO.FileInfo]::new($cached[1]) } else { $null }
        if (-not $info -or -not $info.Exists) {
            $usable = $false
            $path = Find-SessionTranscript -SessionId $SessionId
            if (-not $path) { return @{ Title = ''; Kind = '' } }
            $info = [System.IO.FileInfo]::new($path)
        }
        $size = $info.Length
        if (-not $usable) { $known = [long]0 }
        # Файл стал короче прочитанного — его переписали: прежние имена и смещение ничего не значат.
        if ($known -gt $size) { $usable = $false; $known = [long]0 }
        $custom = if ($usable) { $cached[3] } else { '' }
        $auto = if ($usable) { $cached[4] } else { '' }
        if ($size -gt $known -or -not $usable) {
            $offset = $null
            # ‼️ КОРОТКИЙ ПУТЬ частого случая: кэш есть, хвост короче куска, и записей названия в нём
            # нет (среда дописывает их не на каждом ходу). Тогда хватает прочитать хвост одним куском
            # и убедиться, что слова вида записи в нём нет вовсе — без общего пути обратного чтения,
            # который в свежем процессе стоит в разы дороже.
            #
            # Читаем с байта ПЕРЕД смещением: он обязан быть переводом строки (иначе смещение не на
            # границе строки), и последний байт — тоже (иначе последняя строка ещё дописывается). Не
            # сошлось хоть что-то — идём общим путём, он разбирает все такие случаи.
            if ($usable -and $known -gt 0 -and ($size - $known) -le $script:TabTitleChunkBytes) {
                $length = [int]($size - $known + 1)
                $tail = [byte[]]::new($length)
                $got = 0
                $stream = [System.IO.FileStream]::new($info.FullName, 'Open', 'Read', 'ReadWrite, Delete')
                try {
                    $stream.Position = $known - 1
                    while ($got -lt $length) {
                        $step = $stream.Read($tail, $got, $length - $got)
                        if ($step -le 0) { break }
                        $got += $step
                    }
                } finally {
                    $stream.Dispose()
                }
                if ($got -eq $length -and $tail[0] -eq 10 -and $tail[$length - 1] -eq 10 -and
                    [System.Text.Encoding]::UTF8.GetString($tail, 1, $length - 1).IndexOf('-title"', [System.StringComparison]::Ordinal) -lt 0) {
                    $offset = $size
                }
            }
            if ($null -eq $offset) {
                # Общий путь. Окно — от прочитанного до конца, но не больше потолка.
                $from = $size - [long]$script:TabTitleCeilingBytes
                if ($from -lt $known) { $from = $known }
                $found = @{ Custom = $null; Auto = $null; Offset = $null }
                if ($size -gt $from) { $found = Read-TitleRecords -Path $info.FullName -From $from -To $size }
                if ($null -ne $found.Custom) { $custom = Get-CleanTitle -Raw $found.Custom }
                if ($null -ne $found.Auto) { $auto = Get-CleanTitle -Raw $found.Auto }
                $offset = if ($null -ne $found.Offset) { [long]$found.Offset } elseif ($usable) { $known } else { $from }
            }
            if ($cacheFile) {
                # Запись на месте, без помощника: через временный файл рядом, чтобы читатель не увидел
                # полузаписанное. Перевод строки после КАЖДОЙ строки, включая последнюю: иначе пустые
                # имена в хвосте не посчитались бы строками, и кэш не узнавался бы вовсе.
                $temp = "$cacheFile.tmp-$PID"
                try {
                    $null = [System.IO.Directory]::CreateDirectory($CacheDir)
                    [System.IO.File]::WriteAllText($temp, "$($script:TabTitleCacheMark)`n$($info.FullName)`n$offset`n$custom`n$auto`n")
                    [System.IO.File]::Move($temp, $cacheFile, $true)
                } catch {
                    # Кэш не записался — не беда: следующий вызов прочитает ещё раз.
                    try { [System.IO.File]::Delete($temp) } catch { }
                }
            }
        }
        if ($custom) { return @{ Title = $custom; Kind = 'custom' } }
        if ($auto) { return @{ Title = $auto; Kind = 'auto' } }
        return @{ Title = ''; Kind = '' }
    } catch {
        return @{ Title = ''; Kind = '' }
    }
}

# ─────────────────────────────────────────────────────────────────────────────────────────────
# Реестр вкладок: маленькая запись на сессию рядом с реестром заявок.
#
# Пишет её только своя сессия — сторож доставки на начале сессии и на каждом сообщении человека,
# и объявление потока. Читают все показы. Лежит в общем служебном каталоге репозитория, как и доска:
# видна всем рабочим деревьям и переживает удаление дерева.
# ─────────────────────────────────────────────────────────────────────────────────────────────

# Записи вкладок, уже прочитанные в этом запуске: показ спрашивает одну и ту же вкладку по разу на
# каждую строку.
$script:WaveBoardTabRecords = @{}

# Столько вкладка может молчать, прежде чем перечень вкладок уведёт её в хвост «давно молчат». Сутки,
# а не порог живости заявки: ночная пауза в разговоре вкладку из перечня не выбрасывает.
$script:TabSilentHours = 24

# Столько живёт запись вкладки без единого хода: дальше её убирает сторож на начале сессии. Месяц
# покрывает волну с запасом; вкладку, молчащую месяц, по названию уже не ищут.
$script:TabKeepDays = 30

function Get-TabsDir {
    param([string]$RegistryDir)
    # Соседний с реестром заявок каталог. Выводится из пути реестра, а не спрашивается у git ещё раз:
    # сторож доставки зовётся на каждом ходу, и лишний запуск git был бы платой ни за что.
    if (-not $RegistryDir) { return '' }
    $parent = [System.IO.Path]::GetDirectoryName($RegistryDir.TrimEnd('/', '\'))
    if (-not $parent) { return '' }
    return [System.IO.Path]::Combine($parent, 'tabs')
}

function Read-TabRecordFile {
    param([string]$Path)
    # Запись вкладки — плоский JSON из строк. Нечитаемое и неразбираемое — пустота, а не падение.
    #
    # Разбор средствами .NET, а не оболочки, по той же причине, что и у журнала: название, похожее на
    # дату, оболочка превратила бы в дату, и вкладка «2026-09-04» показывалась бы в виде системной
    # локали.
    $text = $null
    try {
        $text = [System.IO.File]::ReadAllText($Path)
    } catch {
        return $null
    }
    $doc = $null
    try {
        $doc = [System.Text.Json.JsonDocument]::Parse($text)
        if ($doc.RootElement.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) { return $null }
        $fields = [ordered]@{}
        foreach ($property in $doc.RootElement.EnumerateObject()) {
            if ($property.Value.ValueKind -eq [System.Text.Json.JsonValueKind]::String) {
                $fields[$property.Name] = $property.Value.GetString()
            }
        }
        return $fields
    } catch {
        return $null
    } finally {
        if ($doc) { $doc.Dispose() }
    }
}

function Get-TabRecord {
    param([string]$Dir, [string]$SessionId)
    if (-not $Dir -or -not (Test-SessionId $SessionId)) { return $null }
    $key = "$Dir|$SessionId"
    if ($script:WaveBoardTabRecords.ContainsKey($key)) { return $script:WaveBoardTabRecords[$key] }
    $record = Read-TabRecordFile -Path ([System.IO.Path]::Combine($Dir, "$SessionId.json"))
    $script:WaveBoardTabRecords[$key] = $record
    return $record
}

function Get-TabRecords {
    param([string]$Dir)
    # Весь реестр вкладок. Нечитаемые записи пропускаются молча: это перечень для глаз, и одна
    # занятая запись не повод не показать остальные.
    $records = [System.Collections.Generic.List[object]]::new()
    if (-not $Dir) { return @() }
    $files = @()
    try {
        $files = @([System.IO.Directory]::GetFiles($Dir, '*.json'))
    } catch {
        return @()
    }
    foreach ($file in $files) {
        $record = Read-TabRecordFile -Path $file
        if ($record -and (Test-SessionId ([string]$record.session_id))) { $records.Add($record) }
    }
    return @($records)
}

function Update-TabRecord {
    param([string]$Dir, [string]$SessionId, [string]$Cwd, [string]$Tree, [switch]$Prompt)
    # Запись своей вкладки: номер сессии, папка, корень дерева, название и его вид, время последнего
    # сообщения человека. `-Prompt` — это ход человека: только он двигает время сообщения. Начало
    # сессии и объявление потока его не трогают — это не сообщение.
    #
    # Немая при любой неудаче: сорванная запись вкладки не повод мешать работе.
    #
    # ‼️ Прежнюю запись читаем, только когда без неё не обойтись: на начале сессии и в объявлении —
    # ради времени последнего сообщения, на ходе человека — лишь если название не нашлось. Сборка и
    # запись — на месте, без помощников: это самый частый вызов набора (см. шапку файла).
    if (-not $Dir -or -not $SessionId -or $SessionId -cnotmatch $script:TabSessionIdPattern) { return }
    $file = [System.IO.Path]::Combine($Dir, "$SessionId.json")
    $temp = "$file.tmp-$PID"
    try {
        $title = Get-SessionTitle -SessionId $SessionId -CacheDir ([System.IO.Path]::Combine($Dir, 'cache'))
        $old = if (-not $Prompt -or -not $title.Kind) { Read-TabRecordFile -Path $file } else { $null }
        # Журнал на миг не прочитался — прежнее название лучше пустоты.
        if (-not $title.Kind -and $old -and $old.title) {
            $title = @{ Title = [string]$old.title; Kind = [string]$old.title_kind }
        }
        $now = (Get-Date).ToString('s')
        $promptAt = if ($Prompt) { $now } elseif ($old) { [string]$old.prompt_at } else { '' }
        $record = [ordered]@{
            session_id = $SessionId
            cwd        = ([string]$Cwd -replace '\\', '/').TrimEnd('/')
            tree       = ([string]$Tree -replace '\\', '/').TrimEnd('/')
            title      = [string]$title.Title
            title_kind = [string]$title.Kind
            prompt_at  = $promptAt
            seen_at    = $now
        }
        # Плоская запись из строк — в JSON руками, а не общим преобразователем оболочки: тот в свежем
        # процессе стоит десятки миллисекунд. Экранируются обратная косая и кавычка; управляющих
        # знаков в полях нет (название чищено, пути их не содержат), а на всякий случай они
        # заменяются пробелом.
        $pairs = foreach ($key in $record.Keys) {
            "  `"$key`": `"$((($record[$key] -replace '\\', '\\') -replace '"', '\"') -replace '[\x00-\x1f]', ' ')`""
        }
        $null = [System.IO.Directory]::CreateDirectory($Dir)
        [System.IO.File]::WriteAllText($temp, "{`n$($pairs -join ",`n")`n}`n")
        [System.IO.File]::Move($temp, $file, $true)
        $script:WaveBoardTabRecords["$Dir|$SessionId"] = $record
    } catch {
        try { [System.IO.File]::Delete($temp) } catch { }
        return
    }
}

function Remove-StaleTabRecords {
    param([string]$Dir)
    # Чистка записей вкладок, молчащих дольше срока. По времени правки файла: его обновляет каждый
    # ход вкладки, так что живая вкладка сюда не попадает. Немая при любой неудаче.
    if (-not $Dir) { return }
    $deadline = (Get-Date).AddDays(-$script:TabKeepDays)
    # Записи вкладок — `.json` в самом каталоге, кэш названий — `.txt` в подкаталоге.
    $places = @{ $Dir = '*.json'; ([System.IO.Path]::Combine($Dir, 'cache')) = '*.txt' }
    foreach ($folder in @($places.Keys)) {
        try {
            foreach ($file in [System.IO.Directory]::GetFiles($folder, $places[$folder])) {
                try {
                    # Удаляем только своё: имя файла — номер сессии. Чужой файл, оказавшийся в каталоге,
                    # чистка не тронет ни при каком возрасте.
                    if (-not (Test-SessionId ([System.IO.Path]::GetFileNameWithoutExtension($file)))) { continue }
                    if ([System.IO.File]::GetLastWriteTime($file) -lt $deadline) { [System.IO.File]::Delete($file) }
                } catch {
                    continue
                }
            }
        } catch {
            continue
        }
    }
}

function Format-TabTitle {
    param($Tab, [switch]$Past)
    # Один вид на все показы: «вкладка О1-3-1»; для придуманного автоматически — «вкладка без имени
    # (авто: …)»; для потока, который уже не ведут, — «вела вкладка …». Неизвестна — пусто: строку
    # показа не засоряем словом «неизвестно» на каждом потоке.
    if (-not $Tab -or -not [string]$Tab.title) { return '' }
    $lead = if ($Past) { 'вела вкладка' } else { 'вкладка' }
    if ([string]$Tab.title_kind -eq 'custom') { return "$lead $($Tab.title)" }
    return "$lead без имени (авто: $($Tab.title))"
}

function Get-ClaimTabText {
    param($Claim)
    # Название вкладки, ведущей (или ведшей) поток разобранной записи реестра. Каталог вкладок
    # выводится из пути файла заявки: он всегда сосед каталога заявок. Закрытый поток — «вела».
    try {
        if (-not $Claim -or -not $Claim.File -or -not $Claim.Record) { return '' }
        $sessionId = [string]$Claim.Record.session_id
        if (-not (Test-SessionId $sessionId)) { return '' }
        $dir = Get-TabsDir -RegistryDir ([System.IO.Path]::GetDirectoryName([string]$Claim.File))
        return (Format-TabTitle -Tab (Get-TabRecord -Dir $dir -SessionId $sessionId) -Past:([bool]$Claim.Closed))
    } catch {
        return ''
    }
}

function Test-LinkedWorktree {
    param([string]$TreePath)
    # Отдельное рабочее дерево, а не главная папка репозитория. Спрашиваем диск, а не git: сторож
    # доставки зовётся на каждом сообщении, и лишний запуск git стоил бы десятков миллисекунд.
    # У отдельного дерева `.git` — файл со ссылкой в `.../worktrees/<имя>`; у главной папки — каталог.
    # Подмодуль тоже носит файл `.git`, но ссылается в `.../modules/...` — деревом его не считаем.
    if (-not $TreePath) { return $false }
    try {
        $marker = [System.IO.Path]::Combine($TreePath, '.git')
        if (-not [System.IO.File]::Exists($marker)) { return $false }
        $text = [System.IO.File]::ReadAllText($marker) -replace '\\', '/'
        return [bool]($text -match '(?im)^gitdir:\s*\S.*/worktrees/[^/]+/?\s*$')
    } catch {
        return $false
    }
}
