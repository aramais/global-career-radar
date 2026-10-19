# LATAM-founded tech companies (non-Brazilian) hiring analytics leaders for Brazil / São Paulo, with English as the working language

_Snapshot date: 2026-10-15. Method: the public job-board JSON endpoints of each company's applicant tracking system (Lever, Greenhouse, Ashby, BambooHR, Workable, Eightfold for Mercado Libre, Workday for Rappi), pulled on 2026-10-15. Every job link quoted as "live" returned HTTP 200 on 2026-10-15. Press and other sources were checked with web search and fetch. The web-search budget for this session ran out partway through; the gaps this caused are listed in each section._

_Confidence labels: **VERIFIED_OFFICIAL** = company ATS posting or official page. **PRACTICAL_EXTERNAL** = press or third-party data. **HYPOTHESIS** = my own inference. **TO_VERIFY** = an open question, including all work-authorization, migration and tax points, which are out of scope._

_Important dating caveats:_
- _Lever "created" = the date the requisition was created, not necessarily the date it was published._
- _Greenhouse `updated_at` for all Clara jobs is 2026-10-11 because of a bulk refresh. I use `first_published` where I fetched it._
- _Mercado Libre's Eightfold "onsite" label may be a default value and may not reflect the real work model._

## Tiered shortlist (A/B/C): synthesis for the objective

### Takeaway
No company I checked had a live analytics-leadership role (Head, Director or Manager of Analytics, Business/Product/Pricing Analytics, or Decision Science) open to Brazil on 2026-10-15. The shortlist therefore ranks **channels and employers to watch**, not current openings. Two companies have both evidence of English as the working language and a real São Paulo hiring base: **dLocal and Clara (tier A)**. Mercado Libre has the best domain fit, but its Brazil postings are written in Portuguese. Rappi has a strong domain match but works in Spanish. Both are tier B with a language flag.

### Cited Findings

**Tier A: English working language and hiring in São Paulo are both evidenced**

- **dLocal** (Uruguay; cross-border payments).
  - On 2026-10-15 its Lever board had 52 postings. Many list "Sao Paulo (Hybrid)" alongside Montevideo, Buenos Aires and Madrid (VERIFIED_OFFICIAL) — [dLocal Lever API](https://api.lever.co/v0/postings/dlocal?mode=json).
  - Postings are in English and describe "1,300+ teammates from 40+ nationalities". The work model is "self-managed focus time with moments of in-person connection in our collaboration hubs", plus "Work From Anywhere… up to 3 months every year" (VERIFIED_OFFICIAL, posting created 2026-08-18) — [dLocal Data Specialist for Compliance](https://jobs.lever.co/dlocal/9990577d-904e-4bdb-9190-41347645f79b).
  - There was no analytics-leadership opening. The closest roles are an IC Data Specialist (Compliance) and a Data-AI Architect (engineering, excluded by the profile).
  - Red flag: the official careers URL (dlocal.com/careers) now redirects to a Nodi-hosted page that shows "0 positions", while the Lever postings are still live. This may be an ATS migration (TO_VERIFY) — [dLocal Nodi careers page](https://app.nodi.global/company/dlocal).
- **Clara** (Mexico; corporate cards and B2B spend/payments).
  - It moved its headquarters to São Paulo in Aug 2023, after receiving a Central Bank payment-institution licence. About half of its senior leadership relocated to São Paulo (PRACTICAL_EXTERNAL, 2023-08-10) — [Suno](https://www.suno.com.br/noticias/?p=440958).
  - On 2026-10-15 its Greenhouse board had 147 postings, about 33 of them tagged São Paulo (VERIFIED_OFFICIAL) — [Clara Greenhouse](https://job-boards.greenhouse.io/clara).
  - Postings state: "English is our working language, with Spanish and Portuguese every day" (VERIFIED_OFFICIAL) — [Clara Data Analyst](https://job-boards.greenhouse.io/clara/jobs/5205085007).
  - Analytics roles are IC only. There was no analytics manager or head role.

**Tier B: strong presence or fit, but one major blocker**

- **Mercado Libre / Mercado Pago** (Argentina/Uruguay).
  - Fit: the best domain match (marketplace, pricing, monetization).
  - Blocker: its Brazil postings are written in Portuguese and list "inglês avançado" as an extra requirement. This means Portuguese is the de-facto working language in Brazil (VERIFIED_OFFICIAL) — [Insights Specialist – Marketplace](https://mercadolibre.eightfold.ai/careers/job/44666503).
  - No analytics manager or head role appeared among about 312 analytics-related Brazil postings on 2026-10-15.
  - Hybrid policy: leaders must be in the office 20–40% of each quarter, and other office staff "podem trabalhar totalmente remotamente" (PRACTICAL_EXTERNAL, 2025-04-15) — [Bloomberg Línea](https://www.bloomberglinea.com.br/negocios/mercado-livre-planeja-aumentar-o-numero-de-funcionarios-em-33-neste-ano/).
- **Yuno** (Colombia, founded by ex-Rappi people; payment orchestration).
  - Fully remote. Several postings list Brazil or São Paulo as an eligible location. Leadership postings require "Fluent English" (VERIFIED_OFFICIAL) — [Yuno Global Head of GTM](https://jobs.lever.co/yuno/e601c8be-d03a-4aa4-b89a-ee784acf6921).
  - Blockers: no analytics-leadership role is posted. Its data roles are platform engineering based in Europe, and the company is small.
  - Raised a US$45M Series B on 2026-08-12 (PRACTICAL_EXTERNAL) — [Ecosistema Startup](https://ecosistemastartup.com/yuno-recauda-us45m-en-serie-b-lecciones-para-fintech-latam-en-2026/).
- **Belvo** (Mexico; open-finance APIs; offices in São Paulo and Mexico City).
  - Works in English and is "remote-friendly". It is a small team of "more than 90 people", which brings a first-data-hire risk.
  - Its only data opening is an IC Senior Applied Data Scientist, São Paulo/Remote, published 2026-10-15 (VERIFIED_OFFICIAL) — [Belvo posting](https://jobs.ashbyhq.com/belvo/161cfb0e-5ee2-4655-b45b-460c066d6c73).
- **Rappi** (Colombia; delivery marketplace, with heavy investment in Brazil).
  - Fit: very strong domain match.
  - Blockers: the language is Spanish. A Rappi data role posting required "Fluent in English and Spanish" (PRACTICAL_EXTERNAL, indexed only, closed) — [Foundation Capital job board](https://jobs.foundationcapital.com/companies/rappi-2/jobs/74002428-data-business-analyst).
  - Its public Workday board shows only operations and picker roles (VERIFIED_OFFICIAL, 27 postings on 2026-10-15) — [Rappi Workday](https://rappi.wd12.myworkdayjobs.com/Rappi_jobs).
- **Bitso** (Mexico; crypto and stablecoin payments).
  - Has a Brazil business and a new Brazil country manager since Feb 2026 (PRACTICAL_EXTERNAL, 2026-02-10) — [Finsiders](https://finsidersbrasil.com.br/gente-em-fintechs/bitso-muda-comando-do-negocio-no-brasil/).
  - Its official BambooHR board shows only 7 openings and no analytics roles (VERIFIED_OFFICIAL) — [Bitso BambooHR](https://bitso.bamboohr.com/careers/1008).

**Tier C: low probability, or outside the target**

- Language or location blockers:
  - **Despegar**: Alphaville (São Paulo) roles require "Espanhol avançado" (VERIFIED_OFFICIAL) — [Revenue Management Analyst](https://jobs.lever.co/despegar/2680431e-c4f5-4f49-a211-c338b5cc5269).
  - **Kavak**: its São Paulo Tech Lead role requires "Professional proficiency in Spanish" (VERIFIED_OFFICIAL) — [Kavak Tech Lead SP](https://jobs.lever.co/kavak/f34e236a-7a71-4151-af72-010ebc88ff1e).
  - **Nuvemshop/Tiendanube**: 15 roles listed, none in analytics leadership. Portuguese-first is my HYPOTHESIS (VERIFIED_OFFICIAL list) — [Nuvemshop careers](https://www.nuvemshop.com.br/trabalhe-na-nuvemshop).
  - **Addi**: the business serves Colombia only (VERIFIED_OFFICIAL) — [Addi Head of Marketplace Supply](https://jobs.ashbyhq.com/addi/7dd51523-34c3-44a4-b305-f621e99f2a75).
- IT services (flagged): **Globant**. It cut about 3% of staff (roughly 933 people) in June 2025 (PRACTICAL_EXTERNAL, 2025-06-24) — [Bloomberg Línea](https://www.bloomberglinea.com/latinoamerica/argentina/migoya-de-globant-confirma-en-correo-interno-despido-del-3-de-la-plantilla-de-globers/).
- Not enough evidence of Brazil hiring or of the working language: **Pomelo** (Brazil is one of its two largest markets; Series C on 2026-01-20; no ATS found), **Ontop**, **Betterfly**, **Kushki**, **NotCo**, **Merama**.
- Boards show only Mexico, Colombia or Chile locations:
  - **Konfío** — [BambooHR](https://konfio.bamboohr.com/careers/1265)
  - **Kueski** — [Ashby](https://jobs.ashbyhq.com/kueski/e4055ce1-f0ac-45a5-b94e-dcee794ae91b)
  - **Xepelin** — [Lever](https://jobs.lever.co/xepelin/a396da71-efca-4c4c-9bce-84e169c19f00)
  - **Fintual** — [Lever](https://jobs.lever.co/fintual/9c863695-5fdd-40f3-a907-fc8db1227036)
  - **Platzi** — [Workable](https://apply.workable.com/j/DFCD11788D)
  - **Cobre** — [Greenhouse](https://job-boards.greenhouse.io/cobre/jobs/4381331009)
  - **Bold** (Colombia) — [BambooHR](https://bold.bamboohr.com/careers/280)
- No public board found: **Stori, Clip, Ualá, Habi, Truora, Buk**.

### Inferences
- HYPOTHESIS: the most realistic route to an English-speaking analytics-leadership role is to be ready to pounce when one opens, and to apply through talent pools.
  - dLocal: its Finance, Operations and Technology talent pools include São Paulo (Hybrid).
  - Clara: it is about 400 people and HQ'd in São Paulo, so a first "Head of Analytics/BizOps" hire would plausibly be based there.
  - This route is more realistic than relying on current postings.
- HYPOTHESIS: Mercado Libre is worth keeping as a stretch channel in tier B. Its pricing, marketplace and monetization domain matches the profile closely. However, Portuguese would have to reach working level quickly, and the senior Brazil analytics roles seem to be filled internally or through LinkedIn rather than appearing on Eightfold.
- HYPOTHESIS: the fit is better for adjacent manager roles that are analytics-heavy than for pure analytics titles. Examples:
  - Mercado Libre "Gerente de Estratégia & Desenvolvimento Corporativo", which asks for ads/monetization knowledge (Portuguese posting).
  - Addi "Head of Marketplace Supply", a pricing and marketplace-economics role, but limited to Colombia.

### Gaps
- No live analytics-leadership opening open to Brazil was found at any company checked. LinkedIn-only postings could not be checked (no logged-in access, and the search budget ran out).
- Not checked because no public board was found: Stori, Clip, Ualá, Habi, Truora, NotCo, Buk, Merama (merama.io/careers returned HTTP 502), and Kushki (its careers page has no ATS link).

## Which LATAM-founded tech companies are remote-first or remote-friendly for Brazil-based hires?

### Takeaway
- Fully remote, with Brazil explicitly eligible: Yuno and Ontop.
- São Paulo hubs with hybrid or remote-friendly working: dLocal (SP hybrid), Clara (São Paulo HQ, remote-first or hybrid depending on role, with no minimum number of office days for most roles) and Belvo (São Paulo office, remote-friendly).
- Large São Paulo offices with a local-language model: Mercado Libre (Osasco; flexible hybrid), Despegar (Alphaville; hybrid), Kavak (SP dev team), Nuvemshop and Rappi.
- Most Mexican, Chilean and Colombian scaleups (Konfío, Kueski, Xepelin, Fintual, Platzi, Addi) show no Brazil hiring at all.

### Cited Findings
- **dLocal**:
  - Lever lists "Sao Paulo (Hybrid)" for roles across Compliance, Fraud, Account Management, Engineering and the Talent Pools. A People Operations Specialist role for São Paulo was created 2026-10-06 (VERIFIED_OFFICIAL) — [dLocal People Ops Specialist](https://jobs.lever.co/dlocal/6c5e7daa-f1cc-443b-9fed-b2613dc9fcd9).
  - Collaboration hubs plus "Work From Anywhere" for up to 3 months a year (VERIFIED_OFFICIAL) — [dLocal posting](https://jobs.lever.co/dlocal/9990577d-904e-4bdb-9190-41347645f79b).
  - Talent pools for Finance, Operations and Technology list São Paulo (Hybrid), created 2026-05-20 (VERIFIED_OFFICIAL) — [Finance Talent Pool](https://jobs.lever.co/dlocal/a6328e12-992c-4b7b-a51b-3b2d9bc4b997), [Operations Talent Pool](https://jobs.lever.co/dlocal/24547979-e915-403c-9d1c-cba7f2a67808).
- **Clara**:
  - Data Analyst: "This is a remote-first role: you'll work from home day to day. You'll ramp up in person with the team…" (VERIFIED_OFFICIAL, first published 2026-08-05) — [Clara Data Analyst](https://job-boards.greenhouse.io/clara/jobs/5205085007).
  - Data Scientist: "We don't set a minimum number of office days for most roles" (VERIFIED_OFFICIAL, first published 2026-05-22) — [Clara Data Scientist](https://job-boards.greenhouse.io/clara/jobs/5145371007).
  - HQ moved to São Paulo in 2023 (PRACTICAL_EXTERNAL) — [Suno](https://www.suno.com.br/noticias/?p=440958).
- **Belvo**: "We have offices in São Paulo and Mexico City – while a large portion of us work remotely"; "global and remote-friendly" (VERIFIED_OFFICIAL, 2026-10-15) — [Belvo posting](https://jobs.ashbyhq.com/belvo/161cfb0e-5ee2-4655-b45b-460c066d6c73).
- **Yuno**:
  - All 37 Lever postings have workplaceType=remote. Brazil-eligible examples:
    - Global Head of Go To Markets (Brazil and São Paulo among its locations, created 2026-07-29)
    - Engineering Manager – Core Payments (LATAM including Brazil, 2026-02-06)
    - Finx Growth Manager (Brazil, 2026-07-28)
  - The posting text says "Remote Work – You can work from everywhere!" (VERIFIED_OFFICIAL) — [Yuno Lever API](https://api.lever.co/v0/postings/yuno?mode=json), [Finx Growth Manager](https://jobs.lever.co/yuno/2c1863c6-a3a4-4d3e-acb8-6f530f8ebe50).
- **Ontop** (Colombia; HR-tech and payroll): "100% Remote, from day one", "There is no office to return to", "180+ Ontoppers in 35+ countries" (VERIFIED_OFFICIAL, page fetched 2026-10-15) — [Ontop careers](https://www.getontop.com/careers).
- **Mercado Libre**: leaders must be in the office 20–40% of each quarter (about 1–2 days a week). Other office staff may work fully remotely, with roughly one office day a week encouraged (PRACTICAL_EXTERNAL, 2025-04-15) — [Bloomberg Línea](https://www.bloomberglinea.com.br/negocios/mercado-livre-planeja-aumentar-o-numero-de-funcionarios-em-33-neste-ano/), [Folha PE](https://www.folhape.com.br/economia/mercado-livre-vai-contratar-28-mil-novos-funcionarios-na-america/404883/).
  - Not consistent: Eightfold labels 295 of the 312 analytics-related Brazil postings "onsite" and 17 "hybrid" (VERIFIED_OFFICIAL data, but the label is unreliable) — [Mercado Libre Eightfold](https://mercadolibre.eightfold.ai/careers?query=analytics).
- **Despegar**: 18 of its 104 Lever postings are in Alphaville (São Paulo metro), all hybrid (VERIFIED_OFFICIAL) — [Despegar Lever API](https://api.lever.co/v0/postings/despegar?mode=json).
- **Kavak**: of its 11 Lever postings, 9 are in Mexico City, 1 in Buenos Aires and 1 in São Paulo (Tech Lead, team "Brazil Development", hybrid, 2026-10-07) (VERIFIED_OFFICIAL) — [Kavak Tech Lead](https://jobs.lever.co/kavak/f34e236a-7a71-4151-af72-010ebc88ff1e).
- **Rappi**: the public Workday board has 27 postings, all pickers, operations staff or interns, in São Paulo, Lima and Bogotá (VERIFIED_OFFICIAL, 2026-10-15) — [Rappi Workday](https://rappi.wd12.myworkdayjobs.com/Rappi_jobs).
  - Rappi announced R$1.4bn of investment in Brazil through 2028 (PRACTICAL_EXTERNAL, 2025-06-06) — [Descubre](https://www.descubre.vc/noticia/rappi-apuesta-por-tarifas-cero-y-expansi-n-masiva-en-brasil-2025-06-06).
- **Nuvemshop**: "+1500 Pessoas colaboradoras" across 5 countries. The careers page links to an InHire board with 15 roles (VERIFIED_OFFICIAL, 2026-10-15) — [Nuvemshop careers](https://www.nuvemshop.com.br/trabalhe-na-nuvemshop).
- **Addi**: some roles are "LatAm" remote, but the Ashby postal address is Bogotá and the business is Colombia-only (VERIFIED_OFFICIAL) — [Addi Lead Business Analyst](https://jobs.ashbyhq.com/addi/b3b0f097-8961-4001-b8ba-0baacda32bb7).
- **Simetrik** (Colombia): remote, including one role in Brazil (Customer Success Manager – SMB BR, 2026-09-25) (VERIFIED_OFFICIAL) — [Simetrik posting](https://jobs.ashbyhq.com/simetrik/9f56bdfd-0a45-497f-9bb9-6162a3ddfa91).
- Mexico-, Chile- or Colombia-only boards:
  - Konfío: 37 BambooHR roles, mostly CDMX (VERIFIED_OFFICIAL) — [Konfío BambooHR](https://konfio.bamboohr.com/careers/1265).
  - Kueski: "Mexico (remote)" (VERIFIED_OFFICIAL) — [Kueski Ashby](https://jobs.ashbyhq.com/kueski/e1261953-7bcc-45db-a54e-fc2e0bdfbc89).
  - Xepelin: Santiago and CDMX, mostly onsite (VERIFIED_OFFICIAL) — [Xepelin Lever](https://jobs.lever.co/xepelin/a396da71-efca-4c4c-9bce-84e169c19f00).
  - Fintual: Chile and Mexico (VERIFIED_OFFICIAL) — [Fintual Lever](https://jobs.lever.co/fintual/9c863695-5fdd-40f3-a907-fc8db1227036).
  - Platzi: Bogotá, CDMX and San Salvador, not remote (VERIFIED_OFFICIAL) — [Platzi Workable](https://apply.workable.com/j/DFCD11788D).
- **Betterfly**: the careers site's location filter includes "Brasil", but only 2 intern roles were listed on 2026-10-15 (VERIFIED_OFFICIAL) — [Betterfly careers](https://careers.betterfly.com/jobs).

### Inferences
- HYPOTHESIS: for someone based in São Paulo, "hybrid in SP" at dLocal, Clara, Belvo or Mercado Libre is practical. dLocal and Clara do not appear to enforce strict office quotas.
- HYPOTHESIS: Rappi's corporate and analytics roles are probably published on LinkedIn or a second Workday site rather than the public wd12 board. A wd3 Rappi tenant URL exists, but its API returned HTTP 422.

### Gaps
- Not found: an official remote or office policy for Rappi, Bitso, Nuvemshop, Kavak or Pomelo.
- Not confirmed: Mercado Libre's policy after April 2025. No 2026 change was found.
- Glassdoor and levels.fyi data on remote practice could not be retrieved; the search budget ran out.

## Which of them use English as a working language (at least in data, product and leadership)? Evidence?

### Takeaway
- Explicit official statements: **Clara** ("English is our working language") and **Belvo** (requires written and spoken English, 20 nationalities).
- Strong indirect evidence: **dLocal**, whose postings are in English and which has 40+ nationalities and global hubs.
- English required for senior roles: **Yuno**.
- Portuguese or Spanish in practice: **Mercado Libre in Brazil**, **Despegar** and **Kavak** (all VERIFIED_OFFICIAL from postings). **Rappi** is Spanish plus English (indexed only).

### Cited Findings
- **Clara**: "English is our working language, with Spanish and Portuguese every day." The Data Analyst posting lists "Working proficiency in English" as a must-have and "Basic proficiency in Portuguese or a desire to learn it" as a nice-to-have (VERIFIED_OFFICIAL, 2026) — [Clara Data Analyst](https://job-boards.greenhouse.io/clara/jobs/5205085007).
- **Belvo**: "You're proficient in written and spoken English"; the team "currently represents 20 nationalities" (VERIFIED_OFFICIAL, 2026-10-15) — [Belvo posting](https://jobs.ashbyhq.com/belvo/161cfb0e-5ee2-4655-b45b-460c066d6c73).
- **dLocal**: postings are written in English. One cites "1,300+ teammates from 40+ nationalities" and hubs in Montevideo, São Paulo, Madrid, Shanghai and Kuala Lumpur (VERIFIED_OFFICIAL). No explicit "working language" sentence was found — [dLocal posting](https://jobs.lever.co/dlocal/9990577d-904e-4bdb-9190-41347645f79b).
- **Yuno**:
  - Global Head of GTM: "Minimum Qualifications: Fluent English" (VERIFIED_OFFICIAL) — [Yuno posting](https://jobs.lever.co/yuno/e601c8be-d03a-4aa4-b89a-ee784acf6921).
  - The Brazil-facing Finx Growth Manager role requires "Native or fluent Portuguese… professional proficiency in English… Spanish is preferred" (VERIFIED_OFFICIAL) — [Yuno Finx Growth Manager](https://jobs.lever.co/yuno/2c1863c6-a3a4-4d3e-acb8-6f530f8ebe50).
- **Addi**: Lead Business Analyst requires "full professional English proficiency" for work with international lenders (VERIFIED_OFFICIAL, 2026-10-06) — [Addi posting](https://jobs.ashbyhq.com/addi/b3b0f097-8961-4001-b8ba-0baacda32bb7).
- **Mercado Libre (Brazil)**: postings are written in Portuguese.
  - Insights Specialist: "Dominar o inglês avançado" (VERIFIED_OFFICIAL, 2026-10-05) — [Meli Insights Specialist](https://mercadolibre.eightfold.ai/careers/job/44666503).
  - Gerente de Estratégia & Desenvolvimento Corporativo: "comunicar-se em inglês avançado e ter conhecimento de ads/monetização" (VERIFIED_OFFICIAL, 2026-08-19) — [Meli Gerente Estratégia](https://mercadolibre.eightfold.ai/careers/job/38908589).
- **Despegar (Alphaville)**: Revenue Management Analyst requires "Espanhol avançado (interação LATAM) e inglês desejável" (VERIFIED_OFFICIAL, created 2026-07-22) — [Despegar posting](https://jobs.lever.co/despegar/2680431e-c4f5-4f49-a211-c338b5cc5269).
- **Kavak (São Paulo)**: Tech Lead requires "Professional proficiency in Spanish" (VERIFIED_OFFICIAL, 2026-10-07) — [Kavak posting](https://jobs.lever.co/kavak/f34e236a-7a71-4151-af72-010ebc88ff1e).
- **Rappi**: a data role required "Fluent in English and Spanish" (PRACTICAL_EXTERNAL, indexed only, no longer accepting applications) — [Foundation Capital job board](https://jobs.foundationcapital.com/companies/rappi-2/jobs/74002428-data-business-analyst).
- **Ontop**: its talent form asks for both "English Proficiency" and "Spanish Proficiency" levels (VERIFIED_OFFICIAL) — [Ontop careers](https://www.getontop.com/careers).

### Inferences
- HYPOTHESIS: for a candidate with C1 English, about A1 Portuguese and no Spanish:
  - Clara: English is formally the working language, but Spanish and Portuguese are used "every day". There is a risk of being excluded from local commercial conversations.
  - dLocal and Belvo look most English-native.
  - Mercado Libre Brazil, Despegar, Kavak and Rappi would effectively need Portuguese or Spanish.
- HYPOTHESIS: Bitso and Pomelo probably use English in their tech and data teams, but I found no 2025–2026 evidence. TO_VERIFY.

### Gaps
- No explicit working-language statement found for: Bitso, Pomelo, Nuvemshop, Ualá, Stori, Kushki, NotCo, Betterfly, Truora, Habi, Merama.
- dLocal's language is inferred from posting language and team diversity, not from an explicit statement.

## 2025–2026 analytics-leadership openings open to Brazil or "LATAM remote" (URLs, dates, title, eligibility, language)

### Takeaway
On 2026-10-15 **no** Head, Director or Manager of Analytics (or Business/Product/Pricing Analytics, or Decision Science) role was live and open to Brazil at any company checked. What is live falls into three groups:
1. IC data roles in São Paulo (Clara, dLocal, Belvo, Mercado Libre).
2. Adjacent manager or strategy roles that need Portuguese or Spanish, or that sit outside Brazil.
3. Talent pools.

### Cited Findings
All entries are VERIFIED_OFFICIAL and were live (HTTP 200) on 2026-10-15 unless marked otherwise.

**Closest matches: manager-level and analytics-heavy, but with blockers**
- Mercado Libre — "Gerente de Estratégia & Desenvolvimento Corporativo – Mercado Libre"
  - Date: posted 2026-08-19.
  - Location: Sudeste, Brazil; labelled "onsite".
  - Language: posting in Portuguese; asks for advanced English plus ads/monetization knowledge.
  - Type: strategy and corporate development, not analytics line management.
  - Link: [job 38908589](https://mercadolibre.eightfold.ai/careers/job/38908589)
- Addi — "Head of Marketplace Supply"
  - Date: published 2026-08-11.
  - Location: "LatAm", remote, with a Bogotá address.
  - Scope: marketplace selection, pricing against MeLi and Falabella, and merchant growth targets ("Grow Active Sellers 150%+").
  - Fit: commercial leadership with heavy marketplace economics. Colombia-focused, so Spanish is very likely needed (HYPOTHESIS), and whether Brazil residents are eligible is TO_VERIFY.
  - Link: [Addi posting](https://jobs.ashbyhq.com/addi/7dd51523-34c3-44a4-b305-f621e99f2a75)
- Addi — "Lead Business Analyst (Operations)"
  - Date: published 2026-10-06.
  - Location: "LatAm", remote.
  - Process: needs full professional English; includes a live SQL exercise.
  - Type: senior IC or lead, focused on capital markets.
  - Link: [Addi posting](https://jobs.ashbyhq.com/addi/b3b0f097-8961-4001-b8ba-0baacda32bb7)
- Despegar — "Revenue Management Manager B2B"
  - Date: created 2026-08-10.
  - Location: Buenos Aires, hybrid. Not open to Brazil.
  - Link: [Despegar posting](https://jobs.lever.co/despegar/589221e5-866d-4999-a39e-66820528b025)
- Despegar — "Strategy & Planning Manager"
  - Date: created 2026-10-05.
  - Location: Santiago, hybrid.
  - Link: [Despegar posting](https://jobs.lever.co/despegar/5369637d-9fc7-496c-848b-cace33f1ff37)
- Despegar — "Manager, Partner Operations"
  - Date: created 2026-10-09.
  - Location: Alphaville (São Paulo metro), hybrid.
  - Type: commercial operations. Despegar's Alphaville postings ask for advanced Spanish.
  - Link: [Despegar posting](https://jobs.lever.co/despegar/957a00bd-ecb7-4915-9143-9b830ee906b0)
- Konfío — "Director de Datos"
  - Location: CDMX. Spanish posting, Mexico only.
  - Link: [Konfío posting](https://konfio.bamboohr.com/careers/1265)
- Bold (Colombia) — "Sales Enablement & Analytics Leader"
  - Location not stated; Colombian company.
  - Link: [Bold posting](https://bold.bamboohr.com/careers/280)

**IC data or analytics roles in São Paulo (excluded by the profile, but they signal where data teams are based)**
- Clara:
  - Data Analyst (Bogotá/CDMX/SP), first published 2026-08-05 — [Clara 5205085007](https://job-boards.greenhouse.io/clara/jobs/5205085007)
  - Data Scientist, first published 2026-05-22 — [Clara 5145371007](https://job-boards.greenhouse.io/clara/jobs/5145371007)
  - Senior Data Scientist – Risk Modeling — [Clara 5254971007](https://job-boards.greenhouse.io/clara/jobs/5254971007)
  - AI Business Operations Expert – São Paulo (Hybrid), first published 2026-08-26. A "highly strategic, senior individual contributor role" reporting to the Global Head of Operations; the work is AI automation plus Metabase dashboards — [Clara 5223309007](https://job-boards.greenhouse.io/clara/jobs/5223309007)
- dLocal:
  - Data Specialist for Compliance (SP Hybrid/Montevideo), created 2026-08-18 — [dLocal posting](https://jobs.lever.co/dlocal/9990577d-904e-4bdb-9190-41347645f79b)
  - Data-AI Architect, created 2026-09-21; engineering, excluded — [dLocal posting](https://jobs.lever.co/dlocal/9152099e-3a47-48f0-8ab2-c68728012a79)
- Belvo: Senior Applied Data Scientist (SP/Remote), published 2026-10-15 — [Belvo posting](https://jobs.ashbyhq.com/belvo/161cfb0e-5ee2-4655-b45b-460c066d6c73)
- Mercado Libre (all Portuguese postings, Sudeste Brazil):
  - Insights Specialist – Marketplace, 2026-10-05 — [44666503](https://mercadolibre.eightfold.ai/careers/job/44666503)
  - Pricing Specialist 1P – Marketplace, 2026-10-07 — [43668212](https://mercadolibre.eightfold.ai/careers/job/43668212)
  - Analista Sênior de Estratégia de Pricing & Rentabilidade – Mercado Pago, 2026-09-23 — [44630115](https://mercadolibre.eightfold.ai/careers/job/44630115)
  - Data Scientist Technical Leader, 2026-09-25 — [44530010](https://mercadolibre.eightfold.ai/careers/job/44530010)
- Rappi (indexed only, not on its official board): Growth E&A Senior Analyst, Bogotá, hybrid, posted 2026-02-03. Third-party description: 3+ years, A/B testing and causal inference — [marketingmonk](https://www.marketingmonk.so/jobboard/jobs/growth-eanda-senior-analyst-at-rappi-mri7tg). A "Growth E&A Lead" listing was indexed but the page returned 404 on fetch — [growthtalent](https://www.growthtalent.org/empregos/growth-marketing/rappi/growth-e-a-lead-rappi).

**Talent pools that include São Paulo (an application channel when no role exists)**
- dLocal Finance, Operations, Technology and Compliance talent pools, created 2026-05-20 — [Finance](https://jobs.lever.co/dlocal/a6328e12-992c-4b7b-a51b-3b2d9bc4b997), [Operations](https://jobs.lever.co/dlocal/24547979-e915-403c-9d1c-cba7f2a67808), [Technology](https://jobs.lever.co/dlocal/fb65f846-848e-46cf-b6ce-e47c906b663f).
- Belvo "CV for Future Opportunities" (SP/CDMX/Barcelona/Remote) — [Belvo](https://jobs.ashbyhq.com/belvo/5a34dbde-1947-4be7-b048-5acea47b0e86).
- Yuno "Talent Pool – Come join the team!" (Global, remote) — [Yuno](https://jobs.lever.co/yuno/7f25515f-dad5-4918-909b-cbd5349f2090).

### Inferences
- HYPOTHESIS: none of the live roles meets every target criterion (manager level, close to P&L, English, Brazil-eligible).
  - The Mercado Libre strategy/corporate development manager role is the nearest on domain (ads/monetization), but language is a blocker.
  - Addi's Head of Marketplace Supply is nearest on marketplace economics, but it is Colombia-bound.
- HYPOTHESIS: at these companies, senior analytics hiring seems to happen off the public ATS (LinkedIn, referrals, internal promotion). Boards with 100+ roles (Clara 147, Despegar 104) have no analytics-manager titles at all.

### Gaps
- LinkedIn job posts could not be queried, so any LinkedIn-only "Analytics Manager" roles at Mercado Libre, Rappi, Nubank-like LATAM firms, Bitso, Pomelo or Ualá are not covered.
- Spanish or Portuguese requirements for the Addi Head of Marketplace Supply role are not stated in the text I parsed.
- No openings could be checked for Stori, Clip, Ualá, Pomelo, Kushki, Truora, Habi, NotCo, Buk or Merama (no public board found).

## Employment form for Brazil residents (CLT entity, PJ/contractor, EOR), timezone expectations, compensation hints

### Takeaway
- Companies with a Brazil entity and São Paulo offices, likely to hire through a local entity (exact contract form, CLT or otherwise, is TO_VERIFY): Mercado Libre, Clara, dLocal, Belvo, Bitso (via Nvio), Despegar, Kavak, Nuvemshop, Rappi and Globant.
- Fully remote companies whose contract model is unclear or flexible: Yuno and Ontop. Ontop explicitly offers "employee, contractor, or both" and pays in USD.
- No company I checked publishes salary ranges for these roles. The compensation hints are equity or ESOP and company-performance bonuses.
- Work-authorization status for a pending Brazilian residence permit: TO_VERIFY for every company.

### Cited Findings
- **Clara**: a Central Bank-licensed payment institution in Brazil, with HQ moved to São Paulo (PRACTICAL_EXTERNAL, 2023-08-10) — [Suno](https://www.suno.com.br/noticias/?p=440958). "Competitive salary and stock options (ESOP) from day one" and an annual learning budget (VERIFIED_OFFICIAL) — [Clara posting](https://job-boards.greenhouse.io/clara/jobs/5205085007).
- **dLocal**: "Besides the tailored benefits we have for each country…", plus a referral bonus and Work From Anywhere for 3 months (VERIFIED_OFFICIAL) — [dLocal posting](https://jobs.lever.co/dlocal/9990577d-904e-4bdb-9190-41347645f79b).
- **Bitso**: its Brazilian subsidiary Nvio got Central Bank authorization as a payment institution in 2023 (PRACTICAL_EXTERNAL) — [LatamFintech](https://latamfintech.co/articles/defi-fintech-bitso-receives-bc-approval-to-operate-in-brazil).
- **Belvo**: "Annual company bonus linked to company performance", "Flexible working hours", "Health Insurance". Time zones: "we ensure we're always synced up for at least 3 hours per day" (VERIFIED_OFFICIAL, 2026-10-15) — [Belvo posting](https://jobs.ashbyhq.com/belvo/161cfb0e-5ee2-4655-b45b-460c066d6c73).
- **Yuno**: "Competitive Compensation", "Stock Options", "Health Plan wherever you are", Home Office Bonus (VERIFIED_OFFICIAL) — [Yuno posting](https://jobs.lever.co/yuno/2c1863c6-a3a4-4d3e-acb8-6f530f8ebe50).
- **Ontop**: "Your pay, in USD", "Global Account", unlimited PTO. The form asks "Are you open to working as an employee, contractor, or both?" (VERIFIED_OFFICIAL) — [Ontop careers](https://www.getontop.com/careers).
- **Addi**: "a generous salary, equity in the company" (VERIFIED_OFFICIAL) — [Addi posting](https://jobs.ashbyhq.com/addi/b3b0f097-8961-4001-b8ba-0baacda32bb7).
- **Mercado Libre**: Brazil postings are tied to Brazilian locations (e.g. Osasco, SP), and some are affirmative-action roles under Brazilian rules ("Afirmativa para Pessoas com Deficiência") (VERIFIED_OFFICIAL) — [Mercado Libre Eightfold](https://mercadolibre.eightfold.ai/careers/job/43721415).
- **Globant**: 6.2% of headcount was in Brazil in Q1 2025, out of 31,102 total (PRACTICAL_EXTERNAL, 2025-06-24) — [Bloomberg Línea](https://www.bloomberglinea.com/latinoamerica/argentina/migoya-de-globant-confirma-en-correo-interno-despido-del-3-de-la-plantilla-de-globers/).

### Inferences
- HYPOTHESIS: companies with São Paulo hubs (Mercado Libre, Clara, dLocal, Belvo, Despegar, Kavak, Nuvemshop, Rappi) normally hire Brazil residents through the local entity. That would make Brazilian work authorization a gating item: the residence permit is pending and expected after late November 2026 (TO_VERIFY with migration counsel, which is out of scope).
- HYPOTHESIS: remote-first firms (Yuno, Ontop) may offer contractor or EOR arrangements, which can be more flexible before the permit is issued. Tax and legal treatment is TO_VERIFY.
- HYPOTHESIS: on timezones, São Paulo (UTC-3) is aligned with Montevideo and Buenos Aires (dLocal, Pomelo, Ualá, Despegar) and 2–3 hours ahead of Bogotá and Mexico City (Rappi, Yuno, Addi, Clara, Bitso, Kavak). Belvo's 3-hour overlap rule shows that cross-timezone work is the norm.

### Gaps
- No salary bands were found for analytics-manager roles at any of these companies. levels.fyi and Glassdoor pay data were not retrieved (search budget exhausted).
- Not confirmed for any company: whether Brazil hires are CLT, PJ or EOR (Deel, Remote, Oyster). Contract form is not stated in the postings parsed.
- Residence-permit and work-authorization implications: TO_VERIFY (out of scope).

## Culture signals against the four criteria (results orientation, meritocracy, competent employees incl. the business side, data-driven culture), plus 2025–2026 layoffs and return-to-office changes

### Takeaway
- **Clara**: the clearest published culture claims ("high talent-density… low ego, high ownership"), plus breakeven in Brazil at the end of 2024.
- **dLocal**: an "impact over fixed hours" message.
- **Belvo**: shares company performance weekly and pays a performance-linked bonus.
- **Mercado Libre**: an "entrepreneurial" and data-narrative culture, with flexible hybrid working.
- Main 2025–2026 negative events found: Globant's June 2025 layoffs (~3%) and the Despegar ownership change (acquired by Prosus, May 2025; delisted in 2026).
- Not found: 2025–2026 layoffs at dLocal, Clara, Rappi, Bitso, Nuvemshop or Pomelo. Absence of evidence is not evidence of absence, because the search budget ran out.

### Cited Findings
**Results orientation and meritocracy**
- **Clara**:
  - "A high talent-density team… low ego, high ownership"; "Clarity — Open and direct communication" (VERIFIED_OFFICIAL, 2026) — [Clara posting](https://job-boards.greenhouse.io/clara/jobs/5145371007).
  - "~400 people"; "in 2025 the Financial Times named Clara the region's fastest-growing company" (VERIFIED_OFFICIAL, first published 2026-08-26) — [Clara AI BizOps Expert](https://job-boards.greenhouse.io/clara/jobs/5223309007).
  - Breakeven in Brazil at the end of 2024; US$80M raised (PRACTICAL_EXTERNAL, 2025-04-29) — [Finsiders](https://finsidersbrasil.com.br/negocios-em-fintechs/aportes/clara-levanta-us-80-milhoes-e-esta-proxima-da-lucratividade/).
- **dLocal**: "We focus on impact and productivity over fixed hours". The data role is to "push back on a delivery that doesn't meet the need, not just accept what's handed over" (VERIFIED_OFFICIAL) — [dLocal posting](https://jobs.lever.co/dlocal/9990577d-904e-4bdb-9190-41347645f79b).
- **Belvo**: weekly all-hands "to discuss company performance and goals"; annual bonus linked to company performance (VERIFIED_OFFICIAL) — [Belvo posting](https://jobs.ashbyhq.com/belvo/161cfb0e-5ee2-4655-b45b-460c066d6c73).
- **Yuno**:
  - "high level of ownership, with no need for an existing playbook" (VERIFIED_OFFICIAL) — [Yuno posting](https://jobs.lever.co/yuno/e601c8be-d03a-4aa4-b89a-ee784acf6921).
  - The CEO targets profitability by mid-2026 (PRACTICAL_EXTERNAL, 2026-08-12) — [Ecosistema Startup](https://ecosistemastartup.com/yuno-recauda-us45m-en-serie-b-lecciones-para-fintech-latam-en-2026/).
- **Addi**:
  - States it "achieved profitability" in the past year. Hiring includes a case study or live business case, a SQL exercise and a co-founder interview, and the company commits to give feedback (VERIFIED_OFFICIAL, 2026) — [Addi posting](https://jobs.ashbyhq.com/addi/b3b0f097-8961-4001-b8ba-0baacda32bb7).
  - The Head of Supply posting describes "a decision-grade governance cadence (a daily input pulse, a weekly review, a monthly decision instance on inputs not outputs)" (VERIFIED_OFFICIAL) — [Addi Head of Marketplace Supply](https://jobs.ashbyhq.com/addi/7dd51523-34c3-44a4-b305-f621e99f2a75).
- **Ontop**: "freedom with accountability" (VERIFIED_OFFICIAL) — [Ontop careers](https://www.getontop.com/careers).

**Data-driven culture**
- **Mercado Libre**: the Insights Specialist role asks for "habilidade para construir narrativas orientadas por dados" plus BigQuery and AI (VERIFIED_OFFICIAL, 2026-10-05) — [Meli posting](https://mercadolibre.eightfold.ai/careers/job/44666503).
- **dLocal**: works in Databricks, has a dedicated "Data & AI" team, and runs AI R&D Staff Engineer roles (VERIFIED_OFFICIAL) — [dLocal Lever API](https://api.lever.co/v0/postings/dlocal?mode=json).
- **Clara**: ops dashboards in Metabase and AI automation (n8n, LLM APIs) inside Clara Ops (VERIFIED_OFFICIAL) — [Clara AI BizOps Expert](https://job-boards.greenhouse.io/clara/jobs/5223309007).
- **Rappi**: the Growth E&A Senior Analyst role emphasises A/B testing and causal inference (PRACTICAL_EXTERNAL, indexed only, 2026-02-03) — [marketingmonk](https://www.marketingmonk.so/jobboard/jobs/growth-eanda-senior-analyst-at-rappi-mri7tg).

**Layoffs, return-to-office and ownership changes (2025–2026)**
- **Globant**: about 3% of staff cut (≈933), announced in an internal email on 2025-06-23 under the "Vision 2030" AI restructuring. Offices in Ushuaia and Resistencia closed (PRACTICAL_EXTERNAL, 2025-06-24) — [Bloomberg Línea](https://www.bloomberglinea.com/latinoamerica/argentina/migoya-de-globant-confirma-en-correo-interno-despido-del-3-de-la-plantilla-de-globers/); also [Forbes Colombia, 2025-07-08](https://forbes.co/2025/07/08/tecnologia/despidos-en-globant-lo-que-sabemos-del-recorte-de-1-000-personas-en-la-tecnologica/).
- **Despegar**: Prosus completed the acquisition in May 2025 (PRACTICAL_EXTERNAL, 2025-05-15) — [Business Wire](https://www.businesswire.com/news/home/20250515026178/en/Prosus-and-Despegar-Complete-Acquisition). It was later delisted from NYSE (PRACTICAL_EXTERNAL, dated 2026-09-23 by its URL) — [Descubre](https://www.descubre.vc/noticia/despegar-sale-de-la-bolsa-de-nueva-york-tras-ser-adquirida-por-prosus-2026-09-23).
- **Mercado Libre**:
  - Kept hybrid working in 2025 and planned 28,000 hires, reaching about 112,000 staff by end-2025, mostly in logistics (PRACTICAL_EXTERNAL, 2025-04-15) — [Folha PE](https://www.folhape.com.br/economia/mercado-livre-vai-contratar-28-mil-novos-funcionarios-na-america/404883/).
  - In Córdoba, Argentina, it moved staff to fully remote in July 2025 after a tax dispute, then later reopened an office in Villa Allende; the reopening date is unclear (PRACTICAL_EXTERNAL, search-snippet level) — [El Economista](https://eleconomista.com.ar/negocios/la-drastica-decision-acaba-tomar-empresa-mas-importante-argentina-n86360); [La Voz de San Justo](https://www.lavozdesanjusto.com.ar/con-una-inversion-de-us-3-millones-mercado-libre-reabre-sus-oficinas-en-cordoba).
- **Bitso**: the most recent layoffs found date from 2022: about 80 in May 2022, and a 25% cut after the FTX crash in late 2022 (PRACTICAL_EXTERNAL) — [Bloomberg Línea](https://bloomberglinea.com/english/mexican-crypto-unicorn-bitso-cuts-25-of-staff-after-ftx-crash); [The Block](https://www.theblock.co/post/190758/bitso-cuts-more-staff-in-fresh-round-of-layoffs). Brazil leadership changed in Feb 2026 (PRACTICAL_EXTERNAL) — [Finsiders](https://finsidersbrasil.com.br/gente-em-fintechs/bitso-muda-comando-do-negocio-no-brasil/).
- **Nuvemshop**: the most recent layoffs found were about 50 people (≈5%) in 2022 (PRACTICAL_EXTERNAL) — [Startups.com.br](https://startups.com.br/negocios/nuvemshop-enxuga-operacoes-e-demite-50-na-america-latina/).
- **Rappi**: the layoffs found date from 2020 (about 6% of staff; ~150 in Brazil) (PRACTICAL_EXTERNAL) — [Baguete](https://www.baguete.com.br/noticias/rappi-demite-150-no-brasil). In 2025 it was expanding in Brazil with zero fees for restaurants and a R$1.4bn plan, in a price war with iFood and 99Food (PRACTICAL_EXTERNAL, 2025-06-06) — [Descubre](https://www.descubre.vc/noticia/rappi-apuesta-por-tarifas-cero-y-expansi-n-masiva-en-brasil-2025-06-06).
  - Glassdoor rating: 3.7/5 from 596 reviews, seen only in a search snippet and not verified (PRACTICAL_EXTERNAL, low confidence) — [Glassdoor](https://static.glassdoor.com.br/Reviews/Rappi-Reviews-E1275894_P82.htm).
- **Pomelo**: US$55M Series C co-led by Kaszek and Insight Partners; Brazil and Mexico are its two largest markets; no layoffs found (PRACTICAL_EXTERNAL, 2026-01-20) — [Bloomberg Línea](https://www.bloomberglinea.com.br/startups/pomelo-capta-us-55-mi-e-mira-expansao-com-cartao-de-credito-no-brasil-e-no-mexico/).
- **dLocal**: the CEO is Pedro Arnt, former CFO of Mercado Libre (PRACTICAL_EXTERNAL, LinkedIn snippet) — [LinkedIn](https://uy.linkedin.com/in/pedro-arnt-dlocal-ceo). No 2025–2026 layoffs were found, but the search returned irrelevant results; see Gaps.

### Inferences
- HYPOTHESIS: on the four criteria, **Clara** has the strongest published signals: high talent density and ownership, a performance story (breakeven, FT ranking) and data tooling. **dLocal** is a close second on results orientation and seniority: a public company, 1,300+ staff and a former Mercado Libre CFO as CEO. **Mercado Libre** has the deepest analytics bench and data culture in LATAM. **Rappi** and **Kavak** look aggressive and execution-heavy but Spanish-speaking.
- HYPOTHESIS: Despegar after the Prosus deal and Globant after "Vision 2030" carry restructuring or integration risk in 2026.
- HYPOTHESIS: Globant's IT-services model (client delivery) conflicts with the target of partnering a business unit or P&L. Keep it only as a fallback.

### Gaps
- The search budget ran out before checking:
  - dLocal-specific news for 2025–2026 (layoffs, results), its 2022 short-seller controversy, and its CEO transition history
  - Glassdoor or levels.fyi ratings for dLocal, Clara, Mercado Libre and Bitso
  - 2025–2026 layoffs at Kushki, NotCo, Betterfly, Stori, Ualá, Kavak or Merama
- Mercado Libre's current (2026) office policy and any return-to-office tightening were not verified after April 2025.
- Bitso's headcount numbers conflict: about 277 per a July 2025 third-party profile ([Unify](https://www.unifygtm.com/insights-headcount/bitso)) versus "500+ employees in 35 countries" in a 2025 company report ([Bitso report PDF](https://blog.bitso.com/wp-content/uploads/2025/08/INFORME_BITSO2025_PT-FINAL.pdf)). Not reconciled.
