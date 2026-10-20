# Аудит labels-v3 после последних исправлений — 20.10.2026

## 1. Итог

**P0 близок к реализованному контракту, но приёмка без оговорок пока преждевременна:** найдены **0 критических, 0 высоких, 1 средняя и 5 низких находок**. Исходные сценарии всех шести H/M-находок повторного аудита закрыты; новая средняя S-M1 — несовместимость синтаксиса допустимых stream ID и v3-схемы. Текущие рабочие ID и данные от неё не пострадали. Разработку P1 можно вести на текущей таксономии; перед её расширением/общей приёмкой нужно устранить S-M1. Для P2 остаются отдельные предпосылки §7, а реальный пилот не запускался. В этом аудите **3048 passed, 3 failed, 6 errors из 3057**: все девять HTTP-проверок остановились на запрете локальных портов; **438/438 labels cases прошли**, Ruff чистый. Прежний полный прогон 3057 passed с локальными портами — историческое свидетельство, не результат текущего запуска. SHA всех проверяемых данных/конфигов, исходных файлов и eval-отчётов совпали до/после.

## 2. Объём и метод

- Ветка `feature/vacancy-tags`, HEAD `d63d5db9ebed8a91daaa28066a30846bbb2f29db` **плюс текущее незакоммиченное дерево**. Рассмотрены исходный diff `8bb3a2a..d63d5db` (25 файлов, +6162/−41), текущие исправления и четыре новых code/test-файла. SHA-256 текущего tracked-патча: `c1fa34993826792fd4cd654dc6cb2a9f6e7a20e30c69994d76feb33392942efa`. Отчёт не ограничен состоянием HEAD.
- Проверены AGENTS.md, актуальный design doc целиком, optimization §3–§5, разделы README/architecture, `labeling/*.py`, accuracy/frozen/cohorts/CLI, shared validators/writers и восемь labels test-файлов. Три независимые проверки охватили metrics/frozen, schema/runtime и migration/rubric/aggregation; результаты подтверждены примерами и тестами, а не заявлением §16 о закрытии находок.
- **Режим только чтения.** Сеть/API не вызывались, `.env`/ключи не читались, зависимости не устанавливались. Python запускался через `env -i`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHON_DOTENV_DISABLED=1` и временный `sitecustomize.py`, запрещающий сеть/DNS, запись в репозиторий и открытие рабочей SQLite без `mode=ro`. Pytest/Ruff cache отключён. SQLite readers дополнительно устанавливают `PRAGMA query_only=ON`: `annotation/eval_runner.py:97,102`, `accuracy.py:292,296`, `source_compat.py:66,70`.
- Рабочий каталог — `/Users/admin/Documents/HR-Automation`. Все результаты команд/синтетические входы — **`/private/tmp/labels-v3-audit-final-esv59che`**, далее **TMP**. Ни run/reevaluate/annotate/crm/export, ни canonical `--cohort` через CLI не запускались. Когорта проверена Python-вызовом `run_relevance_accuracy`, без exposure-записи.
- Личная карьерная база и CAREER.md не загружались; дополнительная сверка с карьерным каноном не проводилась. Критерии проверены по §5.2/§13 и локальному YAML. Реальные gold notes сравнивались только на равенство, их содержание не цитируется.
- `.venv` доступен; jsonschema **4.26.0** уже установлен. Ограничение текущего full pytest: `socketserver.py:472`, `socket.bind`, `PermissionError: [Errno 1] Operation not permitted`; права на сеть не расширялись из-за исходного запрета сетевых вызовов. Девять проверок не объявлены пройденными или регрессией labels.
- Контроль неизменности: **19/19** файлов (SQLite, 10 gold JSONL, 8 YAML), **276/276** исходных tracked/проверяемых untracked-файлов и **88/88** существующих eval JSON/JSONL/MD совпали. Оба прежних аудита входят в контроль исходных файлов. Единственное добавление в репозиторий — новый отчёт с очередным свободным суффиксом; прежние отчёты не перезаписывались. Полные SHA и команды — §9.

В ссылках ниже `schema.py`, `metrics.py`, `rubric.py`, `predictions.py`, `gold_v3.py`, `aggregate.py`, `numeric.py` — модули `src/job_intake/labeling/`; accuracy/frozen/cohorts — `src/job_intake/annotation/`. **Проверено** означает чтение кода или исполненный пример; **выведено** — возможное следствие без утверждения о событии в рабочих данных. S-* — находки этого прогона; A-* — ID повторного аудита. К1–К8 — доказательства §9.

## 3. Трассировка требований

| Требование | Статус | Доказательство |
| --- | --- | --- |
| §0.1: legacy критерии/веса/пороги/stream ID не менять | реализовано | К6: diff от 8bb3a2a по config/rules/search_profiles/settings и pipeline/scoring пуст; К8 хеши конфигов |
| §0.1/§5.2: YAML буквально, confirmed/proposed не менять | реализовано | К5: 2913 байт = fenced YAML; strong_companies proposed/names=[] |
| §0.1: proposed не применяется/не меняет эффективные хеши | частично | Вложенные секции исключены; корневые anchors — документированное в модуле исключение, S-L3 |
| §0.2: P0 без платных вызовов | реализовано | Все CLI outputs api_calls=0; tests запрещают API/pipeline; К1–К4 |
| §0.3: ключи/ответы провайдера не логировать | реализовано | Provider в P0 не вызывается; env/guard К1; окончательная нормализация errors/usage ещё P2 |
| §0.4: новые gold пути/без перезаписи/атомарность | частично | gold_v3.py:228–272 staging/hardlink/owned rollback; ordinary errors покрыты. Лексически занятый dangling symlink — S-L5; crash-пределы честно названы в §6.9 |
| §0.5: SQLite mode=ro + query_only | реализовано | Readers из §2; К2–К4; все рабочие данные SHA неизменны |
| §0.6: аддитивные SQLite-миграции | не входит в P0 | Storage не изменён, К6; запланирован P3 |
| §0.7: model failure сохраняет правила/вакансию | не входит в P0 | Теггер/failure policy — P2, pipeline не менялся |
| §0.8: недоверенные команды в источнике | не входит в P0 | Prompt/S3 — P2; модель в аудите не вызывалась |
| §0.9: прежний default/two-pass/prefilter | реализовано | К6: соответствующие конфиги/pipeline/prefilter/scoring неизменны; flags ещё P3 |
| §0.10: стиль/dataclass/error codes/pytest | реализовано | Rubric dataclass, стабильные коды, atomic writer; Ruff pass; тестовое ограничение К1 |
| Общая готовность docs/README/index/full pytest | частично | Документация обновлена, но S-M1 не учтён; полный pytest текущими правами не подтверждён |
| §4.1/§13: схема, relevance/gate/reasons enums | реализовано | schema.py:16–63,161–273; schema/prediction tests и snapshot equality К7 |
| §4.1: роли из enabled streams, стабильные ID | частично | Membership labels-eval есть (accuracy.py:1288–1294,1317–1320); regex v3 уже loader — S-M1 |
| §4.2: шесть тегов и все 14 INDUSTRIES | реализовано | schema.py:32–58; значения совпадают с §4.2/§13; obsolete health_pharma_biotech отсутствует |
| §4.2: restrictive human quote / implied не блокирует | реализовано | schema.py:92–145,352–410; positive/negative gold/prediction/CLI tests |
| §4.2: other+непустой domain≤60, office≤80 | реализовано | Общие сохранённые gold/prediction schemas; rubric.py:228–234 использует ту же domain-проекцию; К7 |
| §4.3: null≠0, отсутствие тега не размечено | реализовано | schema.py:179–192; metrics.py:158–207,226–326; partial/missing/focus cases |
| §4.3: human/method/provenance/label_scope | реализовано | schema.py:175–210,484–487; migrate_case:194; перенос полей К2 |
| §4.3: chronology и RFC3339 | частично | schema.py:340–350,488–490; остаётся S-L2 |
| §4.4: closed predictions, типы, finite, безопасные ошибки | реализовано | predictions.py:63–80, schema_validation.py; score helper numeric.py:8; malformed/large-int CLI/frozen К7 |
| §4.4: source binding и уникальные строки | реализовано | accuracy.py:1323–1327, frozen_eval.py:382–389; labels CLI tests; хеши источников не менялись |
| §4.5: три builders, inline/no ref, facts→reason→decisions | реализовано | schema.py:161,215,283–337; snapshots и response tests |
| §4.5: evidence ID enum, duty/title, пустой список | реализовано | schema.py:276–324; явные scope-ID ограничивают enum. Fallback без duty-ID шире — явно описан; S3-проверка — P2 |
| §4.5: общая структура/portable parity | частично | Draft202012Validator применяется; динамические semantic checks отдельно; intentional integer extension — S-L4 |
| §5.1: LabelingConfig/enabled/model clients | не входит в P0 | §12 относит к P2/P3; соответствующие symbols отсутствуют |
| §5.2: буквальный YAML/независимые версии | реализовано | К5, rubric.py:168–215; два digest и literal rubric version, не три digest |
| §5.3: 6–10/баланс/closed fields/no notes | реализовано | rubric.py:237–318; 42 rubric cases, реальный loader К7 |
| §5.3: calibration/development/holdout guard | реализовано | rubric.py:274–318, cohorts.py:194–221,258; registered synthetic loader не подменяет guard |
| §5.3: domain_fit прецедента | реализовано | rubric.py:228–234,264–266; other/domain/none/null/type/length/extra cases К7 |
| §5.3: exposure при каждом показе модели | не входит в P0 | Caller future prompt; rubric.py:282–283; моделей не было |
| §6.5 P0: базы/other cap/upgrade только base=2 | реализовано | aggregate.py:37–69, 44 aggregate cases |
| §6.5 P0: liked/disliked/unfamiliar/own/clamp | реализовано | aggregate.py:65–77; подтверждённая формула не менялась |
| §6.5: argenx2/BVGroup1/PrizePicks0 | реализовано | test_labels_aggregate.py:104–117: явные synthetic tags, не автоматическая разметка gold |
| §6.5: Tiger — employer industry отдельно от required domain | реализовано | Тест:120–132 и §5.2: consultancy0/+focus1/pharma employer1; человеческое Tiger1 сохранено, полный набор фактов не выдумывался |
| §6.5: normalize_company | частично | aggregate.py:21–29; legal suffix с пунктуацией — S-L1 |
| §6.5: p2_candidate на шкале 0–3/None | реализовано | aggregate.py:80–86, tests:157–169. Значение 4 вне контракта, не засчитывается как дефект P0 |
| §6.5: gates/final policy/escalation/LabelResult | не входит в P0 | §12 P2; чистая формула не выдаётся за теггер |
| §6.7: QWK/MAE/±1/Spearman, empty/single-class None | реализовано | metrics.py:50–91,226–326; ручные численные примеры/tied ranks; К1/К3 |
| §6.7: missing-знаменатели/recall/precision по relevance | реализовано | metrics.py:250–307; v2 min(k,N), all missing/partial; исторический v1 отдельно |
| §6.7: confusion4×4 и missing | реализовано | metrics.py:246–256,308–309; human rows/predicted columns и missing counts |
| §6.7: очередь relevance/gate/confidence/score/uid | реализовано | metrics.py:94–107; mixed ±10**400/finite float точны, К7 |
| §6.7: P@5/10 и NDCG/random | реализовано | metrics.py:209–223,261–282; seed1729/256; baseline К3 |
| §6.7: tag accuracy/macro-F1/focus micro-F1/required_domain | реализовано | metrics.py:158–207; malformed focus не empty, required_domain применим отдельно |
| §6.7: независимые tag/gate quality samples/hooks | реализовано | accuracy.py:650, frozen_eval.py:241–332; без relevance/без прогнозов tag N сохраняется |
| §6.7: κ тегов и partial-only repeat | реализовано | metrics.py:341–382, frozen_eval.py:718–727; К7 paired2/agreement0.5/κ0 |
| §6.7: формат frozen не зависит от variant/version | реализовано | frozen_eval.py:335–355,382,477; девять marker probes/v1 malformed отвергнуты; К7 |
| §6.7: исторический replay/прежние ключи | реализовано | К4: 3/3 recorded runs валидны, 16/16 верхних ключей и evaluation shape совпали |
| §6.8: A3/B2/C1/other0 явно в baseline | реализовано | predictions.py:20–32; JSON/Markdown note К3; pipeline не применяет таблицу |
| §6.8: missing/stale saved abstains | реализовано | predictions.py:36–60 + saved reader; CLI/baseline tests |
| §6.8: label_predictions из LabelResult | не входит в P0 | §12 P2 |
| §6.9: перенос 1:1/assisted→curated/first_labeled_at | реализовано | К2:171 строка, 13 проверок равенства, canonical bytes равны |
| §6.9: неизвестное не угадывать, таблица тегов | реализовано | gold_v3.py:29,104–191; tag map v3, полные v2/v3 examples К7, dropped с причиной |
| §6.9: SHA и parse одного captured input | реализовано | gold_v3.py:297–298; source-path replacement оставляет capture/SHA/relevance2, К7 |
| §6.9: occupied out/manifest/source | частично | Regular file/source/refusal/race покрыты; lexical dangling entry — S-L5 |
| §6.9: stage пары и rollback своих inode | реализовано | gold_v3.py:228–272; ordinary second publish failure leaves source only; crash-atomic/TOCTOU isolation явно не обещаются |
| §7 P0: migrate-v3/validate по schema | реализовано | cli.py:546–584,631–662; К2 valid=True, API0 |
| §7 P0: rules/labels accuracy, защищённые inputs/readonly | реализовано | cli.py:489–506,738–800; К3/К7; schema-valid current roles принимаются, S-M1 — отдельный синтаксический разрыв |
| §7: label/export/AI/budget/concurrency | не входит в P0 | P2/P3; отсутствие команд не объявлено ошибкой P0 |
| §11: полнота и содержательность тестов | частично | 438 labels passed; S-M1/S-L1–L5 и независимый v1 fixture не покрыты полностью, §6 |
| §12: артефакты и полная приёмка P0 | частично | Основные артефакты/числа воспроизведены; S-M1 и full-pytest limitation не дают безусловную приёмку |
| §12: P1–P4 | не входит в P0 | Предпосылки/риски §7; фазы не объявлены выполненными |

## 4. Находки по убыванию серьёзности

### S-M1 — средний: labels-v3 отвергает допустимые enabled stream ID

**Проверено.** `src/job_intake/profiles.py:79` допускает `[a-z0-9][a-z0-9_-]*`, но `schema.py:64` задаёт более узкий kebab pattern, используемый gold/prediction (`:183`, `:236`, runtime `:453`). `load_streams` принимает `new_enabled_role`, `new--role`, `new-role-`; response builder включает их в enum. Gold/prediction затем отклоняют эти же enabled ID как invalid_role_label/pattern violation.

Полный synthetic CLI-repro: taxonomy содержит analytics-leadership и new_enabled_role; валидные gold/source с обычной ролью; supplied prediction.role_label=new_enabled_role. `eval accuracy --variant labels` возвращает **exit2, row1: $.role_label violates pattern**, отчёт не создан; временные inputs/SQLite bytes+mtime неизменны. Артефакты — К7, `schema-audit/dynamic-role-cli.json` и исполняемый `test_dynamic_role_cli.py`.

**Следствие:** допустимая таксономия проекта не совместима с P0 labels-eval; принадлежность enabled streams уже проверяется, но до неё мешает более узкая схема. **Предел:** четыре текущих рабочих ID имеют обычную kebab-форму; повреждения/изменения их оценок не установлено. **Рекомендация:** единый канонический ID pattern с loader; сохранить стабильные ID и пользовательские критерии. Добавить позитивные gold/prediction/response/CLI tests для `_`, повторного/конечного `-` и отдельно membership. Завышение метрик не утверждается.

### S-L1 — низкий: нормализация компании зависит от пунктуации вокруг юрформы

**Проверено:** `aggregate.py:27–29`: Acme LLC→acme; Acme (LLC)/Acme-LLC→acme llc; Acme (S.A.)→acme s a (К7). **Выведено:** после утверждения списка возможно пропустить совпадение компании. Сейчас список пуст/proposed, рабочего эффекта нет. **Рекомендация:** согласовать порядок удаления пунктуации/суффикса и добавить скобки/дефис. Это оставшийся A-L1.

### S-L2 — низкий: chronology сравнивает дни, а offset проверяется неполно

**Проверено:** `schema.py:340–350,488–490`. first=2026-10-20T20:00:00Z, labeled=2026-10-20T10:00:00Z принимаются; +00:60 принимается. Корректный UTC-порядок first=2026-10-21T00:00:00+14:00 (20-го10:00Z) → labeled=2026-10-20T23:00:00-12:00 (21-го11:00Z) ошибочно даёт invalid_labeled_at. Причина — `[:10]` и нормализация `datetime.fromisoformat` (К7). **Следствие, выведено:** неверная provenance или отказ будущего UI. На текущие метрики/миграцию date-only gold влияние не показано. **Рекомендация:** определить смешанную date/date-time семантику, проверять offset-компоненты, сравнивать timestamps в UTC. Это расширенное подтверждение A-L2.

### S-L3 — низкий: root proposed остаётся исключением из общего правила

**Проверено:** `rubric.py:194–215` сохраняет root anchors/notes и prompt hash при status=proposed; изменение proposed anchor меняет hash (К7). Module docstring:3–7 называет это намеренным, но §0/§5.2 и architecture говорят о proposed без исключения. **Выведено:** будущий prompt-builder может применить неподтверждённый root. Сейчас root confirmed, prompt ещё P2, фактического применения не показано. **Рекомендация:** уточнить эффективный root guard/документ, сохранив текущие статусы/критерии. Остаётся A-L3.

### S-L4 — низкий: runtime integer строже portable JSON Schema

**Проверено:** стандартный Draft202012Validator(gold_schema()) принимает relevance=2.0; runtime отклоняет как invalid_relevance. `schema_validation.py:53–58` намеренно переопределяет integer через type(value) is int (К7). **Выведено:** внешняя проверка опубликованной схемой не гарантирует приём приложением. Сама JSON Schema корректна: математически 2.0 — integer. **Рекомендация:** явно описать строгое runtime-расширение/паритет и тестировать эту границу; не менять оценку 2 или ослаблять критерии. Остаётся A-L4.

### S-L5 — низкий: dangling output symlink трактуется как свободный target

**Проверено на synthetic TMP.** `annotation/gold_workbook.py:45–50` использует resolve()+exists(); `gold_v3.py:242` публикует по resolved path. Заранее существующий `v3.jsonl -> foreign-target.jsonl` с отсутствующим target принимается: создаются target и manifest, symlink сохраняется (К7). **Предел:** существующий regular file не перезаписан, рабочие данные не затронуты; это не доказательство порчи данных и не критическая находка. **Выведено:** контракт «занятый output-путь отвергается» неоднозначен для существующей directory entry; запись оказывается по её target. **Рекомендация:** уточнить поддержку ссылок либо проверять lexical entry через lstat/lexists до resolve; покрыть output/manifest dangling symlink. Обычные occupied/rollback tests этого не проверяют.

## 5. Признанные отклонения и статус прежних находок

| Исходно признанное отклонение | Вердикт / правка design doc |
| --- | --- |
| company= у rules_relevance | Обосновано strong_company, §6.5 теперь соответствует функции; правка уже внесена |
| required_domain/basis в response | Обосновано фактом требуемого домена/explicit; §4.5 отражает. Удаление пустых provider деталей — явно S3/P2 |
| duty_unit_ids/title_unit_ids | Обосновано ограничениями evidence; явные списки нужны P2, совместимый широкий fallback документирован |
| label_scope | Обосновано provenance 1:1, enum и перенос подтверждены К2 |
| Tiger1 через pharma industry | Прежнее доказательство исправлено: employer consultancy ≠ pharma required domain; синтетические 0/+focus1 разделены. Человеческое Tiger1 не менялось; не обещать воспроизведения без подтверждённых тегов |
| domain_fit requirement/industry | Теперь также required_domain в quality/κ; прежний пробел закрыт |
| MAE/QWK/Spearman только covered | Обосновано отсутствием ordinal значения у missing; coverage и собственные N рядом |
| build/load только gold v2 | build использует v2 validator; load проверяет digest/policy/source, может принять вручную собранный v3. Текущий doc точнее исходной предпосылки; поддержанный v3 freeze ещё нужен |
| LabelResult/TagValue до P2 | Обосновано и явно закреплено §3/§12 |
| cohort assisted→curated | Обосновано rename v2, не assisted_llm; cohorts.py:152–158 и К4 |
| §3/§12 proposed против confirmed | Устаревший текст исправлен; root-исключение S-L3 остаётся отдельным вопросом |

| ID повторного аудита | Проверенный результат после последних исправлений |
| --- | --- |
| A-H1 | Закрыт: неизвестность/альтернативы в любом порядке не создают office_elsewhere; весь location разбирается, dropped/manifest сохранён |
| A-M1 | Закрыт: v3 bundle/row markers работают независимо от experiment variant; v1 не отключает checker; malformed отвергнут до TypeError |
| A-M2 | Закрыт: tag-only repeat измеряет labels_v3 stability при relevance N=0 |
| A-M3 | Закрыт: ±10**400 после JSON roundtrip проходит CLI/frozen/queue без float conversion; NaN/Infinity/bool отвергаются |
| A-M4 | Закрыт: реальный registered loader отвергает other без domain, object/blank/длинный domain; valid none/null и other принимаются |
| A-M5 | Исходный обход неизвестной/disabled роли закрыт, missing taxonomy больше не отключает check. Общая совместимость допустимых ID неполна — новая S-M1 |
| A-L1–A-L4 | Не входили в объём исправлений; сохранены как S-L1–S-L4 |

Первоначальные H1/H4 и M1–M6 также перепроверены по текущим tests/командам: знаменатели, captured bytes, required_domain, independent partial samples, schema structure, ordinary rollback и корректировка контроля Tiger остаются исправленными. Прежние H2/H3 в исходных сценариях теперь закрыты; S-M1 — дополнительно обнаруженная граница таксономии, не возврат старого missing-schema обхода.

## 6. Покрытие тестами

Все **438** labels cases прошли в текущем полном прогоне; число независимо подтверждено collect-only. Целевые прогоны аудиторов пересекаются и к 438 не прибавляются.

| Файл / cases | Что проверено содержательно | Оставшийся пробел |
| --- | --- | --- |
| test_labels_schema /54 | Enums/required, настоящий Draft validator, snapshots, restrictive/other/domain/provenance/nonblank, dynamic roles | INDUSTRIES не сравнивается полным tuple в одном тесте; stream-ID boundary S-M1; chronology/portable integer S-L2/L4 |
| test_labels_prediction_validation /71 | Closed/nested types/enums/duplicates/finite на глубине; exact huge integer order после JSON; membership/error privacy | Валидные loader ID только kebab; нет `_`/повторного/конечного `-` |
| test_labels_cli /34 | Полный labels read-only/schema/source binding; huge int ±; unknown/disabled reject; missing taxonomy fail; new enabled/other/null pass | New enabled test — только new-enabled-role; synthetic audit CLI выявил S-M1 |
| test_labels_gold_v3 /146 | 1:1/SHA/one-read, countries/city-state/aliases/все альтернативы/unknown, full dropped manifest, stage/publish/owned rollback | Dangling output/manifest entry нет; таблица aliases консервативна, неизвестные города требуют review по manifest |
| test_labels_metrics /26 | Ручные QWK/Spearman/NDCG, missing denominators, queue, partial tags/gate, required_domain, malformed focus, v1/v2 | Полный исторический v1 ожидаемый блок вычисляется тем же текущим helper; нужен независимо зафиксированный synthetic fixture |
| test_labels_frozen_compatibility /21 | Markers/custom variants, create/validate/v1 malformed, legacy безmarkers, tag-only repeats, huge score, version handling | Исторический тест — synthetic modern run + current v1; три реальных сохранённых runs отдельно проходят, К4 |
| test_labels_rubric /42 | YAML/hash independence/proposed sections; настоящий balanced8/calibration loader +26 valid/invalid domain cases; cohort/holdout guards | Root proposed S-L3; не все старые holdout unit tests используют реальный registry, новые domain tests используют |
| test_labels_aggregate /44 | Формула/базы/штрафы/cap/clamp/P2/normalization; employer-vs-required domain явны | Скобки/дефис legal suffix S-L1; контрольные теги синтетические, не извлечены из gold |
| prompt/ai_schema/validate/gates/tagger/pipeline/export | По §12 это P2/P3 | Отсутствие будущих тестов не считается пропуском P0 |

Дополнительные probes: девять комбинаций v3 marker key/value, tampered v1, изменённый tag-only repeat (paired2/κ0), empty predictions с сохранением tag N; полный migrate/validate на unknown/alternative locations; реальный registered precedent loader; dynamic-ID CLI; timestamp/portable-schema границы; dangling symlink и source/rollback injection. Все — TMP, без рабочих notes. Не доказанный дефект replay не объявляется находкой лишь из-за слабого теста.

## 7. Готовность к следующим фазам и риски §14

| Предпосылка | Проверенное состояние / вывод |
| --- | --- |
| P0 общий v3 contract | Основные H/M fixes подтверждены; S-M1 нужно устранить до полной приёмки/расширения taxonomy. Низкие не меняют текущие оценки, но даты важны перед P1 UI |
| V3 gold и миграция | Новый JSONL равен текущему снимку; потерь рабочих тегов нет (их N0). Unsupported aliases не угадываются, dropped требует ручной проверки; low symlink отдельно |
| V3 freeze | frozen_eval.py:144 использует v2 validator в build_bundle. load не гарантирует gold schema сам по себе. Поддержанный v3 build/save/load остаётся предпосылкой P2, не скрытой частью P0 |
| Registry/precedents | data/local/evaluation/cohorts.jsonl и data/local/labeling/precedents.jsonl отсутствуют; loader/guards готовы, реальные reasons ещё утверждает пользователь |
| Синхронизация gold | V3 сейчас актуален и равен новой миграции; UI пишет v2, поэтому будущие правки v2 не обновят v3 автоматически |
| jsonschema | Прежняя предпосылка закрыта: dependency в pyproject, установлен4.26.0, реальные schema tests проходят |
| annotation/scope.py / LabelingConfig / LabelResult/TagValue | Отсутствуют, явно P2. Pure aggregate/response builder не доказывают готовность теггера |
| Объём/независимость gold | 54 relevance/17 relevant, blind44/7; не достигнуты150+ решений/30+ relevant/holdout60. Baseline/calibration не доказывают acceptance |
| Provider/basis/usage/errors | Реальные provider adapters/field order/prompt/privacy/budget ещё не проверены. Structural metadata не заменяет P2 error_diagnostic/ledger normalization; фактическая утечка или неверный расход не утверждается |

| Риск §14 | Что уже подтверждено / что остаётся |
| --- | --- |
| Claims fallback вместо labels | annotation/ai.py не менялся, registration+adapter tests — P2 |
| Holdout leakage | Registered membership/overlap guards есть, real registry нет; future caller должен писать каждую exposure |
| Тихое изменение default | Diff pipeline/prefilter/scoring/config пуст; enabled=false parity test для нового pipeline — P3 |
| Порядок provider fields | Builder правильный, реального вызова/adapter proof нет; P2 |
| Стоимость/ретраи | В P0 API0; BudgetGuard/ledger/--max-cost и пилот — P2, платных запусков в аудите нет |
| Личные данные в prompt | Loader closed/no notes, YAML английский; полный prompt+diagnostics тест впереди; происхождение ручного reason loader доказать не может |
| Рост _evaluate_record | Pipeline неизменён; новые ветки/rebuild stream ещё P3 |

## 8. Предлагаемые исправления design doc

Документ в аудите не изменялся. Рекомендации не меняют критерии пользователя.

| Раздел | Было → стало / требуемое уточнение |
| --- | --- |
| §4.1/§4.4/§4.5/§11/§16 | Enabled role принимается → единый синтаксис loader и v3, плюс membership; tests с `_`, повторным/конечным `-`. Stable IDs не переименовывать |
| §4.3/§4.5 | Chronology и RFC3339 → определить mixed date/date-time semantics, UTC comparison, offset ranges; portable integer extension указать явно |
| §6.5/§11 | «Без пунктуации и юрформ» → подтвердить порядок нормализации и случаи скобок/дефиса |
| §0/§5.2/architecture | Любой proposed неэффективен → явно описать root anchors и future prompt guard, сохранив confirmed/proposed текущего YAML |
| §6.9/§11 | Occupied пути отвергаются → определить symlink policy/lexical entry, добавить dangling output/manifest tests; crash-atomicity не обещать |
| §6.7/§11 | Совместимость v1 → добавить независимый фиксированный synthetic fixture полного v1-блока, не только ожидание тем же helper |
| §16 | «Все H/M закрыты» → исходные шесть сценариев подтверждены; общая taxonomy compatibility требует S-M1. Исторический3057 passed отделять от сетево ограниченного текущего прогона |
| §12/§14 | Предпосылки P2 → сохранить v3 freeze/registry/scope/config/adapters/privacy/budget как незавершённые, не называть baseline гейтом |

## 9. Приложение: команды и ключевые выводы

### К1. Среда, тесты и Ruff

`TMP=/private/tmp/labels-v3-audit-final-esv59che`. Все Python/CLI-команды ниже запускались с префиксом:

```sh
env -i HOME=/Users/admin PATH=/usr/bin:/bin:/usr/sbin:/sbin TMPDIR=/private/tmp LC_ALL=C PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH="$TMP"
```

В TMP размещён `sitecustomize.py` с запретами §2. Команды (логи только TMP):

```sh
.venv/bin/pytest -p no:cacheprovider --basetemp="$TMP/pytest-tmp"
.venv/bin/ruff check src tests --no-cache
.venv/bin/pytest -p no:cacheprovider --collect-only -q tests/test_labels_*.py
```

**3057 collected; 3 failed, 3048 passed, 6 errors in 35.34s**; Ruff — All checks passed! Отдельный collect-only —438 cases; все их файлы прошли в full log. `pytest.log`, `ruff.log`, `labels-collected.txt`, `labels-counts.json`.

Все девять препятствий — PermissionError на bind локального HTTP-сервера:

- test_discovery_crm.py::test_discovery_http_blind_labels_and_report;
- test_job_archive.py::test_http_archive_requires_csrf_then_can_restore[False/True];
- test_crm_server.py: два HTTP-теста;
- test_gold_web_server.py: четыре HTTP-теста.

Это ограничение условий аудита; успешный текущий полный pytest не заявлен. Прошлый 3057 passed/39.62s из §16 design doc относится к запуску исправлений с разрешёнными портами. В этом аудите сетевые вызовы запрещены, зависимости не скачивались.

### К2. Миграция/validate

```sh
.venv/bin/job-intake gold migrate-v3 --source data/local/gold/vacancy-tags-v2-labeled.jsonl --out "$TMP/migrated.jsonl"
.venv/bin/job-intake gold validate --gold "$TMP/migrated.jsonl" --output "$TMP/validation.json"
```

Exit0 обеих. 171 строка; manifest: labeled_rows55, decision_labeled44, relevant7, relevance_only10, blind44/curated11/assisted_llm0; migrated_tags0/dropped_tags0. Valid=True,171/171 valid; relevance_labeled54, errors=[], source_problems={}. Полные тройки и все relevance — разные выборки, поэтому relevant7 в manifest не заменяет17 в метриках.

Проверки1:1: job_uid/role_label/relevance/gate_outcome/source/source_hash/text_version/labeled_at/label_scope/notes; method map и first_labeled_at=labeled_at; canonical bytes equal. Реальные notes не опубликованы. Tag map=`v2-to-v3-tags-v3`, rubric_version=`labels-rubric-2026-10-20`; api_calls0/database_access=none. Source SHA=`6cd7a884e2e7c13e7fd189a8dfe336f0857db608c0537d19d2d15674889a4d3d`, output SHA=`936f6259a2343d7ff882f447539a9b137b67eea4eb2580edd8c91bbb3163309a`. Детали без gold text — `migration-summary.json`.

### К3. Rules baseline

```sh
.venv/bin/job-intake eval accuracy --variant rules --gold data/local/gold/vacancy-labels-v3.jsonl --output "$TMP/rules.json"
```

Exit0, read_only, API0; JSON/Markdown явно описывают **A→3/B→2/C→1/role other→0**, labeler rules:tier-map-v1. Mapping не применяется в pipeline. С data/local/eval/accuracy-rules.md совпали N54/QWK≈0.373/recall12/17.

| Показатель | Результат |
| --- | --- |
| N / coverage | 54 /54 из54 |
| QWK / MAE / Spearman | 0.373134 /0.888889 /0.389303 |
| Exact /±1 | 21/54 /42/54 |
| Recall≥2 /precision≥2 | 12/17 /12/24 |
| P@5 /P@10 | 4/5 /8/10 |
| NDCG@5 /random | 0.874799 /0.261829 |
| NDCG@10 /random | 0.771175 /0.261603 |

Manual tag N=0 по рабочему gold; это не измерение точности будущего tagger. Output — TMP/rules.json и rules.md.

### К4. Когорта/saved/frozen

```sh
.venv/bin/job-intake eval accuracy --variant saved --gold data/local/gold/vacancy-tags-v2-labeled.jsonl --output "$TMP/saved.json"
```

Exit0. С after-apply-accuracy.json совпали **все 16 верхних ключей**, section key sets evaluation/operational/stability/readiness/selection и recursive evaluation shape. Уточнение прежнего аудита: там ошибочно написано 17; фактическое число 16, равенство наборов подтвердилось. Значения не объявляются идентичными: old gold_rows163, current171 (`legacy-keys.json`).

Прямой `run_relevance_accuracy(...variant='rules',cohort=Path('data/local/eval/p1-2026-10-19/cohort.json'))` без CLI/exposure: выбранные163 строки =blind44/curated3/method-null116; явный blind-поднабор **N44, recall7/7, precision7/19, QWK0.433663** (`blind-summary.json`).

`load_bundle`+`validate_run` для всех сохранённых пар —pass:

| Run | Bundle |
| --- | --- |
| baseline-run.json | bundle.json |
| candidate-run-v2.json | release-bundle-v2.json |
| release-baseline-run-v2.json | release-bundle-v2.json |

Все пути — data/local/eval/p1-2026-10-19; вывод — `frozen-summary.json`. Файлы прогонов не переписывались.

### К5. YAML/версии/хеши

Fenced YAML §5.2 = config/labeling_rubric.yaml побайтно: **2913 bytes**, SHA=`9b7f767c2817df6a2ed6e5b1738d7586c21ae9ba5cc1ca707d36fc806e10f4d2`.

- rubric version — literal labels-rubric-2026-10-20;
- prompt hash=`8970cd265579fbc293ccc0ed3e78f09990cf37f6bfea17bc5d4168bbb7d395d8`;
- aggregation hash=`9bd2773daa962e8bb8879b5a49594b6de2d86ce7d7b27888f98450edd648899f`.

Два digest независимы от version; anchor меняет prompt hash, aggregation — aggregation hash, version — только version, вложенный proposed content — ни применение, ни эффективные хеши. Это проверено rubric tests; root proposed исключение подтверждено отдельно S-L3. precedents_hash — отдельный content hash, не вычисляемая rubric version. Summary — rubric-summary.json.

### К6. Проверка регрессий/предпосылок

```sh
git diff --exit-code 8bb3a2a -- config/search_profiles.yaml config/rules.yaml config/settings.yaml src/job_intake/pipeline.py src/job_intake/annotation/prefilter.py src/job_intake/scoring src/job_intake/storage
```

Exit0, diff пуст. Наличие schema_validation/numeric и runtime jsonschema проверено; snapshot equality подтверждён. Отсутствие cohort registry, precedents file, scope.py, LabelingConfig и LabelResult/TagValue проверено поиском файлов/символов; они не выдаются за реализованные. Полный оригинальный diff сохранён TMP/original.patch, текущий — TMP/worktree.patch.

### К7. Независимые synthetic проверки

| Артефакт под TMP | Проверенное содержание |
| --- | --- |
| metrics-audit/probes.json | Девять v3-marker combinations отвергнуты; v1 malformed confidence отвергнут; changed tag-only repeat paired2/agreement0.5/κ0; empty predictions не стирают human tag N; huge int queue/CLI/frozen; role membership |
| schema-audit/dynamic-role-repro.json и dynamic-role-cli.json | load_streams/response принимают ID с `_`, v3 gold/prediction отвергают; CLI exit2/no report/inputs unchanged — S-M1 |
| schema-audit/test_dynamic_role_cli.py | Исполняемая synthetic CLI-регрессия аудита, 1 passed; в repository не добавлялась |
| schema-audit/repro.json | Snapshot equality, large-int order, даты/offset/portable2.0 — S-L2/S-L4 |
| migration-audit/results.json, repro.py | Полные v2→v3/manifest/validate для unknown/alternatives; real registered domain loader; normalization/root proposed; captured bytes/ordinary rollback; dangling symlink |

Целевые тесты аудиторов: 237 metrics/nearby frozen+accuracy,232 migration/rubric/aggregate,180 schema/prediction/CLI/frozen. Это перекрывающиеся выборки, не дополнительные уникальные тесты. p2_candidate для relevance4/100 исследован как defensive край вне допустимой шкалы; H/M из него не выводится. Реальные providers/prompt/privacy/field order/budget, crash-atomicity и изоляция от произвольной сторонней подмены файловой системы не проверялись.

### К8. Контроль неизменности

До/после всех запусков: **19/19 protected,276/276 исходных файлов,88/88 существующих eval files совпали**. Снимки — before/after-sha256.json, worktree-before/after-sha256.json, eval-before/after-sha256.json. Обязательные данные:

| Файл | SHA-256 до = после |
| --- | --- |
| `data/local/job_intake.db` | `35fb18d4a28e7222cd6bc9de178df8fc992f4a4a02b89897f62b7432098ac075` |
| `data/local/gold/vacancy-labels-v3.jsonl` | `936f6259a2343d7ff882f447539a9b137b67eea4eb2580edd8c91bbb3163309a` |
| `data/local/gold/vacancy-tags-v1.jsonl` | `2165918bfe1185efb4aced9c1eac73d793177df9acd22390ef904df67ff3a533` |
| `data/local/gold/vacancy-tags-v2-labeled-xlsx-2026-10-17.jsonl` | `710eebb8ef419557cb22eaea3721786a504aad633c3decb3de67e12665f87256` |
| `data/local/gold/vacancy-tags-v2-labeled.before-adapty-2026-10-17.jsonl` | `6fda04402b67e13bdbca50dad6c592d2efd500dab00292309863cea79244fccf` |
| `data/local/gold/vacancy-tags-v2-labeled.before-user-positives-2026-10-17.jsonl` | `2be3a198bca7df14faf1690b2bff22243cfed285be5b5f454860bb9b89e29152` |
| `data/local/gold/vacancy-tags-v2-labeled.jsonl` | `6cd7a884e2e7c13e7fd189a8dfe336f0857db608c0537d19d2d15674889a4d3d` |
| `data/local/gold/vacancy-tags-v2-user-links-2026-10-19.jsonl` | `f9ec399df7bb5c638b90190a8eb9dd017e6ecc5d7ba44ad5eead71804684fce7` |
| `data/local/gold/vacancy-tags-v2-user-positive-adapty-2026-10-17.jsonl` | `3ca9bfa970091f312219c66fa8357f07171dd92e8a5470563c0109d067c80757` |
| `data/local/gold/vacancy-tags-v2-user-positives-2026-10-17.jsonl` | `bcd7dce016c096be837c19eb49d7dcfbee9e642b8796b16465010a565aeae836` |
| `data/local/gold/vacancy-tags-v2.jsonl` | `01566b36b6d2401b913d5c28940e758075ae059ac0533b13da8568aa3880456d` |

Восемь YAML также неизменны: company_watchlist, eval_prices, gmail_search, labeling_rubric, purpose_rubric, rules, search_profiles, settings. Оба прежних аудита сохранены. Новый отчёт не входит в снимок исходных файлов до запуска; его добавление — единственная запись аудита в repository.

Новые относительно HEAD code/test-файлы, включённые в проверяемую ревизию:

| Файл | SHA-256 |
| --- | --- |
| `src/job_intake/labeling/numeric.py` | `0d710771aa66ee90d1c8e1e5a8b01dc8bbc6b2d528f7172479a34081bcefa6ad` |
| `src/job_intake/labeling/schema_validation.py` | `5ef9f8fae60f95e553b54d9e22e39a9305a1bca8165d34e1cef7b3b8d8ce9724` |
| `tests/test_labels_frozen_compatibility.py` | `197a09745f3672706b684fa4e8a189327a44bc173062ffb66000a19fc5afecde` |
| `tests/test_labels_prediction_validation.py` | `c167e03b70dd4d53247fbd4a846c4a10966cce794c9b25000478c0ae1815c1ea` |

Рабочая ветка/HEAD не менялись. Полный список исходных изменений — TMP/git-status-before.txt; SHA tracked patch указан в §2. Выходы миграции, CLI/eval и synthetic проверок находятся только в TMP; рабочие snapshots/reports повторно не записывались.
