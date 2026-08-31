#Requires -Version 7
<#
Общая часть доски волны: где она лежит, как читается, кому адресована запись и чья вкладка жива.

Отдельным файлом, потому что потребителей два и они обязаны понимать доску ОДИНАКОВО: инструмент
(`../wave-board.ps1`, им кладут и закрывают записи) и сторож доставки
(`../hooks/wave-board-deliver.ps1`, он приносит записи во вкладку). Разъедься у них правило
адресации — находка молча не дойдёт, а выглядеть это будет как «соседу нечего сказать».

Сам по себе файл ничего не делает: только объявляет функции.
#>

# ‼️ Снимаем с окружения git-переменные ДО первого обращения к git. Иначе с заданным снаружи GIT_DIR
# доска уезжает в ЧУЖОЙ репозиторий (общий каталог спрашивают у git, и он отвечает заданным), имя
# ветки приходит от чужого дерева, а список живых вкладок — чужой целиком. Всё это молча: положивший
# уверен, что находку доставил, а соседу «нечего сказать» — то самое, ради чего доска и заведена.
# Разбор ловушки целиком — в подключаемом файле.
. (Join-Path $PSScriptRoot 'git-env-clean.ps1')

function Get-BoardPath {
    param([string]$Override)
    if ($Override) { return $Override }
    # Общий каталог, а не свой у рабочего дерева: он один на все деревья, лежит вне веток (в чужую
    # заявку запись не попадёт) и переживает удаление дерева вместе с закрытой вкладкой.
    # Из главной папки git отвечает относительным путём, из рабочего дерева — абсолютным.
    $common = (& git rev-parse --git-common-dir 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $common) { throw 'не репозиторий git — доске волны негде лежать' }
    $common = $common.Trim()
    if (-not [System.IO.Path]::IsPathRooted($common)) { $common = Join-Path $PWD $common }
    return Join-Path (Resolve-Path $common).Path 'wave-board/board.jsonl'
}

function Get-StreamKey {
    param([string]$Raw)
    # Один поток зовут тремя способами: веткой (`feat/wave3-plan-clock`), папкой рабочего дерева
    # (`wave3-plan-clock`) и папкой, где косая черта ветки заменена плюсом
    # (`feat+wave4-measure-and-accept`). Сводим к одному ключу, иначе находка, адресованная веткой,
    # не найдёт вкладку, которая знает себя по имени папки.
    if (-not $Raw) { return '' }
    $key = ($Raw -replace '\\', '/') -replace '\+', '/'
    $key = $key.Split('/')[-1]
    $key = $key -replace '^worktree-', ''
    return $key.Trim().ToLowerInvariant()
}

function Get-StreamKeys {
    param([string]$Raw)
    # Все ключи названного потока. Имя ветки и имя папки сводятся к одному ключу почти всегда, но
    # НЕ обязаны: дерево `oddfolder-tab` с веткой `feat/oddbranch-tab` даёт два разных. Закрытие
    # пишет один из них, а показ спрашивают другим — и он не узнаёт собственное закрытие потока.
    $key = Get-StreamKey -Raw $Raw
    $keys = [System.Collections.Generic.List[string]]::new()
    if ($key) { $keys.Add($key) }
    foreach ($tree in (Get-Worktrees)) {
        $branchKey = Get-StreamKey -Raw $tree.branch
        $folderKey = Get-StreamKey -Raw $tree.path
        if ($key -ne $branchKey -and $key -ne $folderKey) { continue }
        if ($branchKey) { $keys.Add($branchKey) }
        if ($folderKey) { $keys.Add($folderKey) }
    }
    return @($keys | Where-Object { $_ } | Select-Object -Unique)
}

function Get-CurrentKeys {
    # Ключей у вкладки два: по ветке и по имени рабочей папки. Совпало любое — находка её.
    #
    # Спрашиваем git ОДИН раз на запуск, как и про список деревьев. Сторож доставки зовётся на
    # каждом сообщении пользователя, а имена потока нужны ему дважды — при разборе реестра и при
    # отборе своих записей; второй запуск git был платой ни за что.
    if ($null -ne $script:WaveBoardCurrentKeys) { return $script:WaveBoardCurrentKeys }
    $keys = [System.Collections.Generic.List[string]]::new()
    try {
        $branch = (& git rev-parse --abbrev-ref HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $branch) { $keys.Add((Get-StreamKey -Raw $branch.Trim())) }
    } catch {
        # Открепление от ветки или сорванный git — второго ключа хватит.
    }
    $keys.Add((Get-StreamKey -Raw (Split-Path -Leaf $PWD)))
    $script:WaveBoardCurrentKeys = @($keys | Where-Object { $_ } | Select-Object -Unique)
    return $script:WaveBoardCurrentKeys
}

function Get-FailureReason {
    param($Failure)
    # Причину читает человек, а системные сообщения приходят на языке системы — здесь по-английски.
    # Частый случай (файл кто-то держит) переводим сами; остальное отдаём как есть, но помечаем
    # «системное сообщение», чтобы чужую формулировку не приняли за нашу.
    $message = "$($Failure.Exception.Message)".Trim()
    if ($message -match 'being used by another process' -or $message -match 'используется другим процессом') {
        return 'файл занят другим процессом'
    }
    if ($message -match 'Access to the path .* is denied' -or $message -match 'Отказано в доступе') {
        return 'нет доступа к файлу'
    }
    return "системное сообщение: $message"
}

function Add-BoardLine {
    param([string]$Path, [string]$Line)
    # ‼️ Путь режем строкой, а каталог заводим внутри перехвата. Разбор пути средствами оболочки
    # спрашивает у неё про диск и на несуществующем диске (или отвалившейся сетевой шаре)
    # срывается насмерть — наружу выходило сырое английское системное сообщение вместо нашего
    # отказа. В объявлении это вылечено, а приём находки падал ровно так же (воспроизведено).
    $dir = [System.IO.Path]::GetDirectoryName($Path)
    try {
        if ($dir -and -not (Test-Path -LiteralPath $dir -PathType Container)) {
            New-Item -ItemType Directory -Path $dir -Force -ErrorAction Stop | Out-Null
        }
    } catch {
        # Молчим: настоящую причину назовёт попытка записи ниже, и назовёт по-русски.
    }
    # Только дописывание, и с повтором: доску пишут несколько вкладок разом. Переписывание файла
    # целиком теряло бы чужую строку, а занятость файла соседом длится доли секунды.
    $reason = 'причина неизвестна'
    for ($try = 1; $try -le 10; $try++) {
        try {
            # Открываем на чтение-запись, а не на дописывание: перед своей строкой надо посмотреть,
            # чем кончается файл. Оборванная запись (вкладку закрыли на полуслове, диск кончился)
            # остаётся без перевода строки, и приклеенная к ней новая запись губит ОБЕ — не
            # разбирается ни та, ни другая, а инструмент рапортует об успехе.
            $stream = [System.IO.File]::Open($Path, 'OpenOrCreate', 'ReadWrite', 'Read')
            try {
                $prefix = ''
                if ($stream.Length -gt 0) {
                    $stream.Position = $stream.Length - 1
                    if ($stream.ReadByte() -ne 10) { $prefix = "`n" }
                }
                $stream.Position = $stream.Length
                $bytes = [System.Text.Encoding]::UTF8.GetBytes($prefix + $Line + "`n")
                $stream.Write($bytes, 0, $bytes.Length)
            } finally {
                $stream.Dispose()
            }
            return
        } catch {
            # Настоящую причину сохраняем: после десяти попыток «доска занята» может оказаться
            # неправдой — место на диске, права, съёмный диск отвалился лечат по-разному.
            $reason = Get-FailureReason -Failure $_
            Start-Sleep -Milliseconds 50
        }
    }
    throw "не удалось дописать на доску ($Path). Последняя причина: $reason"
}

function Read-BoardContent {
    param([string]$Path)
    # Чтение доски, которое УМЕЕТ СООБЩИТЬ О НЕУДАЧЕ. «Не смогли прочитать» и «доска пуста» — вещи
    # противоположные, а выглядят одинаково: пустой список. На этой разнице стоит уплотнение: приняв
    # занятый файл за пустую доску, оно заменило бы её пустым файлом и стёрло все открытые находки.
    # Держат доску на доли секунды не только соседние вкладки — ещё антивирус, служба индексации и
    # резервное копирование, и в эти доли секунды размер файла как раз НЕ меняется.
    # ‼️ «Доски нет» узнаём ПО ОТКАЗУ ОТКРЫТЬ, а не отдельной проверкой существования. Проверка
    # существования отвечает «нет» и там, где на самом деле «не видно»: несуществующий диск,
    # отвалившаяся сетевая шара, каталог с закрытым заходом. Пустая доска и невидимая доска — вещи
    # противоположные, а выглядели одинаково; на сдаче потока это значит «ящик пуст, сдавайтесь»
    # там, где ящик на самом деле не прочитан.
    $reason = ''
    for ($try = 1; $try -le 5; $try++) {
        try {
            # Делимся файлом на чтение и запись: сосед в этот момент может дописывать свою строку.
            $stream = [System.IO.File]::Open($Path, 'Open', 'Read', 'ReadWrite')
            try {
                $length = $stream.Length
                $reader = [System.IO.StreamReader]::new($stream, [System.Text.Encoding]::UTF8)
                $lines = @(($reader.ReadToEnd() -split "`r?`n") | Where-Object { $_.Trim() })
                return [pscustomobject]@{
                    Ok = $true; Missing = $false; Lines = $lines; Length = $length; Reason = ''
                }
            } finally {
                $stream.Dispose()
            }
        } catch {
            if (Test-MissingPathFailure -Failure $_) {
                return [pscustomobject]@{
                    Ok = $true; Missing = $true; Lines = @(); Length = 0; Reason = ''
                }
            }
            # Причину держим настоящую: «занята» и «нет прав» лечат по-разному.
            $reason = Get-FailureReason -Failure $_
            Start-Sleep -Milliseconds 30
        }
    }
    return [pscustomobject]@{ Ok = $false; Missing = $false; Lines = @(); Length = -1; Reason = $reason }
}

function Read-BoardLines {
    param([string]$Path)
    # Мягкий вид для тех, кому важнее не сорваться, чем узнать правду: сторож доставки при занятой
    # доске обязан промолчать, а не мешать работе. Всем остальным — Read-BoardContent.
    return @((Read-BoardContent -Path $Path).Lines)
}

function Get-BoardEntries {
    param([string[]]$Lines)
    # Пара «исходная строка + разобранная запись». Строка нужна уплотнению: оно переписывает доску
    # ТЕКСТОМ уцелевших строк, а не пересобранными записями, — обратная сборка тихо меняла бы вид
    # полей (то же время суток вернулось бы в другом написании), и доска расходилась бы сама с собой.
    $entries = [System.Collections.Generic.List[object]]::new()
    foreach ($line in $Lines) {
        $record = $null
        try { $record = $line | ConvertFrom-Json } catch { continue }
        if (-not $record.id) { continue }
        $entries.Add([pscustomobject]@{ Line = $line; Record = $record })
    }
    return @($entries)
}

function Get-BoardClosings {
    param($Entries)
    # Закрытие бывает двух видов, и путать их нельзя.
    #
    # Адресная находка закрывается ОБЩЕ: её учёл тот единственный, кому она адресована, — вопрос
    # исчерпан. Широковещательная (`-To *`) адресована многим, и закрытие у неё ПЕРСОНАЛЬНОЕ: строка
    # несёт ключ закрывшего потока и гасит запись только для него. Общее закрытие такой находки
    # прятало её от всех разом: первый же учтивший лишал её остальных, а поток, у которого с момента
    # добавления не было ни хода, ни перезапуска, не видел её никогда.
    #
    # Закрытие — отдельная строка ниже по файлу, поэтому сперва собираем всё, потом отсеиваем.
    $global = [System.Collections.Generic.HashSet[string]]::new()
    $by = @{}
    foreach ($entry in $Entries) {
        if (-not $entry.Record.done) { continue }
        $id = [string]$entry.Record.id
        $who = Get-StreamKey -Raw ([string]$entry.Record.by)
        if (-not $who) { [void]$global.Add($id); continue }
        if (-not $by.ContainsKey($id)) { $by[$id] = [System.Collections.Generic.List[string]]::new() }
        if ($who -notin $by[$id]) { $by[$id].Add($who) }
    }
    return [pscustomobject]@{ Global = $global; By = $by }
}

function Get-BroadcastLifetimeDays {
    # Срок давности записи «всем». Волна живёт недели: находка, не учтённая за две недели, устарела
    # вместе с волной. Платят же за неё контекстом ВСЕ вкладки проекта — включая деревья, заведённые
    # позже и к той волне отношения не имеющие. Это предохранитель на случай, что общее закрытие
    # забыли: у адресной записи выход есть всегда (её закрытие общее), у широковещательной — нет.
    return 14
}

function Get-BroadcastAgeState {
    param($Record)
    # Возраст записи «всем»: `live` — живая, `stale` — просрочена, `broken` — дату не разобрать.
    #
    # Срок давности касается записей «всем» и уведомлений «учтено»: у обычной адресной находки
    # закрытие общее, и заглушать её молча нельзя — её просто ещё не учли. Уведомление же гасится
    # само при показе, и срок ему нужен на случай, когда автор к своей вкладке уже не вернулся:
    # иначе оно лежало бы на доске вечно.
    # Оба широковещательных адреса, а не только одиночная звёздочка: у записи «всем вкладкам
    # проекта» дыра была бы шире — она приходит КАЖДОМУ новому рабочему дереву, включая заведённые
    # под другие волны, и без срока давности жила бы вечно.
    if ((Get-StreamKey -Raw ([string]$Record.to)) -notin @('*', '**') -and [string]$Record.kind -ne 'ack') {
        return 'live'
    }
    $raw = $Record.at
    if ($raw -is [datetime]) {
        return $(if ($raw -lt (Get-Date).AddDays(-(Get-BroadcastLifetimeDays))) { 'stale' } else { 'live' })
    }
    $text = [string]$raw
    $when = [datetime]::MinValue
    # Дату не разобрать (строку правили руками, принесла другая версия, поле пустое или числовое) —
    # считаем запись просроченной, а не бессрочной. Прежняя мягкость возвращала в узком случае ровно
    # ту дыру, ради которой срок давности и заводился: такая запись доставлялась каждому новому
    # дереву и переживала уплотнение. Сторона безопасная: находка с испорченной датой к учёту всё
    # равно не годится, а показ о ней скажет отдельно — молча она не пропадёт.
    if (-not $text -or -not [datetime]::TryParse($text, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$when)) {
        return 'broken'
    }
    return $(if ($when -lt (Get-Date).AddDays(-(Get-BroadcastLifetimeDays))) { 'stale' } else { 'live' })
}

function Get-BoardStates {
    param($Entries, [string[]]$Viewer)
    # Раскладывает записи по состояниям: открыта, закрыта у смотрящего, просрочена, с испорченной
    # датой. Показ обязан их различать — иначе непонятно, почему запись лежит в файле, но нигде не
    # видна, и человек идёт уплотнять доску или заводить дубль находки.
    $closings = Get-BoardClosings -Entries $Entries
    $mine = @($Viewer | Where-Object { $_ })
    $open = [System.Collections.Generic.List[object]]::new()
    $closedForViewer = [System.Collections.Generic.List[object]]::new()
    $stale = [System.Collections.Generic.List[object]]::new()
    $broken = [System.Collections.Generic.List[object]]::new()
    foreach ($entry in $Entries) {
        $record = $entry.Record
        if ($record.done -or -not $record.title) { continue }
        $id = [string]$record.id
        # Общее закрытие снимает запись у всех — и адресную, и ту, что закрыли ключом -ForAll.
        if ($closings.Global.Contains($id)) { continue }
        # Без switch намеренно: его `continue` переходит к следующему условию switch, а не к
        # следующей записи цикла, и запись попала бы разом в два состояния.
        $age = Get-BroadcastAgeState -Record $record
        if ($age -eq 'stale') { $stale.Add($entry); continue }
        if ($age -eq 'broken') { $broken.Add($entry); continue }
        $seen = $closings.By[$id]
        if ($mine.Count -gt 0 -and $seen -and @($seen | Where-Object { $_ -in $mine }).Count -gt 0) {
            $closedForViewer.Add($entry)
            continue
        }
        $open.Add($entry)
    }
    return [pscustomobject]@{
        Open            = @($open)
        ClosedForViewer = @($closedForViewer)
        Stale           = @($stale)
        Broken          = @($broken)
        Closings        = $closings
    }
}

function Select-OpenEntries {
    param($Entries, [string[]]$Viewer)
    # Без `-Viewer` — всё, что открыто хоть для кого-то (показ доски целиком, уплотнение). С ним —
    # то, что открыто ИМЕННО ДЛЯ ЭТОГО потока: свои персональные закрытия он больше не видит.
    return @((Get-BoardStates -Entries $Entries -Viewer $Viewer).Open)
}

function Select-KeepEntries {
    param($Entries)
    # Что переживает уплотнение: записи, открытые хоть для кого-то, и ИМЕННЫЕ закрытия этих записей
    # (без них поток, уже учтивший находку «всем», получит её заново). Всё остальное — закрытые
    # общей строкой, просроченные, их закрытия и нечитаемые обрывки — уходит.
    $liveIds = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($entry in (Get-BoardStates -Entries $Entries).Open) {
        [void]$liveIds.Add([string]$entry.Record.id)
    }
    return @($Entries | Where-Object {
            $id = [string]$_.Record.id
            if ($_.Record.done) { $_.Record.by -and $liveIds.Contains($id) } else { $liveIds.Contains($id) }
        })
}

function Get-OpenRecords {
    param([string]$Path, [string[]]$Viewer)
    $entries = Get-BoardEntries -Lines (Read-BoardLines -Path $Path)
    return @(Select-OpenEntries -Entries $entries -Viewer $Viewer | ForEach-Object { $_.Record })
}

function Compress-Board {
    param([string]$Path)
    # Уплотнение: на доске остаются только открытые записи. Закрытая запись иначе лежит строкой
    # вечно, а разбирают доску целиком на каждом ходу каждой вкладки.
    #
    # Работа опасная — она ПЕРЕПИСЫВАЕТ доску целиком, поэтому каждый шаг сомневается: не сумели
    # прочитать — не трогаем, прочитали непустой файл и не разобрали ни одной записи — не трогаем.
    # Ошибка здесь стоит всех открытых находок сразу, а выглядит как бодрый рапорт об успехе.
    if (-not (Test-Path $Path)) { return [pscustomobject]@{ Before = 0; After = 0; Unreadable = 0 } }
    $reason = 'причина неизвестна'
    for ($try = 1; $try -le 10; $try++) {
        $content = Read-BoardContent -Path $Path
        if (-not $content.Ok) {
            throw "доску не удалось прочитать, уплотнять вслепую нельзя ($Path). Последняя причина: $($content.Reason)"
        }
        $entries = Get-BoardEntries -Lines $content.Lines
        if ($content.Length -gt 0 -and $entries.Count -eq 0) {
            throw "в доске $($content.Length) байт, а разобрать не удалось ни одной записи ($Path) — уплотнение стёрло бы её содержимое; разберитесь с файлом руками"
        }
        # Отбор один на уплотнение и на показ: иначе показ обещал бы убрать не то, что уберётся.
        $keep = @(Select-KeepEntries -Entries $entries | ForEach-Object { $_.Line })
        # Нечитаемые строки уплотнение выбрасывает молча — а это обрывок чьей-то находки. Считаем и
        # называем их в отчёте: стёртое молча неотличимо от того, чего не было.
        $unreadable = $content.Lines.Count - $entries.Count
        # Временный файл рядом с доской: замена в пределах тома идёт одним действием, и оборванного
        # полуфайла на месте доски не остаётся, чем бы работа ни кончилась.
        $temp = "$Path.compact-$PID-$(Get-Random).tmp"
        $text = if ($keep.Count -gt 0) { ($keep -join "`n") + "`n" } else { '' }
        [System.IO.File]::WriteAllText($temp, $text, [System.Text.UTF8Encoding]::new($false))
        try {
            # Размер сверяем с тем, что был у прочитанного файла: сосед мог дописать строку, пока мы
            # переписывали, — тогда заходим заново, иначе его строка пропала бы.
            if ((Get-Item $Path).Length -ne $content.Length) { throw 'доску дописали, пока мы её переписывали' }
            [System.IO.File]::Move($temp, $Path, $true)
            return [pscustomobject]@{ Before = $content.Lines.Count; After = $keep.Count; Unreadable = $unreadable }
        } catch {
            # Причина той же выделки, что и везде: человеку — по-русски, чужой текст — с пометкой.
            $reason = Get-FailureReason -Failure $_
            Remove-Item $temp -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 50
        }
    }
    throw "уплотнить доску не удалось ($Path). Последняя причина: $reason"
}

function Get-AliveBeaconPath {
    param([string]$TreePath)
    # Маячок живой вкладки. Его обновляет сторож доставки в СВОЁМ рабочем дереве на каждом ходу, а
    # читают отсюда все остальные. Адрес объявлен один раз: разъедься писатель с читателем — живые
    # вкладки стали бы невидимыми, и выглядело бы это как «все потоки закрыты».
    return (Join-Path $TreePath '.claude/.cache/wave-board-alive.txt')
}

function Test-AliveBeacon {
    param([string]$TreePath)
    # Порог намеренно щедрый: маячок обновляется на ходу ПОЛЬЗОВАТЕЛЯ, а вкладка может молча
    # работать часами — длинный прогон субагентов, ожидание сборки, ночная пауза в разговоре.
    # Ошибка в сторону «жива» безопасна: находка ляжет на доску и дождётся вкладки. Ошибка в другую
    # сторону уводит находку в «Хвосты волны» мимо живого соседа — то есть мимо всего механизма.
    $aliveHours = 12
    $beacon = Get-AliveBeaconPath -TreePath $TreePath
    try {
        if (-not (Test-Path $beacon)) { return $false }
        return ((Get-Date) - (Get-Item $beacon).LastWriteTime).TotalHours -lt $aliveHours
    } catch {
        return $false
    }
}

function Get-Worktrees {
    # Рабочие деревья репозитория: путь, ветка и признак ЖИВОЙ вкладки (свежий маячок).
    #
    # Живость — не существование дерева: закрытая вкладка дерево за собой оставляет, и по нему
    # находке некуда прийти. Но и отсутствие маячка не значит «закрыта»: вкладка могла стартовать
    # до появления сторожа. Поэтому состояний три — «жива» (свежий маячок), «неизвестно» (дерево
    # есть, маячка нет) и «закрыта» (дерева нет вовсе), и говорить о них надо честно.
    #
    # Блокировка рабочего дерева как признак живости отвергнута: среда ставит её нерегулярно и
    # снимает — 21.08.2026 на 23 деревьях проекта не было НИ ОДНОЙ, включая дерево работавшей в тот
    # момент вкладки. Разбор — в реестре решений (platform-and-build.md).
    # Спрашиваем git ОДИН раз на запуск. Список нужен многим (имена потока, сверка адресата,
    # подсказки), внутри одного короткого запуска он не меняется, а без памятки набегал бы десяток
    # запусков git на каждый ход вкладки.
    if ($null -ne $script:WaveBoardWorktrees) { return $script:WaveBoardWorktrees }
    $lines = @()
    try {
        $lines = @(& git worktree list --porcelain 2>$null)
        if ($LASTEXITCODE -ne 0) { $script:WaveBoardWorktrees = @(); return @() }
    } catch {
        $script:WaveBoardWorktrees = @()
        return @()
    }
    $trees = [System.Collections.Generic.List[hashtable]]::new()
    $current = $null
    foreach ($line in $lines) {
        if ($line -like 'worktree *') {
            $current = @{
                path   = ($line.Substring(9) -replace '\\', '/').TrimEnd('/')
                branch = ''
                live   = $false
            }
            $trees.Add($current)
            continue
        }
        if (-not $current) { continue }
        if ($line -like 'branch *') { $current.branch = $line.Substring(7) -replace '^refs/heads/', '' }
    }
    foreach ($tree in $trees) { $tree.live = Test-AliveBeacon -TreePath $tree.path }
    $script:WaveBoardWorktrees = @($trees | ForEach-Object { [pscustomobject]$_ })
    return $script:WaveBoardWorktrees
}

function Get-KnownStreamKeys {
    param([switch]$AliveOnly)
    # Ключи заведённых деревьев — с ними сверяется адресат находки. Берём обе формы имени (ветку и
    # папку): вкладка знает себя по любой из них. `-AliveOnly` оставляет те, где вкладка недавно
    # отметилась, — это годится для подсказки, но НЕ для сверки адресата: положить находку впрок,
    # вкладке, которую поднимут через час, — обычное дело.
    $keys = [System.Collections.Generic.List[string]]::new()
    foreach ($tree in (Get-Worktrees)) {
        if ($AliveOnly -and -not $tree.live) { continue }
        if ($tree.branch) { $keys.Add((Get-StreamKey -Raw $tree.branch)) }
        $keys.Add((Get-StreamKey -Raw $tree.path))
    }
    return @($keys | Where-Object { $_ } | Select-Object -Unique)
}

function Select-ForStream {
    param($Records, [string[]]$Keys, $Claim)
    # Что из доски адресовано этой вкладке. Четыре вида адреса, и путать их нельзя:
    #   `**`          — всем вкладкам проекта, кроме положившей;
    #   `*`           — всем вкладкам СВОЕЙ волны (когда волна известна обеим сторонам), кроме
    #                   положившей: иначе запись уходит в два десятка деревьев, половина которых
    #                   заведена под другие волны и платит за неё контекстом ни за что;
    #   `волна/поток` — по заявке: это основной вид адреса, потому что так поток назван в плане;
    #   имя ветки или папки — запасной путь для потоков, которые не объявлялись.
    $mine = @($Keys)
    $myWave = if ($Claim) { Get-WaveKey -Raw ([string]$Claim.wave) } else { '' }
    $myStream = if ($Claim) { Get-StreamNumberKey -Raw ([string]$Claim.stream) } else { '' }
    return @($Records | Where-Object {
            $raw = [string]$_.to
            $to = Get-StreamKey -Raw $raw
            $fromMe = (Get-StreamKey -Raw ([string]$_.from)) -in $mine
            $address = Get-StreamAddress -Raw $raw
            if ($to -eq '**') {
                -not $fromMe
            } elseif ($to -eq '*') {
                $recordWave = Get-WaveKey -Raw ([string]$_.wave)
                # Волна не названа хоть у одной стороны — ведём себя как прежде и доставляем: молча
                # не доставить хуже, чем доставить лишнее.
                (-not $fromMe) -and (-not $recordWave -or -not $myWave -or $recordWave -eq $myWave)
            } elseif ($address) {
                [bool]$myWave -and $address.Wave -eq $myWave -and $address.Stream -eq $myStream
            } else {
                $to -in $mine
            }
        })
}

function Test-TaskInList {
    param([string]$Tasks, [string]$Task)
    # Входит ли задача в перечень потока. В плане их пишут как придётся: «10-13», «10, 11, 12»,
    # «6, 7 и 9», «1b». Отвечает на вопрос «чей это кусок», когда вкладку тянет взять соседнюю
    # задачу, а владелец за экраном не знает, что её планировали другому потоку.
    if (-not $Tasks -or -not $Task) { return $false }
    $wanted = Get-StreamNumberKey -Raw $Task
    if (-not $wanted) { return $false }
    foreach ($chunk in ($Tasks -split '[^\p{L}\p{Nd}\-–]+')) {
        $piece = $chunk.Trim()
        if (-not $piece) { continue }
        $range = [regex]::Match($piece, '^(\d+)\s*[-–]\s*(\d+)$')
        if ($range.Success -and $wanted -match '^\d+$') {
            $from = [int]$range.Groups[1].Value
            $till = [int]$range.Groups[2].Value
            if ([int]$wanted -ge [math]::Min($from, $till) -and [int]$wanted -le [math]::Max($from, $till)) {
                return $true
            }
            continue
        }
        if ((Get-StreamNumberKey -Raw $piece) -eq $wanted) { return $true }
    }
    return $false
}

function Format-BoardRecord {
    param($Record)
    $tail = if ($Record.where) { " — $($Record.where)" } else { '' }
    return "  • «$($Record.title)»$tail [метка $($Record.id)]"
}

# ─────────────────────────────────────────────────────────────────────────────────────────────
# Реестр заявок потоков: кто ведёт поток прямо сейчас.
#
# Доска отвечает на вопрос «что передали», реестр — на вопрос «кому и жив ли он». Без реестра
# адрес находки выводится из имени ветки или папки, а имена врут: в плане волны 6 у двух потоков
# объявлена одна ветка, а вкладки работают на других, и одну папку вкладка успела переиспользовать
# под другую задачу. Такой адрес доставляется молча и не тому.
#
# Устройство намеренно другое, чем у доски: файл на рабочее дерево, и пишет его ТОЛЬКО своя
# вкладка. Один писатель на файл — ни повторов, ни разбора всего журнала ради одного поля. Доска
# остаётся многописательным журналом, реестр — набором маленьких файлов рядом.
#
# ‼️ Замок всё же нужен, но не файлу заявки, а ВЫБОРУ НОМЕРА потока: номер выбирается по снимку
# всего реестра, и одновременно объявившиеся вкладки читают один и тот же снимок. Стережёт это
# `Enter-RegistryLock` — см. разбор сценария там же.
# ─────────────────────────────────────────────────────────────────────────────────────────────

function Get-AliveHours {
    # Порог свежести отметки — общий для маячка дерева и для заявки потока: разъедься они, одно и
    # то же дерево считалось бы живым в одном месте и молчащим в другом.
    return 12
}

function Get-SilentDaysBeforeStuck {
    # Столько молчит поток, прежде чем адресованная ему находка попадёт в сводку застрявшего.
    # Сутки — это не «вкладка закрылась», а «пора посмотреть глазами»: ночная пауза в разговоре
    # сюда уже не попадает, а брошенный поток попадает.
    return 1
}

function Get-RegistryDir {
    param([string]$BoardOverride)
    # Рядом с доской, в том же общем каталоге: те же три свойства (виден всем деревьям, вне веток,
    # переживает удаление дерева). Тесты подставляют свою доску — реестр уезжает за ней сам.
    #
    # ‼️ Отсекаем имя файла СТРОКОЙ, а не разбором пути через оболочку: разбор пути спрашивает у
    # оболочки про диск и на несуществующем диске срывается насмерть. Инструмент падал прямо здесь,
    # на первой же строке, и выносил наружу сырое английское системное сообщение — до честного
    # отказа про каталог заявок дело не доходило вовсе.
    $board = Get-BoardPath -Override $BoardOverride
    $parent = [System.IO.Path]::GetDirectoryName($board)
    if (-not $parent) { $parent = '.' }
    return [System.IO.Path]::Combine($parent, 'streams')
}

function Get-PathKey {
    param([string]$TreePath)
    # Имя файла заявки. Читаемая часть — имя папки дерева, хвост — отпечаток полного пути: две
    # папки с одинаковым именем в разных местах диска не должны затирать заявки друг друга.
    $normalized = ($TreePath -replace '\\', '/').TrimEnd('/').ToLowerInvariant()
    $leaf = ($normalized.Split('/')[-1] -replace '[^\p{L}\p{Nd}]+', '-').Trim('-')
    if (-not $leaf) { $leaf = 'tree' }
    $sha = [System.Security.Cryptography.SHA1]::Create()
    try {
        $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($normalized))
    } finally {
        $sha.Dispose()
    }
    $tail = -join ($bytes[0..3] | ForEach-Object { $_.ToString('x2') })
    return "$leaf-$tail"
}

function Get-ClaimPath {
    param([string]$Dir, [string]$TreePath)
    # ‼️ Складываем строкой. Сложение путей средствами оболочки спрашивает у неё про диск и на
    # несуществующем диске срывается насмерть — приём находки падал ровно здесь и выносил наружу
    # сырое английское системное сообщение вместо нашего отказа (воспроизведено).
    return [System.IO.Path]::Combine($Dir, (Get-PathKey -TreePath $TreePath) + '.json')
}

function Get-WaveKey {
    param([string]$Raw)
    # Ключ волны. Волну зовут и «wave6», и «6», и именем файла плана — сводим к одному виду, иначе
    # заявка, поданная одним написанием, не найдётся по другому.
    if (-not $Raw) { return '' }
    $text = $Raw.Trim().ToLowerInvariant()
    # Имя файла плана: берём из него маркер волны, если он там есть.
    $matched = [regex]::Match($text, 'wave\s*(\d+)')
    if ($matched.Success) { return "wave$($matched.Groups[1].Value)" }
    if ($text -match '^\d+$') { return "wave$text" }
    return ($text -replace '[^\p{L}\p{Nd}]+', '-').Trim('-')
}

function Get-StreamNumberKey {
    param([string]$Raw)
    # Ключ номера потока. В плане его пишут и «3», и «П3», и «п3», и «3b» — все они об одном.
    if (-not $Raw) { return '' }
    $text = $Raw.Trim().ToLowerInvariant() -replace '^[пp#№]\s*', ''
    return ($text -replace '[^\p{L}\p{Nd}]+', '')
}

# Имена волн, которые РЕАЛЬНО объявлены в реестре заявок. Нужны разбору адреса: волна зовётся не
# только «wave6» — там, где волн нет вовсе, её подставляет сама заявка, и зовут её датой или словом.
# Список приносит тот, кто уже прочитал реестр (инструмент, сторож доставки), — сам разбор в реестр
# не ходит: его зовут по разу на каждую запись доски, и чтение папки на каждый вызов было бы платой
# ни за что. Список пуст — разбор ведёт себя как прежде и понимает волну из плана и волну-дату.
$script:WaveBoardKnownWaves = @()

function Set-KnownWaves {
    param([string[]]$Keys)
    $script:WaveBoardKnownWaves = @($Keys | Where-Object { $_ } | Select-Object -Unique)
}

function Get-KnownWaves {
    return @($script:WaveBoardKnownWaves)
}

function Set-KnownWavesFromRegistry {
    param([string]$Dir)
    # Удобная форма для тех, у кого реестр под рукой. Молчит при любой неудаче: не разобранный
    # список волн — это адрес, который не разберётся, а не поломка механизма у всех остальных.
    try {
        Set-KnownWaves -Keys @((Get-Claims -Dir $Dir) | ForEach-Object { $_.WaveKey })
    } catch {
        Set-KnownWaves -Keys @()
    }
}

function Get-StreamAddress {
    param([string]$Raw)
    # Разбор адреса «волна/поток»: `wave6/3`, `6/3`, `wave6/П3`, `2026-08-24/2`, `sprint-alpha/1`.
    # Возвращает $null, если это не он.
    #
    # Спутать адрес с именем ветки (`feat/wave6-compute-channel`) нельзя, и держится эта защита на
    # ПРАВОЙ стороне: там обязан стоять номер потока, а не слово. Ни одно имя ветки под это не
    # подходит, поэтому левую сторону можно было расширить, не ослабив сверку.
    #
    # Слева волну зовут тремя способами, и все три обязаны разбираться:
    #   `wave6`, `6`     — волна из плана;
    #   `2026-08-24`     — волна, подставленная по дате там, где волн нет вовсе (заявок на неё может
    #                      ещё не быть: находку кладут и потоку, который откроют завтра);
    #   любое другое имя — только если такая волна РЕАЛЬНО объявлена в реестре (`Set-KnownWaves`).
    # Без этого адрес волны-даты не разбирался бы вовсе и находка соседу просто не уходила бы.
    #
    # Имя волны сверяется ЦЕЛИКОМ, а не приведённым ключом: иначе `wave6-compute/3` (имя папки, а не
    # адрес) сводилось бы к волне `wave6` и разбиралось бы как чужой поток.
    if (-not $Raw) { return $null }
    $parts = @($Raw.Trim() -split '/')
    if ($parts.Count -ne 2) { return $null }
    $left = $parts[0].Trim()
    $right = $parts[1].Trim()
    if ($right -notmatch '^[пp#№]?\s*\d+[a-zа-яё]?$') { return $null }
    $wave = Get-WaveKey -Raw $left
    $isPlanWave = $left -match '^(wave\s*)?\d+$'
    $isDateWave = $left -match '^\d{4}-\d{2}-\d{2}$'
    $isKnownWave = $wave -and $wave -eq $left.ToLowerInvariant() -and $wave -in (Get-KnownWaves)
    if (-not ($isPlanWave -or $isDateWave -or $isKnownWave)) { return $null }
    return [pscustomobject]@{
        Wave   = $wave
        Stream = Get-StreamNumberKey -Raw $right
    }
}

function Get-DateWaveKey {
    # Имя волны, подставленной самой: сегодняшняя дата. Вид `ГГГГ-ММ-ДД` выбран за три свойства —
    # он читается человеком, сортируется как дата и разбирается адресом (`2026-08-24/2`).
    return (Get-Date).ToString('yyyy-MM-dd')
}

function Get-SeenTime {
    param($Claim)
    # Отметка заявки датой. Неразобранную считаем самой старой: по ней нельзя решать, что свежее.
    $raw = $Claim.seen_at
    if ($raw -is [datetime]) { return $raw }
    $when = [datetime]::MinValue
    if ([datetime]::TryParse([string]$raw, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$when)) {
        return $when
    }
    return [datetime]::MinValue
}

function Get-AutoWaveKey {
    param($Claims)
    # К какой волне присоединиться вкладке, которая волну не назвала: к той, где ПРЯМО СЕЙЧАС идёт
    # работа. Иначе каждая вкладка заводила бы свою волну по дате, и соседи не видели бы друг друга.
    #
    # ‼️ Только волны, подставленные САМИ. У названной волны (`wave6`) номера потоков идут из плана:
    # присоединившись к ней, вкладка заняла бы чужой номер, и половина находок ушла бы не туда.
    $live = @($Claims | Where-Object { $_.Record.wave_auto -and $_.State -eq 'ведёт' })
    if ($live.Count -eq 0) { return '' }
    # Рядом может идти не одна работа — берём ту, где отметились последней.
    $freshest = @($live | Sort-Object -Property @{ Expression = { Get-SeenTime -Claim $_.Record } } -Descending)[0]
    return $freshest.WaveKey
}

function Get-NextStreamNumber {
    param($Claims, [string]$WaveKey, [string]$TreePath)
    # Следующий свободный номер потока в волне. Номер нужен всегда — им поток назван в адресе, — но
    # там, где плана нет, брать его человеку неоткуда: в плане он и не объявлялся.
    $here = ($TreePath -replace '\\', '/').TrimEnd('/')
    $inWave = @($Claims | Where-Object { $_.WaveKey -eq $WaveKey })
    # Своя прежняя заявка на эту волну — не сосед: повторное объявление той же вкладки обязано
    # остаться тем же потоком, иначе адрес, который она уже назвала соседям, менялся бы сам собой.
    $mine = @($inWave | Where-Object {
            (([string]$_.Record.worktree) -replace '\\', '/').TrimEnd('/') -eq $here -and $_.StreamKey
        })
    if ($mine.Count -gt 0) { return $mine[0].StreamKey }
    return (Get-FreeStreamNumber -Claims $Claims -WaveKey $WaveKey -TreePath $TreePath)
}

function Get-FreeStreamNumber {
    param($Claims, [string]$WaveKey, [string]$TreePath)
    # Следующий свободный номер в волне, СВОЮ заявку не считая. Отдельно от предыдущей функции
    # потому, что при споре за номер своя заявка уже лежит в реестре — и, посчитай мы её, вкладка
    # «уступала» бы номер сама себе и осталась бы на том же месте.
    #
    # Считаем от наибольшего занятого, а не от количества заявок: сданный поток номер не
    # освобождает (находки к нему адресованы этим номером), да и номера в плане идут не подряд.
    $here = ($TreePath -replace '\\', '/').TrimEnd('/')
    $used = 0
    foreach ($claim in @($Claims | Where-Object { $_.WaveKey -eq $WaveKey })) {
        if (((([string]$claim.Record.worktree) -replace '\\', '/').TrimEnd('/')) -eq $here) { continue }
        $digits = [regex]::Match([string]$claim.StreamKey, '^\d+')
        if (-not $digits.Success) { continue }
        $number = [int]$digits.Value
        if ($number -gt $used) { $used = $number }
    }
    return [string]($used + 1)
}

function Test-ClaimHasPlan {
    param($Claim)
    # Есть ли у потока план волны. От ответа зависит, куда инструмент посылает человека с находкой и
    # с итогом работы: в раздел плана или в ответ владельцу. Совет вписать строку в файл, которого
    # нет, — тупик: вкладка не может ни выполнить его, ни понять, что делать вместо него.
    #
    # Признак ровно один: подставлена ли волна САМА. Волну назвали или взяли из имени плана — план
    # есть, даже если файл плана в заявке не записан: команда объявления называет его не всегда.
    # Заявка старого вида признака не несёт — у неё волна названная, значит план есть.
    #
    # ‼️ Существование файла плана НЕ проверяем. Заявку читают из чужого рабочего дерева, а план
    # лежит в дереве потока: его отсутствие «здесь» не говорит о нём ничего, и прежняя проверка
    # объявляла бы бесплановыми ровно те потоки, чей план лежит в соседней папке.
    if (-not $Claim) { return $false }
    if (Test-ClaimHasField -Claim $Claim -Name 'wave_auto') { return -not $Claim.wave_auto }
    # Заявка прежней версии признака не несёт вовсе, и «нет признака — значит план есть» врало ей
    # ровно там, где она заводилась ВНЕ волны: такому потоку советовали вписать строку в раздел
    # плана, которого у него нет. Поэтому без признака судим по имени волны: волна плана зовётся
    # `waveN`, а дату или слово вкладка завела себе сама.
    return ((Get-WaveKey -Raw ([string]$Claim.wave)) -match '^wave\d+$')
}

function Test-ClaimHasField {
    param($Claim, [string]$Name)
    # Есть ли у заявки поле ВООБЩЕ. «Поля нет» и «поле опущено» приходится различать: заявка,
    # заведённая прежней версией, признака не несёт, и судить её надо по другому правилу.
    if (-not $Claim) { return $false }
    if ($Claim -is [System.Collections.IDictionary]) { return $Claim.Contains($Name) }
    return [bool]$Claim.PSObject.Properties[$Name]
}

function Get-ClaimOrder {
    param($Claim)
    # Ключ порядка среди заявок: время объявления, при равенстве — путь рабочего дерева. Оба поля
    # заявки не меняются никогда, поэтому порядок один и тот же у всех вкладок и не пляшет от
    # прогона к прогону. На нём стоят два решения: кто остаётся с номером потока при споре за него
    # и чья заявка завела волну.
    #
    # Время не разобрать — считаем заявку самой поздней: неизвестное не должно вытеснять известное.
    $when = [datetime]::MaxValue
    $raw = $Claim.claimed_at
    if ($raw -is [datetime]) {
        $when = $raw
    } else {
        $parsed = [datetime]::MinValue
        if ([string]$raw -and [datetime]::TryParse([string]$raw, [cultureinfo]::InvariantCulture,
                [System.Globalization.DateTimeStyles]::None, [ref]$parsed)) {
            $when = $parsed
        }
    }
    return [pscustomobject]@{
        When = $when
        Path = (([string]$Claim.worktree) -replace '\\', '/').TrimEnd('/').ToLowerInvariant()
    }
}

function Get-NumberRivals {
    param($Claims, [string]$WaveKey, [string]$StreamKey, [string]$TreePath)
    # Заявки ДРУГИХ деревьев на тот же адрес — ту же волну и тот же номер потока. Сданные не в
    # счёт: вкладки, которая вела поток, больше нет, и спорить не о чем.
    $here = ($TreePath -replace '\\', '/').TrimEnd('/')
    return @($Claims | Where-Object {
            $_.WaveKey -eq $WaveKey -and $_.StreamKey -eq $StreamKey -and $_.State -ne 'сдан' -and
            ((([string]$_.Record.worktree) -replace '\\', '/').TrimEnd('/')) -ne $here
        })
}

function Test-YieldsStreamNumber {
    param($Mine, $Rivals)
    # Кто из объявившихся одним номером уступает его. Остаётся с номером тот, кто объявился
    # раньше; при равном времени — тот, чей путь дерева меньше по алфавиту. Порядок полный и
    # неизменный, поэтому две вкладки не сдвигаются одновременно и не меняются номерами без конца.
    $me = Get-ClaimOrder -Claim $Mine
    foreach ($rival in $Rivals) {
        $other = Get-ClaimOrder -Claim $rival.Record
        if ($other.When -lt $me.When) { return $true }
        if ($other.When -eq $me.When -and [string]::CompareOrdinal($other.Path, $me.Path) -lt 0) {
            return $true
        }
    }
    return $false
}

function Test-WaveIsAuto {
    param($Claims, [string]$WaveKey)
    # Подставлена ли волна сама — то есть плана у неё нет.
    #
    # Решает ПЕРВАЯ заявка волны, а не любая из них: волну заводит тот, кто объявился раньше всех,
    # остальные к ней присоединяются. Прежнее «есть хоть одна подставленная» делало бесплановой
    # целую волну плана, стоило одной вкладке объявиться в ней без волны, — и тексты про разделы
    # плана пропадали у всех её потоков разом. Порядок берём тот же, что и при споре за номер
    # потока (время объявления, при равенстве — путь дерева): он не меняется от прогона к прогону,
    # и ответ у всех вкладок один.
    if (-not $WaveKey) { return $false }
    $inWave = @($Claims | Where-Object { $_.WaveKey -eq $WaveKey })
    if ($inWave.Count -gt 0) {
        $eldest = @($inWave | Sort-Object -Property @(
                @{ Expression = { (Get-ClaimOrder -Claim $_.Record).When } }
                @{ Expression = { (Get-ClaimOrder -Claim $_.Record).Path } }
            ))[0]
        return -not (Test-ClaimHasPlan -Claim $eldest.Record)
    }
    # Заявок такой волны нет вовсе — известно только имя. Имя-дата бывает только у подставленной.
    return [bool]($WaveKey -match '^\d{4}-\d{2}-\d{2}$')
}

function Test-AddresseeHasPlan {
    param($Claims, [string]$Raw, $Address, $Mine)
    # Есть ли план волны у ПОЛУЧАТЕЛЯ находки. Решение одно на все советы «положите в Хвосты волны»:
    # разъедься они по веткам — половина отказов посылала бы в раздел плана, которого нет.
    #
    # Порядок: заявка адресата (она знает про его план точно) → волна из адреса → своя волна. Не
    # знаем о получателе ничего — ведём себя как прежде и считаем, что план есть: посоветовать
    # раздел плана там, где он есть, безобиднее, чем промолчать о нём там, где он нужен.
    $found = @(Find-Claims -Claims $Claims -Raw $Raw)
    if ($found.Count -gt 0) {
        return (@($found | Where-Object { Test-ClaimHasPlan -Claim $_.Record }).Count -gt 0)
    }
    $waveKey = if ($Address) {
        [string]$Address.Wave
    } elseif ($Mine) {
        Get-WaveKey -Raw ([string]$Mine.wave)
    } else {
        ''
    }
    if (-not $waveKey) { return $true }
    return -not (Test-WaveIsAuto -Claims $Claims -WaveKey $waveKey)
}

function Get-ClaimReadTimeoutMs {
    param([switch]$Strict)
    # Сколько терпим, пока файл чужой заявки отпустят.
    #
    # Держат его на доли секунды не только соседние вкладки — ещё антивирус, служба поиска Windows,
    # резервное копирование и папка облачной синхронизации. Ровно так же и по той же причине читает
    # доску находок `Read-BoardContent`, и там повторы стоят с самого начала.
    #
    # Строгому читателю (выбор номера потока) отпущено на порядок больше: пропущенная заявка стоит
    # ему НАВСЕГДА совпавшего адреса, и лучше подождать две секунды. Терпимому (сторож доставки,
    # показ) столько ждать нельзя: он зовётся на каждом ходу вкладки и обязан быть быстрым.
    return $(if ($Strict) { 2500 } else { 150 })
}

function Test-MissingPathFailure {
    param($Failure)
    # Отличает «этого нет» от «не смог посмотреть». Первое — законный ответ, второе — беда, о
    # которой строгий читатель обязан сказать вслух.
    #
    # ‼️ Спрашивать об этом ИСКЛЮЧЕНИЕМ, а не отдельной проверкой существования пути. Проверка
    # существования отвечает «нет» и там, где на самом деле «не видно»: каталог, в который закрыт
    # заход, несуществующий диск, отвалившаяся сетевая шара. Такой ответ неотличим от честного
    # «заявок ещё нет» — и именно на нём стояли все найденные дыры этого класса.
    $inner = $Failure.Exception
    while ($inner.InnerException) { $inner = $inner.InnerException }
    return ($inner -is [System.IO.FileNotFoundException]) -or
        ($inner -is [System.IO.DirectoryNotFoundException])
}

function Read-ClaimRecord {
    param([string]$Path, [switch]$Strict)
    # Заявка соседа с ЧЕСТНЫМ различением исходов. Четыре состояния, и путать их нельзя:
    #   ok         — прочитали и разобрали;
    #   missing    — файла нет вовсе (дерево не объявлялось, заявку убрали);
    #   unreadable — файл ЕСТЬ, но прочитать не удалось;
    #   broken     — прочитали, но разобрать не смогли.
    #
    # ‼️ Прежде «не прочитали» и «файла нет» отвечали одинаково — пустотой, и с одной попытки.
    # Из-за этого сосед, чей файл в тот миг держал антивирус, становился НЕСУЩЕСТВУЮЩИМ: вторая
    # вкладка брала его номер, бодро рапортовала и не предупреждала ни словом, а круг разрешения
    # спора читал тот же испорченный снимок и соперника тоже не видел. Совпадение адресов
    # оставалось навсегда. Воспроизведено: файл заявки забрали на полторы секунды — и два потока
    # получили один номер.
    #
    # ‼️ «Файла нет» узнаём ПО ОТКАЗУ ОТКРЫТЬ, а не отдельной проверкой существования: та отвечает
    # «нет» и там, где просто не видно (см. `Test-MissingPathFailure`).
    $deadline = (Get-Date).AddMilliseconds((Get-ClaimReadTimeoutMs -Strict:$Strict))
    $reason = 'причина неизвестна'
    while ($true) {
        try {
            # Делимся файлом на чтение и запись: сосед в этот момент может обновлять свою отметку.
            $stream = [System.IO.File]::Open($Path, 'Open', 'Read', 'ReadWrite')
            $text = ''
            try {
                $reader = [System.IO.StreamReader]::new($stream, [System.Text.Encoding]::UTF8)
                $text = $reader.ReadToEnd()
            } finally {
                $stream.Dispose()
            }
            $record = $null
            try {
                $record = $text | ConvertFrom-Json
            } catch {
                # Разбор не удался — это НЕ повод повторять: испорченный файл испорчен насовсем.
                return [pscustomobject]@{
                    State = 'broken'; Record = $null; Reason = (Get-FailureReason -Failure $_)
                }
            }
            if (-not $record.worktree) {
                return [pscustomobject]@{
                    State = 'broken'; Record = $null; Reason = 'в заявке не названо рабочее дерево'
                }
            }
            return [pscustomobject]@{ State = 'ok'; Record = $record; Reason = '' }
        } catch {
            if (Test-MissingPathFailure -Failure $_) {
                return [pscustomobject]@{ State = 'missing'; Record = $null; Reason = '' }
            }
            # Настоящую причину сохраняем: «файл занят» и «нет прав» лечат по-разному.
            $reason = Get-FailureReason -Failure $_
        }
        if ((Get-Date) -ge $deadline) { break }
        Start-Sleep -Milliseconds 30
    }
    return [pscustomobject]@{ State = 'unreadable'; Record = $null; Reason = $reason }
}

function Read-ClaimFile {
    param([string]$Path)
    # Терпимый вид для тех, кому важнее не сорваться, чем узнать правду: своя заявка, отметка «на
    # ходу», сдача. Всем, кто по реестру ВЫБИРАЕТ НОМЕР, — `Read-ClaimRecord` со строгостью.
    return (Read-ClaimRecord -Path $Path).Record
}

function Get-ClaimState {
    param($Claim)
    # Четыре состояния вместо трёх — и все честные:
    #   ведёт      — заявка открыта, отметка свежая;
    #   молчит     — заявка открыта, отметка старая (вкладка могла закрыться без сдачи, а могла
    #                молча работать часами: прогон субагентов, ожидание сборки, пауза в разговоре);
    #   сдан       — поток сдан по правилам, вкладки нет;
    #   нет заявки — поток не объявлялся вовсе; это определяет не эта функция, а тот, кто ищет.
    if (-not $Claim) { return 'нет заявки' }
    if ([string]$Claim.state -eq 'released') { return 'сдан' }
    $seen = [datetime]::MinValue
    $raw = [string]$Claim.seen_at
    if (-not $raw -or -not [datetime]::TryParse($raw, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$seen)) {
        return 'молчит'
    }
    if (((Get-Date) - $seen).TotalHours -lt (Get-AliveHours)) { return 'ведёт' }
    return 'молчит'
}

function Get-ClaimCurrentNames {
    param($Claim)
    # Имена, которые поток носит СЕЙЧАС. Их три источника: записанные в заявке при объявлении
    # (ветка и рабочая папка), нынешняя ветка его рабочего дерева и — если это наша вкладка — то,
    # что видно прямо сейчас, на случай, когда git молчит вовсе.
    if (-not $Claim) { return @(Get-CurrentKeys) }
    $names = [System.Collections.Generic.List[string]]::new()
    foreach ($raw in @([string]$Claim.branch, [string]$Claim.worktree)) {
        $names.Add((Get-StreamKey -Raw $raw))
    }
    $here = (([string]$Claim.worktree) -replace '\\', '/').TrimEnd('/').ToLowerInvariant()
    foreach ($tree in (Get-Worktrees)) {
        if ((($tree.path) -replace '\\', '/').TrimEnd('/').ToLowerInvariant() -ne $here) { continue }
        if ($tree.branch) { $names.Add((Get-StreamKey -Raw $tree.branch)) }
        $names.Add((Get-StreamKey -Raw $tree.path))
    }
    if ($here -eq (($PWD.Path -replace '\\', '/').TrimEnd('/').ToLowerInvariant())) {
        foreach ($key in (Get-CurrentKeys)) { $names.Add($key) }
    }
    return @($names | Where-Object { $_ } | Select-Object -Unique)
}

function Get-ClaimRememberedNames {
    param($Claim)
    # Имена, которые поток лишь ПОМНИТ: ветку переименовали, а вкладка объявилась заново, и
    # объявление переписало заявку целиком. Без памяти находка, УЖЕ ПРИНЯТАЯ по старому имени, не
    # дошла бы и сдачу не задержала — исчезла бы молча.
    #
    # ‼️ Помнить — не то же самое, что носить: права у этих имён разные, см. `Resolve-ClaimNames`.
    if (-not $Claim) { return @() }
    $current = @(Get-ClaimCurrentNames -Claim $Claim)
    $names = foreach ($raw in @($Claim.former_branches)) {
        $key = Get-StreamKey -Raw ([string]$raw)
        if ($key -and $key -notin $current) { $key }
    }
    return @($names | Select-Object -Unique)
}

function Resolve-ClaimNames {
    param($Entries)
    # ‼️ Кто на какое имя отзывается — решается ПО ВСЕМУ РЕЕСТРУ СРАЗУ, а не отдельно по каждой
    # заявке. Иначе одно имя законно указывает на два потока, а закрытие находки с именным адресом
    # ОБЩЕЕ, не персональное: кто закрыл первым — погасил её у всех.
    #
    # Что вышло, когда память имён появилась без этого правила (воспроизведено на настоящем
    # репозитории): имена веток в живом дереве переиспользуют, и новая вкладка взяла имя, которое
    # соседка лишь помнила. Находка ушла обеим, обеим сказали «учли — закройте», и та, что имя лишь
    # помнит, погасила чужую находку. Истинный адресат не увидел ничего, сдался зелёным с пустым
    # ящиком, а автору пришло «учтено» — от потока, которого он не называл. До памяти имён такого
    # случая не было вовсе: находка доходила до правильной вкладки одна.
    #
    # Правило, из которого это следует, одно: НА КАЖДОЕ ИМЯ ОТЗЫВАЕТСЯ НЕ БОЛЬШЕ ОДНОГО ПОТОКА.
    #   • носящий имя сейчас — отзывается всегда;
    #   • помнящий — только если имя не носит и не помнит НИКТО другой;
    #   • сданный поток не отзывается вовсе: вкладки нет, а его память не должна отнимать имя у
    #     живого соседа и гасить чужие находки.
    # Имя, которое помнят двое и не носит никто, не достаётся никому: приём такой находки отказывает
    # вслух (адресата нет), и это честнее тихой доставки наугад.
    $carried = @{}
    $recalled = @{}
    foreach ($entry in $Entries) {
        if ($entry.State -eq 'сдан') { continue }
        foreach ($name in $entry.Current) { $carried[$name] = 1 + [int]$carried[$name] }
        foreach ($name in $entry.Remembered) { $recalled[$name] = 1 + [int]$recalled[$name] }
    }
    foreach ($entry in $Entries) {
        if ($entry.State -eq 'сдан') {
            $entry.Keys = @()
            $entry.Silenced = @($entry.Current + $entry.Remembered | Select-Object -Unique)
            continue
        }
        $keys = [System.Collections.Generic.List[string]]::new()
        $silenced = [System.Collections.Generic.List[string]]::new()
        foreach ($name in $entry.Current) { $keys.Add($name) }
        foreach ($name in $entry.Remembered) {
            if ([int]$carried[$name] -eq 0 -and [int]$recalled[$name] -eq 1) {
                $keys.Add($name)
            } else {
                $silenced.Add($name)
            }
        }
        $entry.Keys = @($keys | Select-Object -Unique)
        $entry.Silenced = @($silenced)
    }
    return $Entries
}

function Get-NameRememberers {
    param($Claims, [string]$Name)
    # Кто ПОМНИТ это имя, никем сейчас не носимое. Нужен одному-единственному отказу: имя, которое
    # помнят двое и не носит никто, нарочно не достаётся никому (см. `Resolve-ClaimNames`), и
    # человеку надо сказать именно это, а не гадать про рабочие деревья.
    if (-not $Name) { return @() }
    $live = @($Claims | Where-Object { $_.State -ne 'сдан' })
    if (@($live | Where-Object { $Name -in $_.Current }).Count -gt 0) { return @() }
    return @($live | Where-Object { $Name -in $_.Remembered })
}

function Get-StreamNames {
    param($Claim, $Claims)
    # Имена, на которые поток отзывается ПРЯМО СЕЙЧАС, с учётом всего реестра. Единственный ответ
    # на вопрос «как зовут этот поток» — им пользуются все трое: приём находки, сторож доставки и
    # сдача потока.
    #
    # Реестр обязателен: без него нельзя узнать, не носит ли запомненное имя живой сосед, — а
    # именно на этом и держится правило «на имя отзывается не больше одного потока».
    if (-not $Claim) { return @(Get-CurrentKeys) }
    $here = (([string]$Claim.worktree) -replace '\\', '/').TrimEnd('/').ToLowerInvariant()
    foreach ($entry in @($Claims)) {
        if ((([string]$entry.Record.worktree) -replace '\\', '/').TrimEnd('/').ToLowerInvariant() -ne $here) {
            continue
        }
        return @($entry.Keys)
    }
    # Заявки в реестре не нашлось (её только что положили, реестр читали раньше) — отзываемся хотя
    # бы носимыми именами: они не спорны по построению.
    return @(Get-ClaimCurrentNames -Claim $Claim)
}

function Get-ClaimFiles {
    param([string]$Dir, [switch]$Strict)
    # Опись каталога заявок — ПЕРВОЕ чтение реестра, и оно обязано срываться честно.
    #
    # ‼️ Здесь стояла самая тихая дыра всего механизма. Перечисление средствами оболочки с
    # образцом имени при закрытом доступе к содержимому каталога ВОЗВРАЩАЕТ ПУСТОЙ СПИСОК и не
    # сообщает об ошибке (проверено; три других способа перечисления в том же опыте срываются
    # честно). Ловить было нечего: исключения нет — значит ни повторы, ни отказ вслух не
    # срабатывали никогда, а строгий заслон выше по коду просто не вызывался. Наружу это выходило
    # так: сосед ведёт первый поток, вторая вкладка объявляется и получает ТОТ ЖЕ номер с кодом
    # успеха; вопрос «чей это кусок» отвечает «никто не взял»; находка живому потоку — «поток ещё
    # не объявлялся».
    #
    # Отсюда правило для всего комплекта: спрашивать файловую систему только теми способами, у
    # которых «не смог» отличается от «пусто». Здесь это исключение, а не пустой ответ.
    #
    # ‼️ «Каталога нет» — законный ответ «заявок ещё нет»: реестр заводит первая же объявившаяся
    # вкладка. Но тем же самым отказом отвечает и МЁРТВЫЙ ПУТЬ — отвалившийся диск, пропавшая
    # сетевая шара, — а это уже беда, и выдавать её за пустой реестр нельзя: на мёртвом диске
    # вопрос «чей это кусок» отвечал «задачу никто не взял», а сдача — «заявки на этой вкладке
    # нет», обе кодом успеха. Поэтому различаем: есть ли ХОТЬ ОДИН существующий каталог выше по
    # пути. Есть — путь жив, реестра просто ещё нет. Нет ни одного — говорим вслух.
    #
    # Всё прочее (нет прав, закрыт заход) — беда сразу, без разбирательств.
    $deadline = (Get-Date).AddMilliseconds((Get-ClaimReadTimeoutMs -Strict:$Strict))
    $reason = 'причина неизвестна'
    while ($true) {
        try {
            return @([System.IO.Directory]::GetFiles($Dir, '*.json'))
        } catch {
            if (Test-MissingPathFailure -Failure $_) {
                if (Test-PathReachable -Path $Dir) { return @() }
                $reason = "путь недостижим целиком: $(Get-FailureReason -Failure $_)"
                break
            }
            $reason = Get-FailureReason -Failure $_
        }
        if ((Get-Date) -ge $deadline) { break }
        Start-Sleep -Milliseconds 30
    }
    if ($Strict) {
        throw "реестр заявок не перечислить ($Dir), а решать по нему вслепую нельзя — номер потока совпал бы с соседним, а чужая задача выглядела бы ничьей. Причина: $reason"
    }
    return @()
}

function Get-Claims {
    param([string]$Dir, [switch]$Strict)
    # Весь реестр, с посчитанным состоянием. Порядок — по волне и номеру потока, чтобы показ не
    # прыгал от запуска к запуску.
    #
    # ‼️ `-Strict` — для того, кто по этому списку ВЫБИРАЕТ НОМЕР потока, ищет владельца задачи или
    # решает судьбу находки. Ему нельзя не увидеть соседа: пропущенная заявка даёт два потока с
    # одним адресом, ответ «задачу никто не взял» и приём находки сданному потоку — всё молча и
    # навсегда. Терпимые читатели (сторож доставки на каждом ходу, показ доски) молчат и работают
    # тем, что прочиталось: там пропуск стоит одной невидимой строки, а не адреса.
    #
    # Существование каталога отдельно НЕ проверяем: проверка существования отвечает «нет» и там,
    # где на самом деле «не видно», и это ровно та подмена, на которой стояли все дыры этого
    # класса. Отвечает опись — она отличает «каталога нет» от «не смог посмотреть».
    $claims = [System.Collections.Generic.List[object]]::new()
    foreach ($file in (Get-ClaimFiles -Dir $Dir -Strict:$Strict)) {
        $read = Read-ClaimRecord -Path $file -Strict:$Strict
        # ‼️ Строгому читателю ЛЮБОЙ исход, кроме «прочитали», — отказ вслух. Для того, кто
        # выбирает номер или ищет владельца задачи, «файл занят» и «файл испорчен» — одно и то же:
        # файл лежит на месте, а поток из списка исчезает. Прежде вслух отказывал только первый, а
        # второй пропускался молча — и пустой файл, обрезанный на середине или пришедший от другой
        # версии комплекта давал ДВА ПОТОКА С ОДНИМ АДРЕСОМ без единого предупреждения
        # (воспроизведено на всех четырёх видах порчи).
        #
        # Повторять на испорченном незачем — испорчен он насовсем; но молчать нельзя тем более:
        # само собой это не пройдёт, в отличие от занятости.
        if ($Strict -and $read.State -eq 'unreadable') {
            throw "заявку соседа не прочитать ($file), а выбирать номер потока вслепую нельзя — он совпал бы с соседним. Причина: $($read.Reason). Повторите через несколько секунд."
        }
        if ($Strict -and $read.State -eq 'broken') {
            throw "заявка соседа испорчена и не разбирается ($file) — пока она такая, её поток невидим, и его номер выдался бы второй раз. Причина: $($read.Reason). Уберите файл или поправьте его."
        }
        $record = $read.Record
        if (-not $record) { continue }
        $claims.Add([pscustomobject]@{
                File       = $file
                Record     = $record
                WaveKey    = Get-WaveKey -Raw ([string]$record.wave)
                StreamKey  = Get-StreamNumberKey -Raw ([string]$record.stream)
                Current    = @(Get-ClaimCurrentNames -Claim $record)
                Remembered = @(Get-ClaimRememberedNames -Claim $record)
                Keys       = @()
                Silenced   = @()
                State      = Get-ClaimState -Claim $record
            })
    }
    # ‼️ Имена раздаём ВТОРЫМ проходом, когда весь реестр уже собран: на кого отзовётся имя,
    # зависит от того, не носит ли и не помнит ли его кто-то ещё. Поодиночке этот вопрос не
    # решается, а неверный ответ на него отдаёт находку двум потокам сразу.
    return @((Resolve-ClaimNames -Entries @($claims)) | Sort-Object WaveKey, StreamKey)
}

function Find-Claims {
    param($Claims, [string]$Raw)
    # Кому адресована находка. Сперва как «волна/поток» — это основной вид адреса, потому что
    # именно им поток назван в плане; не разобралось — как имя ветки или папки.
    $address = Get-StreamAddress -Raw $Raw
    if ($address) {
        return @($Claims | Where-Object {
                $_.WaveKey -eq $address.Wave -and $_.StreamKey -eq $address.Stream
            })
    }
    $key = Get-StreamKey -Raw $Raw
    if (-not $key) { return @() }
    return @($Claims | Where-Object { $key -in $_.Keys })
}

function Get-CurrentClaim {
    param([string]$Dir, [switch]$Strict)
    # ‼️ `-Strict` — тому, для кого «заявки нет» и «файл в этот миг занят» решают разное. Сдача
    # потока по занятому файлу отвечала бы «заявки на этой вкладке нет — сдавать нечего», и вкладка
    # закрывалась бы, не сдав поток: соседи продолжали бы адресовать находки живому, как им кажется,
    # потоку. Тот же корень, что и у выбора номера, — одна попытка чтения и пустота вместо правды.
    $read = Read-ClaimRecord -Path (Get-ClaimPath -Dir $Dir -TreePath $PWD.Path) -Strict:$Strict
    if ($Strict -and $read.State -eq 'unreadable') {
        throw "заявку этой вкладки не прочитать ($($read.Reason)) — сдавать поток вслепую нельзя, иначе он останется числиться за вами. Повторите через несколько секунд."
    }
    # ‼️ И испорченная — тоже отказ. Иначе владелец испорченной заявки невидим САМ СЕБЕ: сдача
    # отвечает «заявки на этой вкладке нет, сдавать нечего» и выходит успехом, вкладка закрывается,
    # а соседи продолжают адресовать находки потоку, который считают живым.
    if ($Strict -and $read.State -eq 'broken') {
        # ‼️ Путь называем ПОЛНОСТЬЮ, а выход даём выполнимый. Прежде отказ не называл файла вовсе
        # и советовал объявиться заново — а объявление читает весь реестр строго, натыкается на тот
        # же файл и тоже отказывает. Человек читал свой собственный отказ и выхода из него не имел.
        throw "заявка этой вкладки испорчена и не разбирается ($(Get-ClaimPath -Dir $Dir -TreePath $PWD.Path)) — пока она такая, поток невидим и соседям, и вам. Причина: $($read.Reason). Уберите этот файл, потом объявитесь заново."
    }
    return $read.Record
}

function Get-RegistryLockPath {
    param([string]$Dir)
    # Замок лежит В САМОМ реестре заявок: он стережёт именно его и уезжает вместе с ним (тесты
    # подставляют свою доску — реестр и замок уходят за ней сами).
    #
    # ‼️ Имя нарочно не оканчивается на `.json`: выдачу заявок читают по этому образцу, и замок
    # оказался бы в ней потоком-призраком — без волны, без номера, зато занимающим место в списке
    # соседей и в счёте потоков волны.
    #
    # Складываем строкой: сложение путей средствами оболочки срывается на несуществующем диске.
    return [System.IO.Path]::Combine($Dir, '.claim.lock')
}

function Get-RegistryLockWaitSeconds {
    # Сколько всего ждём чужой замок, прежде чем пойти дальше без него (сказав об этом вслух).
    # Критическая часть — несколько чтений папки и запись одного файла, доли секунды, так что даже
    # десяток вкладок, объявившихся разом, укладывается в предел с запасом.
    return 30
}

function Get-RegistryLockSpeakAfterSeconds {
    # Через столько молчаливое ожидание начинает выглядеть зависанием инструмента: человек ждёт
    # ответа на свою команду и, не видя ничего, бьёт по клавишам.
    return 2
}

function Get-PathState {
    param([string]$Path)
    # Что лежит по пути: `container` — каталог, `leaf` — файл, `none` — ничего, `unknown` — узнать
    # не удалось. Отдельной функцией, потому что сама проверка пути умеет СРЫВАТЬСЯ: на
    # несуществующем диске она не отвечает «нет», а бросает ошибку, и при строгом режиме оболочки
    # эта ошибка выносила инструмент наружу сырым английским сообщением про диск, которого нет.
    # ‼️ Спрашиваем систему ОДНИМ вопросом, а не двумя подряд. Между двумя ответами мир меняется:
    # вкладки волны заводят каталог реестра разом, и если сосед успел создать его между вопросом
    # «это каталог?» и вопросом «а вообще есть?», выходил уверенный, но ЛОЖНЫЙ ответ «лежит не
    # каталог». Опасен был не сам ответ, а совет, который на нём строился: он указывал удалить
    # папку, где лежат заявки соседей. Под нагрузкой ложь ловилась в каждом пятом запуске.
    #
    # Свойства пути приходят одним ответом системы: есть каталог — значит каталог, есть без
    # признака каталога — файл, нет вовсе — своим видом отказа.
    try {
        $attributes = [System.IO.File]::GetAttributes($Path)
        $kind = if ($attributes -band [System.IO.FileAttributes]::Directory) { 'container' } else { 'leaf' }
        return [pscustomobject]@{ Kind = $kind; Reason = '' }
    } catch {
        if (Test-MissingPathFailure -Failure $_) {
            return [pscustomobject]@{ Kind = 'none'; Reason = '' }
        }
        return [pscustomobject]@{ Kind = 'unknown'; Reason = (Get-FailureReason -Failure $_) }
    }
}

function Test-PathReachable {
    param([string]$Path)
    # Жив ли путь: есть ли выше по нему хоть один существующий каталог. Нужен, чтобы отличить
    # «реестра ещё нет» (нормально: заведёт первая же вкладка) от «пути нет вовсе» (отвалился диск
    # или сетевая шара) — система отвечает на оба случая одним и тем же отказом.
    #
    # Проверка существования тут врёт только в сторону «нет», и это безопасная сторона: сомнение
    # уводит в отказ вслух, а не в тихое «заявок нет».
    $current = [System.IO.Path]::GetDirectoryName($Path)
    while ($current) {
        if ((Get-PathState -Path $current).Kind -eq 'container') { return $true }
        $parent = [System.IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
    return $false
}

function Get-BlockingAncestor {
    param([string]$Path)
    # Ближайший предок пути, который существует и каталогом НЕ является. Нужен ровно для честного
    # отказа: когда папку доски занял файл, каталог реестра создать нельзя, но виноват не он, а
    # файл выше по пути — и назвать надо именно его, иначе человеку нечего искать.
    # Путь режем строкой, а не разбором через оболочку: на несуществующем диске тот срывается, а
    # здесь мы как раз и разбираемся, что с путём не так.
    $current = [System.IO.Path]::GetDirectoryName($Path)
    while ($current) {
        $state = Get-PathState -Path $current
        if ($state.Kind -eq 'container') { return '' }
        if ($state.Kind -eq 'leaf') { return $current }
        if ($state.Kind -eq 'unknown') { return '' }
        $parent = [System.IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
    return ''
}

function Assert-RegistryDir {
    param([string]$Dir)
    # Каталог реестра обязан быть КАТАЛОГОМ, и сказать об этом надо сразу — своими словами и с
    # настоящей причиной.
    #
    # ‼️ Ловушек тут три, и все три уже сработали вживую.
    #
    # Первая: проверка «есть ли такой путь» отвечает «есть» и на файл, а `New-Item -ItemType
    # Directory -Force` поверх файла молча ничего не делает и рапортует успехом. Дальше замок
    # честно ждал бы полминуты, печатал пугающее «замок не отдали, вкладка рядом упала» — и всё
    # равно падал строкой ниже, на записи заявки.
    #
    # Вторая: судить надо ПО ИТОГУ, а не по двум отдельным пробам («каталога нет» + «путь занят» =
    # «лежит файл»). Проба и проба — это гонка: вкладки волны заводят каталог реестра разом, и
    # между двумя пробами сосед успевает его создать. Первая же вкладка обвиняла соседа в том, что
    # тот положил на место каталога файл, и объявление срывалось с уверенным, но ложным отказом.
    #
    # Третья: молчаливый рапорт об успехе оставлял отказ БЕЗ причины — «причина неизвестна», — хотя
    # причина известна и называется: путь перекрыт файлом выше по дереву. А на несуществующем диске
    # до этой развилки дело вообще не доходило: срывалась сама проверка пути.
    $state = Get-PathState -Path $Dir
    if ($state.Kind -eq 'container') { return }
    if ($state.Kind -eq 'leaf') {
        throw "путь для каталога заявок занят: по нему лежит файл, а не каталог ($Dir). Пока он там, объявиться не сможет ни одна вкладка — посмотрите, что это, и освободите путь"
    }
    if ($state.Kind -eq 'unknown') {
        throw "каталог заявок не проверить ($Dir). Причина: $($state.Reason)"
    }
    $reason = ''
    try {
        New-Item -ItemType Directory -Path $Dir -Force -ErrorAction Stop | Out-Null
    } catch {
        # Каталог мог завести сосед в тот же миг — это не отказ, а обычный ход дела; судим ниже, по
        # итогу. Но причину держим: не вышло — человеку нужна ОНА, а не наша догадка.
        $reason = Get-FailureReason -Failure $_
    }
    $state = Get-PathState -Path $Dir
    if ($state.Kind -eq 'container') { return }
    if ($state.Kind -eq 'leaf') {
        throw "путь для каталога заявок занят: по нему лежит файл, а не каталог ($Dir). Пока он там, объявиться не сможет ни одна вкладка — посмотрите, что это, и освободите путь"
    }
    if ($state.Kind -eq 'unknown') {
        throw "каталог заявок не проверить ($Dir). Причина: $($state.Reason)"
    }
    # Каталога нет, а создание о беде не сказало. Значит смотрим выше по пути: чаще всего там файл.
    $blocking = Get-BlockingAncestor -Path $Dir
    if ($blocking) {
        throw "путь к каталогу заявок перекрыт файлом ($blocking) — уберите или переименуйте его, иначе объявиться не сможет ни одна вкладка"
    }
    if (-not $reason) { $reason = 'создание каталога не сообщило о беде, но каталога нет' }
    throw "каталог заявок не завести ($Dir). Причина: $reason"
}

function Enter-RegistryLock {
    param([string]$Dir)
    # Кросс-процессный замок на ВЫБОР НОМЕРА потока. Отвечает открытым дескриптором файла замка —
    # его отдают потом в `Exit-RegistryLock`; $null значит «замок взять не удалось».
    #
    # ‼️ Что он ломает (без этого его уберут как лишний). Выбор номера идёт по снимку всего
    # реестра, и без замка он не атомарен. Шесть вкладок, открытых разом: все читают ещё пустой
    # реестр и берут номер 1; в круге разрешения спора двое одновременно считают следующий
    # свободный от одного и того же снимка — и обе берут 2; та из них, что перечитала реестр
    # раньше, чем вторая успела записать свой сдвиг, соперника не видит и выходит из круга с
    # номером 2, а вторая, сравнив себя со «старшей», тоже остаётся на 2. Повторные заходы тут не
    # спасают: из круга выходят по УСТАРЕВШЕМУ снимку. Под замком вторая вкладка читает реестр уже
    # с записанной заявкой первой, и номера расходятся.
    #
    # Замок — ДЕРЖИМЫЙ ДЕСКРИПТОР файла, открытого без права совместного доступа. Не именованный
    # мьютекс (комплект ставят в любой проект, а межпроцессные мьютексы за пределами Windows не
    # работают) и не «файл существует — значит занято».
    #
    # ‼️ Почему не по существованию файла — это главное. Тогда упавшая вкладка оставляет файл, и
    # чтобы доска не заперлась навсегда, нужен порог протухания и перехват. А перехват невозможно
    # сделать безопасным подручными средствами: решение «замок протух, отнимаю» принимается по
    # ОДНОМУ состоянию файла, а отнимается уже другое — держатель успел уйти, сосед успел завести
    # свежий замок, и отнимают именно его, пока сосед работает внутри. Собрано стендом на восьми
    # процессах: двойной вход ловится. У дескриптора этой развилки нет вовсе — система закрывает
    # его сама, когда процесс умирает, и замок освобождается в тот же миг (проверено убийством
    # держателя: следующий вошёл сразу, ждать порога не пришлось).
    #
    # ‼️ Файл замка НИКОГДА не удаляется — ни при выходе, ни при уборке. Держит замок дескриптор, а
    # не файл: удали его — и второй процесс заведёт файл заново и возьмёт замок на НОВОМ файле,
    # пока первый держит старый. Внутри окажутся двое. Пустой файл в каталоге реестра — плата
    # ничтожная, в выдачу заявок он не попадает (см. имя выше).
    #
    # ‼️ Насколько это надёжно — честно, без обещаний. На Windows взаимное исключение между
    # процессами держит сама система, и здесь это проверено опытом. На прочих системах .NET
    # изображает совместный доступ советующими блокировками ядра, а они действуют не везде: на
    # части сетевых файловых систем не работают вовсе, и их можно отключить настройкой среды
    # выполнения. Проверить это на машине разработки нечем.
    #
    # Стенд взаимного исключения в наборе тестов ловит такую поломку, но полагаться на него как на
    # гарантию нельзя: он идёт там, где гоняют тесты ЭТОГО репозитория, то есть на одной машине.
    # Комплект, поставленный в чужой проект, тестов не гоняет и Python не требует.
    #
    # Отсюда правило для того, кто понесёт комплект дальше: реестр заявок должен лежать на обычном
    # локальном диске. Лёг на сетевую шару — прогоните стенд взаимного исключения там же, прежде
    # чем считать замок работающим.

    # ‼️ Замок НЕ допускает повторного входа: второй заход того же процесса система отвергнет так
    # же, как чужой. Сегодня вложенного входа нет, и заводить его нельзя — вкладка заперла бы сама
    # себя, прождала предел и пошла дальше без замка, напечатав про застрявшего соседа, которого не
    # существует. Понадобится вложенный вход — передавайте уже взятый дескриптор внутрь, а не
    # берите замок второй раз.
    Assert-RegistryDir -Dir $Dir
    $path = Get-RegistryLockPath -Dir $Dir
    $started = Get-Date
    $deadline = $started.AddSeconds((Get-RegistryLockWaitSeconds))
    $spoke = $false
    while ($true) {
        try {
            $stream = [System.IO.File]::Open($path, 'OpenOrCreate', 'Write', 'None')
            try {
                # Кто держит — человеку, который заглянет в файл. Никакая работа замка на это не
                # опирается: держит его дескриптор, а не запись внутри.
                $stream.SetLength(0)
                $note = [System.Text.Encoding]::UTF8.GetBytes(
                    "процесс $PID@$([System.Environment]::MachineName), взят $((Get-Date).ToString('s'))")
                $stream.Write($note, 0, $note.Length)
                $stream.Flush()
            } catch {
                # Отметка не записалась — не беда, замок уже наш. Ронять из-за неё объявление
                # незачем: никакая работа замка на эту запись не опирается.
            }
            if ($spoke) { [Console]::Out.WriteLine('Замок реестра заявок освободился — продолжаю объявление.') }
            return $stream
        } catch {
            # ‼️ Спор за замок и неустранимая помеха выглядят тут одинаково — отказом открыть
            # файл, — а лечатся противоположно: спор надо переждать, помеху ждать бессмысленно
            # (полминуты ожидания, рассказ про соседнюю вкладку — и всё равно отказ строкой ниже,
            # на записи заявки; до замка отказ здесь был мгновенным и честным).
            #
            # Различаем по виду ошибки: «файл занят другим процессом» приходит обычной ошибкой
            # ввода-вывода, а негодный путь и нехватка прав — своими видами (проверено). Пробовать
            # вместо этого «а лежит ли файл на месте» нельзя: такая проверка гоночная — держатель
            # успевает уйти между отказом и проверкой, и обычный спор объявляется помехой.
            $failure = $_.Exception
            while ($failure.InnerException) { $failure = $failure.InnerException }
            #
            # ‼️ Проверка каталога ниже — НЕ та гоночная проверка «а лежит ли файл на
            # месте», о которой предупреждает абзац выше, и разница существенна: файл замка при
            # обычном споре появляется и исчезает, а КАТАЛОГ по пути замка при нормальной работе
            # не бывает никогда — замок всегда файл. Значит каталог там — помеха, а не спор, в
            # любом порядке событий. Проверяем его явно, потому что системы расходятся в виде
            # ошибки: windows отдаёт попытку открыть каталог как файл ошибкой доступа (она и так
            # безнадёжна по виду), а linux — обычной ошибкой ввода-вывода, неотличимой от замка,
            # занятого соседом. Без этой проверки вкладка на linux выжидает весь предел, винит
            # несуществующего соседа и берёт номер вообще без замка. Поймано прогоном публичного
            # хранилища на linux — на машине разработки этого было не увидеть.
            $hopeless = ($failure -isnot [System.IO.IOException]) -or
                ($failure -is [System.IO.DirectoryNotFoundException]) -or
                (Test-Path -LiteralPath $path -PathType Container)
            if ($hopeless) {
                throw "замок реестра заявок не завести ($path). Причина: $(Get-FailureReason -Failure $_)"
            }
        }
        if ((Get-Date) -ge $deadline) { return $null }
        if (-not $spoke -and ((Get-Date) - $started).TotalSeconds -ge (Get-RegistryLockSpeakAfterSeconds)) {
            # Молчаливое ожидание человек читает как зависание инструмента. Пишем в обычный
            # поток вывода, а не в поток ошибок: там в этом инструменте живут только отказы. И не
            # через обычный вывод оболочки — он вернулся бы вызывающему вместо дескриптора замка.
            $spoke = $true
            [Console]::Out.WriteLine("Жду освобождения замка реестра заявок: рядом объявляется другая вкладка ($path). Подожду до $(Get-RegistryLockWaitSeconds) с.")
        }
        # Пауза вразнобой: одинаковая свела бы вкладки в такт, и они толкались бы синхронно.
        Start-Sleep -Milliseconds (20 + (Get-Random -Maximum 40))
    }
}

function Exit-RegistryLock {
    param($Handle)
    # Отпустить замок — значит закрыть дескриптор. Файл при этом остаётся на месте намеренно: см.
    # разбор в `Enter-RegistryLock`, удаление файла впустило бы внутрь второго.
    #
    # Молчим при любой неудаче: даже не закройся дескриптор здесь, система закроет его на выходе
    # процесса, а ронять из-за этого уже состоявшееся объявление незачем.
    if (-not $Handle) { return }
    try {
        $Handle.Dispose()
    } catch {
        return
    }
}

function Write-ClaimFile {
    param([string]$Path, $Claim)
    # Пишем через временный файл рядом: читатель никогда не увидит полузаписанную заявку. Писатель
    # у файла один (своя вкладка), поэтому ни блокировок, ни повторов не нужно.
    # Путь режем строкой — по той же причине, что и на доске: разбор оболочкой срывается на
    # несуществующем диске сырым английским сообщением.
    $dir = [System.IO.Path]::GetDirectoryName($Path)
    if ($dir -and -not (Test-Path -LiteralPath $dir -PathType Container)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    # ‼️ Имя временного файла НЕ содержит `.json`: опись реестра берёт файлы по этому образцу, и
    # оборвавшаяся запись иначе легла бы в реестр обрывком — а обрывок теперь останавливает всё,
    # что зависит от адреса. Убираем его и при неудаче: мусор в каталоге реестра нам не нужен.
    $temp = [System.IO.Path]::ChangeExtension($Path, ".tmp-$PID")
    try {
        [System.IO.File]::WriteAllText($temp, ($Claim | ConvertTo-Json -Depth 5), [System.Text.UTF8Encoding]::new($false))
        [System.IO.File]::Move($temp, $Path, $true)
    } catch {
        Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
        throw
    }
}

function Update-ClaimSeen {
    param([string]$Dir, [string]$TreePath)
    # Отметка «вкладка на ходу». Зовётся сторожем доставки на каждом ходу и обязана быть немой:
    # сорвавшаяся отметка не повод мешать работе.
    try {
        $path = Get-ClaimPath -Dir $Dir -TreePath $TreePath
        $claim = Read-ClaimFile -Path $path
        if (-not $claim) { return }
        if ([string]$claim.state -eq 'released') { return }
        $claim.seen_at = (Get-Date).ToString('s')
        Write-ClaimFile -Path $path -Claim $claim
    } catch {
        return
    }
}

function Get-ProfilePlansFolder {
    param([string]$StartDir)
    # Где в ЭТОМ проекте лежат планы волн: профиль `.parallel-streams.md`, раздел `## Plans`, путь
    # обратными кавычками. Разбор ОДИН на весь комплект, и зовут его двое — сторож-подсказка (он
    # решает, что правится план волны) и отбор мест, которые потоки правят сообща. Разъедься они —
    # сторож считал бы планом одно, а список пересечений другое.
    #
    # Заголовок ищем БЕЗ учёта регистра: профиль пишет человек руками, и «## plans» — тот же раздел.
    #
    # ‼️ Папка не названа — отвечаем пусто, и никаких значений по умолчанию на путь одного проекта.
    # Зашитый путь в чужом проекте не сошёлся бы никогда: сторож молча выходил бы нулём, а план
    # волны попадал бы в список пересечений — и то, и другое неотличимо от исправной работы.
    if (-not $StartDir) { $StartDir = $PWD.Path }
    try {
        $dir = $StartDir
        while ($dir) {
            $profilePath = Join-Path $dir '.parallel-streams.md'
            if (Test-Path -LiteralPath $profilePath -PathType Leaf) {
                $text = [System.IO.File]::ReadAllText($profilePath, [System.Text.UTF8Encoding]::new($false))
                $section = [regex]::Match($text, '(?msi)^##\s+Plans\s*$(.*?)(?=^##\s|\z)')
                if (-not $section.Success) { return '' }
                foreach ($found in [regex]::Matches($section.Groups[1].Value, '`([^`]+)`')) {
                    $value = ($found.Groups[1].Value.Trim() -replace '\\', '/') -replace '^\./', ''
                    if ($value -match '\s') { continue }
                    # Имя файла папкой планов не бывает: в разделе кавычками помечают и файлы.
                    if ($value -notmatch '/$' -and $value -match '\.[A-Za-z0-9]{1,5}$') { continue }
                    if (-not $value.EndsWith('/')) { $value += '/' }
                    return $value
                }
                return ''
            }
            # Выше корня репозитория не поднимаемся: там начинается чужой проект со своим профилем.
            if (Test-Path -LiteralPath (Join-Path $dir '.git')) { break }
            $dir = Split-Path -Parent $dir
        }
    } catch {
        return ''
    }
    return ''
}

function Get-SharedByDesignPattern {
    # Места, которые потоки правят СООБЩА по устройству работы, а не по недосмотру: план волны
    # трогает каждая вкладка (строка состояния, дописка находки). Считать это пересечением —
    # значит сделать сторожа шумным и тем самым бесполезным.
    #
    # Папку планов берём из профиля тем же разбором, что и сторож-подсказка. Пусто — планов в
    # проекте нет, а значит нет и общих по устройству мест: тогда не отсеиваем НИЧЕГО. Пустой
    # образец совпал бы с любым именем и спрятал бы все пересечения разом.
    $plans = Get-ProfilePlansFolder
    if (-not $plans) { return '' }
    return "/$plans"
}

function Get-TouchedFiles {
    # Файлы, которые поток уже тронул: своя ветка против общего предка с основной плюс
    # незакоммиченное. По ним видно пересечение с соседом ДО того, как оно станет конфликтом
    # слияния, — и, что важнее, до того, как вкладка предложит владельцу взять чужую задачу.
    $files = [System.Collections.Generic.List[string]]::new()
    try {
        $base = (& git merge-base HEAD origin/main 2>$null)
        if ($LASTEXITCODE -eq 0 -and $base) {
            foreach ($name in @(& git diff --name-only $base.Trim() HEAD 2>$null)) { $files.Add($name) }
        }
        foreach ($line in @(& git status --porcelain 2>$null)) {
            if ($line.Length -le 3) { continue }
            $name = $line.Substring(3).Trim().Trim('"')
            # Переименование приходит как «было -> стало»: интересует то, что есть сейчас.
            if ($name -match ' -> ') { $name = ($name -split ' -> ')[-1].Trim().Trim('"') }
            $files.Add($name)
        }
    } catch {
        return @()
    }
    $shared = Get-SharedByDesignPattern
    $clean = foreach ($name in $files) {
        if (-not $name) { continue }
        $normalized = ($name -replace '\\', '/').Trim().ToLowerInvariant()
        if (-not $normalized) { continue }
        # Пустой образец — это «планов в проекте нет»: тогда не отсеиваем ничего, иначе он совпал
        # бы с любым именем и список тронутого вышел бы пустым у всех.
        if ($shared -and "/$normalized" -like "*$shared*") { continue }
        $normalized
    }
    # Потолок: список едет в заявку, которую читают все соседи на каждом ходу. Сквозная правка на
    # тысячу файлов пересечётся и по первым двум сотням.
    return @($clean | Select-Object -Unique | Select-Object -First 200)
}

function Update-ClaimFiles {
    param([string]$Dir, [string]$TreePath, [int]$MaxAgeMinutes = 5)
    # Список тронутого пересчитывается не чаще раза в несколько минут: сторож зовётся на КАЖДОМ
    # ходу вкладки, а два запроса к git на каждый ход — плата ни за что.
    try {
        $path = Get-ClaimPath -Dir $Dir -TreePath $TreePath
        $claim = Read-ClaimFile -Path $path
        if (-not $claim) { return }
        if ([string]$claim.state -eq 'released') { return }
        $when = [datetime]::MinValue
        if ($claim.files_at -and [datetime]::TryParse([string]$claim.files_at, [cultureinfo]::InvariantCulture,
                [System.Globalization.DateTimeStyles]::None, [ref]$when)) {
            if (((Get-Date) - $when).TotalMinutes -lt $MaxAgeMinutes) { return }
        }
        $claim | Add-Member -NotePropertyName files -NotePropertyValue (Get-TouchedFiles) -Force
        $claim | Add-Member -NotePropertyName files_at -NotePropertyValue ((Get-Date).ToString('s')) -Force
        Write-ClaimFile -Path $path -Claim $claim
    } catch {
        return
    }
}

function Get-Overlaps {
    param($Claims, $MyClaim)
    # Соседи, которые правят те же файлы. Ровно тот случай, о котором владелец узнать не может:
    # вкладка предлагает ему «сделать заодно ещё вот это», он не знает, что это кусок другого
    # потока, и подтверждает. Пересечение по файлам видно механически и заранее.
    if (-not $MyClaim -or -not $MyClaim.files) { return @() }
    # Общие по устройству работы места отсеиваем и здесь, а не только при сборке списка: заявку
    # мог написать сторож прежней версии, и тогда план волны снова считался бы пересечением.
    $shared = Get-SharedByDesignPattern
    $mine = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($name in @($MyClaim.files)) {
        $text = [string]$name
        if (-not $text) { continue }
        if ($shared -and "/$text" -like "*$shared*") { continue }
        [void]$mine.Add($text)
    }
    if ($mine.Count -eq 0) { return @() }
    $here = ([string]$MyClaim.worktree -replace '\\', '/').TrimEnd('/')
    $found = [System.Collections.Generic.List[object]]::new()
    foreach ($claim in $Claims) {
        if ($claim.State -ne 'ведёт') { continue }
        if ((([string]$claim.Record.worktree) -replace '\\', '/').TrimEnd('/') -eq $here) { continue }
        $common = @(@($claim.Record.files) | Where-Object { $_ -and $mine.Contains([string]$_) })
        if ($common.Count -eq 0) { continue }
        $found.Add([pscustomobject]@{ Claim = $claim; Files = @($common) })
    }
    return @($found)
}

function Get-StuckRecords {
    param($Records, $Claims, [string[]]$KnownKeys)
    # Записи, которым некуда прийти. Сегодня их не видит НИКТО: адресата нет, а отправителю уже
    # отрапортовали об успехе. Молчание механизма неотличимо от «соседу нечего сказать».
    $stuck = [System.Collections.Generic.List[object]]::new()
    $deadline = (Get-Date).AddDays(-(Get-SilentDaysBeforeStuck))
    foreach ($record in $Records) {
        $raw = [string]$record.to
        # Запись «всем» сюда не попадает: у неё свой срок давности и свой смысл — её могли просто
        # ещё не все учесть. Уведомление «учтено» — тем более: гоняться за ним некому и незачем.
        if ((Get-StreamKey -Raw $raw) -in @('*', '**')) { continue }
        if ([string]$record.kind -eq 'ack') { continue }
        $addressed = @(Find-Claims -Claims $Claims -Raw $raw)
        $live = @($addressed | Where-Object { $_.State -eq 'ведёт' })
        if ($live.Count -gt 0) { continue }
        $when = [datetime]::MinValue
        $parsed = [datetime]::TryParse([string]$record.at, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$when)
        $reason = ''
        if (@($addressed | Where-Object { $_.State -eq 'сдан' }).Count -gt 0) {
            # Сдан — единственный случай, где ждать нечего вовсе: вкладки нет и не будет.
            $reason = 'поток сдан'
        } elseif ($addressed.Count -gt 0) {
            if ($parsed -and $when -gt $deadline) { continue }
            $reason = "поток молчит с $(Format-Stamp -Raw $addressed[0].Record.seen_at)"
        } else {
            if ($parsed -and $when -gt $deadline) { continue }
            # Ключи сюда передают ОТМЕТИВШИХСЯ: дерево само по себе адресата не делает — вкладку
            # могли закрыть неделю назад, а находка адресована именем ветки и ждёт её.
            if ((Get-StreamKey -Raw $raw) -in $KnownKeys) { continue }
            $reason = 'заявки на такой поток нет, свежей отметки у дерева тоже'
        }
        $stuck.Add([pscustomobject]@{ Record = $record; Reason = $reason })
    }
    return @($stuck)
}

function Format-Stamp {
    param($Raw)
    # Время в заявке лежит строкой, но при чтении JSON превращается в дату, и тогда `[string]`
    # печатает его в виде системной локали («08/21/2026 23:34:13»). Человеку показываем один и тот
    # же вид независимо от того, каким путём значение пришло.
    if ($Raw -is [datetime]) { return $Raw.ToString('yyyy-MM-dd HH:mm') }
    $text = [string]$Raw
    if (-not $text) { return 'без отметки' }
    $when = [datetime]::MinValue
    if ([datetime]::TryParse($text, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$when)) {
        return $when.ToString('yyyy-MM-dd HH:mm')
    }
    return $text
}

function Format-ClaimLine {
    param($Claim)
    $record = $Claim.Record
    $name = if ($record.name) { " «$($record.name)»" } else { '' }
    $tasks = if ($record.tasks) { ", задачи $($record.tasks)" } else { '' }
    # ‼️ Запомненные имена показываем. Поток отзывается не только на то имя, что носит сейчас, — и
    # пока эти имена не было видно нигде, человек не мог заметить ни того, что находка уйдёт по
    # старому адресу, ни того, что имя у потока отняли. Отнятые метим отдельно: по ним находка НЕ
    # придёт, потому что имя носит или помнит кто-то ещё.
    $kept = @($Claim.Remembered | Where-Object { $_ -in $Claim.Keys })
    $lost = @($Claim.Silenced)
    $memory = ''
    if ($kept.Count -gt 0) { $memory += ", помнит имена: $($kept -join ', ')" }
    if ($lost.Count -gt 0) { $memory += ", имена отняты: $($lost -join ', ')" }
    return "  $($record.wave)/$($record.stream)$name$tasks — $($Claim.State) (отметка $(Format-Stamp -Raw $record.seen_at), ветка $($record.branch)$memory)"
}
