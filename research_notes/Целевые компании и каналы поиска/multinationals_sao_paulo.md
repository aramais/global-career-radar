# Multinational tech companies with São Paulo hubs hiring English-working analytics leaders

Research date: 2026-10-15. Method: I queried official job boards directly (Amazon.jobs JSON, Workday for Mastercard/Visa/PayPal/Samsung/Expedia, TikTok lifeattiktok API, Greenhouse for Airbnb/Stripe/Wise/Binance/Coinbase, Lever for Spotify, SmartRecruiters for Wise, Netflix Eightfold, Google Careers HTML, DiDi iCIMS, Booking jobs API). I also used LinkedIn's public job-listing endpoints for São Paulo, read the job texts, and checked a few press articles. Several things limited the work. The shared web-search budget ran out partway through. Uber's careers site (jobs.uber.com) and Revolut's careers page returned Cloudflare challenges. levels.fyi Brazil pages returned 404 and Glassdoor returned 403. Layoff and compensation research is therefore thin (see Gaps).

Confidence labels:
- **VERIFIED_OFFICIAL**: company ATS, company job description, or company memo/newsroom, read on 2026-10-15.
- **PRACTICAL_EXTERNAL**: LinkedIn listing, press, or aggregator.
- **HYPOTHESIS**: my inference.
- **TO_VERIFY**: needs checking.

"Live" means the listing came back from the live official ATS or the LinkedIn public jobs endpoint on 2026-10-15. "Indexed only" means it appeared only in search-engine snippets. LinkedIn listing dates may be repost dates.

Candidate-fit lens used for tiering (internal, not used in any query): a São Paulo-based analytics leader with marketplace, mobility and e-commerce pricing/monetization and experimentation experience. Works in English (C1). Portuguese at about A1. Residence permit pending. Wants manager/lead roles close to P&L, not IC/reporting/ML-infra roles.

## Q1. Which multinational tech companies have meaningful São Paulo teams in analytics/data science/product/strategy, beyond sales? Tiered shortlist

### Takeaway
The best current SP options where English works are Uber (the SP Tech Center plus LATAM strategy roles), Mastercard (a global pricing director role based in SP) and Meta (a WhatsApp Services Brazil data-science leader role). Amazon, Keeta/Meituan, DiDi/99, TikTok and Visa have large SP teams. However, their current analytics-leadership roles are IC, Portuguese/Spanish-required, or adjacent (PM, risk, consulting). Shopee, Google, Samsung, Wise and Airbnb are SP hubs with little English-only analytics leadership open now. Stripe, Booking.com and Expedia showed no SP roles at all on 2026-10-15.

### Cited Findings

**Uber: SP Tech Center (engineering, product, design, data science) plus regional business teams**
- Uber announced a second SP office for its Tech Center in Itaim Bibi: 16,000 m², 11 floors, capacity for up to 1,000 professionals, operating by Q1 2027. About 600 professionals already work at the SP tech center in engineering, product, design and data science. The Pinheiros office keeps operations and corporate functions. Published 2026-01-30 (PRACTICAL_EXTERNAL) — [Mercado&Consumo](https://mercadoeconsumo.com.br/30/01/2026/tecnologia/uber-anuncia-novo-escritorio-em-sp-para-abrigar-centro-tecnologico/)
- Uber cites more than R$2bn in Brazil tech investment. The 2024 tranche was R$1bn over 5 years to grow the SP Tech Center to 500 people in engineering, UX, product, data, identity, payments, mobility and delivery. A separate Rio tech center gets R$500m for 200 hires. The new SP office is "exclusively" for Tech Center staff and focuses on mobility and delivery. Uber no longer runs its own delivery service in Brazil (it partners with iFood). Published 2026-01-30 (PRACTICAL_EXTERNAL) — [Mobile Time](https://www.mobiletime.com.br/noticias/30/01/2026/uber-r-2-bi-brasil/)
- Uber's engineering director in Brazil said the company is expanding its number of tech professionals in Brazil "exponentially" (2026-01-30) — [Mercado&Consumo](https://mercadoeconsumo.com.br/30/01/2026/tecnologia/uber-anuncia-novo-escritorio-em-sp-para-abrigar-centro-tecnologico/)
- An older Uber-affiliated job page describes the SP office as a hub for Uber's Core Services Analytics team. Indexed only, undated (PRACTICAL_EXTERNAL) — [search result summary pointing to LinkedIn/Uber listings](https://br.linkedin.com/jobs/uber-vagas)
- Live SP LinkedIn listings on 2026-10-15 cover Strategy & Planning (Uber for Business), Engineering Manager, Staff SWE (Mobility), implementation, vendor management, Uber Advertising and brand reputation (PRACTICAL_EXTERNAL) — [LinkedIn: Manager, Strategy & Planning, U4B](https://br.linkedin.com/jobs/view/manager-strategy-planning-uber-for-business-at-uber-4480166789)

**Meta: SP office now covers more than sales (WhatsApp Services data science, enterprise AI engineering, product)**
- Live SP listings (2026-09-26 to 2026-10-13): Data Scientist, Product (WhatsApp Services Brazil leader); Engineering Manager, Enterprise Solutions LATAM; Forward Deployed Engineers, Enterprise AI LATAM; Product Manager, Enterprise Solutions; Business Engineer; Creator Marketing Lead LATAM; Enterprise Technical Sales Director LATAM; Policy roles (PRACTICAL_EXTERNAL) — [LinkedIn: Meta Data Scientist, Product SP](https://br.linkedin.com/jobs/view/data-scientist-product-at-meta-4476092152)
- The WhatsApp JD says Meta is "building a new team" in Brazil, the "newly established WhatsApp Services team", treated "as a zero-to-one effort". It describes Brazil as a market where people "message businesses" and "increasingly send money" on WhatsApp (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn JD](https://br.linkedin.com/jobs/view/data-scientist-product-at-meta-4476092152)

**Amazon: large SP corporate footprint across many businesses**
- Amazon.jobs returned 124 unique Brazil roles across 9 queries on 2026-10-15 (VERIFIED_OFFICIAL). SP teams include:
  - AWS, Amazon Ads LATAM, Prime Video and Amazon MGM Studios
  - Amazon Payments / 1P Crédito (Data Scientist I–III and Data Engineer roles), Amazon LATAM Payments Trust
  - CFX Brazil, Amazon Now (quick commerce from dark stores), Amazon Global Selling
  - ROW SHARP ("Rest of World Supply Chain Analytics, Research & Product", covering 10 countries)
  - Transportation/AMZL (Osasco), Amazon Leo Finance LATAM

  Sources: [Amazon.jobs: Data Scientist III, Amazon 1P Credito](https://www.amazon.jobs/en/jobs/10568161/data-scientist-iii-amazon-1p-credito-payments); [Product Manager, ROW SHARP](https://www.amazon.jobs/en/jobs/10486065/product-manager-row-sharp-rest-of-world-supply-chain-analytics-research-product); [Sr PM - Growth, Amazon Now](https://www.amazon.jobs/en/jobs/10561598/sr-product-manager-growth-amazon-now)

**Mastercard: SP hosts global and LAC teams (pricing, Advisors & Consulting Services, product)**
- The Workday search for "Sao Paulo" returned 29 roles on 2026-10-15 (VERIFIED_OFFICIAL). SP roles include:
  - Director, Strategic Pricing (global Commercial & New Payment Flows pricing team)
  - Advisors & Consulting Services: Performance Analytics, Strategy & Transformation, Business Experimentation, Marketing
  - Manager, Product Management; Product Commercialization SME (LAC); Director, Settlement Operations Brazil

  Source: [Mastercard Workday: Director, Strategic Pricing](https://mastercard.wd1.myworkdayjobs.com/CorporateCareers/job/Sao-Paulo-Brazil/Director--Strategic-Pricing_R-290833)

**Visa: SP hosts VCA (Visa Consulting & Analytics), risk, pricing, and Pismo (Brazil-based platform; many engineering roles are "BR - Remote")**
- The Workday search returned 26 Brazil/SP roles on 2026-10-15 (VERIFIED_OFFICIAL). They include Senior Manager, VCA Core Consulting Practice (SP or Brasília); Senior Director, VCA Implementation Services (SP); Senior Risk Manager; Sr. Pricing Analyst; Pismo compliance; and remote-Brazil engineering — [Visa Workday: Sr Manager VCA](https://visa.wd5.myworkdayjobs.com/Visa/job/BR---Brasilia-Brazil/Senior-Manager--Visa-Consulting-and-Analytics--VCA---Core-Consulting-Practice--So-Paulo-or-Brasilia--Brazil_REF083310W)
- VCA describes itself as "the consulting arm of Visa", drawing on "consulting, data analytics, technology, payments and economics" (VERIFIED_OFFICIAL) — [same JD](https://visa.wd5.myworkdayjobs.com/Visa/job/BR---Brasilia-Brazil/Senior-Manager--Visa-Consulting-and-Analytics--VCA---Core-Consulting-Practice--So-Paulo-or-Brasilia--Brazil_REF083310W)

**TikTok/ByteDance: very large SP hub (ads/GBS, TikTok Shop, Search Ops, Global Payment, LIVE, Music)**
- The lifeattiktok API returned 105 postings matching "Sao Paulo" on 2026-10-15, with 100 inspected (VERIFIED_OFFICIAL). They span GBS sales, Monetization Strategy & Operations LATAM, Industry Strategy, TikTok Shop operations/category/seller governance, Search Operations team lead, Global Payment compliance and customer service, Measurement Partner, and Solutions Engineers — [TikTok: Monetization Strategy & Operations Manager, LATAM](https://lifeattiktok.com/search/7693950927032437045)

**DiDi/99: SP is 99's base (mobility, 99Food, 99Pay). Caveat: 99 was founded in Brazil and has been part of DiDi since 2018.**
- From the JD: 99 has been "part of DiDi Global Inc." since 2018, and 99's innovations scale to "more than 12 countries" where DiDi operates (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn: 99 CX Business Intelligence Manager](https://br.linkedin.com/jobs/view/cx-business-intelligence-manager-at-99-4479744240)
- 99Food: R$2bn in its first year of operation (through June 2026); R$500m earlier to restart in SP; target of 15 cities by end-2025 and 20 by Jan 2026; 99Food sued Keeta over its trademark. Announced 2025-09-15 (PRACTICAL_EXTERNAL) — [Canaltech](https://canaltech.com.br/mercado/99food-dobra-o-investimento-e-aportara-r-2-bilhoes-no-brasil-ate-2026/)
- 99 newsroom posts: more than 100 openings in May 2026 (mostly SP; operations, commercial, BD, tech, anti-fraud, HR) and more than 90 openings in June 2026, from internships to leadership. Indexed only; the page fetch returned 429 (PRACTICAL_EXTERNAL) — [99 newsroom (May)](https://99app.com/newsroom/com-oportunidades-de-estagio-a-cargos-de-lideranca-99-abre-mais-de-100-vagas-de-emprego/); [99 newsroom (June)](https://99app.com/newsroom/com-oportunidades-de-estagio-a-cargos-de-lideranca-99-abre-mais-de-90-vagas-de-emprego/)
- DiDi's iCIMS board for SP mostly showed BD/sales roles on 2026-10-15, including "BD Jr Manager (Mandarim Obrigatório)" (VERIFIED_OFFICIAL) — [DiDi iCIMS](https://careers-didiglobal.icims.com/jobs/search?ss=1&searchKeyword=Sao+Paulo)

**Keeta (Meituan): new food-delivery entrant with SP strategy/ops/product hiring**
- Live SP listings (2026-09-21 to 2026-09-29) (PRACTICAL_EXTERNAL):
  - Target Management Expert (strategy & business operations)
  - User Acquisition Expert
  - Product Safety Manager (Senior PM)
  - Partner Relationship Manager
  - Meituan Marketing Designer (Brazil)

  Source: [LinkedIn: Keeta Target Management Expert](https://br.linkedin.com/jobs/view/target-management-expert-at-keeta-4469677249)
- Keeta calls itself "the international subsidiary of Meituan", with "over 1,000 employees worldwide" (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn: Keeta User Acquisition Expert](https://br.linkedin.com/jobs/view/user-acquisition-expert-at-keeta-4468794555)
- A LinkedIn repost says Keeta opened its first delivery-partner support center in SP and that Meituan targets 15 metro regions by June 2026, with roughly US$1bn of investment. Unverified repost (PRACTICAL_EXTERNAL, TO_VERIFY) — [LinkedIn post](https://br.linkedin.com/in/thgonery)

**Shopee (Sea): large SP operation, Portuguese-first**
- Live SP listings: Strategy Manager; Gerente de Projetos e Dados; Coordenador de Análise de Dados | Operações (fraud); many senior IC data roles, including Analista de Dados Sênior — Competitividade de Preço, Cientista de Dados Sênior — S&OP/Forecast, SPX analytics, and Driver Engagement. Listing dates run 2026-07-22 to 2026-10-14 (PRACTICAL_EXTERNAL) — [LinkedIn: Shopee Strategy Manager](https://br.linkedin.com/jobs/view/strategy-manager-at-shopee-4435258939); [Shopee Price Competitiveness Sr Analyst](https://br.linkedin.com/jobs/view/analista-de-dados-s%C3%AAnior-%E2%80%94-competitividade-de-pre%C3%A7o-s%C3%A3o-paulo-at-shopee-4466593794)
- Brazilian outlets report Shopee opening 400+ roles in 2026 across logistics, tech, planning and data analysis. Figures (403–451) and dates are inconsistent; indexed only (PRACTICAL_EXTERNAL) — [Diário do Pará](https://diariodopara.com.br/concursos-e-empregos/shopee-abre-mais-de-400-vagas-no-brasil-em-multiplas-areas/)

**Google: SP business roles are labelled with required languages; engineering is concentrated in Belo Horizonte**
- SP search results on 2026-10-15 included Business Data Scientist, gTech Ads (English, Portuguese); Cloud Data and Analytics Consultant (English, Portuguese, Spanish); Technical Solutions Consultant (EN/PT); Ads Solutions Engineering Manager (EN/PT); customer engineers; and SP software engineering roles. Senior AI/ML SWE roles sit in Belo Horizonte (VERIFIED_OFFICIAL / PRACTICAL_EXTERNAL) — [Google Careers SP search](https://www.google.com/about/careers/applications/jobs/results/?location=S%C3%A3o%20Paulo%2C%20State%20of%20S%C3%A3o%20Paulo%2C%20Brazil&q=analytics); [LinkedIn: Business Data Scientist, gTech Ads](https://br.linkedin.com/jobs/view/business-data-scientist-gtech-ads-english-portuguese-at-google-4472313822)

**Samsung: SP HQ (Morumbi) plus Campinas R&D**
- SP LinkedIn listing for Consumer Data Insights Sr. Manager LATAM, dated 2026-09-24 (PRACTICAL_EXTERNAL) — [LinkedIn](https://br.linkedin.com/jobs/view/consumer-data-insights-sr-manager-latam-at-samsung-electronics-4469743111)
- Workday (2026-10-15): SP roles are Marketing Jr Manager, AI Project Jr Manager, FP&A Manager, Retail Training Manager. Campinas R&D lists data science and software internships (VERIFIED_OFFICIAL) — [Samsung Workday](https://sec.wd3.myworkdayjobs.com/Samsung_Careers)

**Wise: SP is a LatAm operations/product hub, with no analytics leadership open**
- SmartRecruiters listed 32 SP roles posted 2026-07-01 to 2026-10-15 (VERIFIED_OFFICIAL):
  - FinCrime/KYC operations, payment operations, card disputes
  - Senior Product Manager – PIX; Senior PM Brazil – Risk & Controls
  - LATAM Financial Risk Manager; Senior Finance Manager; LatAm Government Relations; PR Manager LatAm
  - Senior Data Analyst – Compliance (IC); Senior CRM Lead
  - Staff/Senior SWE

  Source: [Wise: Senior Data Analyst – Compliance](https://jobs.smartrecruiters.com/Wise/744000155574019)

**Airbnb: Brazil roles are mostly engineering ("Brazil" location, possibly remote-in-country)**
- Greenhouse listed 10 Brazil roles posted 2026-09-29 to 2026-10-13 (VERIFIED_OFFICIAL): SWE (Analytics Infrastructure, Tech Foundations, Quality Platform, Unified Data Store), Staff Platform PM – Payments Regional Growth, Senior Community Growth Manager, and Market Associate (SP) — [Airbnb: Staff Platform PM, Payments Regional Growth](https://careers.airbnb.com/positions/8120611?gh_jid=8120611)

**Companies with no SP analytics roles on 2026-10-15 (official boards)**
- Stripe: 726 Greenhouse jobs, none in Brazil/LATAM (VERIFIED_OFFICIAL) — [Stripe Greenhouse API](https://boards-api.greenhouse.io/v1/boards/stripe/jobs)
- Booking.com: jobs API for "São Paulo, Brazil" returned totalCount 0 (VERIFIED_OFFICIAL) — [jobs.booking.com](https://jobs.booking.com/)
- Expedia Group: Workday "Sao Paulo" search returned 4 jobs, none in Brazil (VERIFIED_OFFICIAL) — [Expedia Workday](https://expedia.wd108.myworkdayjobs.com/search)
- Netflix: 1 Brazil role, Ads Product Marketing Manager (LATAM), SP (VERIFIED_OFFICIAL) — [Netflix](https://explore.jobs.netflix.net/careers/job/790317859855)
- Spotify: 0 Brazil roles of 81 on Lever (VERIFIED_OFFICIAL). LinkedIn shows a Client Partner role in SP, dated 2026-10-09 (PRACTICAL_EXTERNAL) — [LinkedIn: Spotify Client Partner](https://br.linkedin.com/jobs/view/client-partner-emerging-scaled-at-spotify-4477871768)
- PayPal: 4 SP roles (Sr Manager Treasury, Sr Account Manager, Manager Audit Operations, Sr Manager LATAM Controller), none in analytics (VERIFIED_OFFICIAL) — [PayPal Workday: Sr Manager, LATAM Controller](https://paypal.wd1.myworkdayjobs.com/jobs/job/Sao-Paulo-Brazil/Sr-Manager--LATAM-Controller_R0137797)
- SHEIN: SP/Guarulhos listings are account management, compliance, HRBP, EHS and warehouse planning, and "Gerente de Contas – Marketplace (Mandarim)". No analytics leadership (PRACTICAL_EXTERNAL) — [LinkedIn: SHEIN Compliance Manager](https://br.linkedin.com/jobs/view/compliance-manager-at-shein-4476290937)

**Other tech-adjacent multinationals surfaced by the SP keyword scan (outside the core list)**
- dunnhumby (UK customer-data science): Applied Data Science Manager, SP, dated 2026-10-09 (PRACTICAL_EXTERNAL) — [LinkedIn](https://br.linkedin.com/jobs/view/applied-data-science-manager-at-dunnhumby-4477815032)
- Edelman Intelligence: Data & Intelligence Director, South Cone, SP, dated 2026-10-15 (PRACTICAL_EXTERNAL) — [LinkedIn](https://br.linkedin.com/jobs/view/data-intelligence-director-south-cone-at-edelman-intelligence-4471122567)
- Liberty Latin America: Manager, Commercial Strategy & Data Analytics, Itapecerica da Serra (SP metro), dated 2026-09-19 (PRACTICAL_EXTERNAL) — [LinkedIn](https://br.linkedin.com/jobs/view/manager-commercial-strategy-data-analytics-at-liberty-latin-america-4478467259)
- Bain & Company SP: Expert Senior Manager, AI Engineering, dated 2026-10-04. Engineering-heavy, so outside the target (PRACTICAL_EXTERNAL) — [LinkedIn](https://br.linkedin.com/jobs/view/expert-senior-manager-ai-engineering-at-bain-company-4422413284)

### Inferences

**Tiered shortlist (HYPOTHESIS, built from the evidence above and in Q2–Q5)**

**Tier A: prioritise now (English-workable SP leadership roles live; close to the candidate's strengths)**
1. **Uber.** SP is a real tech and data hub (about 600 people, growing to 1,000 by 2027). A live LATAM Strategy & Planning Manager role lists only English proficiency. Mobility marketplace economics fit the candidate's Yandex Taxi background directly. Watch for Data Science Manager / Analytics Manager reposts (Safety DS Manager was indexed). The ATS could not be enumerated (Cloudflare), so jobs.uber.com should be checked manually.
2. **Mastercard.** Director, Strategic Pricing is SP-based but sits in a global team. It asks for fluent English with no Portuguese stated, 3 office days a week, and pricing/economics work. This is the closest pricing/monetization match found. Advisors & Consulting roles generally require the local language.
3. **Meta.** The WhatsApp Services Brazil DS leader role (10+ years, leads DS and managers, experimentation/causal inference preferred, Brazil 0→1 payments/business messaging) is a strong functional match. Caveats: an explicitly hands-on IC/manager hybrid, a high technical interview bar, and a possible AI-development measurement emphasis.

**Tier B: strong SP hubs, but current roles are adjacent or need Portuguese/Spanish**
4. **Amazon.** A large SP corporate base in many businesses, and fluent English is a standard requirement. However, no Analytics/BI/DS Manager is open in Brazil now. The nearest roles are PM (Amazon Now growth, ROW SHARP), risk/credit managers and ops principals. A 5-day RTO memo and manager-ratio flattening apply.
5. **Keeta (Meituan).** A new delivery marketplace building SP strategy/ops/growth teams. Some roles accept English only (UA Expert, Product Safety); others need English and Portuguese (Target Management). The "Expert" titles may be senior IC rather than people management. Intensity of the operating culture: HYPOTHESIS, TO_VERIFY.
6. **DiDi/99.** Mobility, delivery (99Food) and fintech (99Pay) in SP. A CX BI Manager role is posted in English. Many roles are Portuguese-first and some need Mandarin. The brand is Brazilian-founded but owned by a Chinese multinational.
7. **TikTok.** A very large SP hub. Monetization Strategy & Operations (revenue planning, pricing and incentive policy) is functionally close, but requires business-level Portuguese. About a third of SP postings mention Portuguese.
8. **Visa.** VCA analytics consulting in SP. Current VCA senior roles require Portuguese (Sr Manager) or Spanish (Sr Director), with at least 3 office days.

**Tier C: SP presence but weak fit or Portuguese-first**
9. **Shopee.** Large SP operation with strategy and data teams. Postings are written in Portuguese; Strategy Manager owns P&L planning (fluent English also required). Portuguese is effectively required (HYPOTHESIS).
10. **Samsung.** LATAM Consumer Data Insights Sr Manager (CRM/lifecycle) needs advanced English but is on-site. It is CRM-focused rather than pricing/marketplace.
11. **Google.** SP business and analytics roles are labelled "(English, Portuguese)", and the analytics roles are IC (Business Data Scientist).
12. **Wise and Airbnb.** SP hubs, but no analytics leadership open.

**Not viable now:** Stripe, Booking.com, Expedia, Netflix, Spotify, PayPal, SHEIN (Mandarin and ops roles). Kwai/Kuaishou, Temu/PDD, AliExpress, Revolut and Microsoft were not verifiable (see Gaps).

**Out of scope but worth knowing:** LATAM-founded companies dominate SP analytics-manager listings, for example QuintoAndar's "Gerente de Dados & Analytics" ([LinkedIn, 2026-10-09](https://br.linkedin.com/jobs/view/grupo-quintoandar-gerente-de-dados-analytics-at-quintoandar-4477851142)). QuintoAndar is a real-estate marketplace, which matches the candidate's domain, but its postings are in Portuguese. Despegar (Argentina-founded) was excluded.

**Other inferences**
- The SP labour market for English-only analytics leadership at multinationals is narrow. The best openings are global or LATAM-scoped roles that happen to sit in SP (Uber LATAM, Mastercard global pricing, Meta Brazil product DS). Brazil-market-facing roles mostly require Portuguese (HYPOTHESIS, supported by the JD language requirements in Q2).

### Gaps
- Uber's own ATS (jobs.uber.com) and Revolut's careers page were blocked by Cloudflare, so the full Uber SP analytics/DS list and Revolut Brazil roles are unverified. Uber's "Data Science Manager (Safety)" is indexed only.
- Kwai/Kuaishou, Temu/PDD, AliExpress and Microsoft SP: I found no official-board data. The Microsoft careers API returned "Please try again later" and the old endpoint was dead. LinkedIn keyword scans for Kwai, Temu and Revolut in SP returned no matching company listings, but that does not prove absence.
- Keeta's investment, office and headcount figures for Brazil are unverified (repost only), and the web-search budget ran out before checking primary Meituan/Keeta announcements.

## Q2. 2025–2026 SP openings for analytics leadership (URLs, dates, title, scope, language, Portuguese requirement)

### Takeaway
On 2026-10-15 I found about 12 live SP roles at multinationals that match or sit next to "analytics leader with P&L proximity". Only a few state English without Portuguese: Uber S&P Manager (LATAM), Mastercard Director Strategic Pricing (global), Meta WhatsApp DS leader (language not stated), Keeta UA Expert and Product Safety Manager, and several Amazon PM/risk roles. Most TikTok, Visa VCA, Mastercard consulting, Google and Shopee roles require Portuguese, and some require Spanish.

### Cited Findings

**Uber**
- **Manager, Strategy & Planning, Uber for Business (SP)**, LinkedIn listing 2026-10-15, live.
  - Scope: LATAM U4B (Brazil and Mexico plus emerging markets); thought partner to regional leadership; multi-year strategy, annual planning and forecasting; analytical frameworks.
  - Basic qualifications: 5+ years in consulting, strategy, strategic finance, BI or data analytics; "English proficiency". Preferred: SQL and Python/R.
  - Portuguese is not listed in the qualifications (TO_VERIFY with recruiter). Hybrid, at least 50% in office.
  - (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn](https://br.linkedin.com/jobs/view/manager-strategy-planning-uber-for-business-at-uber-4480166789)
- **Data Science Manager (Safety Team), Greater SP.** Indexed only, posting date unknown (PRACTICAL_EXTERNAL) — [LinkedIn Uber jobs page (snippet)](https://br.linkedin.com/jobs/uber-vagas)
- **Manager II, Data Science, SP.** Expired listing on an aggregator (PRACTICAL_EXTERNAL) — [datasciencejobs.com](https://datasciencejobs.com/companies/uber/)
- **Senior Data Scientist – Experimentation, SP.** IC role, indexed only (PRACTICAL_EXTERNAL) — [experimentationjobs.com](https://experimentationjobs.com/job/uber-senior-data-scientist-experimentation-sao-paulo-brazil/)

**Mastercard**
- **Director, Strategic Pricing (SP)**, posted 2026-09-28, live on Workday.
  - Scope: global CNPF Global Pricing team. Leads strategic projects on pricing, economics and growth; builds executive business cases; partners with product, finance and sales; works "across regions".
  - Requirements: "Fluent in English" (Portuguese not mentioned); "Availability to be in the office 3 times per week".
  - (VERIFIED_OFFICIAL) — [Mastercard Workday](https://mastercard.wd1.myworkdayjobs.com/CorporateCareers/job/Sao-Paulo-Brazil/Director--Strategic-Pricing_R-290833)
- **Managing Consultant, Advisors & Consulting Services, Performance Analytics (SP)**, posted 2026-10-01, live.
  - Requirements: "Ability to communicate effectively in English and the local office language (if applicable)", so Portuguese is expected. Also "Eligibility to work in the country where you are applying".
  - (VERIFIED_OFFICIAL) — [Mastercard Workday](https://mastercard.wd1.myworkdayjobs.com/CorporateCareers/job/Sao-Paulo-Brazil/Managing-Consultant--Advisors---Consulting-Services--Performance-Analytics_R-269414-1)
- **Consultant, A&CS, Business Experimentation (SP)**, posted about 2026-09-18, live. Below the target seniority; experimentation-focused (VERIFIED_OFFICIAL) — [Mastercard Workday](https://mastercard.wd1.myworkdayjobs.com/CorporateCareers/job/Sao-Paulo-Brazil/Consultant--Advisors---Consulting-Services--Business-Experimentation_R-282023)

**Meta**
- **Data Scientist, Product (SP): Data Science leader for Brazil, WhatsApp Services**, LinkedIn listing 2026-10-13, live.
  - Role: "hands-on leader who can… work as an individual contributor or org leader"; "Inspire and lead a team of data scientists and managers".
  - Minimum: 10+ years in analytics and DS. Preferred: experimentation/causal inference and AI-workflow skills.
  - No language requirement stated (English implied; Portuguese TO_VERIFY).
  - (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn](https://br.linkedin.com/jobs/view/data-scientist-product-at-meta-4476092152)
- Meta DS Manager interviews (non-Brazil reviews) include a SQL case study, metric design/product analysis, and leadership rounds on team management (PRACTICAL_EXTERNAL) — [Glassdoor BR interview review](https://www.glassdoor.com.br/Entrevista/Meta-Entrevista-E40772-RVW89736726.htm)

**Amazon (all live on Amazon.jobs, 2026-10-15)**
- **Sr Product Manager – Growth, Amazon Now (SP)**, posted 2026-09-28.
  - Duties: "responsible for the metrics that define whether we win"; runs weekly and daily business reviews; owns pricing and promotions execution; "judgment calls that move the P&L".
  - The basic qualifications listed don't mention language (TO_VERIFY).
  - (VERIFIED_OFFICIAL) — [Amazon.jobs](https://www.amazon.jobs/en/jobs/10561598/sr-product-manager-growth-amazon-now)
- **Principal, Quick Commerce Ops, BR Operations Team (SP)**, posted 2026-08-28. "Speak, write, and read fluently in English" (VERIFIED_OFFICIAL) — [Amazon.jobs](https://www.amazon.jobs/en/jobs/10520812/principal-quick-commerce-ops-br-operations-team)
- **Sr. Product Manager, ROW SHARP (SP)**, posted 2026-09-25. IC role; supply chain products for 10 countries; "advanced English at CEFR level B2/C1" (VERIFIED_OFFICIAL) — [Amazon.jobs](https://www.amazon.jobs/en/jobs/10560158/sr-product-manager-row-sharp)
- **Senior Instock Manager, Cross Border (SP)**, posted 2026-09-08. English "C1 level or above"; experience in planning, pricing or e-commerce (VERIFIED_OFFICIAL) — [Amazon.jobs](https://www.amazon.jobs/en/jobs/10532723/senior-instock-manager-cross-border)
- **Senior Credit Risk Manager, Amazon Payments (SP)**, posted 2026-10-14 (also 2026-08-10). Fluent English; needs a credit-risk background (VERIFIED_OFFICIAL) — [Amazon.jobs](https://www.amazon.jobs/en/jobs/10578442/senior-credit-risk-manager-amazon-payments)
- **Risk and Fraud Manager, Amazon Credit, LATAM Payments Trust (SP)**, posted 2026-09-25. Fluent English (VERIFIED_OFFICIAL) — [Amazon.jobs](https://www.amazon.jobs/en/jobs/10560290/risk-and-fraud-manager-amazon-credit-amazon-latam-payments-trust)
- **External Fulfillment Program Manager Analytics, Marketplace (SP)**, posted 2026-09-25. Duties: "Manage reports and track business metrics in a weekly basis", which makes it reporting-heavy and below the target (VERIFIED_OFFICIAL) — [Amazon.jobs](https://www.amazon.jobs/en/jobs/10560330/external-fulfillment-program-manager-analytics-marketplace-ef-team)
- Searches for "analytics manager", "data science manager", "business intelligence manager", "head of analytics", "senior manager analytics" and "economist" (country BRA) returned no Brazil analytics-manager titles. The only Brazil BI titles were BI Analyst, BI Specialist and BI Intern (VERIFIED_OFFICIAL) — [Amazon.jobs search](https://www.amazon.jobs/en/search?base_query=analytics+manager&country=BRA)

**TikTok (official ATS; posting dates not exposed by the API)**
- **Monetization Strategy & Operations Manager, LATAM – GBS (SP).**
  - Duties: revenue planning, "pricing and incentive policy", QBRs, weekly forecasting.
  - Minimum: "Business-level proficiency in English and Portuguese", so Portuguese is required.
  - (VERIFIED_OFFICIAL) — [TikTok](https://lifeattiktok.com/search/7693950927032437045)
- **Industry Operations Manager – Strategy (LATAM TTS) – GBS (SP).** "Proficiency in English and Portuguese" (VERIFIED_OFFICIAL) — [TikTok](https://lifeattiktok.com/search/7696202093236996405)
- **Industry Client Solutions Manager, Strategy – LATAM (SP).** Spanish and English required (VERIFIED_OFFICIAL) — [TikTok](https://lifeattiktok.com/search/7655801156506110261)
- **Business Analyst – GBS – SP.** IC role; Portuguese and English required; LinkedIn date 2026-09-18 (VERIFIED_OFFICIAL) — [TikTok](https://lifeattiktok.com/search/7581952582203590917)
- 34 of the 100 inspected SP TikTok postings mention Portuguese in their text (VERIFIED_OFFICIAL, my count) — [TikTok careers](https://lifeattiktok.com/search?keyword=Sao%20Paulo)

**Visa**
- **Senior Manager, VCA Core Consulting Practice (SP or Brasília)**, posted 2026-09-18, live. "Fully bi-lingula (English and Portuguese)" [sic], so Portuguese is required (VERIFIED_OFFICIAL) — [Visa Workday](https://visa.wd5.myworkdayjobs.com/Visa/job/BR---Brasilia-Brazil/Senior-Manager--Visa-Consulting-and-Analytics--VCA---Core-Consulting-Practice--So-Paulo-or-Brasilia--Brazil_REF083310W)
- **Senior Director, VCA Implementation Services (SP)**, posted 2026-10-06, live. "Advanced/Fluent English required, Advanced/Fluent Spanish required" (VERIFIED_OFFICIAL) — [Visa Workday](https://visa.wd5.myworkdayjobs.com/Visa/job/BR---So-Paulo-Brazil/Senior-Director--VCA-Implementation-Services--VIS-_REF089087W-1)

**DiDi/99**
- **CX Business Intelligence Manager (Greater SP)**, LinkedIn listing 2026-10-15, live.
  - Duties: lead the CX Data & Insights team for the fintech business; manage CX Fintech stakeholders in Brazil and Mexico; "interact with stakeholders from all over the world"; lead a DS team; NPS-to-business-metrics work; SQL/R/Python.
  - Posted in English; language requirement not stated (TO_VERIFY). CX/NPS focus is a partial fit.
  - (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn](https://br.linkedin.com/jobs/view/cx-business-intelligence-manager-at-99-4479744240)
- **Sales Strategy – Tech & AI Manager** (2026-10-15) and **B2B Sales RevOps Manager** (2026-09-28), Greater SP. Not inspected in detail (PRACTICAL_EXTERNAL) — [LinkedIn](https://br.linkedin.com/jobs/view/sales-strategy-tech-ai-manager-at-99-4480126315); [LinkedIn](https://br.linkedin.com/jobs/view/b2b-sales-revops-manager-at-99-4434900622)

**Keeta (Meituan)**
- **Target Management Expert (SP)**, LinkedIn 2026-09-24. Owns business planning, target setting and MBR/WBR/DBR reviews; 5+ years; hands-on SQL; "Full professional proficiency in English and Portuguese", so Portuguese is required (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn](https://br.linkedin.com/jobs/view/target-management-expert-at-keeta-4469677249)
- **User Acquisition Expert (SP)**, LinkedIn 2026-09-22. Traffic strategy, CAC, ROI tracking, rapid experiments; "Fluent English (written and verbal) for cross-border collaboration" (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn](https://br.linkedin.com/jobs/view/user-acquisition-expert-at-keeta-4468794555)
- **Product Safety Manager (Senior PM, SP)**, LinkedIn 2026-09-21. "Submit your resume in English" (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn](https://br.linkedin.com/jobs/view/product-safety-manager-at-keeta-4469964994)

**Shopee**
- **Strategy Manager (SP)**, LinkedIn date 2026-07-22, likely a bulk refresh.
  - Duties: core-business strategy; long-term P&L planning; free-shipping, logistics and marketing cost optimisation; manages a team of strategy analysts.
  - Requirements: "Inglês fluente"; the posting is written in Portuguese, so Portuguese is effectively required (HYPOTHESIS). Hybrid.
  - (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn](https://br.linkedin.com/jobs/view/strategy-manager-at-shopee-4435258939)
- **Gerente de Projetos e Dados (SP)**, LinkedIn 2026-10-09. Financial reports, Power BI and data-team allocation: reporting-heavy (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn](https://br.linkedin.com/jobs/view/gerente-de-projetos-e-dados-at-shopee-4476254475)
- **Coordenador de Análise de Dados | Operações (SP)**, LinkedIn 2026-09-25. Fraud/chargeback recovery strategy; "Inglês avançado"; posted in Portuguese (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn](https://br.linkedin.com/jobs/view/coordenador-de-an%C3%A1lise-de-dados-opera%C3%A7%C3%B5es-at-shopee-4460205628)

**Samsung**
- **Consumer Data Insights Sr. Manager LATAM (SP)**, LinkedIn 2026-09-24. Consumer data and CRM across 7 LATAM subsidiaries; manages the CRM team; "Advanced English is mandatory"; Spanish a plus; "Work Modality: On Site" (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn](https://br.linkedin.com/jobs/view/consumer-data-insights-sr-manager-latam-at-samsung-electronics-4469743111)

**Google**
- **Business Data Scientist, gTech Ads (English, Portuguese), SP**, LinkedIn 2026-09-30. IC role; Portuguese required per the title (VERIFIED_OFFICIAL title) — [LinkedIn](https://br.linkedin.com/jobs/view/business-data-scientist-gtech-ads-english-portuguese-at-google-4472313822)

### Inferences
- **Most actionable English-only roles right now:** Mastercard Director Strategic Pricing (pricing, global scope), Uber S&P Manager U4B (LATAM strategy and analytics), and Meta WhatsApp Brazil DS leader (product analytics and experimentation). Amazon Now Sr PM Growth is P&L-close but carries a PM title (HYPOTHESIS).
- **Language pattern:** Brazil-market commercial and strategy roles at TikTok, Visa VCA, Mastercard A&CS, Google and Shopee require Portuguese. Global or regional-scope roles (Mastercard global pricing, Amazon ROW/LATAM, Uber LATAM, Keeta cross-border) more often list English only. The candidate should target regional or global mandates (HYPOTHESIS).
- **Reporting-heavy roles** (Amazon EF PM Analytics, Shopee Gerente de Projetos e Dados) should be deprioritised under the exclusion criteria (HYPOTHESIS).

### Gaps
- Exact posting dates are missing for TikTok roles (the API returns a null publish_time) and for indexed-only Uber DS manager roles.
- Portuguese requirements are not stated for Uber S&P, Meta DS, 99 CX BI and Amazon Now. Recruiter confirmation needed (TO_VERIFY).
- I could not run 2025 archive checks, i.e. which analytics-manager roles these companies posted and closed in 2025.

## Q3. Work model, 2025–2026 RTO mandates and layoffs affecting Brazil

### Takeaway
SP multinationals mostly run 3-day hybrid models: Uber (at least 50%, Tue–Thu for some teams), Mastercard (3 days), Visa (at least 3 days), Shopee (hybrid). Amazon's memo mandates 5 days in office from 2025-01-02; whether it applies in Brazil is TO_VERIFY. Samsung's LATAM role is on-site. I could not research layoffs affecting Brazil after the search budget ran out. The only verified restructuring signal is Amazon's push to flatten manager layers.

### Cited Findings
- **Uber:** "Unless approved for full remote work, employees must spend at least 50% of their time in-office"; "Some roles, like those at greenlight hubs, require full-time in-office presence" (2026-10-15 JD) (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn: Uber S&P Manager](https://br.linkedin.com/jobs/view/manager-strategy-planning-uber-for-business-at-uber-4480166789)
- **Uber:** "This is a hybrid role - our team collaborates in-person out of São Paulo 3 days/week (Tuesday-Thursday)" (2026-10-15 JD, U4B Implementation) (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn](https://br.linkedin.com/jobs/view/associate-manager-implementation-uber-for-business-brazil-at-uber-4471182033)
- **Mastercard:** "Availability to be in the office 3 times per week" (Director, Strategic Pricing, posted 2026-09-28) (VERIFIED_OFFICIAL) — [Mastercard Workday](https://mastercard.wd1.myworkdayjobs.com/CorporateCareers/job/Sao-Paulo-Brazil/Director--Strategic-Pricing_R-290833)
- **Visa:** "Visa requires at least 3 days in office, expectations of these days will be confirmed by your Hiring Manager" (VCA Sr Manager, posted 2026-09-18) (VERIFIED_OFFICIAL) — [Visa Workday](https://visa.wd5.myworkdayjobs.com/Visa/job/BR---Brasilia-Brazil/Senior-Manager--Visa-Consulting-and-Analytics--VCA---Core-Consulting-Practice--So-Paulo-or-Brasilia--Brazil_REF083310W)
- **Shopee:** "Adotamos um modelo de trabalho híbrido para posições administrativas alocadas no nossos escritórios" (2026 JDs) (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn: Shopee Strategy Manager](https://br.linkedin.com/jobs/view/strategy-manager-at-shopee-4435258939)
- **Samsung:** "Work Modality: On Site" (LATAM Sr Manager, 2026-09-24) (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn](https://br.linkedin.com/jobs/view/consumer-data-insights-sr-manager-latam-at-samsung-electronics-4469743111)
- **Amazon:** the CEO memo says employees will be in the office five days a week from 2025-01-02, with exceptions only for approved Remote Work Exceptions. Each S-team org must "increase the ratio of individual contributors to managers by at least 15%" by end of Q1 2025. The memo doesn't explicitly list countries (VERIFIED_OFFICIAL) — [About Amazon](https://www.aboutamazon.com/news/company-news/ceo-andy-jassy-latest-update-on-amazon-return-to-office-manager-team-ratio)
- **99:** SP openings are hybrid, per Canaltech (date not clear) (PRACTICAL_EXTERNAL) — [Canaltech](https://canaltech.com.br/empregos/99-abre-vagas-para-area-de-tecnologia-e-mais-veja-como-se-inscrever/)
- **Uber** is expanding, not contracting, in Brazil tech (new 1,000-seat office by Q1 2027; announced 2026-01-30) (PRACTICAL_EXTERNAL) — [Mercado&Consumo](https://mercadoeconsumo.com.br/30/01/2026/tecnologia/uber-anuncia-novo-escritorio-em-sp-para-abrigar-centro-tecnologico/)
- **Brazil hiring is still expanding** at Keeta, 99/99Food (R$2bn through June 2026) and TikTok Shop (100+ SP postings) (PRACTICAL_EXTERNAL / VERIFIED_OFFICIAL) — [Canaltech](https://canaltech.com.br/mercado/99food-dobra-o-investimento-e-aportara-r-2-bilhoes-no-brasil-ate-2026/); [TikTok careers](https://lifeattiktok.com/search?keyword=Sao%20Paulo)

### Inferences
- Plan for 3 office days a week in SP at most targets, and up to 5 at Amazon if the global mandate applies locally. Office location matters: Uber Tech Center moves to Itaim Bibi in 2027; Shopee is at Faria Lima; Samsung is in Morumbi (HYPOTHESIS).
- Amazon's IC:manager flattening suggests fewer people-manager slots in analytics/BI, consistent with no BI/Analytics Manager openings found in Brazil (HYPOTHESIS).
- The SP delivery war (99Food, Keeta, iFood) is driving strategy, growth and analytics hiring now. Delivery-war subsidies may later be cut back (HYPOTHESIS, TO_VERIFY).

### Gaps
- Not researched because the web-search budget ran out:
  - Layoffs affecting Brazil in 2025–2026 (layoffs.fyi; Brazilian press on Amazon, Microsoft, Meta, Google, TikTok, Kwai and Shopee cuts)
  - Meta, Google, TikTok, Microsoft and Keeta RTO rules for Brazil
  - Whether Amazon's 5-day RTO is enforced in Brazil
- All are TO_VERIFY. I deliberately did not assert any specific layoff numbers.

## Q4. Do they hire foreigners in Brazil (work authorization, internal transfer only, etc.)?

### Takeaway
No SP posting I read offers visa sponsorship. Mastercard explicitly requires eligibility to work in Brazil. Amazon's Brazil hiring is local-contract based (CLT/CPF documentation appears in Amazon JDs). TikTok runs LATAM assignment, relocation and visa processes, which suggests internal mobility rather than sponsorship of external hires. Several Chinese-owned employers (DiDi, SHEIN) require Mandarin for some roles. For this candidate, local work authorization is the gating item (TO_VERIFY; migration conclusions are out of scope).

### Cited Findings
- **Mastercard:** "Eligibility to work in the country where you are applying, as well as apply for travel visas as required by travel needs" (SP Managing Consultant, posted 2026-10-01) (VERIFIED_OFFICIAL) — [Mastercard Workday](https://mastercard.wd1.myworkdayjobs.com/CorporateCareers/job/Sao-Paulo-Brazil/Managing-Consultant--Advisors---Consulting-Services--Performance-Analytics_R-269414-1)
- **Amazon Brazil** staffing JDs reference "work authorization documentation (CLT requirements, CPF validation, and applicable labor documentation per Brazilian leg[islation])" (2026-09-25). This shows local CLT employment practice, not a policy on foreign hires (VERIFIED_OFFICIAL) — [Amazon.jobs: Staffing Lead, Cajamar](https://www.amazon.jobs/en/jobs/10560422/staffing-lead-workforce-staffing-wfs)
- **TikTok** SP HR intern JD: "Help track LATAM assignment, relocation, visa, base city, and business travel processes… with… mobility teams" (VERIFIED_OFFICIAL) — [TikTok](https://lifeattiktok.com/search/7678759156266993925)
- **DiDi** SP: "BD Jr Manager (Mandarim Obrigatório)" (VERIFIED_OFFICIAL) — [DiDi iCIMS](https://careers-didiglobal.icims.com/jobs/search?ss=1&searchKeyword=Sao+Paulo)
- **SHEIN** SP: "Gerente de Contas - Marketplace (Mandarim)" (PRACTICAL_EXTERNAL) — [LinkedIn](https://br.linkedin.com/jobs/view/gerente-de-contas-marketplace-mandarim-at-shein-4468457372)
- **Keeta** asks for resumes in English and "cross-border collaboration", which signals an internationally mixed team (VERIFIED_OFFICIAL text via LinkedIn) — [LinkedIn](https://br.linkedin.com/jobs/view/product-safety-manager-at-keeta-4469964994)

### Inferences
- English-language JDs, a global or regional scope and Chinese/US parent companies (Uber, Meta, Mastercard, Amazon, Keeta) suggest non-Brazilians are hired. However, every lead examined assumes the hire is already authorised to work in Brazil (HYPOTHESIS).
- **Practical implication:** the candidate's pending residence permit (expected after late November 2026) is likely to be checked at offer/onboarding (CPF and work authorization for CLT). Whether the pending or granted permit allows CLT employment is TO_VERIFY with a migration lawyer and each recruiter. Out of scope here.

### Gaps
- No company-level statement on sponsoring work visas for external hires in Brazil was found for any company.
- No data on whether these employers accept candidates with a residence permit in process.

## Q5. Culture signals against the four criteria, plus compensation bands

### Takeaway
Culture evidence collected here comes almost entirely from company self-descriptions in 2026 job descriptions, not independent employee data, because Glassdoor (403) and levels.fyi Brazil (404 / no Brazil rows) were not accessible. JD language signals results orientation, a strong business-side bar and data-driven decision-making at Uber, Amazon, Mastercard, TikTok, DiDi/99 and Keeta. Meritocracy evidence is missing apart from Amazon's flattening memo. I obtained no reliable SP compensation bands for analytics leaders.

### Cited Findings

**Results orientation**
- Uber asks for a "hands-on, results-oriented mindset… drive execution without direct authority" and says it "isn't the kind of place where you follow a playbook" (2026-10-15) — [LinkedIn: Uber S&P](https://br.linkedin.com/jobs/view/manager-strategy-planning-uber-for-business-at-uber-4480166789)
- Amazon Now PM: "You will be responsible for the metrics that define whether we win… make the day-to-day judgment calls that move the P&L" (2026-09-28) — [Amazon.jobs](https://www.amazon.jobs/en/jobs/10561598/sr-product-manager-growth-amazon-now)
- Keeta: owns target setting and MBR/WBR/DBR reviews "to drive decision-making" (2026-09-24) — [LinkedIn](https://br.linkedin.com/jobs/view/target-management-expert-at-keeta-4469677249)
- Visa VCA wants a "results oriented individual" (2026-09-18) — [Visa Workday](https://visa.wd5.myworkdayjobs.com/Visa/job/BR---Brasilia-Brazil/Senior-Manager--Visa-Consulting-and-Analytics--VCA---Core-Consulting-Practice--So-Paulo-or-Brasilia--Brazil_REF083310W)

**Competent colleagues, including the business side (hiring bar)**
- Uber S&P requires 5+ years in "management consulting, corporate strategy, strategic finance… data analytics"; MBA preferred — [LinkedIn](https://br.linkedin.com/jobs/view/manager-strategy-planning-uber-for-business-at-uber-4480166789)
- Mastercard pricing director requires strategy consulting, pricing, economics or "highly analytical problem-solving environments" — [Mastercard Workday](https://mastercard.wd1.myworkdayjobs.com/CorporateCareers/job/Sao-Paulo-Brazil/Director--Strategic-Pricing_R-290833)
- TikTok MSO "combines the problem-solving mindset of top-tier consulting with strong operational execution" and calls itself a "high-performing" organization — [TikTok](https://lifeattiktok.com/search/7693950927032437045)
- Meta DS leader requires 10+ years — [LinkedIn](https://br.linkedin.com/jobs/view/data-scientist-product-at-meta-4476092152)

**Data-driven culture**
- DiDi/99 lists "We are data-driven… making informed decisions" among its values (2026-10-15) — [LinkedIn: 99 CX BI Manager](https://br.linkedin.com/jobs/view/cx-business-intelligence-manager-at-99-4479744240)
- Mastercard wants someone who can "translate data-driven insights into actionable recommendations" — [Mastercard Workday](https://mastercard.wd1.myworkdayjobs.com/CorporateCareers/job/Sao-Paulo-Brazil/Director--Strategic-Pricing_R-290833)
- Keeta asks for people who are "Highly data-sensitive… hands-on proficiency in SQL" — [LinkedIn](https://br.linkedin.com/jobs/view/target-management-expert-at-keeta-4469677249)
- Amazon's preferred qualifications include "influencing senior leadership through data driven insights" — [Amazon.jobs](https://www.amazon.jobs/en/jobs/10560158/sr-product-manager-row-sharp)

**Meritocracy and structure**
- Amazon is flattening, with "at least 15%" more ICs per manager by end of Q1 2025 (VERIFIED_OFFICIAL) — [About Amazon](https://www.aboutamazon.com/news/company-news/ceo-andy-jassy-latest-update-on-amazon-return-to-office-manager-team-ratio)

**Interview process**
- Shopee manager interviews involve situational questions and a short case; the process took about 4 weeks (Glassdoor reviews, 2022 and 2026) (PRACTICAL_EXTERNAL) — [Glassdoor](https://www.glassdoor.com/Interview/Shopee-Interview-E1263091-RVW105033023.htm)
- Meta DS Manager loops include SQL and product-sense cases and leadership rounds (non-Brazil reviews) (PRACTICAL_EXTERNAL) — [Glassdoor BR](https://www.glassdoor.com.br/Entrevista/Meta-Entrevista-E40772-RVW89736726.htm)

**Compensation**
- One weak data point: a Shopee SP Growth Specialist role was listed at R$120K–R$180K a year. The listing had expired and is an aggregator post (PRACTICAL_EXTERNAL) — [growthtalent.org](https://www.growthtalent.org/jobs/growth-marketing/shopee/growth-specialist-social-commerce-shopee)
- levels.fyi's Uber Data Scientist page (updated 2026-10-15) shows US data only, with no Brazil rows. Level ladder: 3, 4, 5a, 5b, 6 plus hidden levels (PRACTICAL_EXTERNAL) — [levels.fyi Uber DS](https://www.levels.fyi/companies/uber/salaries/data-scientist)

### Inferences

**Red flags and risks (HYPOTHESIS unless noted)**
- **Amazon:** 5-day RTO memo (VERIFIED_OFFICIAL globally; Brazil TO_VERIFY), manager flattening, and no analytics-manager openings in Brazil.
- **Meta:** a hands-on IC/manager hybrid role with a high technical interview bar.
- **TikTok, Visa, Mastercard A&CS, Shopee, Google:** Portuguese required.
- **Samsung:** on-site, CRM-centric.
- **Keeta, 99Food:** subsidy-driven delivery war, which may cut back after the investment windows (99Food's R$2bn ran through June 2026). Expected Chinese-style operating intensity is TO_VERIFY.
- **DiDi, SHEIN:** some roles require Mandarin.
- **Uber:** the U4B S&P role is B2B; the SP Tech Center move happens in 2027.

**Four-criteria fit, provisional and based on self-descriptions only**
- Uber, Amazon and Mastercard score highest on results orientation, business-side bar and data-driven decisions.
- TikTok and Keeta signal high intensity and data focus, but meritocracy evidence is missing.
- DiDi/99 says it is data-driven, but CX-focused BI is less P&L-central.

### Gaps
- No Glassdoor Brazil ratings (overall, culture, senior leadership, % recommend): pages returned 403.
- No levels.fyi or Glassdoor salary bands for SP Analytics/DS Managers at Uber, Meta, Amazon, Mastercard, Visa, TikTok, Shopee, DiDi or Keeta: Brazil location pages returned 404 or had no Brazil rows.
- No independent 2025–2026 press on SP culture (Brazil Journal, Exame, Valor, Neofeed): not searched because the budget ran out.
- Everything above should be TO_VERIFY with employee interviews or LinkedIn network checks (read-only) before applying.
