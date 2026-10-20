# Повторный аудит labels-v3 — 20.10.2026

## 1. Итог

**P0 существенно улучшен, но к окончательной приёмке ещё не готов.** В текущем рабочем дереве найдены **0 критических, 1 высокая, 5 средних и 4 низкие находки** (§4). Из десяти прежних H/M-находок восемь закрыты в заявленном контракте, H2 и H3 закрыты частично; появились дополнительные пограничные случаи. Основные метрики воспроизведены, gold перенесён 1:1, данные и критерии сохранены. P1 можно разрабатывать, но прежде чем накапливать новый эталон, следует закончить защиту миграции географии; для пилота P2 нужно также закрыть frozen/precedent-валидацию, проверку таксономии ролей и стабильность частичных меток и предпосылки §7. Отсутствие P1–P4 само по себе дефектом не считается. В этом аудите **2955 passed, 3 failed, 6 errors из 2964**, все девять препятствий связаны с запрещённым локальным HTTP-портом; **все 345 labels-тестов прошли**, Ruff чистый (К1). Поэтому полный зелёный pytest в условиях текущего аудита не подтверждён. Сводная оценка не опирается на прежнее заявление «все H/M исправлены»: исправления проверены заново.

## 2. Объём и метод

- Проверена ветка `feature/vacancy-tags`, HEAD **`d63d5db9ebed8a91daaa28066a30846bbb2f29db` плюс незакоммиченные исправления**. Только `git diff 8bb3a2a..d63d5db` не описывает текущую реализацию: дополнительно проверены `git diff HEAD` и три новых untracked code/test-файла. Патч tracked-файлов сохранён во временный каталог; SHA-256 — `2358427d467d4df8d7c4021a8f7207fe2ce45d6876169410c3c00b2bc848757f`. SHA трёх новых файлов и список исходных изменений — К8.
- Прочитаны AGENTS.md, актуальный design doc целиком, optimization §3–§5, README «Единая шкала vacancy-labels-v3», architecture «AI-кеш и переоценка», все `labeling/*.py`, затронутые accuracy/frozen/cohorts/CLI и shared JSONL parser, YAML, оба снимка схем и восемь `test_labels_*.py`. Прочитаны исходный diff и текущие исправления. Проведены три независимые проверки: метрики/frozen, schema/runtime, миграция/рубрика; интеграционные прогоны выполнены отдельно.
- Учитывались исходный аудит и обновлённый контракт §15 design doc, но их выводы не принимались как доказательство. Это повторная проверка текущего состояния, не аудит только прежних коммитов. Текущий design doc уже исправил часть предпосылок исходного запроса: jsonschema установлен; domain_fit измеряется по трём компонентам; пример Tiger уточнён; «вся рубрика proposed» убрано.
- Сеть и платные вызовы не использовались; API-ключи и реальный `.env` не читались. Основные интеграционные и pytest-прогоны запускались с очищенным `env -i`, `PYTHON_DOTENV_DISABLED=1`, `PYTHONDONTWRITEBYTECODE=1`. Временный `sitecustomize.py` запрещал соединения/DNS, чтение проектного `.env`, запись Python в репозиторий и открытие рабочей SQLite без `mode=ro`. Независимая проверка миграции/рубрики использовала `python -B`, отключение dotenv/bytecode и собственный audit hook. Независимая schema-проверка запускалась только с отключением bytecode и sandbox, без `env -i`/sitecustomize/отдельного guard; её код не обращался к loader конфигов, сети, `.env`, ключам или рабочим данным. Основные воспроизведения этих краёв дополнительно выполнены с общим guard. Кеши pytest/Ruff отключены.
- Рабочая SQLite читалась существующими функциями с **`mode=ro` + `PRAGMA query_only=ON`**: `annotation/eval_runner.py:97,102`, `accuracy.py:291,295`, `source_compat.py:66,70`. Команды run/reevaluate/annotate/crm/export не запускались. Синтетические SQLite/cohort/gold/output разрешались только во временном каталоге.
- Все выходы запусков — **`/private/tmp/labels-v3-reaudit-t_p7vhvh`**, далее TMP. Канонический `--cohort` через CLI не использован: когорта проверена прямым `run_relevance_accuracy` из Python. Exposure не дописывался. Личная карьерная база не загружалась; дополнительная разрешённая сверка её канона не проводилась. Реальные `notes` не цитируются; проверялось только их равенство.
- До/после проверены SHA-256 БД и 10 gold JSONL, а также 8 YAML-конфигов: **19/19 совпали**. До заключительной сверки совпадали все **283** хеша исходных tracked/проверяемых untracked-файлов. В финальной сверке **282/283** совпали: вне действий аудита в README добавлены три строки ссылки CAREER.md; точное исключение этой вставки в памяти возвращает исходный SHA. Labels-раздел README, исходники, тесты, конфиги и прежний аудит неизменны; автора параллельной вставки не определяли. Аудит существующие файлы не записывал; новый результат аудита записан по свободному пути с суффиксом `-2`.
- `.venv` доступен, симлинк на `/Users/admin/.venvs/hr-automation`; jsonschema **4.26.0** уже присутствует, ничего не устанавливалось. **Ограничение:** девять HTTP-тестов остановились на `socket.bind` с `PermissionError: [Errno 1] Operation not permitted`. Сетевые права не расширялись, поскольку повторный запрос вновь запрещает сетевые вызовы. Заявление design doc §15 о прошлом прогоне 2964 passed относится к другому запуску с локальными портами и не подменяет текущий результат.

Ниже **проверено** — чтением конкретного контракта или выполненным примером; **выведено** — практическое последствие без утверждения, что оно уже случилось в рабочих данных. Ссылки `schema.py`, `metrics.py`, `rubric.py`, `gold_v3.py`, `predictions.py` — внутри `src/job_intake/labeling/`; `accuracy.py`, `frozen_eval.py`, `cohorts.py` — внутри `src/job_intake/annotation/`. К1–К8 — проверки из §9.

## 3. Трассировка требований

Оценка относится к текущему design doc; изменения относительно исходного запроса явно отмечены. Планы P2/P3 не превращены в требования P0.

| Требование | Статус | Доказательство |
| --- | --- | --- |
| §0.1: legacy критерии, веса, пороги и stream ID не менять | реализовано | К6: diff от 8bb3a2a по rules/search_profiles/settings/pipeline/scoring пуст; конфиги SHA прежние |
| §0.1/§5.2: утверждённый YAML буквально, strong_companies proposed/пуст | реализовано | К5: 2913 байт = YAML-блок §5.2; names=[], status=proposed |
| §0.2: P0 без платных вызовов | реализовано | CLI миграции/eval не строит pipeline; `test_labels_cli.py` запрещает API; К1–К4 |
| §0.3: ключи/ответы провайдера не логировать | реализовано | В P0 нет provider call; текущие запуски `.env` отключают; безопасный состав будущих usage/errors нужно завершить до P2 (§7) |
| §0.4: gold не перезаписывать, новые пути и атомарная запись | реализовано | `gold_v3.py:225–269`: staging/no-overwrite hardlink/owned inode rollback; К7; crash-atomic пары не обещается в текущем §6.9 |
| §0.5: eval/export только mode=ro/query_only | реализовано | `eval_runner.py:97,102`, `accuracy.py:291,295`, `source_compat.py:66,70`; К8 |
| §0.6: миграции SQLite только аддитивные | не входит в P0 | Storage не изменён; §12 относит его к P3; К6 |
| §0.7: model failure не удаляет и не понижает вакансию | не входит в P0 | Нет теггера; §12 P2; pipeline неизменён |
| §0.8: недоверенный текст в промпте | не входит в P0 | Prompt/S3 относятся к P2; аудит модель не вызывал |
| §0.9: default поведение, двухпроходная разметка, prefilter | реализовано | К6: соответствующие config/pipeline/prefilter/scoring не изменены |
| §0.10: dataclass/error codes/atomic writing/pytest/style | частично | `Rubric` dataclass; `RubricError.code`; gold stable error codes; К1 Ruff pass; A-M1/A-M3 — поздние исключения без нужной диагностики |
| Общая готовность: README/architecture/index и тесты | частично | Документация P0 обновлена; §15 преждевременно полностью закрывает H2/H3; полный pytest ограничен портами |
| §4.1/§13: LABELS_SCHEMA, relevance/gate/reasons enums | реализовано | `schema.py:16–63,168–191`; проверки enum/null/bool в schema/prediction tests |
| §4.1: роли из enabled streams, не фиксированный список | частично | Gold: `schema.py:448–455`, CLI передаёт load_streams; response builder :330–332 получает role_ids. Prediction checker проверяет синтаксис ID, но текущий eval entrypoint не проверяет членство — A-M5 |
| §4.2: шесть тегов и все 14 INDUSTRIES | реализовано | `schema.py:32–54`; перечень совпал с §4.2/§13, без старого health_pharma_biotech |
| §4.2: restrictive human quote, implied не ставит блокер | реализовано | `schema.py:116–122,371–406`; gold runtime и структурные tests. Frozen custom-variant может обойти машинный checker, A-M1 |
| §4.2: в сохранённых gold/prediction other требует непустой domain≤60; office≤80 | реализовано | `schema.py:113,129–145,379–389`; schema/runtime/CLI tests; для прецедентов есть отдельный разрыв A-M4 |
| §4.3: null≠0, отсутствие тега неразмечено | реализовано | `schema.py:184,191`; метрики v2 различают missing/empty focus; К7 |
| §4.3: human/method/provenance/label_scope | реализовано | `schema.py:168–214,484–487`; `gold_v3.py:207–220`; assisted→curated; snapshots 1:1 К2 |
| §4.3: корректность дат/времени | частично | `schema.py:341–350,488–490`; A-L2: срез дня и некорректный offset |
| §4.4: prediction required/closed/legacy fields/source binding | частично | `predictions.py:64–77`, `schema_validation.py`; accuracy binding `accuracy.py:1323`; A-M1/A-M3 |
| §4.5: три builders, inline/no $ref, facts→reason→decisions | реализовано | `schema.py:161,215,283–337`; snapshot equality и response tests |
| §4.5: evidence enum source-ID; duty+title/focus duty; пустой ID-list | частично | `schema.py:292–304,315–324`; при явных scope-ID верно, fallback без duty-ID разрешает все; текущий §4.5 это оговаривает, S3 ещё P2 |
| §4.5: стандартная структурная проверка и runtime parity | частично | Draft202012Validator применяется, исходные M5-примеры закрыты; integer-override требует документации, A-L4 |
| §5.1: LabelingConfig/enabled/modes/model clients | не входит в P0 | Нет LabelingConfig/labeling блока; §12 P2/P3; это сохранённая предпосылка |
| §5.2: два хеша и независимое version | реализовано | `rubric.py:194–212,82`; К5: изменения anchor/aggregation/version независимы |
| §5.2: proposed не применяется и не влияет на хеши | частично | Вложенные разделы :191–212 исключены; root anchors — исключение, A-L3 |
| §5.3: 6–10, баланс, closed fields, отсутствие notes | реализовано | `rubric.py:224–294`; тесты :155; loader не копирует notes |
| §5.3: registered calibration/development и holdout guard | реализовано | `rubric.py:295–309`, `cohorts.py:194–221`; синтетический registered loader К7 |
| §5.3: корректность вложенных domain_fit прецедентов | отклонение | `rubric.py:251–257`; A-M4: other без domain и domain-object приняты |
| §5.3: экспозиция каждого показа модели | не входит в P0 | Caller обязан записать exposure при будущем показе; `rubric.py:271–274`; модели не вызываются |
| §6.5 P0: таблица баз, other cap, upgrades только base=2 | реализовано | `aggregate.py:48–69`; aggregate tests 44 случая |
| §6.5 P0: liked/disliked/unfamiliar/own/clamp | реализовано | `aggregate.py:70–77`; подтверждённая формула не менялась |
| §6.5: argenx→2/BVGroup→1/PrizePicks→0 | реализовано | `test_labels_aggregate.py:104–117`: явные синтетические теги, не автоматическое извлечение |
| §6.5: Tiger — employer industry отдельно от required domain; точное 1 без подтверждённых тегов не обещается | реализовано | Текущий §5.2 больше не обещает это без фактов; тест :120–132 показывает consultancy=0, +focus=1, pharma employer=1; gold не изменён |
| §6.5: normalize_company | частично | `aggregate.py:21–29`; скобки/дефис перед LLC остаются, A-L1 |
| §6.5: p2_candidate на допустимой шкале | реализовано | `aggregate.py:80–86`; tests с relevance None/0–3 и аналитическими/неаналитическими функциями |
| §6.5: gates/escalation/final_policy/LabelResult | не входит в P0 | §12 P2; `aggregate.py:3–4` |
| §6.7: QWK/MAE/±1/Spearman и single-class None | реализовано | `metrics.py:49–90,291–307`; известные значения, tied ranks, пустые входы; К1/К7 |
| §6.7: missing-знаменатели, recall/precision по relevance | реализовано | `metrics.py:249–305`; H1 закрыт; explicit v1 replay сохраняет историческую семантику |
| §6.7: confusion 4×4 и missing | реализовано | `metrics.py:245–255,307–308`; отдельный missing по human-классам, как в обновлённом doc |
| §6.7: queue relevance/gate/confidence/score/uid | частично | `metrics.py:93–106`; корректно для обычного конечного score, но разрешённый большой int обрушает сортировку, A-M3 |
| §6.7: P@5/10, min(k,N), partial | реализовано | `metrics.py:263–281`; missing tests и frozen denominator; A-H1 не связан с P@k |
| §6.7: NDCG и seeded random рядом | реализовано | `metrics.py:209–222,260–282`; численные ideal/reverse tests, seed1729/256 |
| §6.7: tag accuracy/macro-F1/focus micro-F1/required_domain | реализовано | `metrics.py:158–205`; применимый required_domain включён, malformed focus — missing, К7 |
| §6.7: независимые partial tag/gate выборки и quality hooks | реализовано | `metrics.py:283,314–325`; `accuracy.py:651–656`; `test_labels_frozen_compatibility.py:102` |
| §6.7: compare_stability по relevance и тегам | частично | Standalone `metrics.py:345–380` корректен; frozen repeat hook теряет tag-only, A-M2 |
| §6.7: v1 replay / старые frozen / прежние ключи | реализовано | К4: 3/3 saved runs pass, старые ключи сохранены; v1 differential 200/200 equal |
| §6.8: A→3/B→2/C→1/other→0; явное пояснение | реализовано | `predictions.py:21–34`; К3 JSON/Markdown. Не применяется в pipeline, К6 |
| §6.8: missing/stale abstains | реализовано | `predictions.py:42–60`; saved-reader и CLI baseline tests |
| §6.8: label_predictions из LabelResult | не входит в P0 | Текущий §12 явно относит к P2 |
| §6.9: перенос 1:1/assisted→curated/first_labeled_at | реализовано | К2: 171 строка, поля равны, новый JSONL = canonical bytes |
| §6.9: таблица v2 тегов, неизвестное не угадывать | частично | Authorization/common office случаи исправлены; fallback office_elsewhere остаётся ошибочным, A-H1 |
| §6.9: единые captured bytes для input SHA/JSONL | реализовано | `gold_v3.py:294–295`; К7 race reads=1; H4 закрыт |
| §6.9: refusal occupied out/manifest/source | реализовано | `gold_v3.py:289–293`; tests occupied/race; исходные пути сохранены |
| §6.9: staging/rollback пары без перезаписи | реализовано | `gold_v3.py:225–269`; К7 second-link failure; crash-atomicity явно исключена в §6.9 |
| §7 P0: migrate-v3/validate schema recognition | реализовано | `cli.py:546–584,631–662`; К2 |
| §7 P0: accuracy rules/labels, protected inputs, readonly DB | частично | CLI-пути защищены, source bound, обычные malformed отклоняются; большой int даёт поздний сбой A-M3 |
| §7: label --ai/budget/export/offline/concurrency | не входит в P0 | §12 P2/P3; отсутствующие команды не объявляются дефектом |
| §11: обязательные P0-тесты и содержательность | частично | 345 cases pass; новые непокрытые края — §6; предусмотренные P2/P3 тесты вне объёма |
| §12: P0 артефакты/метрики/гейты готовности | частично | Артефакты К2/К3 есть; A-H1/A-M1–M5 и HTTP-ограничение не позволяют принять P0 целиком |
| §12: P1–P4 | не входит в P0 | Разделение фаз сохранено; предпосылки и риски §7 |

## 4. Находки по убыванию серьёзности

### A-H1 — высокий: неопределённый офис и список с São Paulo всё ещё превращаются в ограничение

**Проверено:** `gold_v3.py:149–156` анализирует первый фрагмент location и короткий список неоднозначных значений, затем безусловно выбирает office_elsewhere. Полная синтетическая миграция с обоими валидаторами (К7):

| v2 office_location / синтетическая цитата | Получено |
| --- | --- |
| `Location TBD` / `Onsite work is required; office location is TBD.` | office_elsewhere, basis=explicit, dropped=[] |
| `TBC` / аналогичная фраза о ещё неизвестном месте | то же |
| `London / São Paulo` / `The onsite role is based in London or São Paulo.` | то же |

Во всех примерах v2_valid=True и v3_valid=True. **Следствие, выведено:** в human gold появляется ложный ограничивающий факт, хотя офис не определён или São Paulo остаётся предложенным вариантом. Это остаток прежнего H2; Campinas/state и неизвестная authorization country уже исправлены. На рабочем gold ущерб не обнаружен: migrated_tags=0. **Рекомендация:** разбирать все альтернативы, не доказывать «вне SP» отрицанием короткого списка; неоднозначные случаи не переносить и учитывать в manifest. Нужны тесты фраз неизвестности и SP после разделителя. Текущий §6.9:916 и §15 утверждают более полное исправление, чем подтверждено.

### A-M1 — средний: custom frozen variant обходит v3 checker

**Проверено:** `frozen_eval.py:341–343` учитывает наличие schema и **run.variant**, но не row.variant. При run.variant=candidate и row.variant=labels строка без schema принята `create_run` и `validate_run` (:466–467). Синтетический restrictive tag с basis=implied получает tag accuracy **1/1**; добавление правильного schema заставляет checker отклонить тот же тег по `basis violates const`. Через этот обход `confidence={}` вызывает TypeError в очереди (К7).

**Следствие:** подтверждённое принятие запрещённого формата на пути frozen; общая защита прежнего H3 неполна, хотя исходный `eval accuracy --variant labels` защищён. **Рекомендация:** явно определять формат независимо от имени экспериментального варианта, учитывать v3-маркеры строки либо объявлять схему в bundle/run; сохранить исторический replay отдельно. Тесты отсутствующего schema сейчас проверяют только run.variant=labels (`test_labels_frozen_compatibility.py:78`).

### A-M2 — средний: frozen comparison не измеряет κ для tag-only v3

**Проверено:** `frozen_eval.py:708–712` вызывает labels stability только при integer relevance. Валидные candidate/repeat с relevance=null и двумя размеченными function_core имеют quality tag N=2, но не получают `stability.labels_v3`. Прямой `metrics.compare_stability` на тех же прогнозах даёт **κ=1, paired=2** (К7). Legacy stability function_core тоже не измеряет.

**Следствие:** теряется предусмотренное §6.7 измерение стабильности частичных меток; отсутствие результата нельзя выдать за согласие моделей. **Рекомендация:** вызывать v3 stability по формату/наличию соответствующих тегов, независимо от ordinal relevance. Добавить partial-only repeat comparison. Прежний M2 для качества закрыт; это соседний непокрытый hook.

### A-M3 — средний: большой допустимый JSON integer обрушает метрики

**Проверено:** prediction с `fit_score=10**400` после обычного JSON roundtrip проходит `check_prediction_rows`. `relevance_scale`/`queue_order` падают с **OverflowError: int too large to convert to float**. Причины: `schema_validation.py:21` проверяет finite только для float, schema допускает number; `metrics.py:98` и `frozen_eval.py:294` вызывают math.isfinite для int (К7).

Число математически конечно и допустимо в JSON; это не NaN и не ошибка сериализации. **Следствие:** обещанный принимаемый числовой формат не поддержан downstream, вместо диагностического отказа возникает авария. Рабочего score такого размера не найдено и не предполагалось. **Рекомендация:** сделать обработку int безопасной либо определить технический диапазон и отклонять его нарушение до метрик с номером строки/полем. Не менять пользовательские веса/пороги. Регрессии большого int нет.

### A-M4 — средний: loader прецедентов принимает повреждённый domain_fit

**Проверено:** `rubric.py:251–257` проверяет enum, но не условие other+domain и тип/лимит domain. Реальный `load_precedents` с временным registered calibration, role_ids и восемью сбалансированными примерами принимает как отсутствие domain при required_domain=other, так и `domain={"arbitrary":"synthetic"}`. Результат возвращён без изменения (К7).

**Следствие, выведено:** будущий промпт может получить прецедент, противоречащий единому v3 тегу §4.2, хотя P0 loader объявил его валидным. Модель сейчас не вызывалась. **Рекомендация:** переиспользовать общие ограничения доменных фактов в валидаторе прецедентов; проверить required_domain/requirement, непустую строку≤60 и негативные вложенные случаи. Gold/prediction-часть прежнего M3 исправлена; это отдельная граница loader.

### A-M5 — средний: labels-eval принимает роль вне enabled streams

**Проверено:** §4.1 ограничивает role_label текущими enabled streams плюс other; §4.5 оставляет проверку текущей таксономии runtime. `predictions.py:64–77` проверяет лишь закрытую структурную схему и синтаксис ID. В `accuracy.py:1317–1324` после checker проверяется source binding, но нет проверки роли по доступным версиям/таксономии (:1290–1292). Полный CLI-repro (К7): в копии baseline predictions во временном каталоге у одной вакансии с human role заменено только role_label на `audit-nonexistent-role`. `eval accuracy --variant labels` завершился exit 0; неизвестная роль сохранена в итоговом отчёте. API calls=0, database=read-only.

**Следствие:** P0 принимает семантически недопустимую роль, вопреки объявленному runtime-контракту. Завышение метрик не утверждается; relevance baseline при этой замене остался прежним. **Рекомендация:** перед оценкой проверять supplied role_label по enabled profile IDs плюс other/null и выдавать безопасную диагностику строки/поля. Структурный checker может оставаться независимым от конфигурации. Добавить CLI-регрессию неизвестной роли и позитивные other/null. Это новая находка; критерии/ID пользователя менять не требуется.

### A-L1 — низкий: юридическая форма нормализуется непоследовательно

**Проверено:** `aggregate.py:27–29`: `Acme LLC → acme`, но `Acme (LLC)` и `Acme-LLC → acme llc` (К7). **Следствие, выведено:** после утверждения списка возможен пропуск компании; сейчас names пуст и proposed, текущего эффекта нет. **Рекомендация:** согласовать удаление пунктуации и суффиксов, добавить формы в тест. Это прежний L1, не включённый в исправления H/M.

### A-L2 — низкий: provenance проверяется по дню, а RFC3339 offset валидируется неполно

**Проверено:** `schema.py:488–490` сравнивает `[:10]`; first_labeled_at=`2026-10-20T20:00:00Z`, labeled_at=`2026-10-20T10:00:00Z` принимаются. `_valid_date` вместе с custom FormatChecker принимает offset `+00:60`; оба примера дали `errors=[]` (К7). **Следствие:** имя/документация проверки обещают более строгую хронологию/формат. **Рекомендация:** определить date/date-time семантику, корректно проверять offset и сравнивать времена в общей зоне. Прежний L2 остался.

### A-L3 — низкий: общее правило proposed не охватывает root anchors

**Проверено:** при корневом status=proposed `rubric.py:194–197` всё равно сохраняет anchors и включает их в prompt hash (К5/К7); это намеренно описано в module docstring:3–7, но общий контракт §0/§5.2 и `docs/architecture.md:525` не делает такого исключения. **Следствие, выведено:** будущий prompt-builder может применить неподтверждённые anchors. Сейчас root confirmed и промпта нет, применения модели не обнаружено. **Рекомендация:** разделить декларативное хранение и эффективную рубрику, уточнить guard будущего промпта; текущие подтверждённые статусы не менять. Устаревшее «вся рубрика proposed» уже исправлено, эта часть прежнего L3 — нет.

### A-L4 — низкий: portable JSON Schema и runtime различаются в понимании integer

**Проверено:** стандартный `Draft202012Validator(gold_schema())` принимает relevance=2.0, runtime возвращает invalid_relevance. `schema_validation.py:53–58` намеренно переопределяет integer как `type(value) is int` (К7). **Следствие, выведено:** внешний клиент, проверивший только опубликованный JSON Schema, может получить отказ приложения. Опубликованная JSON Schema корректна: стандарт считает 2.0 целым числом, runtime намеренно строже. Это ограничение паритета, не ошибка численной оценки 2 и не довод ослаблять runtime. **Рекомендация:** документировать расширение парсинга/типа и тестировать эту границу отдельно от обещания переносимого структурного паритета. Исходные примеры M5 исправлены.

## 5. Признанные отклонения и повторная проверка прежних находок

| Исходное отклонение | Вердикт сейчас / нужная правка документа |
| --- | --- |
| company= у rules_relevance | Обосновано strong_company-правилом; текущий §6.5:640–642 уже содержит keyword-only company. Дополнительная правка не нужна |
| required_domain/basis в response | Обосновано §4.2 и агрегацией; текущий §4.5 отражает поля/required. Нормализация пустых provider-деталей явно оставлена S3/P2 |
| duty_unit_ids/title_unit_ids | Обосновано ограничениями evidence; сигнатура и широкий fallback без duty-ID теперь описаны. Строгая передача scope остаётся обязанностью P2 |
| label_scope | Обосновано provenance 1:1; необязательный enum описан в §4.3, перенос подтверждён К2 |
| Tiger только при pharma industry | Исправлено в доказательстве: doc/test больше не подменяют employer industry pharma practice. Consultancy без focus=0, с focus=1; gold Tiger=1 не изменён. Реальные теги ещё не подтверждены и не выдаются за извлечённые |
| domain_fit по requirement/industry | Теперь есть required_domain в качестве и κ с явной применимостью; прежний M1 закрыт. Сохранить разложение, устранить отдельный A-M4 loader |
| MAE/QWK/Spearman только covered | Обосновано отсутствием ordinal значения у abstention; документ уточнил исключение, N/coverage есть |
| build_bundle и load_bundle только v2 | Прежняя предпосылка была неточна: build_bundle использует v2 validator; load_bundle проверяет digest/policy/source binding и может принять вручную собранный v3. Текущий §6.7 точно описывает границу. Полного поддержанного v3 freeze всё ещё нет |
| LabelResult/TagValue отложены | Обосновано P2; граница теперь явно закреплена §3/§12 |
| assisted в когорте → curated | Обосновано переименованием v2, не относится к assisted_llm. `cohorts.py:152–158`, выборка К4 сохраняет provenance |
| §3/§12 proposed против confirmed | Устаревшие формулировки исправлены, YAML по-прежнему буквально подтверждённый. Осталась иная неоднозначность root proposed, A-L3 |

| ID первого аудита | Повторный результат |
| --- | --- |
| H1 | закрыт: min(k,N), missing штраф, v1 отдельно |
| H2 | частично: старые примеры исправлены, общее правило office остаётся ошибочным — A-H1 |
| H3 | частично: исходные CLI/schema/focus примеры закрыты, custom frozen обход — A-M1; дополнительный числовой край — A-M3 |
| H4 | закрыт: captured bytes, reads=1, SHA соответствует им |
| M1 | закрыт: required_domain quality/κ |
| M2 | закрыт для independent quality samples/hooks; новый repeat-stability gap — A-M2 |
| M3 | закрыт для gold/prediction; прецеденты требуют отдельной проверки A-M4 |
| M4 | закрыт в документированном контракте обычных ошибок; crash-atomic пары и изоляция от произвольной подмены пути не обещаются |
| M5 | исходные структурные противоречия закрыты; portable integer caveat — A-L4 |
| M6 | закрыт корректировкой контроля/doc без изменения критериев и человеческой оценки |
| L1/L2/L3 | не входили в запрос исправлений; оставшиеся части отражены A-L1–A-L3 |

## 6. Покрытие тестами

Все **345** labels-случаев прошли в общем прогоне; счётчик подтверждён отдельным collect-only (К1). Это не доказывает отсутствие новых краёв.

| Файл / случаев | Соответствие §11 и содержательность | Остаточные пробелы P0 |
| --- | --- | --- |
| test_labels_schema / 54 | Enum/required/snapshots, реальный Draft202012Validator, nonblank/other+domain/provenance/restrictive, source snapshot до roundtrip | INDUSTRIES проверен длиной и несколькими значениями, не полным равенством; даты/offset и portable 2.0 boundary не закрыты |
| test_labels_prediction_validation / 65 | Required/unknown/nested enum/type, finite на глубине, float subclasses, abstention, duplicate IDs и безопасные field paths | Нет большого int, допустимого schema number и опасного downstream; A-M3 |
| test_labels_cli / 25 | API/pipeline forbidden, DB bytes+mtime, schema recognition, protected outputs, source binding; malformed input отвергается до отчётов | Нет custom frozen variant, huge score и неизвестной синтаксически корректной роли; CLI-тесты не заменяют проверки других entry points |
| test_labels_metrics / 26 | Исправленные denominators/all missing, partial tag/gate, required_domain/macro-F1/κ, malformed focus, численные NDCG/tied Spearman и v1 replay | Не проверяется применение stability в tag-only frozen compare; standalone правильный, hook нет |
| test_labels_frozen_compatibility / 9 | Новый v2 и старый v1, unknown version, create/validate malformed, schema typo/missing в labels, partial quality без прогнозов | Missing schema только при run.variant=labels; нет candidate/custom с row.variant=labels. Partial-only repeat comparison отсутствует |
| test_labels_gold_v3 / 106 | 1:1/SHA/occupied paths/one-read race; unknown country; city/state/regions; staging/second publish/foreign inode; parser errors | Office unknown представлен перечнем exact placeholders, а SP-альтернативы — в начале строки. Нет Location TBD/TBC/SP после London; A-H1 |
| test_labels_rubric / 16 | Буквальный YAML, версии/proposed, 6–10/баланс/privacy/cohort/holdout guard | Нет повреждённых nested domain details; часть holdout guard подменяется monkeypatch. Временный реальный registry loader A-M4 выявил пропуск |
| test_labels_aggregate / 44 | Таблица базы/other/upgrades/штрафы/clamp/P2 и обычные формы имён; контроль employer/domain исправлен | Нет LLC в скобках/после дефиса; контрольные теги синтетические, не извлечены из gold |
| prompt/ai_schema/validate/gates/tagger/pipeline/label export | не входят в P0 | Их отсутствие соответствует §12; не засчитывается как пропуск P0 |

Дополнительно независимо сравнены **200** синтетических входов с исходным metrics.py из `git show d63d5db`: current version=v1 совпал во всех случаях (К7). Числа QWK/Spearman/NDCG и обычные знаменатели не дали новых ошибок. Для неизвестной root proposed схемы future prompt и фактического provider field order проверки невозможны до P2; они не объявлены пройденными.

## 7. Готовность к P1/P2 и риски §14

| Предпосылка | Проверенное состояние / вывод |
| --- | --- |
| Безопасный перенос эталона | Обычный gold переносится корректно; остаточный A-H1 следует закрыть до миграции размеченных офисных тегов |
| Единый v3 контракт P1 | Основные M3/M5 исправлены, partial quality сохранено. До UI учесть A-L2/A-L4; предусмотреть отдельные N осей и не требовать relevance для тега/gate |
| Полный v3 freeze | build_bundle всё ещё вызывает v2 validate (`frozen_eval.py:142`), load сам не гарантирует схему; требуется поддержанный build/save/load/create/validate v3 до P2 |
| Registry/precedents | `data/local/evaluation/cohorts.jsonl` и `data/local/labeling/precedents.jsonl` отсутствуют. Loader guard есть; реестр/утверждённые reasons нужно создать отдельной разрешённой работой, A-M4 закрыть |
| Актуальность v3-снимка | Сейчас v3 побайтно равен новой миграции; рассинхронизации не найдено. UI v3 не подключён; дальнейшие изменения v2 сами не обновят снимок |
| jsonschema | Прежняя предпосылка закрыта: dependency в pyproject и версия4.26.0 в .venv, реальный validator запускается. Установок в аудите не было |
| annotation/scope.py, LabelingConfig, LabelResult/TagValue | Отсутствуют и явно относятся к P2. Не считать выполненными из-за наличия response_schema/pure aggregate |
| Frozen pilot input/stability | A-M1/A-M2/A-M3/A-M5 не позволяют считать все пути одинаково защищёнными; descriptive scale по-прежнему не предобъявленный acceptance gate |
| Достаточность gold/holdout | 54 relevance/17 relevant, blind44/7. Это не 150+ решений/30+ relevant/holdout60; текущий baseline не доказывает прохождение гейтов |
| Финализация basis/usage/errors | Response basis строгий для fact, но сохранённая prediction допускает отсутствие basis; usage/errors допускают произвольные metadata. Приняты input_tokens={} и errors.message synthetic marker. Детали P2 diagnostics/нормализации нужно определить и протестировать; фактическая утечка или неверный расход не утверждается |

| Риск §14 | Подтверждённая защита сейчас / оставшаяся работа |
| --- | --- |
| Claims fallback вместо labels | ai.py не изменён, adapter registration ещё P2; обязательны Gemini/Anthropic/OpenAI tests до вызовов |
| Holdout в прецедентах | Registered membership и overlap guard реализованы; реестра нет, новый A-M4 относится к структуре примеров. Exposure caller — P2 |
| Тихая смена default | Diff pipeline/prefilter/scoring/config пуст; будущий enabled=false parity test всё ещё P3 |
| Provider field order | Builder задаёт facts→reason→decisions; настоящий provider не проверен, P2 |
| Стоимость/ретраи | В P0 вызовов нет; BudgetGuard/ledger/--max-cost ещё P2. Платный пилот не запускался |
| Личные данные в prompt | Loader не принимает notes-поле, YAML английский; полный prompt-test и safe diagnostics ещё впереди. Происхождение вручную введённого reason loader доказать не может |
| Сложность _evaluate_record | Pipeline не менялся; дальнейшие ветки и stream rebuild остаются P3 |

## 8. Предлагаемые исправления design doc

В аудите design doc не изменялся. Предложения не затрагивают критерии пользователя.

| Раздел | Было → стало / необходимое уточнение |
| --- | --- |
| §6.9/§15 H2 | «неподдержанное значение отражено как dropped» → пока только ограниченный список; закрыть A-H1 и указать проверку всех альтернатив, неизвестных фраз и запрет дефолтного restrictive результата |
| §6.7/§15 H3 | Полная frozen-проверка → определить независимый от имени варианта discriminator; custom candidate + row.variant=labels должен пройти тот же checker |
| §4.1/§4.4/§4.5 | Текущая таксономия проверяется runtime → проверять её и на входе labels-eval, A-M5; структурная схема с regex не заменяет эту проверку |
| §6.7/§11 | Stability по тегам → явный tag-only repeat случай, без требования integer relevance |
| §4.4/§4.5 | Number без технических ограничений → явно выбрать int-safe обработку либо диапазон, согласованный со всеми downstream score-функциями |
| §5.3/§11 | Валидный precedent domain_fit → тот же required_domain/domain контракт, что у общего v3 тега; nested негативные tests |
| §4.3/§4.5 | ISO/RFC3339 и chronology → определить сравнение date/date-time, зоны и допустимые offsets; runtime integer extension `1.0` отдельно от стандартной JSON Schema |
| §0/§5.2/architecture | Любой proposed неэффективен → уточнить root anchors и будущую эффективную prompt-рубрику; текущие confirmed/proposed значения оставить прежними |
| §15 | H/M полностью закрыты → отметить частичное закрытие H2/H3 и отделить доказанные исправления от новых остаточных путей |
| §11 | Общие гарантии validation → тесты A-H1/A-M1–M5; не заменять интеграционную проверку endpoint тестами standalone helper |
| §12/§15 | Общий зелёный pytest → сохранять конкретный контекст запуска: прошлый 2964 passed с локальными портами и текущий аудит с 9 портовыми препятствиями |


## 9. Приложение: команды и ключевые выводы

Рабочий каталог всех команд — `/Users/admin/Documents/HR-Automation`. Ниже `TMP=/private/tmp/labels-v3-reaudit-t_p7vhvh`; для повторного запуска нужен **новый** временный каталог, поскольку миграция откажется перезаписывать существующие выходы. Это запись выполненных проверок, не предложение писать в канонические пути.

### К1. Тесты и Ruff

Основные Python/CLI-прогоны запускались с префиксом:

```sh
env -i HOME=/Users/admin PATH=/usr/bin:/bin:/usr/sbin:/sbin \
  TMPDIR=/private/tmp LC_ALL=C PYTHONDONTWRITEBYTECODE=1 \
  PYTHON_DOTENV_DISABLED=1 PYTHONPATH="$TMP"
```

`$TMP/sitecustomize.py` включал описанные в §2 запреты. Запуски:

```sh
.venv/bin/pytest -p no:cacheprovider --basetemp="$TMP/pytest-tmp"
.venv/bin/ruff check src tests --no-cache
.venv/bin/pytest -p no:cacheprovider --collect-only -q tests/test_labels_*.py
```

Результаты: **2964 collected; 3 failed, 2955 passed, 6 errors in 34.72s**; Ruff — **All checks passed!** Отдельно собрано **345 labels cases**, все они отмечены passed в полном прогоне. Содержательность разложена в §6. Логи: `$TMP/pytest.log`, `$TMP/ruff.log`, счётчики `$TMP/labels-counts.json`.

Все девять препятствий — `socketserver.py:472`, `socket.bind`, `PermissionError: [Errno 1] Operation not permitted`:

- `test_discovery_crm.py::test_discovery_http_blind_labels_and_report`;
- `test_job_archive.py::test_http_archive_requires_csrf_then_can_restore[False/True]`;
- два HTTP-теста `test_crm_server.py`;
- четыре HTTP-теста `test_gold_web_server.py`.

Это ограничение разрешений, а не установленная регрессия labels. Успешное выполнение этих девяти тестов в этом запуске не подтверждено. Запрет соединений/DNS дополнительно обеспечивал временный guard; запрет bind проявился на уровне среды.

### К2. Миграция и валидация

С тем же очищенным окружением:

```sh
.venv/bin/job-intake gold migrate-v3 \
  --source data/local/gold/vacancy-tags-v2-labeled.jsonl \
  --out "$TMP/migrated.jsonl"
.venv/bin/job-intake gold validate --gold "$TMP/migrated.jsonl" \
  --output "$TMP/gold-validation.json"
```

Обе команды exit 0. Перенесено **171** строк; `decision_labeled=44`, `relevant=7`, `relevance_only=10`, `labeled_rows=55`; методы blind=44, curated=11, assisted_llm=0; migrated_tags=0, dropped_tags=0. Полные тройки role/relevance/gate и все размеченные relevance — разные выборки: валидатор показывает **54** relevance. Семь relevant среди полных троек не заменяют **17** relevant в relevance-метриках.

`valid=true`, 171/171 valid, errors=[], source_problems={}. Сверены равенство job_uid, role_label, relevance, gate_outcome, source, source_hash, text_version, labeled_at, label_scope и notes; метод преобразован по blind→blind/assisted→curated, first_labeled_at=labeled_at. Поле notes сравнивалось без публикации содержания. Новый JSONL **побайтно равен** `data/local/gold/vacancy-labels-v3.jsonl`.

Manifest: `vacancy-labels-v3-migration-v1`, `tag_map_version=v2-to-v3-tags-v2`, rubric_version=`labels-rubric-2026-10-20`, api_calls=0, database_access=none. Source SHA=`6cd7a884e2e7c13e7fd189a8dfe336f0857db608c0537d19d2d15674889a4d3d`; output SHA=`936f6259a2343d7ff882f447539a9b137b67eea4eb2580edd8c91bbb3163309a`. Они совпали с фактически прочитанными/записанными байтами.

### К3. Базовый уровень rules

```sh
.venv/bin/job-intake eval accuracy --variant rules \
  --gold data/local/gold/vacancy-labels-v3.jsonl \
  --output "$TMP/accuracy-rules.json"
```

Exit 0; `database_access=read_only`, api_calls=0. Вывод явно задаёт **A→3, B→2, C→1, other→0**, `rules:tier-map-v1`; это преобразование сохранённого tier для baseline, не изменение pipeline или criteria.

| Показатель | Повторный результат |
| --- | --- |
| N / coverage | 54 / 54 из 54 |
| QWK | 0.373134 |
| Recall relevance≥2 | 12/17 = 0.705882 |
| Precision relevance≥2 | 12/24 = 0.5 |
| Exact / ±1 / MAE | 21/54; 42/54; 0.888889 |
| Spearman | 0.389303 |
| P@5 / P@10 | 4/5; 8/10 |
| NDCG@5 / seeded random | 0.874799 / 0.261829 |
| NDCG@10 / seeded random | 0.771175 / 0.261603 |

N=54, QWK≈0.373 и 12/17 совпали с `data/local/eval/accuracy-rules.md`. Отчёт сохранён только в TMP. Рабочий gold не содержит ручных v3 тегов, поэтому качество тегов по нему имеет N=0 и не подтверждает tagger.

### К4. Когорта, saved и frozen

```sh
.venv/bin/job-intake eval accuracy --variant saved \
  --gold data/local/gold/vacancy-tags-v2-labeled.jsonl \
  --output "$TMP/accuracy-saved.json"
```

Exit 0. Со старым `data/local/eval/p1-2026-10-19/after-apply-accuracy.json` совпали все **17 верхних ключей**, наборы ключей evaluation/operational/stability/readiness/selection и рекурсивная структура evaluation (`legacy-keys.json`). Значения не объявлены идентичными: старый отчёт содержит 163 gold rows, новый —171.

Вызов `annotation.accuracy.run_relevance_accuracy` из Python с variant=rules и `cohort=Path('data/local/eval/p1-2026-10-19/cohort.json')` не записывает exposure. В выбранных 163 строках: blind44, curated3, method=null116. Для явного поднабора blind: **N=44, recall=7/7, precision=7/19, QWK=0.433663** (`blind-summary.json`). CLI с этой когортой не запускался.

`load_bundle` + `validate_run` прошли для всех трёх записанных run-файлов:

| Run | Bundle | Результат |
| --- | --- | --- |
| baseline-run.json | bundle.json | pass |
| candidate-run-v2.json | release-bundle-v2.json | pass |
| release-baseline-run-v2.json | release-bundle-v2.json | pass |

Все пути — `data/local/eval/p1-2026-10-19/`; вывод — `$TMP/frozen-summary.json`. Это проверяет историческую совместимость, а не готовность всех v3 custom variants.

### К5. YAML и независимость версий

`config/labeling_rubric.yaml` побайтно равен fenced YAML §5.2: **2913 bytes**, SHA-256=`9b7f767c2817df6a2ed6e5b1738d7586c21ae9ba5cc1ca707d36fc806e10f4d2`.

- rubric_version — literal ID `labels-rubric-2026-10-20`, не вычисляемый третий digest;
- prompt_version=`8970cd265579fbc293ccc0ed3e78f09990cf37f6bfea17bc5d4168bbb7d395d8`;
- aggregation_version=`9bd2773daa962e8bb8879b5a49594b6de2d86ce7d7b27888f98450edd648899f`;
- precedents_hash — отдельный content hash, не заменяет rubric version.

Синтетические изменения в памяти: version меняет только version; anchor — только prompt hash; подтверждённый aggregation base — только aggregation hash; предложенный company name не меняет эффективную агрегацию/хеши. Root status=proposed сохраняет anchors/hash — A-L3. Файл конфигурации не менялся. Вывод — `migration-rubric-confirmations.json`; независимость также покрыта `test_labels_rubric.py`.

### К6. Регрессии критериев и границы P0

```sh
git diff --exit-code 8bb3a2a -- config/search_profiles.yaml config/rules.yaml \
  config/settings.yaml src/job_intake/pipeline.py src/job_intake/annotation/prefilter.py \
  src/job_intake/scoring src/job_intake/storage
```

Exit 0, diff пуст. Код pipeline/prefilter/scoring/storage и исполняемые legacy-критерии не менялись относительно базы. Хеши всех восьми YAML совпали до/после аудита. `annotation/scope.py`, LabelingConfig, LabelResult/TagValue, cohort registry и файл прецедентов отсутствуют; отсутствие проверено поиском файлов/символов. По §12 эти части ещё предстоят в P1/P2, подробности §7. jsonschema dependency присутствует; установленная версия4.26.0.

### К7. Дополнительные воспроизведения

Все входы синтетические; временные SQL/gold/cohort/output только под `/private/tmp`. Рабочие notes в них не использовались.

| Проверка | Вывод / временное доказательство |
| --- | --- |
| Office неизвестен или SP после альтернативы | Полный migrate+v2/v3 validate; ложный office_elsewhere, A-H1; `migration-rubric-independent.json`, geo0–geo4 |
| Неизвестная role_label | Полный CLI labels принимает audit-nonexistent-role при существующем source binding, exit0, роль присутствует в отчёте; A-M5; `unknown-role-summary.json`, `unknown-role-predictions.jsonl`, `unknown-role-accuracy.json` |
| Custom variant со строкой labels без schema | create/validate принимают; forbidden implied accuracy1/1; confidence-object падает; A-M1; `metrics-hook-repro.py`, `.json` |
| Tag-only repeat | Качество N2 есть, stability.labels_v3 нет; direct κ1/paired2; A-M2; тот же repro |
| fit_score=10**400 после JSON roundtrip | Checker принимает; relevance_scale и create_run выдают OverflowError. Прямые вызовы, не полный CLI; A-M3; `root-edge-confirmations.json` и независимая schema-проверка |
| Precedent domain missing/object | Настоящий registered calibration loader принимает 8/8; A-M4; `migration-rubric-confirmations.json` |
| LLC punctuation, reversed timestamps, offset+00:60, relevance2.0 | Проверенные результаты A-L1/A-L2/A-L4; `root-edge-confirmations.json` |
| Captured-source race | source read один раз; parsed relevance=2; manifest SHA соответствует capture/output. H4 закрыт; `migration-rubric-confirmations.json` |
| Ошибка второй публикации | Остался только source.jsonl, out/manifest/staging отсутствуют. Обычный rollback; не доказательство crash-atomicity; тот же JSON |
| v1 replay | 200 детерминированных синтетических входов current version=v1 против загруженного в память metrics.py из `git show d63d5db`; 200/200 equal; `v1-differential-summary.json` |

Команда дополнительного CLI-repro A-M5 (в общем очищенном окружении):

```sh
.venv/bin/job-intake eval accuracy --variant labels --gold data/local/gold/vacancy-labels-v3.jsonl --predictions "$TMP/unknown-role-predictions.jsonl" --output "$TMP/unknown-role-accuracy.json"
```

Остальные значения копии predictions сохранены; исходный baseline и рабочие файлы не менялись. CLI вернул N54/QWK0.373134/recall12/17. Изменённая роль сохранена; это доказательство отсутствия проверки таксономии, а не ошибки расчёта relevance.

Реальные вызовы провайдеров, field order у провайдеров, полный будущий prompt/privacy-контур и crash/враждебная подмена файловой системы **не проверялись**.

### К8. Контроль неизменности и ревизия

SHA-256 до и после всех запусков: **19/19 одинаковы**. Обязательные БД и все `data/local/gold/*.jsonl`:

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

Дополнительно неизменны company_watchlist, eval_prices, gmail_search, labeling_rubric, purpose_rubric, rules, search_profiles и settings YAML. Снимки — `$TMP/before-sha256.json` и `after-sha256.json`. Проверка 283 исходных файлов — `worktree-before-sha256.json`/`worktree-after-sha256.json`; исходный аудит входит в этот контроль. Итог **282/283**: единственная разница — параллельно добавленные три строки CAREER.md в README вне labels-раздела. Проверка удаления только этой вставки в памяти точно восстановила исходный хеш. До неё промежуточная сверка давала 283/283. Это не изменение данных и не действие аудита; реализации и тесты остались в проверенной ревизии.

К началу аудита tracked diff: **18 files, +1528/−167**, плюс три новых code/test-файла:

| Файл | SHA-256 |
| --- | --- |
| src/job_intake/labeling/schema_validation.py | `5ef9f8fae60f95e553b54d9e22e39a9305a1bca8165d34e1cef7b3b8d8ce9724` |
| tests/test_labels_prediction_validation.py | `f9f3955f85946bbb9c1d385d4b36f3d7a2b9707f865e23dc51a3135872b3c617` |
| tests/test_labels_frozen_compatibility.py | `e3a6981bbeac9e51b622df6bd6a7533479e64335cfbf9765421f50e1935dca94` |

Tracked-файлы исправлений: README.md, docs/architecture.md, docs/schemas/vacancy-labels-v3-{gold,prediction}.schema.json, docs/vacancy-labeling-architecture.md, pyproject.toml; annotation/{accuracy,benchmark,frozen_eval}.py; labeling/{gold_v3,metrics,predictions,schema}.py; tests/test_labels_{aggregate,cli,gold_v3,metrics,schema}.py. Полные пути модулей начинаются с src/job_intake/. Существовавшие прочие untracked пользовательские файлы аудит не записывал. Исходный diff `8bb3a2a..d63d5db` —25 files, +6162/−41. SHA текущего tracked-патча указан в §2. Ветка/HEAD сохранены; аудит добавляет только этот новый отчёт.

Контроль параллельного изменения README: before SHA=`31f12e4162f2a830868ccb6444668b249ef841d271f658237eba71812aa2c623`, after SHA=`034991c937b0e0ee9902e9c2ab21b0ae9f8a7f8d3de1d026192bb80745fe887e`; артефакт `$TMP/concurrent-readme-change.json`. SHA tracked-патча в §2 относится к началу аудита, без этой вставки. Новые личные материалы по ссылке CAREER.md не читались.

В заключительном `git status --short` также появился untracked `CAREER.md`; аудит его не создавал и содержимое/цель не читал. Это параллельная работа вне объёма labels-v3.
