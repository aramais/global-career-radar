# Vacancy discovery channels for analytics-leadership roles (São Paulo, LATAM remote, global remote from Brazil)

Research date: 2026-10-15. Confidence labels: VERIFIED_OFFICIAL (vendor/official doc or live call to an official public endpoint), PRACTICAL_EXTERNAL (third-party guide, vendor blog, secondary press), HYPOTHESIS (my inference, not tested), TO_VERIFY (tested or found but unresolved/conflicting). "Live probe 2026-10-15" means a single read-only anonymous GET (or one read-only search POST) to a public endpoint, made from a scratch directory; no accounts, logins, applications or forms. No personal data about the candidate was used in any query. Note: the shared WebSearch budget ran out mid-research, so some topics (Measure Slack, Reforge, Wellfound, Otta/WTTJ, Torre, Revelo, Talently, Vagas.com, Catho, Huntr-style response-rate data) have thin or no coverage and are listed as gaps.

## 1. ATS boards and public APIs: which ATS LATAM/global companies use, URL/JSON patterns, X-ray strings

### Takeaway
Brazilian/LATAM tech companies use a mix of ATSs: the bigger English-friendly tech companies (Nubank on Ashby; iFood, QuintoAndar, VTEX, Wildlife, EBANX, Wellhub, XP, C6, Inter and Stone on Greenhouse; dLocal, Kavak on Lever) can be polled through keyless JSON APIs that the candidate's pipeline already supports. Banks, retailers and many scale-ups (PicPay on Oracle HCM, PagBank/Creditas/Ambev on Gupy, Olist on InHire, Rappi on Workday, Bitso on BambooHR) need other adapters or are mostly Portuguese-language. On any given day only a handful of explicit analytics-leadership titles exist per company, so you need a wide watchlist plus alerts, not a few target boards.

### Cited Findings
**Public endpoints (official docs and live checks)**
- Greenhouse Job Board API: `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs` (add `?content=true` for full descriptions). No key needed for GET. The pipeline already uses it — VERIFIED_OFFICIAL — [Greenhouse Job Board API docs](https://docs.greenhouse.io/job-board.html); confirmed by live probes 2026-10-15, e.g. [QuintoAndar board API](https://boards-api.greenhouse.io/v1/boards/quintoandar/jobs).
- Ashby Public Job Posting API: `GET https://api.ashbyhq.com/posting-api/job-board/{board_name}` (`?includeCompensation=true`). Returns `title, department, team, location, secondaryLocations, publishedAt, isListed, isRemote, workplaceType, address, jobUrl, descriptionHtml, compensation` — VERIFIED_OFFICIAL — [Ashby API docs](https://developers.ashbyhq.com/docs/public-job-posting-api); fields confirmed by live probe of [Nubank's Ashby board](https://api.ashbyhq.com/posting-api/job-board/nubank) on 2026-10-15.
- Lever Postings API: `GET https://api.lever.co/v0/postings/{site}?mode=json` (EU instance `api.eu.lever.co`). Returns `categories.location`, `allLocations` and `workplaceType` — VERIFIED_OFFICIAL — [Lever postings-api README](https://github.com/lever/postings-api/blob/master/README.md); live probe of [dLocal's Lever API](https://api.lever.co/v0/postings/dlocal?mode=json) on 2026-10-15 returned 52 postings.
- SmartRecruiters Posting API: `GET https://api.smartrecruiters.com/v1/companies/{companyIdentifier}/postings`, with filters for search text, country, region, city, department and language. The official page says it supports API-key auth, while vendor guides say the feed is keyless when the customer enables it. A live probe on 2026-10-15 with identifier "Visa" returned `totalFound: 0`, so the identifier or enablement is unknown — TO_VERIFY — [SmartRecruiters Posting API](https://developers.smartrecruiters.com/docs/posting-api); [JobsPipe vendor note](https://jobspipe.dev/sources/smartrecruiters).
- Workday: an undocumented `POST https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` with JSON `{"appliedFacets":{},"limit":20,"offset":0,"searchText":"analytics"}`. `postedOn` is a localized sentence, not a date, and totals cap at about 2,000 — PRACTICAL_EXTERNAL — [dev.to write-up](https://dev.to/udaninn/workday-job-boards-have-a-json-api-too-its-just-better-hidden-23fl). A live probe on Rappi's tenant (`rappi.wd3.myworkdayjobs.com/.../Rappi_jobs`, linked from [Rappi's site](https://www.rappi.com/careers)) on 2026-10-15 returned an error object — TO_VERIFY.
- Teamtailor: career sites expose `/jobs.rss`. Verified on Teamtailor's own career site on 2026-10-15; whether every customer site does the same is HYPOTHESIS — PRACTICAL_EXTERNAL — [career.teamtailor.com/jobs.rss](https://career.teamtailor.com/jobs.rss).
- Workable: a live probe of `apply.workable.com/api/v1/widget/accounts/workable` returned "Not Found" on 2026-10-15. Public JSON pattern unresolved — TO_VERIFY.
- Gupy: the official API (`api.gupy.io/api/v2/jobs`) is for employers and uses Bearer/JWT tokens — VERIFIED_OFFICIAL — [Gupy developer reference](https://developers.gupy.io/v2.0/reference/jobscontroller_getall); [Digibee connector doc](https://docs.digibee.com/documentation/reference-guides/connectors/industry-solutions/gupy). Public career pages `https://{company}.gupy.io/` embed a `__NEXT_DATA__` JSON with `jobs[]` (`id, title, type, department, workplace, quickApply`), confirmed live on 2026-10-15 for [Creditas](https://creditas.gupy.io/) and [Ambev](https://ambev.gupy.io/). The aggregated search page `https://portal.gupy.io/job-search/term={q}` embeds `initialJobList.data[]` (`name, careerPageName, workplaceType, city, state, publishedDate, jobUrl`), confirmed live — [Gupy portal](https://portal.gupy.io/job-search/term=analytics). The guessed JSON hosts `portal.api.gupy.io/api/v1/jobs` and `employability-portal.gupy.io/api/v1/jobs` returned 404 on 2026-10-15 — TO_VERIFY.

**Which ATS each company uses (live checks of careers pages and APIs, 2026-10-15)**
- Nubank: the Greenhouse token `nubank` still exists but lists 0 jobs. Nubank's careers pages now link to `jobs.ashbyhq.com/nubank`. The Ashby API returned 99 jobs: 30 São Paulo, 24 Palo Alto, 16 Mexico City; 83 Hybrid, 11 OnSite; only 2 Portuguese-looking titles — VERIFIED_OFFICIAL (live) — [nu.com careers](https://nu.com/en/careers); [Nubank Ashby API](https://api.ashbyhq.com/posting-api/job-board/nubank); [Greenhouse nubank (empty)](https://boards-api.greenhouse.io/v1/boards/nubank/jobs). An older third-party tracker also recorded days with no openings on the Greenhouse board — [Visualping](https://visualping.io/pages/nubank-job-board-alerts-4874221).
- iFood: on Greenhouse under token `ifoodcarreiras` (linked from [carreiras.ifood.com.br](https://carreiras.ifood.com.br/)). 58 jobs; about 72% Portuguese-looking titles (42/58); locations "Brasil", "São Paulo", "Osasco", 4 "Remote" — live — [iFood Greenhouse API](https://boards-api.greenhouse.io/v1/boards/ifoodcarreiras/jobs).
- Other Greenhouse boards on 2026-10-15 (total jobs / share of Portuguese-looking titles by a keyword heuristic):

  | Company | Token | Jobs | Portuguese-looking titles | Main locations |
  |---|---|---|---|---|
  | Grupo QuintoAndar | `quintoandar` | 95 | 46% | Brasil, São Paulo, Mexico City |
  | VTEX | `vtex` | 32 | 6% | Brazil, São Paulo, NY |
  | Wildlife Studios | `wildlifestudios` | 16 | 0% | São Paulo |
  | EBANX | `ebanx` | 40 | 0% | Curitiba, São Paulo |
  | Stone (board titled "Stone - Linkedin") | `stone` | 373 | 78% | São Paulo, "Remoto", Rio |
  | Wellhub (ex-Gympass) | `gympass` | 87 | 5% | "Brazil (Remote)" 20, "Brazil (São Paulo - Hybrid)" 10 |
  | C6 Bank | `c6bank` | 156 | 92% | São Paulo |
  | Inter | `inter` | 158 | 4% | Belo Horizonte, São Paulo |
  | XP Inc. | `xpinc` | 149 | 48% | São Paulo |

  Live — e.g. [Wellhub API](https://boards-api.greenhouse.io/v1/boards/gympass/jobs), [C6 API](https://boards-api.greenhouse.io/v1/boards/c6bank/jobs), [XP API](https://boards-api.greenhouse.io/v1/boards/xpinc/jobs), [Stone API](https://boards-api.greenhouse.io/v1/boards/stone/jobs), [EBANX careers linking boards.greenhouse.io/ebanx](https://www.ebanx.com/en/careers/).
- Lever (live 2026-10-15): dLocal has 52 postings (Montevideo, Madrid, Shanghai, Barcelona; hybrid/remote); Kavak 11 (9 Mexico City, 1 São Paulo); Neon 10 (all "Remoto", 7 Portuguese); CloudWalk's board exists with 0 postings — [Kavak Lever API](https://api.lever.co/v0/postings/kavak?mode=json); [Neon Lever API](https://api.lever.co/v0/postings/neon?mode=json).
- Ashby (live 2026-10-15): Addi 32, Belvo 3, Neon 4, Loft 0 — [Addi Ashby API](https://api.ashbyhq.com/posting-api/job-board/addi).
- Not on Greenhouse/Lever/Ashby (live 2026-10-15): PicPay redirects to Oracle Cloud HCM Candidate Experience (`epdm.fa.la1.oraclecloud.com/hcmUI/CandidateExperience/pt-BR/sites/PicPay`); PagBank links to `pagseguro.gupy.io`; Olist uses `olist.inhire.app` (InHire); Bitso links BambooHR — [PicPay careers](https://www.picpay.com/carreiras); [PagBank careers](https://pagbank.com.br/carreiras); [Olist careers](https://olist.com/carreiras/); [Bitso jobs](https://bitso.com/jobs). Creditas, Ambev and Stone have Gupy pages (Stone uses both Gupy and Greenhouse) — [stone.gupy.io](https://stone.gupy.io/).
- Explicit analytics-leadership titles on these Brazilian boards were scarce on 2026-10-15. A title filter (analytics/data/strategy plus head/director/manager/lead) found, for example: QuintoAndar strategy senior manager roles (Mexico/Argentina); Wellhub "Senior Strategy Manager - AI & Operations" (Brazil); iFood "Gerente de Growth e Outbound B2B" and "Staff Data Scientist - Logistics Intelligence"; nothing analytics-leadership at Nubank that day — live probes above.

**Global remote companies (live 2026-10-15; location field only)**
- Canonical: 75 of 310 jobs carry Americas/LATAM-type locations, including "Manager, People Analytics" ("Home Based - Americas; Home based - EMEA"). Datadog 13/439; MongoDB 4/402; Elastic 4/380; GitLab 2/218; Twilio 2/137. Ramp, Notion, Linear, Supabase, Replit, Cloudflare and Okta had 0 jobs with explicit Brazil/LATAM/Americas labels — [Canonical Greenhouse API](https://boards-api.greenhouse.io/v1/boards/canonical/jobs); [Datadog API](https://boards-api.greenhouse.io/v1/boards/datadog/jobs).

**X-ray domains seen live (2026-10-15)**
- New Greenhouse domain `job-boards.greenhouse.io/{token}` (iFood, Nubank legacy) alongside legacy `boards.greenhouse.io/{token}` (EBANX); `jobs.ashbyhq.com/{name}`; `jobs.lever.co/{site}`; `{company}.gupy.io`; `{company}.inhire.app`; `{tenant}.wd{N}.myworkdayjobs.com`; `{company}.teamtailor.com`; `*.oraclecloud.com/hcmUI/CandidateExperience` — [iFood careers](https://carreiras.ifood.com.br/); [Nubank Greenhouse job page](https://job-boards.greenhouse.io/nubank/jobs/7272645).

### Inferences
- HYPOTHESIS (Google X-ray strings, untested here because the search budget ran out; Google caps queries at about 32 words):
  - `(site:job-boards.greenhouse.io OR site:boards.greenhouse.io) ("Head of Analytics" OR "Director of Analytics" OR "Analytics Manager" OR "Decision Science") ("São Paulo" OR "Sao Paulo" OR Brazil OR LATAM)`
  - `site:jobs.ashbyhq.com (analytics OR "decision science" OR "business operations") (head OR director OR manager) (Brazil OR "São Paulo" OR LATAM OR "Latin America")`
  - `site:jobs.lever.co (analytics OR insights) (head OR director OR "senior manager") (Brazil OR "Sao Paulo" OR LATAM OR Americas)`
  - `site:myworkdayjobs.com (analytics OR "data science") (manager OR director) ("Sao Paulo" OR Brazil)`
  - `site:teamtailor.com OR site:apply.workable.com OR site:jobs.smartrecruiters.com analytics (manager OR head) (Brazil OR LATAM)`
  - `site:gupy.io ("Head of Analytics" OR "Analytics Manager" OR "Head de Dados" OR "Gerente de Analytics")` (expect mostly Portuguese roles)
  - Add the Google "past week" tools filter for freshness. Run against both Greenhouse domains, because companies are split between them.
- HYPOTHESIS: hitting the ATS APIs directly beats X-ray for companies already known: it is complete, structured and dated. X-ray is mainly a way to discover new companies whose boards you don't yet know.
- HYPOTHESIS: Portuguese-title share is a rough proxy for language. An English title (for example at Inter, where most roles are in Belo Horizonte) does not prove English is the working language; the pipeline's rule that English text ≠ English working language remains correct.
- HYPOTHESIS: companies where English roles are realistic for a beginner-Portuguese speaker are Nubank, Wellhub, VTEX, EBANX, Wildlife, dLocal, Kavak and the global remote employers. C6, Stone, iFood, QuintoAndar's Brazil roles, PagBank, Creditas and PicPay are predominantly Portuguese.

### Gaps
- Mercado Libre's ATS could not be fetched (connection failed); Hotmart, CloudWalk, Nuvemshop and Creditas ATSs were not resolved beyond Gupy/empty boards.
- Workable, SmartRecruiters and Workday keyless patterns were not confirmed live; BambooHR, InHire and Oracle HCM JSON endpoints were not tested.
- The X-ray strings were not executed, so their yield is unmeasured.

## 2. LinkedIn Jobs: filters, boolean queries, alerts, hiring posts, pitfalls

### Takeaway
LinkedIn has the broadest coverage of São Paulo analytics leadership roles, but matching is loose and many results are Portuguese-language. Use quoted titles with Boolean operators, the Brazil/São Paulo geoIds, experience levels 4–6 and recency filters. Then route alerts into the existing Gmail import instead of scraping. Ghost and reposted postings are a material share, so verify each role on the company's ATS before investing time.

### Cited Findings
- Boolean rules (official): AND/OR/NOT must be uppercase; quotes give an exact phrase; parentheses are the only grouping symbols; precedence is quotes → parentheses → NOT → AND → OR; braces, brackets, angle brackets and `*` wildcards are unsupported; `+`/`-` are not officially supported. The page was "last updated 6 months ago" as of 2026-10-15 and does not say explicitly that the rules apply to job search — VERIFIED_OFFICIAL — [LinkedIn Help 75814](https://www.linkedin.com/help/linkedin/answer/75814).
- Geo IDs: the LinkedIn guest page for location "São Paulo, Brazil" carries `geoId=104746682` — VERIFIED (live 2026-10-15) — [LinkedIn guest search SP](https://www.linkedin.com/jobs/search?keywords=Head%20of%20Analytics&location=S%C3%A3o%20Paulo%2C%20Brazil). A guest query with `geoId=106057199` returned only Brazil-located jobs, consistent with that ID being Brazil (live 2026-10-15) — TO_VERIFY in the logged-in UI.
- URL parameters (community-documented, unofficial): `f_TPR=r86400` (24h) / `r604800` (7d) / `r2592000` (30d); `f_E` 1 Internship … 4 Mid-Senior, 5 Director, 6 Executive; `sortBy=DD` (date); `start` for pagination (25/page). For workplace, one guide gives `f_WT` 1 On-site, 2 Remote, 3 Hybrid, while another project names it `f_WRA` with a different value order — PRACTICAL_EXTERNAL / TO_VERIFY — [Glama scraping guide](https://glama.ai/mcp/servers/@adamd9/mcp-jobsearch/blob/2c861f861b5788f814153ef3ebfbe6f7c1ba6ef6/linkedin_job_search_scraping_guide.md); [WorkIn GitHub](https://github.com/Systemsetco/WorkIn); [Kondo](https://www.trykondo.com/blog/linkedin-url-job-hack).
- Live sample (2026-10-15, guest, `keywords="Head of Analytics" OR "Analytics Director"`, Brazil geoId, `f_WT=2`): 10 cards, including Edelman "Data & Intelligence Director | South Cone", Blip "Senior Manager Data & Analytics", Turbi "Head of Data & Analytics", QuintoAndar "Gerente de Dados & Analytics", Meta "Data Scientist, Product" (São Paulo) and Serasa "Gerente de Inteligência de Dados". 4 of the 10 titles were Portuguese. Several showed São Paulo city locations despite the remote filter — [LinkedIn guest endpoint](https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=%22Head%20of%20Analytics%22%20OR%20%22Analytics%20Director%22&geoId=106057199&f_WT=2&start=0).
- Loose matching: unquoted "Head of Analytics" returned "865 … jobs in São Paulo" and "4,000+ … jobs in Latin America" (guest page titles, 2026-10-15), far more than real head-level roles; "Latin America" is accepted as a location — live — [LinkedIn LATAM search](https://www.linkedin.com/jobs/search?keywords=Head%20of%20Analytics&location=Latin%20America).
- In Jan 2025 LinkedIn partnered with Greenhouse to add "verified" labels to genuine postings; ghost postings were reported at roughly one in five LinkedIn postings (WSJ via Daily Hive, not primary) — PRACTICAL_EXTERNAL — [Daily Hive, 2025-01](https://dailyhive.com/canada/linkedin-ghost-job).
- The "Reposted" label means the employer re-listed the role. The posted date then understates how long it has been open, and applications in the old pool are often not read — PRACTICAL_EXTERNAL (guide dated 2026-09) — [JobMentis ghost-jobs guide](https://www.jobmentis.com/en/guide/ghost-jobs-linkedin).
- An aggregator scores 21.2% of active LinkedIn postings with at least one ghost-job warning sign vs 17.8% across the 10 boards it tracks (own method, commercial interest) — PRACTICAL_EXTERNAL — [JobsPipe ghost jobs](https://jobspipe.dev/ghost-jobs/linkedin.md).
- Ashby says the inbound channel has become saturated with fraudulent candidates and added automatic fraud checks. One-click/Easy-Apply volume therefore meets heavier automated screening — PRACTICAL_EXTERNAL (vendor product note) — [Ashby product update](https://www.ashbyhq.com/product-updates/introducing-fraudulent-candidate-detection-and-management-to-help-you-focus-on-legitimate-candidates).
- The pipeline already imports LinkedIn job-alert emails through Gmail (8 alerts → 29 cards on 2026-10-02) and strips tracking parameters — VERIFIED (local doc) — [README](/Users/admin/Documents/HR-Automation/README.md).

### Inferences
- HYPOTHESIS (search templates):
  - Remote-Brazil: `https://www.linkedin.com/jobs/search/?keywords=("Head of Analytics" OR "Director of Analytics" OR "Analytics Manager" OR "Senior Manager, Analytics" OR "Decision Science" OR "Business Analytics Manager" OR "Product Analytics Manager") NOT (Engineer OR Intern OR Estágio)&geoId=106057199&f_WT=2&f_E=4,5,6&f_TPR=r604800&sortBy=DD`
  - São Paulo onsite/hybrid: same keywords with `geoId=104746682&f_WT=1,3`.
  - LATAM remote: `location=Latin America&f_WT=2`.
  - Save each as an alert (daily) so it flows into Gmail → `import-gmail`.
  - Optional add-on: an English-only filter `NOT (Gerente OR Coordenador OR Analista)`. It may also drop bilingual roles.
- HYPOTHESIS ("hiring" posts): in the Posts tab, `("hiring" OR "we're hiring" OR "estamos contratando") ("head of analytics" OR "analytics manager" OR "decision science") (Brazil OR "São Paulo" OR LATAM)`, sorted by latest. Hiring-manager posts often appear before or instead of a formal posting. Unmeasured.
- HYPOTHESIS: treat "Reposted", over 200 applicants with no update after weeks, and missing ATS counterparts as ghost/low-priority signals. Prefer applying on the company ATS (link from the API) over Easy Apply.
- Work authorization flag: Brazil-located LinkedIn roles are typically local-employment (CLT/PJ) and may require the pending residence permit; out of scope here.

### Gaps
- No official LinkedIn documentation of URL parameters (`f_WT`, `f_E`, geoIds) was found; only community sources plus live checks.
- No data on Easy Apply vs ATS-apply response rates, or on how many hiring posts never become postings.
- The search budget ran out before LinkedIn job-alert help pages or Open-to-Work/recruiter-visibility docs could be checked.

## 3. Remote job boards and LATAM-focused platforms: Brazil/LATAM eligibility filter quality for leadership roles

### Takeaway
Of the generic remote boards, only Himalayas combines a real country filter (`country=BR`), seniority filters (Manager/Director/Executive) and structured `locationRestrictions`. On 2026-10-15 it returned 139 analytics-related manager+ roles eligible for Brazil (66 excluding worldwide). Remotive, Jobicy, Working Nomads, We Work Remotely and Remote OK carry very few analytics-leadership roles, and their eligibility fields are coarse. Get on Board is LATAM-native with a language field, but it is engineering-heavy. Jobgether's Brazil page mixes in US-only roles.

### Cited Findings
- Himalayas API: free and keyless (an optional free key gives 60 req/min and 10k/day); `GET https://himalayas.app/jobs/api/search` with `q`, `country` (ISO-2), `worldwide`/`exclude_worldwide`, `seniority` (Entry-level…Manager, Director, Executive), `employment_type`, `company`, `timezone`, `sort`, `page`; the browse feed uses `cursor`. Fields include `locationRestrictions` and timezone restrictions, salary, `pubDate`, `expiryDate`, `applicationLink`. Data refreshes daily. Terms: credit Himalayas with a link, apply via `applicationLink`, don't redistribute to other boards, no CORS (backend use only). The OpenAPI 3.1 spec and RSS of the 100 latest jobs are available — VERIFIED_OFFICIAL — [Himalayas API](https://himalayas.app/api).
- Himalayas live probe (2026-10-15) `q=analytics&country=BR&seniority=Manager,Director,Executive` gave `totalCount=139`; with `exclude_worldwide=true`, 66. The first 20 included Customer.io "Senior Manager, Data Science & Analytics" (LATAM/Caribbean countries), Canonical "Manager, People Analytics" (Brazil among countries), Blip "Senior Manager Data & Analytics", Novakid "Head of Data and Analytics", Hard Rock Digital "Senior Manager - Payments Analytics" and Capgemini "Predictive Analytics Manager" (Brazil) — [Himalayas search API](https://himalayas.app/jobs/api/search?q=analytics&country=BR&seniority=Manager,Director,Executive).
- Remotive API: `GET https://remotive.com/api/remote-jobs` (`search`, `category`, `company_name`, `limit`), keyless JSON. Jobs are delayed 24h; the docs advise at most 4 calls a day, and more than 2 req/min is blocked. Must link back to and credit Remotive; no redistribution to LinkedIn/Google Jobs/Jooble — VERIFIED_OFFICIAL — [Remotive API README](https://github.com/remotive-com/remote-jobs-api). Live (2026-10-15) `search=analytics` returned 14 jobs, 0 leadership titles. `candidate_required_location` values were coarse ("Worldwide" 6; "Northern America, LATAM, Europe, APAC" 3) — [Remotive API](https://remotive.com/api/remote-jobs?search=analytics).
- Jobicy API: `GET https://jobicy.com/api/v2/remote-jobs?count=1..100&geo=…&industry=…&tag=…`, keyless, attribution requested (apiVersion 2.2.19). Live `geo=latam` returned 75 jobs (jobGeo: "Anywhere" 13, "LATAM" 13, "Mexico" 8, "Brazil" 7) and 0 analytics-leadership titles — VERIFIED (live) — [Jobicy API/RSS page](https://jobicy.com/jobs-rss-feed); [Jobicy API](https://jobicy.com/api/v2/remote-jobs?count=100&geo=latam).
- Remote OK API: `GET https://remoteok.com/api?tag=…`, keyless JSON. Its legal notice requires a followed link back and mention of Remote OK, or access is suspended. Live `tag=analytics` returned 25 jobs; 5 leadership-ish titles, mostly US-restricted (for example "Principal Marketing Analytics Manager — Anywhere in the United States") — VERIFIED (live) — [Remote OK API](https://remoteok.com/api?tag=analytics).
- Working Nomads: `GET https://www.workingnomads.com/api/exposed_jobs/`, keyless, with fields `url, title, description, company_name, category_name, tags, location, pub_date`. Live: 51 latest jobs, 0 analytics-leadership — VERIFIED (live) — [Working Nomads API](https://www.workingnomads.com/api/exposed_jobs/).
- We Work Remotely RSS: `https://weworkremotely.com/remote-jobs.rss`, with item tags `region, country, state, skills, category, type, expires_at`. Live: 87 items, 1 leadership title (Databricks director, "Anywhere in the World") — VERIFIED (live) — [WWR RSS](https://weworkremotely.com/remote-jobs.rss).
- Get on Board: the public API needs no login and includes job search; the private API uses a bearer JWT — VERIFIED_OFFICIAL — [Get on Board API help](https://www.getonbrd.com/help/que-puedo-hacer-con-la-api-de-get-on-board?lang=en); [API doc](https://api-doc.getonbrd.com/?version=latest). Live `GET https://www.getonbrd.com/api/v0/search/jobs?query=analytics&per_page=50` returned 5 pages. Attributes include `lang, countries, remote, remote_modality, remote_zone, seniority, min_salary, max_salary, published_at, response_time_in_days, applications_count`. The first page held 2 "Lead" roles (Lead Data Scientist, Lead Data Engineer); relevance was loose (the first hit was a Java/ETL developer) — [Get on Board search API](https://www.getonbrd.com/api/v0/search/jobs?query=analytics&per_page=50).
- Jobgether Brazil page (2026-10-15): header "21272 jobs", "Updated daily". Filters cover salary, language (including English and Portuguese), contract, industry, company and experience. Location tags such as "Remote from Latin America" / "Remote from Anywhere", yet a "Head of GTM" tagged "Remote from United States" appeared on the Brazil page. Staffing-firm postings are included — PRACTICAL_EXTERNAL — [Jobgether Brazil](https://jobgether.com/remote-jobs/brazil).
- The DailyRemote catalog, already in the pipeline, gave 437 unique jobs from 15 queries with an authorized session on 2026-10-02. Anonymous detail pages are gated, and 5 queries hit the page limit (partial coverage) — VERIFIED (local doc) — [README](/Users/admin/Documents/HR-Automation/README.md).
- dbt's job page aggregates analytics/data jobs "100% remote-friendly" (some with timezone restrictions), compiled with Outer Join; employers are asked to post in dbt Slack #jobs. Fetched 2026-10-15, the page body did not render listings — PRACTICAL_EXTERNAL — [getdbt jobs](https://getdbt.com/analytics-engineering/jobs/).

### Inferences
- HYPOTHESIS (priority for this target): Himalayas (high) > Get on Board (medium; LATAM with a `lang` field, but few analytics-leadership roles) > Jobgether (medium-low; noisy eligibility) > Remotive / Remote OK / WWR / Jobicy / Working Nomads (low yield for analytics leadership; cheap to poll). For coarse fields like "Worldwide/LATAM/Americas", keep the pipeline's `REVIEW` default and confirm in the description.
- HYPOTHESIS: boards that tag LATAM/Americas mostly surface contractor/EOR-style remote roles (via Deel/Oyster etc.). Leadership roles open to Brazil are a small fraction, so polling the ATS boards of known LATAM-hiring employers will beat board polling.

### Gaps
- Not assessed because the search budget was exhausted: Wellfound (no public API known), Otta/Welcome to the Jungle, Torre, Revelo, Talently, DailyRemote's own Brazil filter quality, and Jobgether's eligibility method.
- No independent measurement of eligibility-label accuracy for any board.

## 4. Brazilian channels (Gupy, Vagas.com, Catho, LinkedIn Brasil) and their share of English-language roles

### Takeaway
Brazilian-native channels are overwhelmingly Portuguese-language. On Gupy's portal on 2026-10-15, English leadership queries returned nothing, and "analytics" returned Portuguese titles only. Even LinkedIn Brazil results for English head-of-analytics queries were about 40% Portuguese titles. "English required" statistics (over 50% to 92% of postings) measure English as a skill, not English as the working language, so they overstate how open these roles are to a beginner Portuguese speaker.

### Cited Findings
- Gupy portal live (2026-10-15): `term=analytics` → 12 first-page results, all Portuguese titles (for example "Coordenador(a) de Analytics, Insights & Operações de Dados", Memed; "Analista de Produtos Pl. (Business Analytics)", PagBank). Workplace: hybrid 6, on-site 4, remote 2. `term=head of data` and `term=analytics manager` → 0 results in the server-rendered list. `term=gerente de analytics` → 1 result ("Gerente Sr de Data, Analytics & AI", Alpargatas, São Paulo, hybrid) — VERIFIED (live) — [Gupy portal search](https://portal.gupy.io/job-search/term=analytics).
- Gupy-hosted company pages sampled (Creditas, Ambev) list Portuguese titles (for example "Analista de Riscos", "Ajudante de Armazém") — live 2026-10-15 — [Creditas Gupy](https://creditas.gupy.io/).
- LinkedIn Brazil sample (2026-10-15): 4 of 10 cards for an English head/director-of-analytics query had Portuguese titles — live — [LinkedIn guest endpoint](https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=%22Head%20of%20Analytics%22%20OR%20%22Analytics%20Director%22&geoId=106057199&f_WT=2&start=0).
- Robert Half Guia Salarial 2025 (via Gazeta do Povo 2026): English is required in 92% of managerial LinkedIn postings in Brazil, and in over 70% of tech/marketing/finance postings — PRACTICAL_EXTERNAL (secondary) — [Gazeta do Povo 2026](https://gazetadopovo.com.br/vozes/parana-sa/ingles-deixa-de-ser-diferencial-e-se-torna-requisito-de-contratacao-no-brasil-em-2026).
- Catho: over 50% of Brazilian postings require English; Page Personnel: about 60% (undated, older article) — PRACTICAL_EXTERNAL — [Gazeta do Povo (older)](https://www.gazetadopovo.com.br/economia/pos-e-carreira/em-10-anos-todas-as-vagas-de-emprego-vao-exigir-fluencia-em-ingles-agugkn4r1p1q3ladpfk9qbumv/).
- Deel reported that international demand for Brazilian talent rose 53% in 2025 (secondary) — PRACTICAL_EXTERNAL — [Educa Mais Brasil](https://www.educamaisbrasil.com.br/educacao/noticias/contratacao-de-brasileiros-cresce-53-no-mundo-diz-pesquisa).
- Brazilian Greenhouse boards vary widely in Portuguese title share (C6 92%, Stone 78%, iFood 72%, XP 48%, QuintoAndar 46%; VTEX 6%, Wellhub 5%, EBANX/Wildlife 0%) — live 2026-10-15 (see section 1).

### Inferences
- HYPOTHESIS: for a beginner Portuguese speaker, Gupy, Vagas.com, Catho and InHire are low-yield. Keep at most one Gupy query set (for example "Head de Analytics", "Gerente de Analytics", "Head of Data") for market mapping, not as a primary channel.
- HYPOTHESIS: "English required" in a Brazilian posting usually means reading or speaking with global stakeholders while the team works in Portuguese. Only an explicit "working language: English" or an English-only job description at a multinational or global-team company signals real fit.

### Gaps
- Vagas.com and Catho were not probed (search budget exhausted), and no measured share of English-language postings exists for them.
- No primary-source methodology was found for the Catho, Page or Robert Half English-requirement figures.

## 5. Executive search and recruiters for data/analytics leadership in São Paulo

### Takeaway
Evidence is thin and mostly secondary. The global retained firms (Korn Ferry, Russell Reynolds, Spencer Stuart, Heidrick & Struggles, Egon Zehnder) and Stanton Chase have São Paulo presence and work client-funded mandates, mostly at C-level/VP. Manager, Senior Manager and Head roles in analytics are more typically covered by Michael Page/Page Executive, Robert Half and boutiques. Getting on their radar is mainly passive: a findable LinkedIn profile, being referred by placed executives, and responding to researcher outreach.

### Cited Findings
- The largest firms in the Americas by estimated revenue (2022 ranking) are Korn Ferry, Russell Reynolds, Spencer Stuart, Heidrick & Struggles and Egon Zehnder. Executive search is a client-paid headhunting service — PRACTICAL_EXTERNAL — [Wikipedia list of executive search firms](https://en.wikipedia.org/wiki/List_of_executive_search_firms).
- Egon Zehnder's privacy notice (last updated 2023-10-08) says it holds candidate data, including employment and compensation history and "suitability for Client opportunities" from its assessments. So candidate profiles exist in its database — VERIFIED_OFFICIAL — [Egon Zehnder CCPA notice](https://egonzehnder.com/ccpa).
- Stanton Chase's São Paulo office has operated since 1995 and runs CEO/director/senior executive searches — VERIFIED_OFFICIAL (firm page) — [Stanton Chase São Paulo](https://www.stantonchase.com/office/executive-search-firm-in-sao-paulo-brazil).
- Michael Page Brasil has offices in São Paulo, Campinas, Curitiba, Recife and Rio, offering executive search, specialized recruitment and leadership consulting. It previously opened a division for executives with digital capabilities, and its job board has listed "Head de Dados" roles — PRACTICAL_EXTERNAL — [Mundo do Marketing](https://mundodomarketing.com.br/michael-page-reposiciona-marca-e-passa-a-concentrar-servicos-em-uma-unica-identidade); [IT Forum](https://itforum.com.br/?p=116103); [Michael Page jobs](https://www.michaelpage.com.br/jobs/analista?page=1).
- FESA Group's CEO is described as placing C-level and senior technology executives (promotional list) — PRACTICAL_EXTERNAL — [Echo Global list](https://echoglobal.tech/it-recruiters-brazil/).
- Flow Executive Finders: senior management, director, C-level and board searches, with a focus on finance, sales and marketing per one consultant profile; a technology-leader hire is cited in a 2022 campaign article — PRACTICAL_EXTERNAL — [Marcas pelo Mundo 2022](https://marcaspelomundo.com.br/agencias/iris-worldwide-apresenta-a-primeira-campanha-publicitaria-da-flow-executive-finders/).
- Boutiques found: Gobetti Recruitment Group (senior management + IT, 1–10 staff); RSG Brasil (founder later an N2Growth partner); Orientha (no data focus) — PRACTICAL_EXTERNAL — [Pitch N Hire SP listing](https://www.pitchnhire.com/locations/brazil/sao-paulo/executive-search); [Hunt Scanlon on N2Growth Brazil](https://huntscanlon.com/n2growth-rapidly-expanding-latin-america-search-operations-adding-new-partner-in-brazil/).
- Ashby (data 2021–Jun 2025): agency is a small share of applications (0.4%) and hires (declining), but has a high conversion rate: 42% application→interview and 8% interview→offer — VERIFIED_OFFICIAL (vendor data) — [Ashby referrals report](https://www.ashbyhq.com/talent-trends-report/reports/referrals); [Ashby inbound report](https://www.ashbyhq.com/talent-trends-report/reports/inbound).

### Inferences
- HYPOTHESIS: for Manager/Senior Manager/Head of Analytics in São Paulo, the most relevant intermediaries are contingency or specialized recruiters (Michael Page/Page Executive Technology and Digital, Robert Half Technology, Hays, Talenses, Exec, boutiques). Retained firms rarely run Manager-level analytics searches.
- HYPOTHESIS (radar tactics, all passive or low-cost): an English-language LinkedIn headline matching target titles; "Open to Work" visible to recruiters only; accepting connection requests from data/tech recruiters at these firms; registering CVs where firms offer a candidate portal. The candidate should do any registration personally.
- HYPOTHESIS: Portuguese-language interviews are likely with Brazilian search firms. Their mandates skew to Portuguese-speaking roles at Brazilian companies, so yield for an English-only candidate is uncertain.

### Gaps
- Korn Ferry, Heidrick, Spencer Stuart, Russell Reynolds, Exec, Talenses, Fesa and Hunt Talentos São Paulo data/analytics practice pages were not verified (search budget exhausted).
- No data on how often analytics Manager/Head roles in Brazil are filled via agencies vs other channels.

## 6. Community job channels

### Takeaway
The verified community job channels are Locally Optimistic's #job-postings (analytics leaders; invite by email) and dbt Community Slack #jobs (100K+ members; dbt's remote analytics jobs page). Data Hackers is Brazil's largest data community (47K+), but its jobs channel and English share are unverified. Lenny's Jobs targets product/engineering/design/growth roles rather than analytics. Measure Slack, Reforge and LATAM founder newsletters were not verified.

### Cited Findings
- Locally Optimistic: a community where "current and aspiring analytics leaders" share lessons. Join by emailing the admins with a short intro; members use their real name. There is a #job-postings channel where recruiters post and invite candidates to reach out; other recruiting DMs are prohibited (page undated, fetched 2026-10-15) — VERIFIED_OFFICIAL — [Locally Optimistic community](https://locallyoptimistic.com/community/).
- dbt Community: 100K+ active members and over 5K Slack messages a day. The #jobs channels require agreeing to the Code of Conduct; dbt Labs does not vet postings — VERIFIED_OFFICIAL — [dbt community](https://www.getdbt.com/community); [dbt #jobs terms](https://docs.getdbt.com/community/resources/jobs-terms-and-conditions); [dbt jobs page](https://getdbt.com/analytics-engineering/jobs/).
- Data Hackers: over 47K data professionals plus the State of Data Brazil survey. Slack is still used to share events; an older (2019) Slack count was about 1.5K. No jobs-channel detail was found — PRACTICAL_EXTERNAL — [Data Hackers newsletter](https://www.datahackers.news/p/a-import-ncia-de-comunidades-de-tecnologia-no-brasil-com-a-globo); [TabNews comment](https://www.tabnews.com.br/Caslu/area-de-dados).
- Lenny's Jobs (LennysJobs.com) focuses on four builder roles (product, engineering, design, growth/marketing), with AI assistants per listing; basic use is free and paid subscribers get alerts (announcement text reposted on LinkedIn; date unverified) — PRACTICAL_EXTERNAL — [announcement repost](https://br.linkedin.com/in/nicolekuczynski).

### Inferences
- HYPOTHESIS: for English-language analytics leadership, Locally Optimistic and dbt #jobs give the highest-signal community postings, though they skew toward US/EU and analytics engineering. Data Hackers is most useful for Brazil market intelligence and referrals; its postings are likely mostly Portuguese.
- HYPOTHESIS: community Slack channels are not machine-readable for the pipeline without a Slack app/token. Treat them as manual weekly scans, and log leads into CRM manually with channel = referral/cold as appropriate.

### Gaps
- Measure Slack (digital analytics), Reforge, product-led communities and LATAM founder newsletters were not verified (search budget exhausted).
- No activity or volume data for any #jobs channel; no data on Brazil eligibility of community postings.

## 7. Effectiveness evidence 2024–2026: referrals vs inbound vs sourced, hidden job market, response rates

### Takeaway
Inbound applications are the largest source of hires (43–52%, per Ashby to mid-2025) but convert terribly per application. In Ashby's data, inbound goes 3% application→interview vs 40% for referrals; Gem puts referrals at 11x and sourced at about 8x inbound hire rates. Executive roles are filled through inbound less often (about 35%). The "70–80% hidden job market" figure is not evidence-based, but referral, sourcing and internal routes do matter disproportionately at senior levels.

### Cited Findings
- Ashby inbound report (published 2025-10-17; about 250K hires, Jan 2021–Jun 2025): inbound was 43–52% of hires, peaking at 52% in Q2 2025; sourced about 16%, referrals about 18% (stable about 17% regardless of inbound volume); agency declining. Executive roles: about 35% from inbound. Tech roles skew to sourcing (24% vs 18% for business roles) — VERIFIED_OFFICIAL (vendor data) — [Ashby inbound](https://www.ashbyhq.com/talent-trends-report/reports/inbound).
- Ashby referrals report (2025-05-16; 38M applications, 93K jobs, 2021–2024): referral share of applications averaged 1% (2% in Q1 2021 → under 1% by Q1 2024); inbound 93.8%, sourced 4.8%, agency 0.4%. Application→interview: referred 40%, inbound 3%, sourced 25%, agency 42%, internal 42%. Interview→offer: referred 16%, inbound 6%, sourced 6%, internal 32%. Inbound offer rate fell from 0.7% to 0.2% (Q3 2021 → Q4 2024); referral offer rate fell from 12.1% to 7.3% — VERIFIED_OFFICIAL (vendor data) — [Ashby referrals](https://www.ashbyhq.com/talent-trends-report/reports/referrals).
- Gem 2026 Recruiting Benchmarks (posted Dec 1, presumably 2025; 165M+ applicants, 1.2M hires, Jun 2021–May 2025): job boards and company marketing channels produce about 90% of applications but about half of hires; direct sourcing gives 11% of hires from 2.6% of applications; referrals convert at 11x inbound, internal mobility at 32x; sourced candidates are about 8x more likely to be hired. 8% of applicants pass initial screening; 0.5% get offers (about 1 hire per 200 applications); interviews per hire up 33% since 2021 — VERIFIED_OFFICIAL (vendor data) — [Gem 2026 takeaways](https://app.gem.com/blog/key-takeaways-from-the-2026-recruiting-benchmarks-report).
- Gem 2025 report (data through 2024): sourced 5x more likely to be hired than inbound; job boards/social were 49.0% of applications and 24.6% of hires. This conflicts with Gem 2026 (8x, about 90% of applications); the category definitions likely differ — PRACTICAL_EXTERNAL (vendor summary) — [Gem 2025 takeaways](https://vercel.gem.com/blog/10-takeaways-from-the-2025-recruiting-benchmarks-report); [Gem 2025 PDF](https://lp.gem.com/rs/972-IVV-330/images/2025%20Recruiting%20Benchmarks%20-%20Gem.pdf?version=0).
- Greenhouse (secondary): 61% of US job seekers were ghosted after interviews, up 9 points since April 2024 — PRACTICAL_EXTERNAL — [Unleash](https://www.unleash.ai/artificial-intelligence/greenhouse-61-of-job-seekers-have-been-ghosted-during-the-recruitment-process). 18–22% of jobs posted on Greenhouse are classified as ghost jobs in a given quarter; 2024 vs 2025 attribution is unclear — TO_VERIFY — [Noon.ai roundup](https://www.noon.ai/blog/articles/354-candidate-ghosting-statistics-2026).
- Hidden job market: the 70–80% claims trace to anecdote and unnamed reports (a 1979 75% claim had no evidence); figures quoted range from 65% to 95% with no data — PRACTICAL_EXTERNAL — [UCF Career Services, 2024-08-12](https://career.ucf.edu/blog/2024/08/12/is-it-true-that-80-of-job-openings-are-not-advertised/); [Sam Young, 2021-04](https://www.samyoung.co.nz/2021/04/how-big-is-hidden-job-market.html).

### Inferences
- HYPOTHESIS: for analytics leadership, applying cold via public postings is necessary but low-yield: about 3% interview rate per inbound application (Ashby, all levels). Converting a posting into a referral or hiring-manager contact (40% application→interview) is the highest-leverage step, and the CRM's `referral_status` field is the right place to track it.
- HYPOTHESIS: at roughly 1 hire per 200 applications (Gem) and the CRM goal of 4 team interviews a month, pure inbound needs on the order of 100+ quality applications a month. A mix weighted to referrals/recruiters reduces that by an order of magnitude. This is a planning estimate, not a measured rate for this candidate.

### Gaps
- No 2025–2026 data specific to senior or leadership response rates by channel. Huntr-type tracker reports and LinkedIn Talent Insights were not retrieved (search budget exhausted).
- No Brazil- or LATAM-specific channel-effectiveness data was found.

## 8. Machine-readable sources the existing pipeline could add

### Takeaway
The pipeline (README/architecture read 2026-10-15) already supports Greenhouse, Ashby and Lever APIs, DailyRemote (HTML with an optional cookie session), generic HTML pages, a mixed watchlist, and Gmail/.eml alerts. The current watchlist enables only Supabase and Wikimedia. The fastest gain needs no code: add verified LATAM/global board tokens to `config/company_watchlist.yaml`. The best new adapter is Himalayas (country=BR, seniority, structured `locationRestrictions`), followed by Get on Board, Remotive, Jobicy and the WWR RSS. LinkedIn should stay on the email-alert route.

### Cited Findings
- Existing adapters: `greenhouse` (`board_token`, `content=true`), `ashby` (`board_name`), `lever` (`site`, `region`, pagination), `dailyremote` (`search_url_template`, `max_pages: 1`, `max_detail_fetches: 40`), `html`, `watchlist`, `email_files`. The pipeline treats a structured `applicantLocationRequirements` as hiring geography and keeps "remote" without Brazil confirmation in `REVIEW` — VERIFIED (local) — [README](/Users/admin/Documents/HR-Automation/README.md); [architecture](/Users/admin/Documents/HR-Automation/docs/architecture.md); [settings.yaml](/Users/admin/Documents/HR-Automation/config/settings.yaml).
- The current watchlist enables only Supabase (Ashby) and Wikimedia (Greenhouse); legacy Stripe/Airbnb HTML configs are disabled — VERIFIED (local) — [company_watchlist.yaml](/Users/admin/Documents/HR-Automation/config/company_watchlist.yaml).
- Board tokens verified live on 2026-10-15, usable with existing types: Ashby `nubank`, `addi`, `belvo`, `neon`, `oyster`, `zapier`; Greenhouse `ifoodcarreiras`, `quintoandar`, `vtex`, `wildlifestudios`, `ebanx`, `gympass` (Wellhub), `xpinc`, `c6bank`, `inter`, `stone`, `canonical`, `datadog`, `gitlab`, `mongodb`, `elastic`, `twilio`; Lever `dlocal`, `kavak`, `neon` — VERIFIED (live; see sections 1 and 3).
- Keyless JSON/RSS feeds verified live on 2026-10-15, with their terms:
  - Himalayas: search API, attribution, no redistribution, daily refresh — [Himalayas API](https://himalayas.app/api).
  - Remotive: 24h delay, at most 4 calls a day, attribution — [Remotive README](https://github.com/remotive-com/remote-jobs-api).
  - Jobicy: `geo=latam`, attribution — [Jobicy](https://jobicy.com/jobs-rss-feed).
  - Remote OK: followed backlink required — [Remote OK API](https://remoteok.com/api).
  - Working Nomads — [API](https://www.workingnomads.com/api/exposed_jobs/).
  - WWR: RSS with region/country — [RSS](https://weworkremotely.com/remote-jobs.rss).
  - Get on Board: `/api/v0/search/jobs` with `lang`/`countries`/`remote_modality` — [API](https://www.getonbrd.com/api/v0/search/jobs?query=analytics&per_page=50).
  - Teamtailor: per-company `/jobs.rss` — [example](https://career.teamtailor.com/jobs.rss).
- Gupy: public pages embed jobs in `__NEXT_DATA__` (no documented public API) — live; [Gupy portal](https://portal.gupy.io/job-search/term=analytics). Workday CXS and SmartRecruiters were unconfirmed live (section 1).
- LinkedIn guest endpoints are unofficial, and one guide warns they may break or violate LinkedIn's terms — PRACTICAL_EXTERNAL — [Glama guide](https://glama.ai/mcp/servers/@adamd9/mcp-jobsearch/blob/2c861f861b5788f814153ef3ebfbe6f7c1ba6ef6/linkedin_job_search_scraping_guide.md).

### Inferences
- HYPOTHESIS (priority list for the pipeline owner; no files were modified):
  1. **No code change:** add the verified tokens above to `company_watchlist.yaml`. Nubank is on Ashby now, not Greenhouse; iFood's token is `ifoodcarreiras`, not `ifood`; Wellhub's is `gympass`. Expect low Portuguese-free yield from C6, Stone and iFood.
  2. **New `himalayas` adapter:** call `/jobs/api/search` with `q` from profile keywords, `country=BR`, `seniority=Manager,Director,Executive`, cursor or page pagination. Map `locationRestrictions` to `applicantLocationRequirements` and `timezoneRestriction(s)` to the timezone field (the field name differs within the docs, so verify live). Respect attribution and the no-redistribution terms; a personal, local, non-published pipeline appears compatible, but check the API terms.
  3. **New `getonboard` adapter:** use `/api/v0/search/jobs`; map `countries`/`remote_modality`/`remote_zone` to geography and `lang` to a language signal. The `lang` field must not be treated as proof of English working language, in line with the pipeline's rules.
  4. **Generic `json_feed`/`rss` adapters** covering Remotive (`candidate_required_location`; at most 4 calls a day), Jobicy (`jobGeo`, `geo=latam`), Remote OK, Working Nomads and WWR RSS (`region`/`country`). These are cheap, but expect low analytics-leadership yield.
  5. **`teamtailor` RSS type** for any target company found on Teamtailor.
  6. **Low priority:** a Gupy `__NEXT_DATA__` extractor (mostly Portuguese roles); a Workday CXS adapter (unofficial POST, unverified); a SmartRecruiters Posting API adapter (needs a confirmed identifier/key).
  7. **LinkedIn:** keep daily job alerts → Gmail → `import-gmail` (already implemented) rather than scraping.
- HYPOTHESIS: run an ATS-board "discovery" pass periodically. X-ray or Himalayas `companySlug` results reveal new employers; after testing the token with one GET, add them to the watchlist. This turns noisy aggregator discovery into complete, dated ATS polling.

### Gaps
- Terms of use for using these APIs in a personal local tool were read only where the API pages state them; formal API terms pages (Himalayas API terms, Get on Board terms) were not reviewed in full.
- No adapter was implemented or tested against the project code (out of scope: research only, no commands run in the project).
