# Исправления роли, найма и версий — 19 октября 2026

Пакеты R06/R07 аудита. Основание карьерных критериев — мастер-вектор
`../aramais-vault/codex/projects/career-orientation/codex/areas/job-search-vector.md`
от 2026-10-16, уточнение пользователя 2026-10-10 и текущий запрос исправить P1.
Прямые подчинённые необязательны; английское объявление само по себе не доказывает
рабочий язык. Личный статус авторизации на работу остаётся неизвестным.

| Файл / поле | Было | Стало | Основание |
|---|---|---|---|
| `config/search_profiles.yaml`: `streams[].keywords` | Один список для поиска и таксономии | Явные `search_queries` и `titles`; старый `keywords` читается совместимо | CLS-09: поисковая фраза не меняет профессию |
| Там же, `business-analytics.titles` и `rules.positive_title_signals` | Decision Science одновременно BA и DSM | Поисковый запрос сохранён; occupation относится к существующему DSM | CLS-04: устранено одинаковое таксономическое совпадение; DSM уже явно включает Decision Science |
| Там же, `business-analytics.scoring.title_weights` | `decision science lead: 8` | Убран недостижимый вес соседней профессии; DSM `decision science lead: 8` сохранён | Не меняет целевой диапазон или вес назначенной DSM роли |
| `config/rules.yaml`: `target_geographies` | Brazil/BR, worldwide/global remote, LATAM, Americas | Добавлены точные региональные aliases `Global`, `AMER` | X4: названия регионов в полях вакансии; не основание права на работу |
| `config/rules.yaml`: комментарий актуальности | Synced 2026-10-10 | Reviewed 2026-10-16 / recognition 2026-10-19 | Канон текущего вектора |
| Title grammar | Теряла границы скобок и Manager/Data Analytics; ограниченная product grammar | Сохранены границы скобок, перестановки Data Analytics, Director/VP of Product и Chief Product Officer | CLS-04: распознавание профессии отдельно от fit |
| Duty evidence | direct reports считались отчётами; guide decisions/partner with пропускались | Разделены reporting outputs и подчинённые, добавлены фактические duty-фразы | CLS-03; company, requirements, история и чужая ответственность по-прежнему исключены |
| Scope cap | Любое отсутствие analytical mandate давало C до проверки неполноты | Неизвестное в неполном описании ограничено B; доказанные mismatch/reporting остаются C | CLS-05; B — потолок, а не гарантированный минимум |
| US authorization recognition | Только несколько буквальных фраз | Точные варианты US/U.S./United States и for any employer; отрицания/вопросы/optional не блокируют | X4 / CLS-07; расширяет уже настроенную политику, не устанавливает статус пользователя |

Stable profile ID, пороги **19/6**, числовые веса остальных титулов и бонусы bucket
не изменены. Director/VP не получили автоматически большего веса; CPO без расшифровки
остаётся неоднозначным. Запрет PM/Staff/IC не добавлен. «Anywhere in US» не означает
worldwide; исключение Brazil имеет приоритет над широким разрешением.

`raw_fit_score` сохраняет результат до caps и отдельно от конечного `fit_score`.
`score_cap_reasons` объясняет потери; старое значение raw остаётся `null` до пересчёта.
Бонусы bucket A (+3) и bucket B (+1,5) намеренно остаются дополнительными к весам
содержательных концептов: они не были одновременно удалены с перенастройкой порогов.
Это операционная шкала, не вероятность найма и не измеренный прирост качества.

`eligibility_status=eligible|ineligible|unknown` и
`eligibility_evidence_status=confirmed|blocked|unconfirmed` независимы от профессии,
role fit и общей маршрутизации PASS/REVIEW/REJECT. REVIEW по неизвестному scope
не отменяет подтверждённое условие найма; REJECT по роли не выдумывает запрет найма.
Это состояние доступных свидетельств, а не оценка шанса трудоустройства.

Версии разделены на `query`, `taxonomy`, `scoring`, `classification_prompt`,
`annotation`, `prefilter`, `source_units`. Композитная детерминированная версия
профиля включает scoring/taxonomy; prompt-версии и причины stale хранятся отдельно.
Изменение только `search_queries` не инвалидирует классификацию/оценку и не вызывает
новый API-вызов. Legacy без component manifest имеет явную причину
`version_components:legacy_unknown`, а не угаданную совместимость.

Обработка завершающего `U.S.?` сохраняет вопрос вместе с исходным утверждением,
вместо отдельной клаузы `?`. Контракт units изменён на `source-units-v5`;
публичный `TEXT_VERSION` и исторические source hashes сохранены. Это не разрешает
молча переносить несовместимые paid reviews: вход/доказательства проверяются как прежде.

Проверки включают замороженные матрицы титулов, duty phrases, отрицаний авторизации,
Global/AMER, ограничения Anywhere/US, раздельной eligibility, неполноты и raw caps.
Рост человеческой релевантности этим техническим изменением не заявляется:
нужны независимые cohorts/bundle и последующая разметка. Этот пакет сам не применяет
изменения к рабочей SQLite; контролируемое применение описано в
[p1-data-operations / R10](p1-data-operations.md).

## Уточнения после проверки переходов R10

Read-only проверка всех шести прежних PASS обнаружила пропущенные условия в
существующих исходниках. У Delivery Associates (Ireland) parser уже сохранял
отдельные units «This role is based in Ireland», обязательное право на работу и
отсутствие визового спонсорства. Ошибка находилась в распознавании условий и
в разрешающем global context, а не в потере HTML или полномочиях пользователя.

- Явное «this role/position is based/located in …» имеет тот же приоритет, что
  обязательное место проживания. Ireland не перекрывается Worldwide, Brazil в
  карточке или глобальной деятельностью компании: результат REJECT/ineligible
  основан на месте этой роли, а не на предположении о гражданстве пользователя.
- Обязательное «must have the right to work …» и условное «as long as … authorized
  to work where you live» дают REVIEW/unknown, пока разрешение пользователя не
  установлено. Это действует и для Brazil; blanket REJECT страны не добавлен.
  Отсутствие спонсорства сохраняется отдельным условием для проверки, а не
  доказательством того, что конкретному пользователю нужен спонсор.
- География в описании подтверждается только явной политикой места работы/найма.
  «Global operations», «global remote team», «opportunities for global
  collaboration» и «benefits … your location in LATAM» не подтверждают доступность
  найма. Прямой Worldwide в поле места работы и «work from anywhere» сохраняют
  значение политики работодателя; обязательная авторизация остаётся отдельной осью.
- У Delivery Brazil и Oyster результат стал REVIEW/unknown, у Blend360 география
  осталась unknown, у Giti2c и Tilt явно подтверждённые места работы сохранились.

`ELIGIBILITY_POLICY_VERSION=eligibility-v5-scoped-location-permission` делает эти
изменения видимой причиной устаревания старой оценки. Тесты проходят полный путь
Greenhouse/Ashby/Lever → HTML/legacy flattened text → units → RuleEngine и проверяют
отрицания, вопросы, необязательность и чужие требования. Права пользователя не
добавлены в config и не выведены из английского текста или региона объявления.
