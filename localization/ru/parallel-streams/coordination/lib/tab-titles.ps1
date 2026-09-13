#Requires -Version 7
<#
Название вкладки: какая сессия ведёт поток и как её назвал человек.

Зачем. Владелец держит десятки вкладок и называет их сам («О1-3-1»). Доска знает поток по папке и
ветке, и на вопрос «в какой вкладке ведут поток волна9/3» ответа у неё не было — его искали
перебором журналов всех сессий, минутами. Здесь лежит всё, что связывает поток с вкладкой:

  • чтение названия из журнала сессии — быстро, дочитывая только новый хвост;
  • реестр вкладок — маленькая запись на сессию рядом с реестром заявок;
  • какая сессия ведёт заявку, если объявление её не назвало (подхват и перенос);
  • общий вид строки «вкладка О1-3-1» для всех показов.

‼️ Из журнала сессии читаются РОВНО две служебные записи — `custom-title` (имя, данное человеком)
и `ai-title` (придуманное автоматически). Ни разговор, ни что-либо ещё не читается и не
сохраняется: журнал — это вся переписка человека, и набор, поставленный в чужой проект, не вправе
выносить из него больше, чем название.

‼️ Внутренности редактора (его базы, память рабочей области) не читаются вовсе: этот приём уже
отвергнут — он давал ложные срабатывания. Источник один — журнал сессии.

‼️ Всё, что лежит в служебном каталоге репозитория (реестр вкладок, кэш, заявки), считается
доверенным ровно настолько, насколько доверен сам каталог: писать туда может любой процесс того же
человека. Поэтому то, что оттуда ПЕЧАТАЕТСЯ, чистится в момент печати, путь журнала из кэша
сверяется с каталогом журналов, а чистка удаляет только файлы, опознанные по содержимому.

‼️ Частый путь писан скупо на вызовы. Сторож доставки зовёт запись вкладки на КАЖДОМ сообщении
человека, в свежем процессе оболочки, а там первое обращение к каждой функции стоит миллисекунды.
Замер 13.09.2026 (процессорное время, машина под полной нагрузкой): та же работа через цепочку
вспомогательных функций — около 150 мс, одной функцией — около 55 мс. Поэтому постоянные здесь —
переменные, а запись файлов в частом пути сделана на месте, без посредников. Название из журнала
на ходе человека перечитывается не чаще раза в две минуты, а время сообщения — это время правки
маленькой отметки, а не перезапись записи вкладки.

Файл ничего не делает сам и ни от чего в комплекте не зависит: только объявляет функции. Любая
неудача чтения — тихое «не найдено»: сорванное название не повод ронять команду или сторожа.
#>

# Номер сессии идёт в имя файла, поэтому принимаем только безопасный вид: буквы, цифры, дефис и
# подчёркивание. Косая, точка или звёздочка в нём означали бы выход из своей папки или образец поиска
# вместо имени — а номер приходит снаружи (переменная окружения, данные сторожа).
#
# ‼️ Якорь конца — `\z`, а не `$`: `$` в этих образцах совпадает и перед завершающим переводом строки,
# и номер «abc» с переводом строки проходил проверку.
$script:TabSessionIdPattern = '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}\z'

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

# Бюджет разбора за один вызов. Строки-кандидаты отбираются текстом, и разбирается КАЖДАЯ — а слово
# вида записи бывает и строковым значением внутри вложенного объекта (результат инструмента, ответ
# стороннего сервера). Опыт ревью 13.09.2026: мегабайт таких строк — четыре секунды на одном
# сообщении человека, до доставки находок. Поэтому:
#   • строка длиннее 8 КБ не разбирается вовсе — запись названия занимает сотни байт;
#   • после 200 разборов, не давших записи, чтение останавливается: смещение и прежние имена ложатся
#     в кэш, а имя вернёт ближайший повтор записи в новом хвосте.
$script:TabTitleLineMaxChars = 8192
$script:TabTitleParseLimit = 200
$script:TabTitleParsesLeft = 200

# Первая строка кэша названия: по ней видно, что файл — наш кэш этого вида. Сменится вид кэша —
# сменится метка, и старые файлы просто перечитаются.
$script:TabTitleCacheMark = 'parallel-streams tab title cache 1'

# Первая строка отметки сессии — по ней чистка узнаёт свой файл.
$script:TabStateMark = 'parallel-streams tab state 1'

# Название на ходе человека перечитывается из журнала не чаще раза в столько секунд (по времени правки
# файла кэша). Переименование доходит до показа с задержкой до двух минут; начало сессии и объявление
# потока читают журнал всегда.
$script:TabTitleRereadSeconds = 120

# Журнала не нашлось — искать его заново не чаще раза в столько секунд. Поиск перебирает все папки
# проектов, а среди них бывают соединения папок на сетевые ресурсы: без этой памяти недоступный узел
# стоил бы тайм-аута на каждом сообщении. Срок тот же, что у перечитывания названия, а не длиннее:
# журнал новой сессии заводится после её начала, и более долгая память о промахе на столько же
# задерживала бы и название, и перенос заявки после очистки контекста.
$script:TabTitleMissRecheckSeconds = 120

# Кодировка записи без исключений на недопустимом знаке. Кодировка файлов по умолчанию на одиночной
# половине суррогатной пары бросает исключение — и кэш с записью вкладки молча не писались никогда.
$script:TabUtf8 = [System.Text.UTF8Encoding]::new($false)

# Названия, прочитанные из журнала в этом запуске: проверка переноса заявки и запись вкладки
# спрашивают одно и то же, и журнал второй раз не читается.
$script:TabTitleNow = @{}

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
    # Каталога журналов нет вовсе (другой клиент) — это «не найдено», которое кэш запомнит, а не
    # исключение, после которого поиск повторялся бы на каждом ходу.
    $dirs = @()
    try { $dirs = [System.IO.Directory]::GetDirectories($projects) } catch { return '' }
    foreach ($dir in $dirs) {
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
    #
    # Бюджет разбора общий на весь вызов чтения названия (см. `$script:TabTitleParseLimit`): кончился —
    # останавливаемся, не разобрав остальное.
    $needle = '"' + $Type + '"'
    $at = $Text.LastIndexOf($needle, [System.StringComparison]::Ordinal)
    while ($at -ge 0 -and $script:TabTitleParsesLeft -gt 0) {
        $lineStart = if ($at -gt 0) { $Text.LastIndexOf([char]10, $at - 1) + 1 } else { 0 }
        $lineEnd = $Text.IndexOf([char]10, $at)
        if ($lineEnd -lt 0) { $lineEnd = $Text.Length }
        if ($lineEnd - $lineStart -le $script:TabTitleLineMaxChars) {
            $value = Get-TitleFromLine -Line $Text.Substring($lineStart, $lineEnd - $lineStart) -Type $Type -Field $Field
            if ($null -ne $value) { return $value }
            $script:TabTitleParsesLeft--
        }
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
    #
    # Кончился бюджет разбора — чтение останавливается: смещение уже стоит на конце окна, а что не
    # нашлось, остаётся прежним у зовущего.
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
            if ($atWindowStart -or $script:TabTitleParsesLeft -le 0) { break }
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
    param([string]$Raw, [int]$Max = 80)
    # Название пишет человек или модель, а печатается оно в строку показа и в контекст соседних
    # вкладок. Поэтому:
    #   • знаки-метки U+E0000–E007F убираем целиком — это невидимый человеку текст, который модель
    #     читает (известный приём скрытых указаний); в строке они лежат суррогатными парами
    #     `\uDB40[\uDC00-\uDC7F]`, и категория форматирующих знаков по кодовым единицам их не ловит;
    #   • одиночные половины суррогатных пар убираем — записать их в файл нельзя;
    #   • управляющие и форматирующие знаки (нулевой ширины, мягкий перенос, переключатели направления
    #     письма) заменяем пробелом — они ломали бы строку, переставляли бы её слова и делали бы два
    #     разных названия неотличимыми глазами;
    #   • пробелы сводим, длину ограничиваем: строке показа нужно узнаваемое имя, а не абзац.
    #
    # `-Max 0` — без обрезки: так чистятся пути в строках показа.
    if (-not $Raw) { return '' }
    $text = $Raw -replace '\uDB40[\uDC00-\uDC7F]', ''
    $text = $text -replace '[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]', ''
    $text = $text -replace '[\p{Cc}\p{Cf}]', ' '
    $text = ($text -replace '\s+', ' ').Trim()
    if ($Max -gt 0 -and $text.Length -gt $Max) {
        # ‼️ Не оставляем на месте среза первую половину пары (эмодзи, редкие иероглифы): одиночный
        # суррогат срывал запись и кэша, и записи вкладки.
        $cut = $Max - 1
        if ([char]::IsHighSurrogate($text[$cut - 1])) { $cut-- }
        $text = $text.Substring(0, $cut) + '…'
    }
    return $text
}

function Get-SessionTitle {
    param([string]$SessionId, [string]$CacheDir, [switch]$Fresh)
    # Действующее название вкладки по номеру сессии: `Title` и вид `Kind` — `custom` (имя от
    # человека), `auto` (придуманное автоматически) или пусто (не найдено).
    #
    # Действующее — ПОСЛЕДНЯЯ запись имени от человека; нет её вовсе — последняя автоматическая.
    #
    # ‼️ Сторож доставки зовёт это на ходе человека, а журналы бывают по двести мегабайт. Поэтому кэш на
    # сессию: путь журнала, смещение, до которого он прочитан, и найденные имена. Следующий вызов
    # дочитывает только НОВЫЙ хвост от смещения. Файл стал короче смещения — его переписали, читаем
    # заново. Хвост вырос больше потолка — читаем назад от конца до потолка, а не найдя там ничего,
    # остаёмся при прежнем.
    #
    # Кэш — пять строк простого текста: метка вида, путь журнала, смещение, имя от человека,
    # автоматическое имя (оба уже очищены). Не JSON намеренно: разбор JSON в свежем процессе сторожа
    # стоит десятки миллисекунд на каждом ходу, а пять строк читаются одним обращением к диску. Из
    # журнала в кэш не попадает ничего, кроме этих двух имён. Пустой путь — «журнала не нашлось»: его
    # ищут заново не чаще раза в `$script:TabTitleMissRecheckSeconds`.
    #
    # `-Fresh` — начало сессии и объявление: журнал ищется заново, а не берётся из кэша (сессию могли
    # возобновить из другой папки, и журнал того же номера появился в другой папке проектов), и память
    # о промахе не действует. Совпал найденный журнал с запомненным — кэш остаётся в силе.
    #
    # Любая неудача — «не найдено», без исключения наружу.
    if (-not $SessionId -or $SessionId -cnotmatch $script:TabSessionIdPattern) { return @{ Title = ''; Kind = '' } }
    $script:TabTitleParsesLeft = $script:TabTitleParseLimit
    try {
        $cacheFile = if ($CacheDir) { [System.IO.Path]::Combine($CacheDir, "$SessionId.txt") } else { '' }
        $cached = $null
        if ($cacheFile) {
            try { $cached = [System.IO.File]::ReadAllLines($cacheFile) } catch { $cached = $null }
        }
        $known = if ($cached -and $cached.Count -eq 5 -and $cached[0] -eq $script:TabTitleCacheMark) { $cached[2] -as [long] } else { $null }
        $usable = $null -ne $known -and $known -ge 0
        $info = $null
        if ($usable -and -not $cached[1]) {
            # Память о промахе: журнала не было совсем недавно — не перебираем папки проектов снова.
            if (-not $Fresh -and ([datetime]::Now - [System.IO.File]::GetLastWriteTime($cacheFile)).TotalSeconds -lt $script:TabTitleMissRecheckSeconds) {
                return @{ Title = ''; Kind = '' }
            }
            $usable = $false
        }
        if ($usable) {
            # ‼️ Путь журнала из кэша принимаем, только если это журнал ЭТОЙ сессии в каталоге журналов:
            # имя `<номер>.jsonl`, а каталог двумя уровнями выше — `<каталог настроек>/projects`.
            # Сверка строками, без обращения к диску. Иначе кэш, подправленный кем угодно, называл бы
            # вкладку чужим именем или заставлял бы сторожа на каждом ходу ходить по сетевому пути.
            $info = [System.IO.FileInfo]::new($cached[1])
            $projects = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine((Get-ClaudeConfigDir), 'projects')).TrimEnd('\', '/')
            $journalRoot = if ($info.Directory -and $info.Directory.Parent) { $info.Directory.Parent.FullName.TrimEnd('\', '/') } else { '' }
            if ($info.Name -cne "$SessionId.jsonl" -or -not [string]::Equals($journalRoot, $projects, [System.StringComparison]::OrdinalIgnoreCase)) {
                $usable = $false
                $info = $null
            } elseif (-not $Fresh -and -not $info.Exists) {
                $info = $null
            }
        }
        if ($Fresh -or -not $info) {
            $path = Find-SessionTranscript -SessionId $SessionId
            if ($path -and $info -and [string]::Equals($path, $info.FullName, [System.StringComparison]::OrdinalIgnoreCase)) {
                # Нашёлся тот же журнал, что в кэше, — кэш в силе.
            } elseif ($path) {
                $usable = $false
                $info = [System.IO.FileInfo]::new($path)
            } else {
                $usable = $false
                $info = $null
            }
        }
        $custom = if ($usable) { $cached[3] } else { '' }
        $auto = if ($usable) { $cached[4] } else { '' }
        $pathText = ''
        $offset = [long]0
        $write = $true
        if ($info) {
            $pathText = $info.FullName
            $size = $info.Length
            if (-not $usable) { $known = [long]0 }
            # Файл стал короче прочитанного — его переписали: прежние имена и смещение ничего не значат.
            if ($known -gt $size) { $usable = $false; $known = [long]0; $custom = ''; $auto = '' }
            $write = ($size -gt $known -or -not $usable)
            if ($write) {
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
            }
        }
        if ($write -and $cacheFile) {
            # Запись на месте, без помощника: через временный файл рядом, чтобы читатель не увидел
            # полузаписанное. Перевод строки после КАЖДОЙ строки, включая последнюю: иначе пустые
            # имена в хвосте не посчитались бы строками, и кэш не узнавался бы вовсе.
            $temp = "$cacheFile.tmp-$PID"
            try {
                $null = [System.IO.Directory]::CreateDirectory($CacheDir)
                [System.IO.File]::WriteAllText($temp, "$($script:TabTitleCacheMark)`n$pathText`n$offset`n$custom`n$auto`n", $script:TabUtf8)
                [System.IO.File]::Move($temp, $cacheFile, $true)
            } catch {
                # Кэш не записался — не беда: следующий вызов прочитает ещё раз.
                try { [System.IO.File]::Delete($temp) } catch { }
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
# Пишет её только своя сессия — сторож доставки на начале сессии и на ходе человека, и объявление
# потока. Читают все показы. Лежит в общем служебном каталоге репозитория, как и доска: видна всем
# рабочим деревьям и переживает удаление дерева.
#
# На сессию до трёх файлов:
#   • `<номер>.json` — сама запись (номер, папка, дерево, стартовое дерево, название, вид);
#     переписывается только когда что-то из этого изменилось;
#   • `<номер>.prompt` — отметка: время её правки и есть время последнего сообщения человека, а
#     внутри — те же папка, дерево и название, чтобы на ходе человека сверить их без разбора JSON;
#   • `cache/<номер>.txt` — кэш названия (см. `Get-SessionTitle`).
# ─────────────────────────────────────────────────────────────────────────────────────────────

# Записи вкладок, уже прочитанные в этом запуске: показ спрашивает одну и ту же вкладку по разу на
# каждую строку.
$script:WaveBoardTabRecords = @{}

# Столько вкладка может молчать, прежде чем перечень вкладок уведёт её в хвост «давно молчат». Сутки,
# а не порог живости заявки: ночная пауза в разговоре вкладку из перечня не выбрасывает.
$script:TabSilentHours = 24

# Подхват заявки без сессии не случается, если в том же дереве за столько часов писал человек в
# ДРУГОЙ вкладке: две вкладки в дереве — неоднозначность, и выбирать за человека нельзя.
$script:TabAdoptRivalHours = 24

# Столько живёт запись вкладки без единого хода: дальше её убирает сторож на начале сессии. Месяц
# покрывает волну с запасом; вкладку, молчащую месяц, по названию уже не ищут.
$script:TabKeepDays = 30

# Столько живёт остаток временного файла (процесс убили между записью и переносом).
$script:TabTempKeepHours = 24

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
    # Время последнего сообщения человека берётся из отметки сессии (время её правки), если она есть:
    # запись вкладки на каждом ходу не переписывается. Нет отметки — поле самой записи.
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
        $mark = [System.IO.Path]::ChangeExtension($Path, '.prompt')
        if ([System.IO.File]::Exists($mark)) {
            $fields['prompt_at'] = [System.IO.File]::GetLastWriteTime($mark).ToString('s')
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
    param(
        [string]$Dir,
        [string]$SessionId,
        [string]$Cwd,
        [string]$Tree,
        # `Prompt` — ход человека (двигает время сообщения), `Start` — начало сессии (пишет стартовое
        # дерево), `Claim` — объявление потока. Начало сессии и объявление читают журнал всегда.
        [string]$Stage,
        # Начало сессии после сжатия контекста: стартовое дерево остаётся прежним — вкладка могла
        # перейти в чужое дерево, а сжатие стартом в нём не делает.
        [switch]$KeepStartTree,
        # Переписать запись, даже если ничего не изменилось (сторож только что сменил сессию заявки).
        [switch]$Force
    )
    # Запись своей вкладки: номер сессии, папка, корень дерева, стартовое дерево, название и его вид.
    #
    # ‼️ Самый частый вызов набора — ход человека. Там, в пределах двух минут от последнего чтения
    # журнала, работа — одно чтение маленькой отметки и одно касание её времени: название берётся из
    # отметки же, запись вкладки (JSON) не разбирается и не переписывается, если не изменились папка,
    # дерево или название. Сборка и запись — на месте, без помощников (см. шапку файла).
    #
    # Немая при любой неудаче: сорванная запись вкладки не повод мешать работе.
    if (-not $Dir -or -not $SessionId -or $SessionId -cnotmatch $script:TabSessionIdPattern) { return }
    $file = [System.IO.Path]::Combine($Dir, "$SessionId.json")
    $temp = "$file.tmp-$PID"
    try {
        $cacheDir = [System.IO.Path]::Combine($Dir, 'cache')
        $markFile = [System.IO.Path]::Combine($Dir, "$SessionId.prompt")
        $state = $null
        try { $state = [System.IO.File]::ReadAllLines($markFile) } catch { $state = $null }
        $stateOk = $state -and $state.Count -eq 5 -and $state[0] -eq $script:TabStateMark
        $cwdText = (([string]$Cwd -replace '\\', '/').TrimEnd('/')) -replace '[\x00-\x1f]', ' '
        $treeText = (([string]$Tree -replace '\\', '/').TrimEnd('/')) -replace '[\x00-\x1f]', ' '
        $title = $script:TabTitleNow[$SessionId]
        if (-not $title) {
            if ($Stage -eq 'Prompt' -and $stateOk -and
                ([datetime]::Now - [System.IO.File]::GetLastWriteTime([System.IO.Path]::Combine($cacheDir, "$SessionId.txt"))).TotalSeconds -lt $script:TabTitleRereadSeconds) {
                $title = @{ Title = $state[4]; Kind = $state[3] }
            } else {
                $title = Get-SessionTitle -SessionId $SessionId -CacheDir $cacheDir -Fresh:($Stage -ne 'Prompt')
            }
        }
        # Журнал на миг не прочитался — прежнее название лучше пустоты.
        if (-not $title.Kind -and $stateOk -and $state[3]) { $title = @{ Title = $state[4]; Kind = $state[3] } }
        if ($Stage -eq 'Prompt' -and -not $Force -and $stateOk -and $state[1] -ceq $cwdText -and $state[2] -ceq $treeText -and
            $state[3] -ceq [string]$title.Kind -and $state[4] -ceq [string]$title.Title) {
            # Ничего не изменилось: время сообщения — касанием отметки.
            [System.IO.File]::SetLastWriteTime($markFile, [datetime]::Now)
            return
        }
        $old = Read-TabRecordFile -Path $file
        if (-not $title.Kind -and $old -and $old.title) {
            $title = @{ Title = [string]$old.title; Kind = [string]$old.title_kind }
        }
        $now = (Get-Date).ToString('s')
        $startTree = if ($Stage -eq 'Start' -and -not ($KeepStartTree -and $old -and $old.start_tree)) {
            $treeText
        } elseif ($old) {
            [string]$old.start_tree
        } else {
            ''
        }
        # Время сообщения в самой записи — на момент её перезаписи; точное — у отметки.
        $promptAt = if ($Stage -eq 'Prompt') { $now } elseif ($old) { [string]$old.prompt_at } else { '' }
        $record = [ordered]@{
            session_id = $SessionId
            cwd        = $cwdText
            tree       = $treeText
            start_tree = $startTree
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
        [System.IO.File]::WriteAllText($temp, "{`n$($pairs -join ",`n")`n}`n", $script:TabUtf8)
        [System.IO.File]::Move($temp, $file, $true)
        $script:WaveBoardTabRecords["$Dir|$SessionId"] = $record
        # Отметка — ПОСЛЕ записи: сорвись запись, отметка останется прежней, и следующий ход увидит
        # расхождение и перепишет запись снова.
        $stateText = "$($script:TabStateMark)`n$cwdText`n$treeText`n$(([string]$title.Kind) -replace '[\x00-\x1f]', ' ')`n$(([string]$title.Title) -replace '[\x00-\x1f]', ' ')`n"
        if ($Stage -eq 'Prompt') {
            [System.IO.File]::WriteAllText($markFile, $stateText, $script:TabUtf8)
        } elseif ([System.IO.File]::Exists($markFile)) {
            # Начало сессии и объявление — не сообщение человека: содержимое отметки обновляем, а время
            # её правки возвращаем прежнее.
            $when = [System.IO.File]::GetLastWriteTimeUtc($markFile)
            [System.IO.File]::WriteAllText($markFile, $stateText, $script:TabUtf8)
            [System.IO.File]::SetLastWriteTimeUtc($markFile, $when)
        }
    } catch {
        try { [System.IO.File]::Delete($temp) } catch { }
        return
    }
}

function Remove-StaleTabRecords {
    param([string]$Dir)
    # Чистка файлов вкладок, молчащих дольше срока. Жизнь сессии — самая свежая правка любого её файла
    # (запись, отметка, кэш): живая вкладка касается отметки на каждом сообщении человека. Немая при
    # любой неудаче.
    #
    # ‼️ Удаление необратимо, поэтому удаляется только ОПОЗНАННОЕ своё:
    #   • каталог вкладок или кэша оказался ссылкой или соединением папок — чистки нет вовсе: за ним
    #     может лежать что угодно чужое;
    #   • запись `.json` — только если разбирается и её номер сессии равен имени файла;
    #   • кэш `.txt` и отметка `.prompt` — только если первая строка — наша метка;
    #   • остаток временного файла — только старше суток и с именем вида `<номер>.<наше расширение>.tmp-<число>`.
    # Чужой файл с простым именем (`package.json`, `notes.txt`) переживает чистку при любом возрасте.
    if (-not $Dir) { return }
    try {
        $cacheDir = [System.IO.Path]::Combine($Dir, 'cache')
        foreach ($folder in @($Dir, $cacheDir)) {
            $info = [System.IO.DirectoryInfo]::new($folder)
            if ($info.Exists -and (($info.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or $info.LinkTarget)) { return }
        }
        $now = Get-Date
        $deadline = $now.AddDays(-$script:TabKeepDays)
        $tempDeadline = $now.AddHours(-$script:TabTempKeepHours)
        $latest = @{}
        $candidates = [System.Collections.Generic.List[object]]::new()
        foreach ($place in @(@{ Folder = $Dir; Kinds = @('.json', '.prompt') }, @{ Folder = $cacheDir; Kinds = @('.txt') })) {
            $names = @()
            try { $names = @([System.IO.Directory]::GetFiles($place.Folder)) } catch { continue }
            foreach ($path in $names) {
                try {
                    $name = [System.IO.Path]::GetFileName($path)
                    $at = $name.IndexOf('.tmp-', [System.StringComparison]::Ordinal)
                    if ($at -gt 0) {
                        $base = $name.Substring(0, $at)
                        if ($name.Substring($at + 5) -cmatch '^[0-9]+\z' -and
                            [System.IO.Path]::GetExtension($base) -cin $place.Kinds -and
                            (Test-SessionId ([System.IO.Path]::GetFileNameWithoutExtension($base))) -and
                            [System.IO.File]::GetLastWriteTime($path) -lt $tempDeadline) {
                            [System.IO.File]::Delete($path)
                        }
                        continue
                    }
                    $kind = [System.IO.Path]::GetExtension($name)
                    if ($kind -cnotin $place.Kinds) { continue }
                    $session = [System.IO.Path]::GetFileNameWithoutExtension($name)
                    if (-not (Test-SessionId $session)) { continue }
                    $when = [System.IO.File]::GetLastWriteTime($path)
                    if (-not $latest.ContainsKey($session) -or $when -gt $latest[$session]) { $latest[$session] = $when }
                    $candidates.Add(@{ Path = $path; Session = $session; Kind = $kind })
                } catch {
                    continue
                }
            }
        }
        foreach ($candidate in $candidates) {
            if ($latest[$candidate.Session] -ge $deadline) { continue }
            try {
                $ours = $false
                if ($candidate.Kind -eq '.json') {
                    $record = Read-TabRecordFile -Path $candidate.Path
                    $ours = [bool]($record -and [string]$record.session_id -ceq $candidate.Session)
                } else {
                    $reader = [System.IO.StreamReader]::new($candidate.Path)
                    try { $first = $reader.ReadLine() } finally { $reader.Dispose() }
                    $expected = if ($candidate.Kind -eq '.txt') { $script:TabTitleCacheMark } else { $script:TabStateMark }
                    $ours = ($first -ceq $expected)
                }
                if ($ours) { [System.IO.File]::Delete($candidate.Path) }
            } catch {
                continue
            }
        }
    } catch {
        return
    }
}

function Format-TabTitle {
    param($Tab, [switch]$Past)
    # Один вид на все показы: «вкладка О1-3-1»; для придуманного автоматически — «вкладка без имени
    # (авто: …)»; для потока, который уже не ведут, — «вела вкладка …». Неизвестна — пусто: строку
    # показа не засоряем словом «неизвестно» на каждом потоке.
    #
    # ‼️ Название чистится ЗДЕСЬ, в момент печати, а не только при чтении журнала: запись вкладки
    # лежит в общем каталоге, и изготовленная руками или другой программой запись донесла бы перевод
    # строки и управляющие последовательности терминала до экрана и до контекста соседних вкладок.
    if (-not $Tab) { return '' }
    $name = Get-CleanTitle -Raw ([string]$Tab.title)
    if (-not $name) { return '' }
    $lead = if ($Past) { 'вела вкладка' } else { 'вкладка' }
    if ([string]$Tab.title_kind -eq 'custom') { return "$lead $name" }
    return "$lead без имени (авто: $name)"
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
    # Связанное рабочее дерево git, а не главная папка репозитория. Спрашиваем диск, а не git: лишний
    # запуск git стоил бы десятков миллисекунд.
    #
    # Признак — файл `commondir` в каталоге, на который указывает `gitdir:` из файла `.git`. Он есть
    # только у связанных деревьев. Прежний признак (путь оканчивается на `/worktrees/<имя>`) ложно
    # срабатывал на подмодуле, лежащем по пути `worktrees/<имя>`, и на главной папке с вынесенным
    # каталогом git — а там живёт много вкладок, и первая написавшая вписала бы себя в чужой поток.
    if (-not $TreePath) { return $false }
    try {
        # У главной папки `.git` — каталог: чтение как файла срывается, и это «нет».
        $text = [System.IO.File]::ReadAllText([System.IO.Path]::Combine($TreePath, '.git'))
        $found = [regex]::Match($text, '(?im)^gitdir:[ \t]*(\S[^\r\n]*?)[ \t\r]*$')
        if (-not $found.Success) { return $false }
        $gitDir = $found.Groups[1].Value
        if (-not [System.IO.Path]::IsPathRooted($gitDir)) { $gitDir = [System.IO.Path]::Combine($TreePath, $gitDir) }
        return [System.IO.File]::Exists([System.IO.Path]::Combine($gitDir, 'commondir'))
    } catch {
        return $false
    }
}

function Get-TabFolderKey {
    param([string]$Path)
    # Папка в одном виде для сравнения — тот же приём, что у ключа папки заявок: слэши вперёд, без
    # хвостового, без разницы в регистре. Своя копия, потому что этот файл ни от чего в комплекте не
    # зависит.
    return (([string]$Path -replace '\\', '/').TrimEnd('/')).ToLowerInvariant()
}

function Get-TabOwnTitle {
    param([string]$Dir, [string]$SessionId, [string]$Stage)
    # Название своей вкладки для решения о переносе заявки. На ходе человека — из отметки сессии
    # (свежее двух минут), на начале сессии — из журнала. Прочитанное из журнала запоминается: запись
    # вкладки в конце хода его не перечитывает.
    if ($script:TabTitleNow.ContainsKey($SessionId)) { return $script:TabTitleNow[$SessionId] }
    if ($Stage -eq 'Prompt') {
        try {
            $state = [System.IO.File]::ReadAllLines([System.IO.Path]::Combine($Dir, "$SessionId.prompt"))
            if ($state.Count -eq 5 -and $state[0] -eq $script:TabStateMark -and $state[3]) {
                return @{ Title = $state[4]; Kind = $state[3] }
            }
        } catch {
            # Отметки ещё нет — первый ход сессии: читаем журнал.
        }
    }
    $title = Get-SessionTitle -SessionId $SessionId -CacheDir ([System.IO.Path]::Combine($Dir, 'cache')) -Fresh:($Stage -ne 'Prompt')
    $script:TabTitleNow[$SessionId] = $title
    return $title
}

function Test-TabMayAdoptClaim {
    param([string]$Dir, [string]$SessionId, [string]$Tree)
    # Можно ли этой сессии вписать себя в заявку дерева, поданную без сессии.
    #
    #   • Стартовое дерево сессии известно — оно обязано быть ЭТИМ деревом: вкладка, начатая в главной
    #     папке и зашедшая в чужое дерево посмотреть, чужой поток себе не вписывает.
    #   • Нет ДРУГОЙ вкладки этого дерева, где человек писал за последние сутки: две вкладки в дереве —
    #     неоднозначность, и первая написавшая назвала бы себя ведущей навсегда.
    # Стартового дерева нет (сессия началась до обновления) — решает только второе условие.
    $treeKey = Get-TabFolderKey -Path $Tree
    if (-not $treeKey) { return $false }
    $own = Get-TabRecord -Dir $Dir -SessionId $SessionId
    $startTree = if ($own) { [string]$own.start_tree } else { '' }
    if ($startTree -and (Get-TabFolderKey -Path $startTree) -ne $treeKey) { return $false }
    $since = (Get-Date).AddHours(-$script:TabAdoptRivalHours)
    foreach ($tab in (Get-TabRecords -Dir $Dir)) {
        if ([string]$tab.session_id -ceq $SessionId) { continue }
        if ((Get-TabFolderKey -Path ([string]$tab.tree)) -ne $treeKey) { continue }
        $moment = [datetime]::MinValue
        if ([string]$tab.prompt_at -and [datetime]::TryParse([string]$tab.prompt_at, [ref]$moment) -and $moment -ge $since) {
            return $false
        }
    }
    return $true
}

function Test-TabIsSameNamedTab {
    param([string]$Dir, [string]$SessionId, [string]$HeldSession, [string]$Tree, [string]$Stage)
    # Та же ли это вкладка, что записана в заявке, только с новым номером сессии.
    #
    # Очистка контекста и возобновление с ответвлением дают вкладке новый номер, а имя, данное
    # человеком, переносится. Признак один: у записанной сессии то же дерево, и у обеих — имя от
    # человека, непустое и совпадающее без учёта регистра. ‼️ Безымянные и названные по-разному НЕ
    # совпадают никогда: иначе две вкладки в одном дереве перетягивали бы заявку на каждом сообщении.
    $held = Get-TabRecord -Dir $Dir -SessionId $HeldSession
    if (-not $held -or [string]$held.title_kind -ne 'custom') { return $false }
    if ((Get-TabFolderKey -Path ([string]$held.tree)) -ne (Get-TabFolderKey -Path $Tree)) { return $false }
    $heldName = (Get-CleanTitle -Raw ([string]$held.title)).ToLowerInvariant()
    if (-not $heldName) { return $false }
    $mine = Get-TabOwnTitle -Dir $Dir -SessionId $SessionId -Stage $Stage
    if ([string]$mine.Kind -ne 'custom') { return $false }
    return ((Get-CleanTitle -Raw ([string]$mine.Title)).ToLowerInvariant() -ceq $heldName)
}

function Update-ClaimSessionFromTab {
    param($Claim, [string]$Session, [string]$Stage, [string]$TabsDir, [string]$Tree)
    # Какая сессия ведёт заявку, если объявление её не назвало или назвало прежний номер вкладки.
    # Правит разобранную заявку на месте и отвечает, изменил ли её; записывает зовущий (отметка живости).
    # Закрытость заявки проверяет зовущий — сюда приходят только открытые и не перенесённые.
    #
    #   • Подхват: в заявке сессии нет. Только на ходе человека, только в связанном рабочем дереве и
    #     только если сессия вправе (`Test-TabMayAdoptClaim`). Помечается `session_adopted`: это
    #     догадка сторожа, а не объявление, и показ говорит об этом; объявление пометку снимает.
    #   • Перенос: в заявке ДРУГАЯ сессия той же вкладки (`Test-TabIsSameNamedTab`) — на начале сессии
    #     и на ходе человека, в связанном дереве. Тоже помечается как сделанное сторожем.
    #
    # ‼️ Название вкладки — подсказка человеку, куда идти, а не доказательство: адресацию и доставку
    # номер сессии не меняет нигде.
    if (-not $Claim -or -not $TabsDir -or -not (Test-SessionId $Session)) { return $false }
    if ($Stage -ne 'Start' -and $Stage -ne 'Prompt') { return $false }
    $held = [string]$Claim.session_id
    if ($held -ceq $Session) { return $false }
    if (-not $held) {
        if ($Stage -ne 'Prompt') { return $false }
        if (-not (Test-LinkedWorktree -TreePath $Tree)) { return $false }
        if (-not (Test-TabMayAdoptClaim -Dir $TabsDir -SessionId $Session -Tree $Tree)) { return $false }
    } else {
        if (-not (Test-SessionId $held)) { return $false }
        if (-not (Test-LinkedWorktree -TreePath $Tree)) { return $false }
        if (-not (Test-TabIsSameNamedTab -Dir $TabsDir -SessionId $Session -HeldSession $held -Tree $Tree -Stage $Stage)) { return $false }
    }
    $Claim | Add-Member -NotePropertyName session_id -NotePropertyValue $Session -Force
    $Claim | Add-Member -NotePropertyName session_adopted -NotePropertyValue $true -Force
    return $true
}
