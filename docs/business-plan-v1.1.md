# Chosen Local Business Plan V1.1

Oct 10, 2026 · Jack

Chosen Local is a Denver-first acquisition and matching network for high-ticket men's coaching, operated by AI systems with one human job at its center: Sam's calls with leads. V1.1 keeps every V1 decision, fixes the places where V1 disagreed with itself, and specifies the AI operating system that runs everything around the call.

## What changed from V1

Nothing decided in V1 was reversed. Geography, ICP, channel, journey stages, commission rate, role split, proof objective and core stack are all as written. The edits below fix places where V1 contradicted itself or left a term undefined.

| # | Issue in V1 | V1.1 correction |
| --- | --- | --- |
| 1 | Journey order differed by section. The summary put pre-sell before the match; section 4 put the match before pre-sell. | One order everywhere: call and qualify, match, pre-sell the specific coach, fit call. You cannot pre-sell a coach before you know which one. |
| 2 | "Spiff" and "commission" were used for the same thing. | "Commission" everywhere. A spiff is a bonus paid to a salesperson; this is a referral commission paid by the coach. |
| 3 | The $500 budget and the November scenario did not fit together. 90 leads on $500 is $5.56 a lead, but V1's own CAC ($200–$500) and 20 leads per sale imply $10–$25 a lead. | Both kept, with the arithmetic stated. $500 buys 20–50 leads and 1–2.5 sales. The 90-lead scenario needs $900–$2,250 of ad spend. |
| 4 | "90 leads, 5 sales" did not match the 20-leads-per-sale assumption, which gives 4.5. | Shown as about 4.5 sales and $9,000–$13,500. |
| 5 | The ICP's income floor and price floor are in tension. $2,000 a month is $24,000 a year: 30% of gross pay at $80k, 9.6% at $250k. | ICP unchanged. Flagged as the first thing the paid test must answer, and intake now captures income band so the answer is measurable. |
| 6 | "$20,000/month cash flow" was not defined and its requirements were not stated. | Defined as commissions collected in the month. It requires 7–10 closes, 140–200 leads, $1,400–$5,000 of ad spend and coach capacity for 7–10 new clients a month. |
| 7 | The "telemetry/attribution layer" was a placeholder. | Specified as the Ledger: one database Chosen Local owns that records every event from ad click to commission collected. |
| 8 | The metrics list skipped the steps the operating system controls. | Added speed-to-lead, lead-to-qualified rate, coach response time and close-reporting lag. |
| 9 | V1 did not say which work is human and which is automated. | Every stage now names its human owner and its AI system. Calls with leads are human. Everything else is AI-run with stated approval gates. |

## 1. Executive summary

Chosen Local helps men find the right local coach without sorting through internet gurus, courses and noise. Chosen Local acquires, qualifies, matches and pre-sells the buyer. Coaches fulfill the service. Chosen Local earns a performance commission when a coaching contract closes.

The business serves both sides of the market:

- **For the customer:** reduce the trust, search and decision friction involved in finding a credible local coach.
- **For the coach:** deliver qualified local prospects who are ready, financially able and meaningfully pre-sold before the coach enters the process.

**Customer journey.** Video ad → landing page → call with Chosen Local (qualification) → coach match → pre-sell → coach fit call → contract close → Chosen Local commission.

**Monetization.** For V1, Chosen Local earns a commission equal to 100% of the coach's first month of program revenue when a referred customer closes. Payment timing, attribution, cancellation, refund and clawback rules remain open and must be defined before scale.

**Operating model.** Chosen Local is run by two founders and a set of AI systems, with no planned hires before the loop is proven. Sam talks to every lead and fronts the creative. AI systems handle intake, follow-up, call notes, matching, coach operations, attribution, invoicing and reporting. Three rules govern them:

1. A human holds the conversation with the lead, the decision to vet a coach, and anything touching safety.
2. Call recordings and transcripts are processed by models running on Chosen Local's own hardware. They are not sent to third-party AI services.
3. Every AI action is written to the Ledger, and every message a prospect or coach receives traces to an approved template or a human approval.

**Initial proof objective.** Complete one full transaction profitably: acquire one qualified Denver customer, match him with the right coach, close the coaching contract, collect a $2,000+ commission, and understand every conversion step well enough to repeat it.

Denver-first. Paid acquisition first. One completed economic loop before scale. This document stays explicit about what is decided, what is a hypothesis and what is open. It is a V1 operating plan, not a claim that every assumption has been validated.

## 2. What is decided vs. still open

Six V1 decisions stand, one operating decision is added, and four hypotheses and six open areas remain. Proposed defaults for the open areas are in section 9.

| Status | Area | Current position |
| --- | --- | --- |
| DECIDED | Launch geography | Denver-first. |
| DECIDED | Consumer focus | Men seeking local, high-ticket coaching. Initial rough ICP is age 24–32, income roughly $80k–$250k, able to spend at least \~$2k/month. |
| DECIDED | Acquisition | Paid social first: Meta and/or TikTok. Sam is likely the face of the creative. |
| DECIDED | Journey | Ad → landing page → Chosen Local call (qualification) → match → pre-sell → coach fit call → close. |
| DECIDED | Revenue model | Performance commission equal to 100% of month one, initially. |
| DECIDED | Role split | Sam: positioning, acquisition, front-end sales. Jack: systems, intake, data, matching, automation, ops. Shared: sales, finance, support and partner relations. |
| DECIDED (new) | Operating model | AI systems run the business except the calls with leads. Local-first: models run on Jack's workstation wherever the task allows. |
| HYPOTHESIS | Sales ownership | Chosen Local should own most of the commercial sale, while the coach participates in the final chemistry and fit decision. |
| HYPOTHESIS | Core differentiation | Local + vetted + relational + fast will create enough trust to outperform generic online search and guru or course discovery. |
| HYPOTHESIS | Unit economics | Average commission and paid CAC can support strong contribution margin. Current rough assumptions are not validated. |
| HYPOTHESIS (new) | AI operations | AI systems can run intake through invoicing at a quality prospects and coaches accept, with founder review limited to approvals and exceptions. Not validated. |
| OPEN | Coach supply | Who is on the roster, which categories launch first, capacity, pricing, geography, and whether coaches must be male. |
| OPEN | Vetting standard | What "Chosen Local vetted" concretely means. |
| OPEN | Coach incentives | Lead acceptance, response standards, exclusivity, capacity commitments, and what happens when a coach rejects a match. |
| OPEN | Commission mechanics | Payment timing, refunds, cancellations, financing, attribution window, chargebacks, renewals. |
| OPEN | Safety boundaries | Which coaching needs are in or out of scope, and how clinical, crisis, addiction or other licensed-care needs are handled. |
| OPEN | Founder story | Which parts of Sam and Jack's recovery experiences are used publicly and how they are framed. |

## 3. Problem, positioning and brand thesis

**The consumer problem.** Men looking for meaningful help online face a trust problem. Search is noisy. Coaching quality is hard to evaluate before buying. Large audiences, polished content and expensive courses are not the same thing as a credible human fit.

**The coach problem.** A capable coach may be strong at delivery but weak at consistent acquisition, qualification, follow-up or paid distribution. Chosen Local becomes an independent acquisition layer, so the coach does not have to become a marketer.

**Working positioning**

- **Consumer-facing promise:** Tell us what you are trying to change. We will help you find a vetted local coach who actually fits.
- **Coach-facing promise:** We bring you qualified local prospects who are ready and financially able to buy coaching. You pay us when one becomes a customer.

**Founder story as trust architecture.** Chosen Local is not positioned as a detached directory. Sam and Jack both have intense personal recovery histories and firsthand experience of how hard and how important it is to find the right human support. That history explains why Chosen Local cares about trust, fit and real relationships. The exact public story remains to be written, and it does not substitute for a real coach-vetting standard.

**How an AI-run company keeps a human promise.** The brand sells a real person and a real relationship, so the automation has to stay behind that promise. Four commitments follow:

- A prospect always speaks with a human. No AI voice calls and no chatbot posing as a person.
- Automated messages are sent as Chosen Local. A message carries Sam's name only when Sam wrote or approved it.
- The founders write the founder story. AI systems do not draft, extend or embellish their recovery histories.
- "Vetted" is a record. Every claim made about a coach must trace to an item in that coach's vetting file.

**Important boundary.** Chosen Local does not position coaching as a replacement for therapy, licensed mental-health care, addiction treatment, crisis support or medical care. V1 needs a clear scope and a referral and escalation policy before launch. Section 7 describes how the systems enforce it.

## 4. Product and customer journey

**What the customer is actually buying.** The customer is not buying "lead routing." He is buying confidence in choosing the right person. Chosen Local's product is the reduction of search, trust and fit friction between "I need help" and "this is the coach I should meet."

**V1 journey.** Eight stages, each with one human owner and one AI system. The systems are specified in section 7.

| # | Stage | What happens | Human | AI system |
| --- | --- | --- | --- | --- |
| 1 | Acquire | Sam-led paid social creative drives a Denver prospect to a focused landing page. | Sam films. A founder approves budget changes. | Drafts scripts and variants, checks copy against platform rules, reports performance daily. |
| 2 | Intake | The prospect submits enough information to justify a conversation. Jack owns the intake schema. | None. | Records the lead, sends a booking link within one minute, pre-scores fit, screens for out-of-scope needs. |
| 3 | Chosen Local call | Chosen Local understands the problem, urgency, budget, goals and readiness, and begins the commercial sale. | Sam runs the call. | Writes a one-page brief before the call. Afterward transcribes it locally and extracts need-state, budget, urgency, readiness and objections into the record. |
| 4 | Match | The prospect is matched to a coach on need-state, fit, availability, budget, geography and, later, performance data. | Sam confirms the match. | Filters the roster and ranks two or three coaches with reasons. Runs once on intake data before the call and again after it. |
| 5 | Pre-sell | Chosen Local frames why the recommended coach fits and tries to get the customer most of the way to a decision. | Sam delivers it, on the call when the pre-call match holds. | Drafts the "why this coach" summary from the vetting file and the prospect's own words, and the follow-up message. |
| 6 | Coach fit call | The prospect meets the coach before committing. This validates chemistry and final fit. | The coach. | Sends the coach a handoff brief, books the call, reminds both sides, tracks the coach's response time. |
| 7 | Close | The customer signs the coaching agreement. V1 must determine whether payment and contract run through the coach or Chosen Local. | The coach, with Sam if needed. | Checks in with the customer and the coach on a schedule until an outcome is recorded. |
| 8 | Commission + learning | Chosen Local records the close, collects the commission and feeds outcome data back into matching and acquisition. | Jack approves the invoice. | Drafts the invoice, follows up on payment, records refunds, updates the funnel report. |

**Sales architecture to validate.** The working target is for Chosen Local to own as much of the sale as possible, without violating the obvious reality that a high-ticket coaching buyer will want to meet the coach before committing. V1 measures how much "closing" happens before the fit call and how much depends on the coach. The call system makes this measurable: every call record carries a readiness rating before the fit call, compared later against whether the customer closed.

## 5. Initial market and go-to-market

**Initial customer hypothesis.** Denver men, initially concentrated around ages 24–32, with meaningful discretionary income and willingness to spend at least \~$2,000 a month on coaching. The rough plan estimates a qualified local audience of \~37,500. That figure is an internal hypothesis until market analysis validates it.

One tension to test early: $2,000 a month is 30% of gross income at $80k. The ICP stands, but intake records an income band and the funnel report shows close rate by band, so the first test says whether the low end of the range buys.

**Need-state, not "male coaching," organizes demand**

- Career, leadership, entrepreneurship
- Fitness, performance, accountability
- Dating, relationships
- Life direction, discipline, personal development
- Other coaching categories only as coach supply and safety boundaries become clear

Every ad, lead and coach is tagged with one need-state. That tag is what lets the Ledger connect a message to a buyer to a coach to a close.

**Paid acquisition test**

- Primary launch channels: Meta and TikTok paid social.
- Sam is likely the face of the first creative because he is local and can produce high-quality video in-house.
- Founder-led creative leads with the actual trust and problem insight, not "find a coach near you" directory language.
- Initial paid budget: \~$500, used as a learning budget, not as evidence of scale economics.

**V1 acquisition question.** Can Chosen Local acquire a qualified coaching buyer for materially less than the first-month commission earned on the eventual sale?

**What $500 can answer.** At V1's own assumptions it buys 20–50 leads. That is enough to measure cost per lead, booking rate and show rate, and to hear real objections on calls. It is not enough to establish a close rate: the expected result is one or two sales.

**How AI runs acquisition.** Each week the acquisition system drafts hooks and scripts, drawing on the anonymized language prospects use on calls. Sam chooses and films. The system builds the variants, reports cost per lead and cost per booked call by ad every morning, and recommends what to pause or extend. A founder approves any change in spend.

**Platform and consent checks before the first dollar is spent**

- **Meta data-source category.** Meta sorts advertiser domains into categories, and a domain classed as health and wellness can lose standard events such as Lead. The Conversions API does not bypass this ([Stape](https://stape.io/news/meta-data-sharing-restrictions-healthcare)). Check the category in Events Manager once the pixel is live and request a review if it is wrong. Keep landing-page copy and event data free of health language either way.
- **Ad copy about the viewer.** Meta restricts copy that asserts or implies personal attributes of the person seeing the ad. This plan did not re-verify the current wording. The copy check in the acquisition system should be built from the live policy text.
- **Consent.** The intake form collects written consent to be called and texted, and every call opens with a recording disclosure. Have counsel confirm the form language.
- **Text registration.** US carriers require business texting to be registered before messages deliver reliably. Start that registration in Close during Phase 2, since approval takes time.

**Organic and referrals.** Organic social, local partnerships and referrals can support trust and reduce CAC over time. V1 does not require an audience-building strategy before the paid funnel is tested.

## 6. Business model and unit economics

**Revenue model.** Chosen Local earns a one-time performance commission when a referred prospect closes into a coaching contract. The V1 working rule is a commission equal to 100% of the first month of the coach's program.

**Example.** If a coach sells a $5,000/month program and a Chosen Local prospect becomes a customer, Chosen Local earns a $5,000 commission. If the program is $2,500/month, the commission is $2,500. Exact payment mechanics remain to be decided.

**Current rough assumptions, not validated.** The middle column is V1 as written. The right column is what those numbers imply when worked through together.

| Assumption | V1 hypothesis | What it implies |
| --- | --- | --- |
| Startup test budget | $500 | 20–50 leads and 1–2.5 sales at the assumptions below |
| Average commission | $2,000–$3,000 in the rough plan; market research pending | Coach programs priced at $2,000–$3,000 a month |
| Lead-to-sale | \~20 leads per sale | 5% of leads become customers |
| Initial CAC | $200–$500 per customer | $10–$25 per lead |
| Short-term proof | 1 sale worth at least \~$2,000 | Returns the test budget four times over |
| Rough November scenario | 90 hot leads → 5 sales → \~$12,500 revenue | About 4.5 sales and $9,000–$13,500, on $900–$2,250 of ad spend |
| 90-day aspiration | $20,000/month cash flow by month 3 | 7–10 closes, 140–200 leads and $1,400–$5,000 of ad spend a month |

**Contribution margin.** On a $2,500 commission, a $200–$500 CAC leaves $2,000–$2,300 before calling costs, software and founder time. The AI operating model keeps those other costs low: section 8 puts the software stack at tens of dollars a month.

**How much room the model has.** With a $2,000 commission, ad spend breaks even when cost per lead times leads per sale equals $2,000. At $25 a lead that tolerates 80 leads per sale. At $100 a lead, four times V1's upper assumption, it tolerates exactly the 20 leads per sale V1 assumes. Lead cost has slack. Close rate and coach capacity are more likely to bind.

**What $20,000 a month requires beyond ad spend.** Sam handles 32–46 new leads a week. The roster absorbs 7–10 new clients a month. Ad spend goes out weeks before commissions come in, so the commission payment terms in section 9 set how much working capital growth needs.

**Metrics that determine viability**

- Cost per qualified lead
- Speed-to-lead: minutes from form submission to first contact
- Lead → qualified rate
- Qualified lead → Chosen Local call booking rate
- Show rate
- Chosen Local call → coach fit call rate
- Coach response time to a match
- Coach fit call → paid customer close rate
- Close-reporting lag: days from close to Chosen Local knowing
- Average collected commission
- Refund, cancellation and clawback rate
- Contribution margin per closed customer

The Ledger computes all twelve from recorded events. Nobody maintains them by hand.

**Economic truth.** If the first-month commission is consistently much larger than the fully loaded cost to acquire and convert a customer, the model can scale. If not, brand work and marketplace complexity do not matter.

## 7. AI operating system

The business runs on one database, one orchestrator and one local model server. Eleven systems sit on top of them, one for each job that would otherwise need a hire. Sam's calls are the only step in the customer journey with no AI in the conversation.

```
Tools:   Ads (Meta, TikTok) | Landing page | Close | QuickBooks | Slack
              ^  webhooks and API calls, both ways  v
Cloud:   n8n orchestrator  |  Ledger (Postgres)  |  Job queue
              v  jobs pulled over an outbound connection   ^ structured results
Local:   Worker  |  Language model (Qwen3.6 27B)  |  Transcription
```

Every tool reports to the Ledger through n8n. Model jobs wait in the queue until the workstation pulls them, and results come back as structured records.

### Design rules

1. **The Ledger is the source of truth.** Every event, from ad click to commission collected, is written to one database Chosen Local owns. Close, Meta and QuickBooks are tools that read from it and write to it. This is the compounding asset V1 describes, built from day one.
2. **Always-on work runs in the cloud. AI work runs on the local machine.** A lead who submits the form at 3 a.m. gets a response whether or not the workstation is on. Model jobs wait in a queue and run when it is.
3. **Nothing time-critical waits on a model.** The first response to a lead is a template sent in under a minute. The AI brief arrives later.
4. **Rules where rules work, models where language is involved.** Routing, scheduling, reminders and invoicing are deterministic. Models summarize, extract, draft and explain.
5. **Model output that becomes data is structured and traceable.** Each extracted field is stored with the transcript passage it came from, the prompt version and the model.
6. **Approval scales with risk.** Low-risk, reversible, templated actions run on their own. Anything a prospect reads as personal, anything about money and anything about safety passes a founder.

### The eleven systems

| # | System | Job | Autonomy |
| --- | --- | --- | --- |
| 1 | Ledger and orchestration | Records every event and routes work between tools, the queue and the founders. | Auto |
| 2 | Intake and speed-to-lead | Writes the lead to the Ledger and Close, sends the booking link, runs reminders and no-show recovery from templates. | Auto |
| 3 | Safety screen | Flags crisis, clinical, addiction and medical language at intake and in transcripts. Stops automation on that lead and alerts a founder. | Auto flag, human response |
| 4 | Call system | Pre-call brief, local transcription, structured extraction into the record, follow-up draft. | Notes auto, messages draft |
| 5 | Matching engine | Filters the roster on scope, capacity, budget and geography, then ranks two or three coaches with written reasons. | Recommend |
| 6 | Pre-sell and handoff | Drafts the "why this coach" summary for the prospect and the handoff brief for the coach. | Draft |
| 7 | Coach operations | Recruiting research and outreach drafts, vetting-file assembly, weekly capacity check, response-time tracking. | Capacity check auto, outreach draft, vetting human |
| 8 | Close tracking and commission | Scheduled check-ins with customer and coach until an outcome is recorded, invoice draft, payment follow-up, refund records. | Check-ins auto, invoice approved |
| 9 | Acquisition | Hooks and scripts, ad variants, copy check, daily performance report, spend recommendations. | Draft and recommend |
| 10 | Learning loop | Weekly operating review: funnel by ad, need-state and coach, anomalies, proposed experiments. | Auto report |
| 11 | Back office | Bookkeeping preparation, monthly close summary, upkeep of procedures in Notion. | Draft |

**Autonomy levels.** *Auto* acts without review. *Draft* prepares the work and a founder approves it with one click, from a Slack alert that links to the review screen when the draft contains call content. *Recommend* proposes a decision and a founder decides. *Human only* covers calls with leads, the decision to vet a coach, responses to safety flags, the founder story, contract terms and any movement of money.

### Guardrails

- **Templates only, at first.** In V1 no model-written free text reaches a prospect without approval. Automated messages are fixed templates with fields filled in.
- **Vetting file or silence.** No system states anything about a coach that is not in that coach's vetting file.
- **Safety stops the line.** A safety flag pauses every automated message to that lead until a founder clears it. No system gives advice on mental health, medication, addiction or medical questions. The scope and escalation policy itself is still open (section 9); the systems enforce whatever it says.
- **Spend can go down by itself, never up.** The acquisition system may pause an ad that breaches its cost limit. Raising a budget takes a founder.
- **Prospect conversations stay local.** Recordings and transcripts are processed on the workstation and stored encrypted, with a written retention period. Founders view call content on a review screen reachable only over their private network. Slack alerts carry IDs and links, never call content.
- **Everything is logged.** Inputs, outputs, prompt version and approver are written to the Ledger.

### Quality control

For the first 20 calls Sam checks every extracted record against what he heard, and each correction is saved. Those calls become the test set: any change to a prompt or model is run against them before it goes live. Every match Sam confirms or overrides is logged the same way.

### What the matching engine is and is not in V1

With a handful of coaches and no closes yet, there is nothing to learn from statistically. V1 matching is hard filters, Sam's judgment, and a model that writes out the reasoning so it can be checked. Its real job in V1 is to capture each decision and outcome in a consistent shape, so that learned matching becomes possible once there are dozens of outcomes per need-state.

### When something is down

If the workstation is off, model jobs queue and nothing a prospect sees breaks. Slack alerts a founder when the oldest job is more than a few hours old. If Close is unreachable, leads still land in the Ledger.

## 8. Operations and technology

The V1 stack stays. Two things are added to it: the Ledger and a local AI server. Close and the cloud server together cost roughly $15–$75 a month plus call minutes.

### Role split

| Owner | Primary lane | Responsibilities | With the AI systems |
| --- | --- | --- | --- |
| Sam | Market + customer | Positioning, offer framing, paid acquisition, creative, founder-facing trust, front-end qualification and pre-sale, customer insight. | His week is calls, filming and approving drafts. Briefs, notes, follow-ups and ad reports arrive prepared. |
| Jack | Systems + operations | Intake architecture, CRM, automation, data, matching logic, routing, attribution, partner operations, telemetry. | Builds and supervises the eleven systems, owns the guardrails and the test set, approves invoices. |
| Shared | Commercial + company | Sales, coach relationships, finance, support, business-model decisions, learning loop. | Both read the weekly operating review and decide the next experiment from it. |

### Operating stack

| Tool | Role | Change from V1 |
| --- | --- | --- |
| [Close](https://www.close.com/pricing) | Where Sam works: calls, texts, email, pipeline, recordings. | Kept. One seat, for Sam. Jack and the systems use the API, which every plan includes. |
| [n8n](https://www.promptquorum.com/power-local-llm/n8n-review) | Orchestrator: receives webhooks, runs the templated flows, hands model jobs to the queue. | Kept. Self-host the free Community edition on a small cloud server. |
| Ledger (Postgres) | Source of truth: leads, events, calls, coaches, vetting items, matches, outcomes, commissions, ad spend. | New. This is V1's "telemetry/attribution layer," specified. Runs beside n8n. |
| Local AI server | Transcription, extraction, briefs, drafts, match reasoning, reports. | New. Jack's workstation: RTX 5090 (32 GB) and Ryzen 9 9950X. |
| Slack | Internal and partner communication, plus alerts that link to drafts and flags. Alerts carry IDs, never call content. | Kept, with the approval role added. |
| Notion | Operating knowledge and procedures. | Kept. |
| QuickBooks | Accounting and commission invoices. | Kept. |
| Landing page and scheduler | Intake form and call booking. | Unspecified in V1. Any pair that can post to a webhook works. |

**Close plan.** Solo is $19 a month ($9 billed annually) for one user and up to 10,000 leads. Essentials is $49 ($35 annually). Close's own automated workflows start at the $109 Growth plan and are not needed, because n8n does that work. Solo and Essentials keep call recordings for 30 days, so the call system copies each recording to local storage the day it is made.

### Should Chosen Local build its own CRM?

Not now. The hard part of a sales CRM is telephony: calling, recording, texting, carrier registration and deliverability. Close provides that for one seat at the prices above. The part worth owning is the data and the logic, and the Ledger already gives you that: custom records for coaches, matches and commissions, full history, and direct access for the AI systems. Close also runs an [official MCP server](https://developer.close.com/mcp/) with read-only and read-write scopes, so an agent can work inside the CRM without custom integration code.

Revisit when Close's seat cost or data model starts to block something. The candidate then is Twenty, the self-hosted CRM rated highest in [Marmelab's 2026 open-source benchmark](https://marmelab.com/blog/2026/01/09/open-source-crm-benchmark-2026.html). It has REST and GraphQL APIs and an AGPL license. That review lists no telephony, so a calling tool would still be needed alongside it.

### Local AI server

| Job | Model | Why it fits |
| --- | --- | --- |
| Language work | [Qwen3.6 27B](https://blog.buildfastwithai.com/qwen3-6-27b-review-2026), 4-bit | Apache 2.0, about 17 GB, tool calling supported. Leaves about 15 GB of the card for context and a second model. |
| Transcription | [Parakeet or Whisper large-v3](https://openwhispr.com/blog/parakeet-vs-whisper-vs-nemotron) | Parakeet's English model is about 630 MB at 5.9% word error. Whisper is larger and covers 99 languages. Either runs beside the language model. |

- **Speed.** One published benchmark reports 60–90 tokens a second for this model on an RTX 5090 ([Local AI Master](https://localaimaster.com/vram/best-llm-32gb-vram)). That is not a Chosen Local measurement. At that rate a call summary takes seconds.
- **Serving.** Run the model behind llama.cpp's server, which exposes an OpenAI-compatible API that the worker calls. One guide reported the model failing in Ollama at release in April 2026, so test before relying on it.
- **Speakers.** Neither transcription model labels who is speaking. The call system needs a speaker-separation step so the extraction knows Sam from the prospect.
- **Connection.** A worker on the workstation pulls jobs from the cloud queue over an outbound connection. No port is opened on the home network.
- **Cloud models.** The local rule is about prospect data. Creative and strategy work that contains none of it may use a cloud model when quality matters more.
- **Model changes.** Newer models will ship. A swap is made only when it scores better on the test set from section 7.
- **Backups.** The Ledger is backed up nightly, and the workstation is never the only copy of anything.

### The compounding asset

If Chosen Local works, the long-term advantage is not merely a roster of coaches. It is data about which acquisition messages attract which need-states, which prospects convert, which coach and customer pairings close, and which pairings retain and succeed. Matching quality improves as the system observes real outcomes. The Ledger is where that data accumulates, and it belongs to Chosen Local regardless of which CRM or model is in use.

## 9. Open questions, risks and preconditions to scale

All eight open questions from V1's section 8 remain open. Each now has a proposed default so the systems can be built against something concrete. The defaults are proposals for Sam and Jack to accept or change. They are not decisions.

| Open area | Question to resolve | Proposed default to test |
| --- | --- | --- |
| Coach supply | Which categories launch first? How many coaches are needed before paid acquisition begins? What minimum price, program duration, capacity and geography are required? | Two need-states, with at least two coaches in each before ads run, so one decline or a full calendar does not strand a qualified buyer. Programs of three months or more at $2,000+ a month. |
| Vetting | What evidence qualifies someone as "Chosen Local vetted"? Interview, references, outcomes, testimonials, credentials where relevant, background checks, trial session, or another standard? | A five-item file: recorded founder interview, two client references a founder speaks to, evidence of paying clients, written program and price, signed partner agreement including the scope boundary. The system assembles it. A founder signs it. |
| Final close | Who owns the contract and payment? What must Chosen Local sell before the fit call? What is the coach expected to do on the final call? | The coach contracts with and collects from the customer. Chosen Local invoices the coach. This keeps Chosen Local from holding client money. |
| Coach incentives | Why should strong coaches accept the commission? What response SLA, lead acceptance, availability, exclusivity and reporting obligations apply? | Accept or decline a match within 24 hours. Hold the fit call within 72 hours. Confirm capacity weekly. Report the outcome within 48 hours of the fit call. No exclusivity in V1. |
| Commission terms | When is commission earned and paid? What happens with refunds, cancellations, financing, chargebacks, renewals and delayed closes? | Earned when the customer's first payment clears. Invoiced that day, due in 7 days. Attribution runs 90 days from introduction. A refund inside 30 days reverses the commission. No commission on renewals. |
| Customer ownership | Is the buyer fundamentally a Chosen Local customer matched to a coach, or the coach's customer referred by Chosen Local? The former creates more marketplace leverage but requires clearer operational design. | Whichever is chosen, Chosen Local keeps a direct line to the customer through its check-ins after the match. That line is what makes a close verifiable. |
| Safety | How are crisis, addiction-treatment, clinical mental-health or medical needs identified and routed away from ordinary coaching? | A written out-of-scope list. A flag pauses automation and a founder responds personally with a short list of licensed and crisis resources, including the 988 Suicide & Crisis Lifeline. A licensed clinician reviews the list and the screening questions before launch. |
| Founder story | What can Sam and Jack share authentically without turning private recovery histories into performative marketing? | The founders write it themselves and record in writing what stays private. No AI system drafts it. |
| Legal review (new) | Which documents need a lawyer before launch? | One review covering intake consent language, the call-recording disclosure, the partner agreement and the commission terms. |

One note for that legal review: Colorado's replacement AI law applies to automated tools used in "consequential decisions" made on or after January 1, 2027, across seven categories that include health care but not coaching ([Vaquill summary](https://www.vaquill.ai/blog/colorado-ai-act-explained-from-the-statute)). That is one more reason to keep clinical needs out of scope. This is general information, not legal advice.

### Primary risks

| Risk | How the operating system addresses it |
| --- | --- |
| Poor coach-client fit damages Chosen Local trust. | Match reasons are logged. The customer gets a check-in 30 days in. Outcomes are added to the coach's file. |
| A coach closes poorly, so Chosen Local carries acquisition cost without earning commission. | The weekly review shows fit-call-to-close rate by coach. A coach below the agreed rate after several introductions is paused. |
| A coach bypasses attribution or fails to report a close. | Check-ins go to the customer as well as the coach after the fit call. The partner agreement sets the reporting duty. |
| Customers expect clinical or crisis support that is outside coaching scope. | The safety screen runs at intake and on every transcript, and stops automation on a flag. |
| Paid acquisition produces interest but not buyers with sufficient budget or readiness. | Intake captures income band and readiness. Ads are judged on cost per qualified lead and per booked call, not per click. |
| Month-one-only commission is unattractive to coaches with weak retention or short programs. | The proposed three-month minimum leaves a coach at least two months of revenue per client. |
| An automated message misfires or reads as impersonal. (new) | Templates only in V1. Anything personal passes a founder. |
| An extraction error puts wrong facts in a record. (new) | Each field keeps its source passage. Sam reviews the first 20 calls. Changes are tested before release. |
| Sensitive conversation data leaks. (new) | Local processing, encrypted storage, a written retention period, access limited to the two founders. |
| Only Jack can repair the systems. (new) | A runbook in the code repository, mirrored to Notion. Every system fails back to manual work: Sam can run the whole journey inside Close. |
| The automation is built ahead of the proof. (new) | The build order in section 10 is tied to the loop. Nothing beyond the minimum is built until a sale closes. |

### Preconditions to launch the paid test

- [ ] Partner agreement signed by every launch coach
- [ ] Commission terms decided and written into that agreement
- [ ] Vetting file complete for every launch coach
- [ ] Scope and escalation policy written and reviewed
- [ ] Intake consent language and recording disclosure reviewed
- [ ] Text-message registration approved

## 10. V1 launch plan and milestones

The four V1 phases stand. Each now carries the systems to build during it and a gate that must pass before the next phase starts. Ten of the eleven systems are stood up in a minimal form for the first loop. The back office and the fuller versions of the others wait until a sale closes.

| Phase | Business work (from V1) | Systems built | Gate to pass |
| --- | --- | --- | --- |
| 1. Supply and offer | Define launch coaching categories and recruit the minimum viable roster. Define the vetting standard and partner agreement. Document program prices, duration, capacity and the coach close process. | Ledger with coach and vetting records. Local model server running. Coach operations: recruiting research, outreach drafts, vetting-file assembly. | Two coaches per launch need-state, each with a complete vetting file and a signed agreement. |
| 2. Funnel and system | Build the landing page and intake flow. Build CRM, matching, attribution, partner tracking and follow-up. Create Sam-led creative for Meta and/or TikTok. Define the sales script and coach fit-call handoff. | Intake and speed-to-lead. Safety screen. Call system. Matching engine as filters plus written reasons. Handoff templates. Script drafts and copy check. | A founder's test lead travels the whole journey: booking link in under a minute, call transcribed and extracted, match recommended, handoff brief sent, test invoice drafted. |
| 3. Paid test | Launch approximately $500 of paid acquisition as a learning test. Track every step from impression through commission collected. Intervene manually where necessary. | The daily ad report, the one new piece. Sam checks the first 20 call records. Whatever breaks is fixed. | 20–50 leads handled. Cost per lead, booking rate and show rate are known. |
| 4. Prove one loop | Acquire one qualified buyer. Complete one successful match. Close one coaching agreement worth at least \~$2,000 in commission. Measure CAC, close behavior, coach handoff and customer experience. | Close tracking and commission, used on a real sale. The first weekly operating review. | Commission collected, with every step's numbers in the Ledger. |

**Why build any of it before the first sale.** At 20–50 leads Sam could do every step by hand, and V1 is right that the test optimizes learning, not automation purity. The minimum build earns its place for a different reason: it records each step the same way every time, so the second loop can be compared with the first.

**After the first loop.** Built only once a sale has closed: the back office system, automatic capacity checks, spend recommendations, model-drafted personal messages under approval, and matching that learns from outcomes.

**Scale decision.** Do not optimize for 200 customers before proving one repeatable economic loop. Once one sale is completed, repeat it. Once several are profitable, increase paid spend and deepen the roster. If the economics fail, change the acquisition, sales architecture, supply or business model before adding complexity.

**Current ambition.** Be live quickly, prove the marketplace transaction, and work toward approximately $20,000 a month in collected commissions by month three, only if the early acquisition and conversion economics justify reinvestment. Section 6 lists what that level requires.

## 11. Expansion logic

Chosen Local expands only after the Denver male-coaching model proves that the acquisition, matching, trust and commission engine works.

- Broaden need-states and coach categories within Denver.
- Broaden male age segments if the acquisition economics support it.
- Expand regionally city by city, preserving the local relationship layer.
- Explore recurring partner economics or retainers only after performance commissions are proven.
- Consider adjacent local matching and referral categories only after separate market, legal and regulatory analysis.

**What the operating system changes about expansion.** A new city needs a coach roster, local creative and a person to take calls. It does not need a new back end: the Ledger, the eleven systems and the test set carry over unchanged. The constraint on growth is therefore call capacity and coach supply, and the first hire, when one is justified, is a second caller.

**Long-term asset.** The most defensible version of Chosen Local is not a directory. It is a trusted local brand plus an increasingly intelligent acquisition-and-matching system that knows which people need what, which coaches fit them, and which pairings produce durable outcomes.

End of V1.1 business plan.

## Sources

Pages opened on October 10, 2026 for the facts added in V1.1. Everything else comes from the V1 plan.

- [Close pricing](https://www.close.com/pricing): plan prices, user and lead limits, recording retention, API access
- [Close MCP server](https://developer.close.com/mcp/): server address, authentication, scopes
- [Marmelab open-source CRM benchmark 2026](https://marmelab.com/blog/2026/01/09/open-source-crm-benchmark-2026.html): Twenty's rating, APIs, license
- [n8n review, PromptQuorum](https://www.promptquorum.com/power-local-llm/n8n-review): Community edition license and AI nodes
- [Best LLM for 32GB VRAM, Local AI Master](https://localaimaster.com/vram/best-llm-32gb-vram): model size and speed on an RTX 5090
- [Qwen3.6-27B review, Build Fast with AI](https://blog.buildfastwithai.com/qwen3-6-27b-review-2026): license, file size, tool calling, serving notes
- [Parakeet vs Whisper vs Nemotron, OpenWhispr](https://openwhispr.com/blog/parakeet-vs-whisper-vs-nemotron): transcription accuracy, size, licenses
- [Meta data sharing restrictions, Stape](https://stape.io/news/meta-data-sharing-restrictions-healthcare): data-source categories and blocked events
- [Colorado AI law explained, Vaquill](https://www.vaquill.ai/blog/colorado-ai-act-explained-from-the-statute): effective date and covered decision categories
