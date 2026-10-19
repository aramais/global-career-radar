# Brazilian tech companies in São Paulo with English as a working language, as employers for a senior analytics leader

Research date: 2026-10-15. Method: live public ATS APIs (Greenhouse `boards-api`, Ashby `posting-api`, Lever `v0/postings`, Gupy `__NEXT_DATA__` career pages), checked on 2026-10-15, plus web search and press. Live API data is the strongest evidence. Search-index snippets that could not be opened are marked "indexed only".

Confidence labels: **VERIFIED_OFFICIAL** (company's own page or ATS board, live 2026-10-15), **PRACTICAL_EXTERNAL** (press, aggregators, reviews), **HYPOTHESIS** (inference), **TO_VERIFY** (open question).

"Posting-language share" means the share of all live postings on the company board written mainly in English (EN) or Portuguese (PT). It comes from a word-frequency classifier I ran over the full board on 2026-10-15. It works as a proxy for internal working language but does not prove it.

---

## Q1. Which São Paulo Brazilian tech companies use English as a working language, and what is the evidence?

### Takeaway
Only a small group posts in English across the board: **Nubank, Wellhub (ex-Gympass), EBANX, Wildlife Studios, VTEX and CloudWalk**. Nubank's São Paulo postings say "Fluency in English is mandatory" and treat Portuguese only as an advantage, which is the strongest single signal. Most other large Brazilian tech companies (iFood, QuintoAndar, Stone, PagBank, C6, XP, BTG, Creditas, Grupo OLX, Neon, Méliuz) post overwhelmingly in Portuguese. At those companies English is at best "inglês avançado" as a requirement or a nice-to-have, so Portuguese should be treated as the dominant working language.

### Cited Findings

**English-first (posting language and explicit statements)**
- **Nubank**: the live board has moved to Ashby (98 jobs, 30 in São Paulo, all in English) — VERIFIED_OFFICIAL — [Nubank Ashby board](https://jobs.ashbyhq.com/nubank). The Greenhouse board token `nubank` now returns 0 jobs; older Greenhouse links are indexed only — [Greenhouse API nubank](https://boards-api.greenhouse.io/v1/boards/nubank/jobs).
  - Senior Staff Product Manager, São Paulo, published 2026-10-15: "Fluency in English is mandatory. Portuguese and/or Spanish is a strong advantage." — VERIFIED_OFFICIAL — [posting](https://jobs.ashbyhq.com/nubank/8b86f6af-d7d7-4f61-8cfb-e50f3b2a6e9c)
  - Chief of Staff, Credit Card, São Paulo, published 2026-10-14: "Fluent English; Spanish or Portuguese is an advantage." — VERIFIED_OFFICIAL — [posting](https://jobs.ashbyhq.com/nubank/b45d9e02-0264-45a8-ba21-275c697b56d8)
  - Nubank's hiring hubs (live board) are São Paulo, Palo Alto, Mexico City, Miami, Bogotá, Toronto, Virginia and Buenos Aires — VERIFIED_OFFICIAL — [Nubank Ashby board](https://jobs.ashbyhq.com/nubank)
  - Posting boilerplate: "Nu serves more than 140 million customers" — VERIFIED_OFFICIAL — [Chief of Staff posting](https://jobs.ashbyhq.com/nubank/b45d9e02-0264-45a8-ba21-275c697b56d8)
- **Wellhub (Gympass)**: 82 of 87 live postings are in English. The postings say the company is "Headquartered in NYC with team members in Europe, North America and South America" — VERIFIED_OFFICIAL — [Wellhub Greenhouse board](https://job-boards.greenhouse.io/gympass); [Senior Strategy Manager posting](https://job-boards.greenhouse.io/gympass/jobs/8859330002)
- **EBANX**: 40 of 40 live postings are in English. Example requirement: "English proficiency — Comfortable using English in day-to-day communication, including documentation, meetings, and conversations with global teams." Another posting asks for "Fluent written and spoken English … from developers to C-level" — VERIFIED_OFFICIAL — [Senior Analytics & Intelligence Analyst](https://job-boards.greenhouse.io/ebanx/jobs/8013346003); [Senior Product Analyst, Financial Products](https://job-boards.greenhouse.io/ebanx/jobs/7655627003). The board also has roles in San Francisco and Mumbai — [EBANX board](https://job-boards.greenhouse.io/ebanx)
- **Wildlife Studios**: 16 of 16 live postings are in English. The Senior Data Scientist posting in São Paulo (2026-10-06) asks for "Proficiency in written English" — VERIFIED_OFFICIAL — [posting](https://job-boards.greenhouse.io/wildlifestudios/jobs/8872414002)
- **VTEX**: 28 of 32 live postings are in English. Benefits include a "Language development incentive program (English, Spanish, Portuguese)", and one role is in Bucharest — VERIFIED_OFFICIAL — [Analytics Engineer – Campaign Intelligence [Ads]](https://job-boards.greenhouse.io/vtex/jobs/6218060004); [VTEX board](https://job-boards.greenhouse.io/vtex)
- **CloudWalk / InfinitePay**: the 22 open positions on the company's own jobs page are in English. The page makes no explicit working-language statement — VERIFIED_OFFICIAL — [CloudWalk jobs](https://lp.cloudwalk.io/jobs). Older indexed postings (Lever board now 404) list "Advanced English … within our global team" for analyst roles — PRACTICAL_EXTERNAL — [Jobgether Data Alchemist](https://dynamic.jobgether.com/offer/68281fc89304184706c7335a-data-alchemist); [CloudWalk People Analyst](https://www.cloudwalk.io/jobs-positions/people-analyst-d64f675a). A third-party guide (not an official source) claims senior engineering at CloudWalk works mostly in English while customer-facing, legal, compliance and finance work mostly in Portuguese — PRACTICAL_EXTERNAL, low quality — [ResumeGeni](https://resumegeni.com/companies/cloudwalk-technology/how-to-apply)

**Portuguese-dominant (English required or nice-to-have at most)**
- **QuintoAndar Group**: 70 of 95 live postings are in Portuguese, 25 in English or Spanish (Mexico, Argentina, Portugal hub). The analytics-manager postings are in Portuguese and list "Inglês avançado" as a requirement on one and a differential on the other. The company claims presence in 6 countries, 3,100+ people, a tech hub in Portugal and a US$5.1B valuation — VERIFIED_OFFICIAL — [Gerente de Dados & Analytics](https://job-boards.greenhouse.io/quintoandar/jobs/4375416009); [Growth Intelligence Manager](https://job-boards.greenhouse.io/quintoandar/jobs/4368242009)
- **iFood**: 55 of 58 live postings are in Portuguese — VERIFIED_OFFICIAL — [iFood Greenhouse board](https://job-boards.greenhouse.io/ifoodcarreiras)
- **Stone**: 373 of 373 live postings are in Portuguese, and only 3 mention English — VERIFIED_OFFICIAL — [Stone Greenhouse board](https://job-boards.greenhouse.io/stone)
- **C6 Bank**: 156 of 156 postings in Portuguese, 7 mention English — VERIFIED_OFFICIAL — [C6 Greenhouse board](https://job-boards.greenhouse.io/c6bank)
- **XP Inc.**: 149 of 149 in Portuguese, 16 mention English — VERIFIED_OFFICIAL — [XP Greenhouse board](https://job-boards.greenhouse.io/xpinc)
- **BTG Pactual**: 273 of 275 in Portuguese. 97 mention English (typically "inglês" as a requirement) — VERIFIED_OFFICIAL — [BTG careers (Greenhouse-backed)](https://carreiras.btgpactual.com/vagas?gh_jid=5814844004)
- **PagBank (PagSeguro)**: about 807 live Gupy postings, Portuguese titles and text — VERIFIED_OFFICIAL — [PagBank Gupy](https://pagseguro.gupy.io/)
- **Creditas**: 8 live Gupy postings, all with Portuguese titles. The Greenhouse boards `creditas` and `creditasen` (Spanish/English, Mexico) now return 404, so those are indexed only — VERIFIED_OFFICIAL — [Creditas Gupy](https://creditas.gupy.io/); indexed: [creditasen posting](https://job-boards.greenhouse.io/creditasen/jobs/7926408002)
- **Grupo OLX (OLX Brasil / ZAP / VivaReal)**: 18 live Gupy postings, all in Portuguese — VERIFIED_OFFICIAL — [Grupo OLX Gupy](https://vemsergrupoolx.gupy.io/)
- **Neon**: 10 live Lever postings, all with Portuguese titles and all remote. That this board belongs to Banco Neon is a HYPOTHESIS based on the titles — [Neon Lever](https://jobs.lever.co/neon)
- **Méliuz**: 3 live Gupy postings in Portuguese, Belo Horizonte-based. **Dock**: Gupy page has 0 live postings — VERIFIED_OFFICIAL — [Méliuz Gupy](https://meliuz.gupy.io/); [Dock Gupy](https://dock.gupy.io/)
- **Hotmart**: indexed postings on an EU Greenhouse board (`hotmartcareersbr`), e.g. a Product Manager for Data Platforms in São Paulo or Belo Horizonte. The live API could not be read — PRACTICAL_EXTERNAL (indexed only) — [Hotmart posting](https://job-boards.eu.greenhouse.io/hotmartcareersbr/jobs/4799691101)

### Inferences
- HYPOTHESIS: English is the default internal language only where hiring is multi-country by design: Nubank (BR/MX/CO/US), Wellhub (NYC HQ, Europe), EBANX (cross-border payments), VTEX (global B2B SaaS) and Wildlife (global games). At the other companies, data and analytics teams likely run in Portuguese even where "inglês avançado" is required, because the posting itself is in Portuguese. For QuintoAndar's analytics roles, English is only a differential on one and a requirement on the other.
- HYPOTHESIS, background knowledge not re-verified this session (TO_VERIFY via investor-relations pages): US-listed are Nubank (NYSE: NU), StoneCo (Nasdaq: STNE), PagSeguro/PagBank (NYSE: PAGS), Inter&Co (Nasdaq: INTR), VTEX (NYSE: VTEX) and XP (Nasdaq: XP). The board data shows a US listing does **not** imply an English working language: Stone, PagBank and XP post 100% in Portuguese.
- HYPOTHESIS: OLX Brasil and Pismo are not "Brazilian-founded independent" in today's sense. Ownership of OLX Brasil and Visa's ownership of Pismo were not verified this session, so TO_VERIFY.

### Gaps
- Not checked this session: Glassdoor or LinkedIn "working language" reviews, and employee-written statements on internal language.
- ATS not located or not readable for PicPay, Loft (Ashby board exists, 0 jobs), Loggi, Magalu, Inter&Co, Pismo, Cora, Olist and Zé Delivery (parent Ambev has 322 Gupy jobs, none clearly Zé-branded). Their English-language status is unknown (TO_VERIFY).
- Nuvemshop's ATS was not identified. 2026 postings appear on the Accel portfolio job board in Portuguese and English — [Accel / Nuvemshop](https://jobs.accel.com/companies/nuvemshop/jobs/84437111-business-analyst-credit-and-collections)
- PicPay's US listing status was not reliably sourced (TO_VERIFY).

---

## Q2. Current (2026) or recent (2025–26) analytics-leadership openings

### Takeaway
On 2026-10-15 **no English-language Head/Director/Manager of Analytics opening exists in São Paulo** at the English-first companies (Nubank, Wellhub, EBANX, Wildlife, VTEX, CloudWalk). Their analytics openings are individual-contributor roles, or adjacent strategy roles such as Nubank's Chief of Staff and Wellhub's Senior Strategy Manager.

The closest manager-level fits for the profile are live but **in Portuguese**:
- QuintoAndar's Growth Intelligence Manager (risk plus new monetization such as ads)
- PagBank's Gerente Geral de Precificação (pricing/monetization, on-site)
- BTG's Gerente de Portfolio Management & Pricing de Crédito (on-site)

### Cited Findings (all VERIFIED_OFFICIAL, live on 2026-10-15 via ATS API unless noted)

| Company | Title | Location / model | Published | Language / PT requirement | Fit note | URL |
|---|---|---|---|---|---|---|
| Nubank | Chief of Staff, Credit Card | São Paulo, hybrid at least 2x/week | 2026-10-14 | EN; "Fluent English; Spanish or Portuguese is an advantage" | Partner to the GM of the Credit Card BU; "governance, strategic planning and business analysis"; "designed as an entry point to other roles". Not an analytics title, but very close to the P&L owner | [link](https://jobs.ashbyhq.com/nubank/b45d9e02-0264-45a8-ba21-275c697b56d8) |
| Nubank | Staff Business Analyst | Palo Alto, hybrid | 2026-10-09 | EN | IC, US-based, outside SP | [link](https://jobs.ashbyhq.com/nubank/d4890c44-a1c2-4e57-a8c0-edda18d3d287) |
| Nubank | Business Analytics Manager | Bogotá | date unknown | EN | Indexed only (old Greenhouse); shows the job family exists | [indexed](https://job-boards.greenhouse.io/nubank/jobs/7457819) (search snippet only) |
| Nubank | Lead Analytics Engineer | São Paulo, hybrid | indexed; a Senior AE listing dated May 2026 | EN | Engineering-leaning; excluded by profile | [indexed](https://international.nubank.com.br/pt-br/job-page/?id=7457819) — PRACTICAL_EXTERNAL |
| Wellhub | Senior Strategy Manager – AI & Operations (Wellz) | São Paulo, hybrid | 2026-09-30 | EN | Reports to Wellz Head of Strategy & Ops. Requires a "Proven track record of actually shipping an AI agent or workflow into production", so AI-ops heavy and only a partial fit | [link](https://job-boards.greenhouse.io/gympass/jobs/8859330002) |
| Wellhub | Business Intelligence Analytics Projects Senior Analyst | São Paulo, hybrid | updated 2026-10-01 | EN | IC, excluded | [link](https://job-boards.greenhouse.io/gympass/jobs/8659151002) |
| Wildlife | Business Analyst – Growth & User Acquisition | São Paulo | 2026-10-02 | EN | IC, but the team does experimentation, monetization and UA investment decisions | [link](https://job-boards.greenhouse.io/wildlifestudios/jobs/8864832002) |
| Wildlife | Senior Data Scientist | São Paulo | 2026-10-06 | EN | IC | [link](https://job-boards.greenhouse.io/wildlifestudios/jobs/8872414002) |
| EBANX | Senior Analytics & Intelligence Analyst | Curitiba | 2026-10-05 | EN | IC, outside SP | [link](https://job-boards.greenhouse.io/ebanx/jobs/8013346003) |
| VTEX | Analytics Engineer – Campaign Intelligence [Ads] | Brazil, remote-first | updated 2026-10-06 | EN | IC/engineering | [link](https://job-boards.greenhouse.io/vtex/jobs/6218060004) |
| CloudWalk | (none: Data Scientist – Collection, ML Engineer, etc.) | Brazil / SP | undated | EN | No analytics leadership among 22 roles | [jobs page](https://lp.cloudwalk.io/jobs) |
| QuintoAndar | **Growth Intelligence Manager — Risk & Strategic Projects** | São Paulo listing; remote-first in Brazil | first published 2026-08-26, updated 2026-10-08 | **PT posting**; "Inglês avançado" is only a *diferencial* | Leads a multidisciplinary team. Two pillars: loss prevention, and "impulsionar novas frentes de expansão de receita … novos canais de monetização via anúncios (ads)". Works with Product, Legal, Finance and Ops | [link](https://job-boards.greenhouse.io/quintoandar/jobs/4368242009) |
| QuintoAndar | Gerente de Dados & Analytics | São Paulo listing; remote-first | 2026-10-09 | **PT**; "Inglês avançado" required; Spanish a differential | BI/DataOps analytical layer (Power BI/Tableau/Superset). More BI/platform than decision-science | [link](https://job-boards.greenhouse.io/quintoandar/jobs/4375416009) |
| QuintoAndar | Strategy & New Business Senior Manager; Senior Strategy & Operations Manager; Sr Strategy Manager | Mexico City (hybrid 3x/week) / Argentina | 2026-07-13 to 2026-10-08 | **Spanish** postings | Outside SP | [link](https://job-boards.greenhouse.io/quintoandar/jobs/4289904009) |
| PagBank | **Gerente Geral de Precificação (Large Retail)** | São Paulo, **on-site** | 2026-10-05 | **PT** | Leads pricing, profitability and monetization strategy for large retail. "Principal parceiro dos gestores comerciais e diretores de negócio"; leads executive approval forums. Strong domain match | [link](https://pagseguro.gupy.io/jobs/12514915) |
| PagBank | Coordenador de Estratégia Comercial | SP | live | PT | Coordinator level | [board](https://pagseguro.gupy.io/) |
| BTG Pactual | Gerente de Portfolio Management & Pricing de Crédito | São Paulo | first published 2026-07-08 | PT | Credit pricing and profitability for BTG and PAN retail books, including "elasticidade" and price simulations | [link](https://carreiras.btgpactual.com/vagas?gh_jid=6112158004) |
| BTG Pactual | Gerente de Dados e Analytics | São Paulo, **presencial** | first published 2026-02-27 | PT | "Atuar de forma hands-on"; SQL/Python/Spark/AWS pipelines. Engineering-heavy, excluded by profile | [link](https://carreiras.btgpactual.com/vagas?gh_jid=5814844004) |
| BTG Pactual | Data Science Lead; Gerente / Coordenador de People Analytics | São Paulo | updated 2026-10-01 | PT | People analytics is a domain mismatch | [DS Lead](https://carreiras.btgpactual.com/vagas?gh_jid=6174686004); [People Analytics Mgr](https://carreiras.btgpactual.com/vagas?gh_jid=6104164004) |
| iFood | (no analytics leadership) Group Product Manager II – Tech Growth; iFood Pago – Gerente de Growth e Outbound B2B (Osasco); Staff Data Scientist – Logistics Intelligence | Brasil / Osasco | updated 2026-10-13 | PT | Growth adjacency only | [board](https://job-boards.greenhouse.io/ifoodcarreiras); [Gerente de Growth](https://job-boards.greenhouse.io/ifoodcarreiras/jobs/8853109002) |
| XP Inc. | Gerente Estratégico de Negócios (Partner Development); Product Manager Sênior IA & Dados | São Paulo | 2026-07-14 / 2026-10-09 | PT | Weak analytics fit | [link](https://job-boards.greenhouse.io/xpinc/jobs/8623105002) |
| Stone | PM Sênior – IA Conversacional e Growth (Híbrido/SP) | SP, hybrid, CLT | 2026-10-15 | PT | PM, not analytics | [link](https://job-boards.greenhouse.io/stone/jobs/8015609003) |
| Neon | Analista de Analytics e Planejamento Sênior; Expert Business Analyst – Consumer & AI | Remote | live | PT | IC | [board](https://jobs.lever.co/neon) |
| Grupo OLX | Senior Marketing Data Analyst; Analista de Dados Pleno | Remote | live | PT | IC | [board](https://vemsergrupoolx.gupy.io/) |
| Nuvemshop | Business Analyst – Credit & Collections (remote, 2026-06-27); Sales Development Manager (SP hybrid, 2026-05-07) | — | 2026 | PT/EN mixed | No analytics leadership found | [Accel board](https://jobs.accel.com/companies/nuvemshop/jobs/84437111-business-analyst-credit-and-collections) — PRACTICAL_EXTERNAL |
| Creditas | (no analytics leadership; 8 roles) | SP hybrid | live | PT | — | [Creditas Gupy](https://creditas.gupy.io/) |

### Inferences
- HYPOTHESIS: in October 2026 the English-first employers are hiring analytics leaders mainly in Palo Alto, Mexico and Colombia (Nubank) or not at all. São Paulo analytics leadership at these firms may be filled internally or through LinkedIn/referrals rather than public boards. Treat these as **watchlist plus network** targets rather than apply-now targets.
- HYPOTHESIS: Nubank's Chief of Staff, Credit Card is the only live English, São Paulo role close to a P&L owner. It is a strategy/operating-rhythm role, not analytics leadership, so it matches "close to decision-makers" but not "lead analytics".
- HYPOTHESIS: the best substantive domain matches (QuintoAndar ads monetization plus risk; PagBank and BTG pricing) all fail the English-working-language constraint for a candidate with A1 Portuguese.

### Gaps
- The Gupy pages expose `publishedAt` only per job page. Only PagBank's pricing role was opened individually, so dates for other Gupy roles are missing.
- LinkedIn Jobs was not searched (it needs login). Head- and director-level analytics roles are often posted only there or filled by search firms (TO_VERIFY).
- No 2025 archive of past openings was compiled. "Recent 2025" openings are mostly unknown apart from indexed Nubank Greenhouse links.

---

## Q3. Work model and 2025–2026 return-to-office changes or layoffs

### Takeaway
- **Nubank** moved from remote-first to mandatory hybrid: at least 2 days/week from July 2026, rising to 3 days/week from January 2027.
- **QuintoAndar** and **VTEX** remain remote-first.
- **Wellhub** is "Flexible First" (hybrid or remote).
- **PagBank, C6, XP and BTG** postings skew on-site.
- **Stone** reportedly made a mass layoff of about 370 people that ended up in a labor-court dispute (date approximately early 2026, TO_VERIFY).
- **PagBank** had repeated cuts (300–500 reported).
- **Wildlife** cut staff in 2022, 2023 and 2024. No 2025–26 cuts were found for Wildlife, VTEX or Wellhub.

### Cited Findings
- **Nubank**: current postings state "Hybrid 2-3 times/week: Our hybrid work model brings us to the office at least twice a week, on strategic days" — VERIFIED_OFFICIAL — [Senior Staff PM](https://jobs.ashbyhq.com/nubank/8b86f6af-d7d7-4f61-8cfb-e50f3b2a6e9c); policy page [building.nu.com hybrid model](https://building.nu.com/nu-hybrid-work-model/) (redirected, not read)
- **Nubank**: press reports hybrid from July 2026 at 2 days/week and 3 days/week from January 2027. The CEO cited culture, innovation and operational excellence; some areas stay remote; São Paulo offices are expanding. Reported reaction: forums with about 7k of 9.5k employees, and 12 dismissals for cause after internal discussions — PRACTICAL_EXTERNAL (search snippet; the page returned 403 on fetch) — [Tecnoblog](https://tecnoblog.net/noticias/nubank-abandona-trabalho-remoto-e-tera-modelo-hibrido-a-partir-de-2026/); [Seu Crédito Digital](https://seucreditodigital.com.br/nubank-modelo-hibrido-trabalho-retorno/)
- **Nubank**: postings list a "Relocation Assistance Package, if applicable" — VERIFIED_OFFICIAL — [Chief of Staff posting](https://jobs.ashbyhq.com/nubank/b45d9e02-0264-45a8-ba21-275c697b56d8)
- **QuintoAndar**: "Atuamos no modelo remote-first … de qualquer lugar do Brasil", with an SP office, coworkings, and "Work From Anywhere … até 90 dias no ano". Mexico City roles are hybrid 3x/week — VERIFIED_OFFICIAL — [Gerente de Dados & Analytics](https://job-boards.greenhouse.io/quintoandar/jobs/4375416009); [Strategy & New Business Sr Manager (CDMX)](https://job-boards.greenhouse.io/quintoandar/jobs/4289904009)
- **Wellhub**: "As a Flexible First company, we offer hybrid and remote options … The model for this specific role can be discussed"; the Strategy role is "Based in São Paulo — hybrid model" — VERIFIED_OFFICIAL — [posting](https://job-boards.greenhouse.io/gympass/jobs/8859330002)
- **VTEX**: "Flexible work schedule and remote-first culture … #LI-Remote" — VERIFIED_OFFICIAL — [posting](https://job-boards.greenhouse.io/vtex/jobs/6218060004)
- **EBANX**: postings are labelled On-site or Hybrid, mostly "Curitiba or São Paulo" — VERIFIED_OFFICIAL — [EBANX board](https://job-boards.greenhouse.io/ebanx)
- **iFood**: in June 2026 it opened senior tech roles (AI, Data, Software Eng.) across Brazil in a hybrid model — PRACTICAL_EXTERNAL — [ABC da Comunicação](https://www.abcdacomunicacao.com.br/ifood-expande-area-de-tecnologia-e-abre-vagas-para-profissionais-de-todo-o-brasil/). Its last confirmed mass layoff was 355 people (6.4% of about 5,500) on 2023-03-01, which is **stale** — [Startups](https://startups.com.br/negocios/demissoes/ifood-faz-demissao-em-massa-e-cortes-podem-chegar-a-300/)
- **Stone**: posting terms "CLT: 8 horas diárias + 1h de intervalo | Escala: 5x2" — VERIFIED_OFFICIAL — [posting](https://job-boards.greenhouse.io/stone/jobs/8015609003)
- **Stone layoffs**: about 370 people were laid off ("ganho de eficiência"). A court ordered reinstatement, and Stone and Sindpd-SP jointly suspended the ruling to negotiate. The finsiders tag page is undated; a search snippet dates the Sindpd-SP item to about March 2026 (TO_VERIFY) — PRACTICAL_EXTERNAL — [Finsiders "demissões" tag](https://finsidersbrasil.com.br/tag/demissoes)
- **PagBank layoffs**: "PagBank demite centenas" (300–500 per sources, unconfirmed), and "PagBank demite funcionários pela terceira vez em três anos". A search summary places the 300–500 round in 2025 (TO_VERIFY) — PRACTICAL_EXTERNAL — [Finsiders tag](https://finsidersbrasil.com.br/tag/demissoes); [Startups](https://startups.com.br/negocios/pagbank-faz-sua-onda-de-layoffs-e-demite-7-do-quadro/)
- **PicPay**: cut about 50 people after the abrupt end of a partnership and project (context about early 2024, **stale**) — PRACTICAL_EXTERNAL — [Finsiders](https://finsidersbrasil.com.br/noticias-sobre-fintechs/picpay-faz-cortes-apos-fim-abrupto-de-parceria-e-projeto)
- **Wildlife Studios**: cut about 20% in 2022, 13% in 2023 with a CEO change, and 21% (133 people) in 2024. No 2025–26 reports found — PRACTICAL_EXTERNAL — [Bloomberg Línea 2022](https://www.bloomberglinea.com/english/brazilian-unicorn-wildlife-a-tencent-wannabe-slashes-20-of-staff/); [Bloomberg Línea 2023](https://www.bloomberglinea.com/english/game-over-for-wildlifes-ceo-as-brazilian-unicorn-restructures-cuts-13-of-staff/); [PocketGamer.biz 2024](https://www.pocketgamer.biz/wildlife-studios-lays-off-133-staff/)
- **VTEX**: cut 193 people in May 2022 and 32 (2.4% of 1,339) in June 2023. No 2025–26 reports found — PRACTICAL_EXTERNAL — [Startups](https://startups.com.br/noticias/vtex-demite-mais-de-10-dos-funcionarios/); [Baguete](https://www.baguete.com.br/noticias/vtex-faz-nova-rodada-de-demissoes)
- **Wellhub**: the last reported layoff was 2020 (COVID). In early 2025 it reported 4M subscribers — PRACTICAL_EXTERNAL — [IstoÉ Dinheiro](https://istoedinheiro.com.br/wellhub-ceo-gympass-entrevista); [Suno 2020](https://www.suno.com.br/noticias/?p=60435)
- **Loft**: 855 layoffs in 2022 across three rounds (**stale**); nothing found for 2025–26 — PRACTICAL_EXTERNAL — [Mercado & Consumo](https://mercadoeconsumo.com.br/08/12/2022/economia/unicornio-loft-faz-nova-rodada-de-cortes-e-soma-855-demissoes-no-ano/)
- **Posting-model mentions across boards (2026-10-15)** — VERIFIED_OFFICIAL, keyword counts, so these are indicative only:
  - C6: 110 of 156 postings mention presencial/on-site
  - XP: 149 of 149 mention presencial
  - BTG: 79 mention on-site, 57 hybrid
  - PagBank pricing role: on-site
  - Creditas: hybrid SP (Chácara Santo Antônio)
  - Grupo OLX: tech and data roles remote
  - Sources: [C6](https://job-boards.greenhouse.io/c6bank); [XP](https://job-boards.greenhouse.io/xpinc); [Creditas Gupy](https://creditas.gupy.io/); [Grupo OLX Gupy](https://vemsergrupoolx.gupy.io/)

### Inferences
- HYPOTHESIS: the market trend is toward more office time (Nubank shifting toward 3 days in 2027; on-site banks). Remote-first options (QuintoAndar, VTEX, partly Grupo OLX) sit mostly at Portuguese-dominant employers, apart from VTEX.
- **Work authorization, all companies TO_VERIFY**:
  - No posting found states how non-Brazilian candidates without a residence permit are hired (CLT vs. PJ contractor vs. foreign entity).
  - Nubank's relocation package and Wellhub's and EBANX's US entities (US/SF roles) suggest experience hiring internationally (HYPOTHESIS).
  - Stone explicitly hires under CLT.
  - Migration and tax conclusions are out of scope.

### Gaps
- No verified 2025–26 RTO statements for iFood, PicPay, Stone, C6, Inter, XP or Magalu.
- The Stone layoff date and the PagBank 2025 numbers are not confirmed from dated primary articles.
- No layoffs.fyi or Layoffs Brasil tracker extract for 2025–26.

---

## Q4. Culture signals against four criteria: results orientation, meritocracy, competent employees (incl. business side), data-driven culture

### Takeaway
Within this session's budget, culture evidence comes mainly from the companies' own posting language, not from Glassdoor or LinkedIn. Results- and ownership-oriented language is strongest at **Nubank, EBANX, Wildlife, QuintoAndar and Wellhub**. Data-driven decision rights are visible at Wildlife (experimentation and UA investment decisions), QuintoAndar ("Dive deep" culture pillar) and PagBank/BTG pricing. Meritocracy and performance-system evidence was **not** gathered and is a gap.

### Cited Findings
- **Nubank** (results orientation / ownership): "It is a role built on ownership. We expect someone who anticipates needs, moves work forward without being pushed … brings structure to ambiguity." Candidates are assessed "for their potential to take on broader responsibilities over time" — VERIFIED_OFFICIAL — [Chief of Staff, Credit Card](https://jobs.ashbyhq.com/nubank/b45d9e02-0264-45a8-ba21-275c697b56d8)
- **Nubank** (data at scale): "Proprietary technology and data at scale power Nu's digital platform" (boilerplate). Engineering blog: building.nu.com — VERIFIED_OFFICIAL — [posting](https://jobs.ashbyhq.com/nubank/b45d9e02-0264-45a8-ba21-275c697b56d8)
- **Nubank** (RTO friction, a culture red flag): reported dismissals of 12 employees for cause after internal debate on the hybrid policy — PRACTICAL_EXTERNAL — [Tecnoblog](https://tecnoblog.net/noticias/nubank-abandona-trabalho-remoto-e-tera-modelo-hibrido-a-partir-de-2026/) (snippet)
- **EBANX** (results / accountability): requirements include "Bias for action & accountability — Strong sense of urgency and ownership" and "taking responsibility for outcomes rather than only execution" — VERIFIED_OFFICIAL — [Senior Product Analyst](https://job-boards.greenhouse.io/ebanx/jobs/7655627003); [Senior Analytics & Intelligence Analyst](https://job-boards.greenhouse.io/ebanx/jobs/8013346003)
- **Wildlife** (data-driven, experimentation, business decisions): the growth team works "across experimentation, modeling, product analytics … and investment decisions" and wants people "sitting close to the product to build true growth strategies rather than just executing downstream". Also: "You are impact-driven" — VERIFIED_OFFICIAL — [Business Analyst – Growth & UA](https://job-boards.greenhouse.io/wildlifestudios/jobs/8864832002); [Senior Data Scientist](https://job-boards.greenhouse.io/wildlifestudios/jobs/8872414002)
- **QuintoAndar** (data-driven): "'Dive deep' é um pilar muito importante em nossa cultura." It claims to pioneer large-scale AI in real estate — VERIFIED_OFFICIAL — [Gerente de Dados & Analytics](https://job-boards.greenhouse.io/quintoandar/jobs/4375416009)
- **Wellhub** (results / bold change): "Wellz's leadership recently asked a blunt question: if we restarted Wellz today with AI as a given, which workflows would we actually build? You will own the answer" — VERIFIED_OFFICIAL — [posting](https://job-boards.greenhouse.io/gympass/jobs/8859330002)
- **PagBank** (business-side partnership): the pricing GM is "principal parceiro dos gestores comerciais e diretores de negócio" and leads "fóruns executivos de aprovação e tomada de decisão" — VERIFIED_OFFICIAL — [posting](https://pagseguro.gupy.io/jobs/12514915)
- **BTG Pactual** (rigor / challenge culture): "Ser voz ativa e crítica, trazendo provocações inteligentes"; the pricing team "valorizamos perfis analíticos … capacidade de estruturação de problemas" — VERIFIED_OFFICIAL — [Gerente de Dados e Analytics](https://carreiras.btgpactual.com/vagas?gh_jid=5814844004); [Pricing de Crédito](https://carreiras.btgpactual.com/vagas?gh_jid=6112158004)
- **CloudWalk** (culture tone): postings describe "not your typical startup … we want the hackers, the artists, the crafters". A third-party guide calls "AI-native operating" its biggest hiring signal — PRACTICAL_EXTERNAL — [ResumeGeni](https://resumegeni.com/companies/cloudwalk-technology/how-to-apply)
- **Stability red flags** (layoffs as culture and results signals): Wildlife had three rounds in 2022–24; Stone about 370 (about 2026); PagBank three rounds in three years — PRACTICAL_EXTERNAL — sources in Q3.

### Inferences
- HYPOTHESIS: Nubank and EBANX show the clearest "ownership/outcomes" language in English. Wildlife shows the clearest analytics-with-decision-rights language (experimentation plus investment allocation), at IC level.
- HYPOTHESIS: BTG's partnership model is commonly described as meritocratic, but **this was not verified this session** (TO_VERIFY via BTG's site and 20-F/annual report).

### Gaps
- No Glassdoor/LinkedIn ratings, review themes, promotion or performance-system descriptions, or founder letters were collected for any company. These criteria remain largely **TO_VERIFY**, especially meritocracy and "competent employees incl. business side".
- Tech and data blogs (Nubank building.nu.com, iFood tech Medium, QuintoAndar tech blog) were not reviewed for experimentation-platform evidence.

---

## Q5. Domain match (monetization, pricing, experimentation, two-sided marketplace economics, P&L partnership) and tiered shortlist

### Takeaway
**No company combines all three: English working language, an open São Paulo analytics-leadership role, and a strong domain match.**
- English-first employers with real domain overlap: **Nubank** (BU/GM structure, credit-card P&L, pricing of credit), **Wildlife** (monetization, UA, experimentation) and **Wellhub** (two-sided B2B2C network: employers, partners, gyms).
- Strongest pure domain matches, but Portuguese-dominant: **QuintoAndar** (real-estate marketplace, ads monetization; the closest analogue to classifieds), **PagBank** (pricing/monetization GM) and **iFood** (three-sided marketplace).

### Cited Findings
- QuintoAndar Growth Intelligence Manager: "criação de novos canais de monetização via anúncios (ads)", leading a multidisciplinary team across Product, Legal, Finance and Ops. QuintoAndar Group owns real-estate portals Inmuebles24, Zonaprop, ImovelWeb and others — VERIFIED_OFFICIAL — [posting](https://job-boards.greenhouse.io/quintoandar/jobs/4368242009); [CDMX posting (brand list)](https://job-boards.greenhouse.io/quintoandar/jobs/4289904009)
- PagBank Gerente Geral de Precificação: "Desenvolver modelos inovadores de precificação, incentivos, fidelização e monetização"; requires "Precificação, Rentabilização, Revenue Management, Analytics" — VERIFIED_OFFICIAL — [posting](https://pagseguro.gupy.io/jobs/12514915)
- BTG Pricing de Crédito: "estudos de sensibilidade, simulações de preço, elasticidade"; supports targets, budget and projections — VERIFIED_OFFICIAL — [posting](https://carreiras.btgpactual.com/vagas?gh_jid=6112158004)
- Nubank Chief of Staff, Credit Card: a "trusted partner to the General Manager" of the Credit Card BU — VERIFIED_OFFICIAL — [posting](https://jobs.ashbyhq.com/nubank/b45d9e02-0264-45a8-ba21-275c697b56d8)
- Wildlife growth: analyzes "engagement, monetization, and cost of acquisition" and decides "how much should we invest" — VERIFIED_OFFICIAL — [posting](https://job-boards.greenhouse.io/wildlifestudios/jobs/8864832002)
- VTEX Ads (retail media) team, "Campaign Intelligence" — VERIFIED_OFFICIAL — [posting](https://job-boards.greenhouse.io/vtex/jobs/6218060004)
- Wellhub: "platform connects employees worldwide to the best partners for fitness, mindfulness, therapy…" in "one simple subscription" — VERIFIED_OFFICIAL — [posting](https://job-boards.greenhouse.io/gympass/jobs/8859330002)

### Inferences — tiered shortlist (HYPOTHESIS: synthesis of the evidence above)

**Tier A: English-first, SP presence, plausible analytics-leadership path. Monitor actively and network in.**

| Company | English | Live analytics-lead role in SP (2026-10-15) | Work model | Domain fit | Red flags |
|---|---|---|---|---|---|
| Nubank | Strong: "English mandatory", PT an advantage (VERIFIED_OFFICIAL) | None. Adjacent: Chief of Staff, Credit Card (EN, SP). Analytics Manager family exists (Bogotá, indexed) | Hybrid at least 2 days/week; 3 days from Jan 2027 (PRACTICAL_EXTERNAL) | High: BU/GM model, credit and pricing, growth, experimentation at scale | RTO tightening and reported dismissals over the policy; very high bar; SP analytics leads may need PT with local BUs (TO_VERIFY) |
| Wellhub (Gympass) | Strong: 82 of 87 postings EN, NYC HQ | None in analytics. Senior Strategy Manager – AI & Ops (Wellz) is AI-ops heavy | Flexible First, hybrid/remote | Medium-high: multi-sided network (employers, partners, users), subscription pricing | Current open role needs shipped AI agents (outside profile); no 2025–26 layoff data found (gap) |
| Wildlife Studios | Strong: 16 of 16 EN | None (IC Business Analyst Growth/UA, Senior DS) | Hybrid (some postings) | High for monetization, UA and experimentation; low for marketplace | Three layoff rounds 2022–24; small board (16 roles); leadership hiring rare |

**Tier B: either English-first with weaker fit or SP footprint, or best domain fit with a Portuguese risk.**

| Company | Why B | Key flag |
|---|---|---|
| QuintoAndar | **Best domain analogue** (real-estate marketplace and portals, ads monetization, pricing); a live **Growth Intelligence Manager** and Gerente de Dados & Analytics | **Postings are Portuguese and English is only a "diferencial"**, so Portuguese is dominant and A1 Portuguese is a blocker for these roles. Remote-first in Brazil only (work authorization TO_VERIFY) |
| EBANX | English-first (40 of 40), cross-border payments, pricing and FX economics | HQ Curitiba; roles mostly "Curitiba or São Paulo", on-site/hybrid; no leadership role open |
| VTEX | English-first, remote-first, B2B SaaS plus retail media (ads) | No analytics leadership open; 2022–23 layoffs; marketplace and B2B SaaS monetization fit is medium |
| CloudWalk / InfinitePay | English postings, SP; payments pricing for SMB merchants | Only 22 roles, none in analytics leadership; "AI-native" bar; language internally mixed per a third-party source (low confidence) |
| iFood | Three-sided marketplace (a domain fit); hiring hybrid tech in 2026 | 55 of 58 postings in Portuguese; no analytics leadership open; HQ Osasco |

**Tier C: Portuguese-dominant and/or on-site banks or brokers, or insufficient data. Deprioritize for an English-only candidate.**

| Company | Evidence | Note |
|---|---|---|
| PagBank | Pricing GM role (strong domain fit) but in Portuguese and on-site; layoffs (repeated) | Only if Portuguese becomes working-level |
| BTG Pactual | Credit pricing manager plus Data & Analytics manager; Portuguese; on-site; analytics role is hands-on engineering | — |
| Stone | 373 of 373 Portuguese postings; about 370 layoffs (about 2026, TO_VERIFY) | — |
| XP Inc., C6 Bank | 100% Portuguese postings; mostly on-site | — |
| Creditas, Grupo OLX, Neon, Méliuz, Dock | Portuguese postings; no leadership roles; Méliuz in BH; Dock has 0 jobs | OLX's foreign ownership TO_VERIFY |
| PicPay, Inter&Co, Magalu, Loggi, Loft, Hotmart, Pismo, Cora, Olist, Zé Delivery, Nuvemshop | ATS not read or no relevant data this session | TO_VERIFY. Hotmart is BH-centred; Pismo is Visa-owned (TO_VERIFY) |

**Implications for search channels (HYPOTHESIS):**
- Public boards show almost no English-language analytics-leadership roles in São Paulo right now. The practical channel for Tier A is likely referral or outreach to analytics and BU leaders, plus alerts on [Nubank Ashby](https://jobs.ashbyhq.com/nubank), [Wellhub Greenhouse](https://job-boards.greenhouse.io/gympass), [Wildlife Greenhouse](https://job-boards.greenhouse.io/wildlifestudios), [EBANX Greenhouse](https://job-boards.greenhouse.io/ebanx) and [VTEX Greenhouse](https://job-boards.greenhouse.io/vtex).
- The Greenhouse and Ashby JSON endpoints used here, e.g. `boards-api.greenhouse.io/v1/boards/<token>/jobs` and `api.ashbyhq.com/posting-api/job-board/nubank`, can be polled read-only. Board tokens: `nubank` (Ashby), `gympass`, `wildlifestudios`, `ebanx`, `vtex`, `quintoandar`, `ifoodcarreiras`, `stone`, `c6bank`, `xpinc`, `btgpactual`.

### Gaps
- Whether Nubank, Wellhub, Wildlife or EBANX will engage a candidate without a current Brazilian work permit, or offer contractor/PJ or foreign-entity arrangements: TO_VERIFY with recruiters (research only; nobody was contacted).
- Whether QuintoAndar data and BI teams actually operate day-to-day in English: TO_VERIFY. The postings suggest they don't.
- Compensation bands and leveling were not collected.
