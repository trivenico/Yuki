# Phase 162 — Income Operations and Platform Integrations

## Product objective

Yuki should help its owner build legitimate, diversified online income streams by discovering opportunities, validating demand, creating useful products or services, preparing listings and proposals, fulfilling approved work, supporting customers, and measuring actual net results.

This is an income-operations capability, not a promise of earnings or a mechanism for bypassing platform rules. No platform can be assumed to permit unattended bots. Each integration must be reviewed against the platform's current terms, API policy, automation rules, identity requirements, tax rules, and payment rules before activation.

## Existing foundation and gap

The repository already contains planning-only or local-only skills such as:
- `skills/monetization_advisor.py`: evaluates ideas and validation experiments; it does not launch campaigns.
- `skills/content_marketer.py`: creates content briefs; it does not publish or perform outreach.
- `skills/seo_optimizer.py`: local heuristic checks; it does not fetch live keyword or ranking data.
- `skills/financial_manager.py`: manual local ledger; it does not connect to banks or move money.

These are useful starting points, not live marketplace integrations. Do not describe them as autonomous income generation.

## Opportunity portfolio

The opportunity catalog should group platforms by the economic activity they support, not by a hard-coded claim that every platform can be automated.

| Category | Example opportunity types | Yuki-supported workflow |
| --- | --- | --- |
| Freelance services | Software, design, writing, translation, research, video editing, virtual assistance | Find briefs, assess fit, draft tailored proposals, prepare deliverables, track milestones |
| Expert and consulting marketplaces | Advisory, tutoring, coaching, specialist consulting | Package expertise, prepare profile and session materials, manage approved bookings |
| Digital products | Templates, ebooks, courses, design assets, code, datasets where rights permit | Research demand, create original assets, quality-check, prepare listings and updates |
| Creator monetization | Video, newsletters, podcasts, blogs, memberships | Editorial calendar, original content production, repurposing, analytics and sponsor research |
| Affiliate and referral programs | Product referrals, software partnerships, creator affiliate programs | Compare terms, disclose relationships, create compliant content, attribute conversions |
| E-commerce | Print-on-demand, handmade goods, physical products, marketplace storefronts | Product research, listing drafts, catalog quality, inventory and customer-service workflows |
| Software and app marketplaces | Apps, extensions, plugins, SaaS, API products | Build, test, document, package, prepare release and support |
| Developer ecosystems | Bug bounties, issue bounties, grants, hackathons, open-source sponsorship | Discover scoped work, assess authorization, create patches, test, submit for review |
| Research and testing | User research, usability tests, paid studies, QA tasks | Match eligibility, prepare participation, organize findings and submissions |
| Licensing and royalties | Photography, illustration, music the owner has rights to, stock assets, code licensing | Rights/provenance checks, metadata, packaging, royalty tracking |
| Education | Courses, tutoring, study materials, workshops | Course design, lesson generation, assessment and learner support |
| Local-to-online services | Remote admin, booking, appointment and specialist services | Lead research, approved communications, scheduling and delivery |

Examples are categories, not endorsements or a guarantee that a particular platform is currently available, legal in the owner's jurisdiction, or open to automation.

## Agent team

Build specialist roles behind the existing task runtime and central policy enforcement. Agents should produce typed artifacts and hand off work; they must not independently gain credentials or permissions.

1. Opportunity Scout — discovers platforms, programs, briefs, grants and demand signals.
2. Platform Policy Analyst — verifies current terms, API availability, automation restrictions, identity and payout requirements.
3. Market Research Analyst — estimates competition, customer demand, pricing and evidence quality.
4. Business Portfolio Planner — balances time, risk, costs and concentration across opportunities.
5. Offer Designer — packages services, product tiers, scope, delivery and support.
6. Product Studio — creates original digital products, code, designs and educational assets.
7. Content Studio — drafts original content, media briefs and editorial plans.
8. Listing Optimizer — prepares accurate titles, descriptions, metadata and accessible media.
9. Proposal Specialist — drafts tailored, truthful proposals based on verified capabilities.
10. Delivery Engineer — fulfills accepted work in an isolated workspace with tests and review.
11. Customer Success Agent — drafts support replies and tracks issues, refunds and service levels.
12. Distribution Analyst — plans compliant SEO, newsletters, social publishing and affiliate disclosures.
13. Revenue Analyst — reconciles platform statements, fees, refunds, costs and net revenue.
14. Compliance Sentinel — blocks prohibited, deceptive, spammy, infringing or unauthorized workflows.
15. Portfolio Evaluator — compares realized net return, conversion, time spent and opportunity cost.

The initial implementation should be a small number of reliable orchestrated roles. Do not instantiate 100 agents merely to satisfy a count; scale specialist agents only where evals show a measurable benefit.

## Platform adapter contract

Create a versioned platform registry. Each platform adapter must declare:
- stable platform ID, canonical domains and supported countries/regions;
- activity categories and account prerequisites;
- official API / OAuth availability and documented scopes;
- allowed automation modes: `official_api`, `user_present_browser`, `draft_only`, or `unsupported`;
- actions and exact permissions required for each action;
- actions requiring owner approval, including application, proposal submission, publication, pricing changes, customer messages, accepting contracts, refunds, withdrawals and payout changes;
- rate limits, pagination, webhook support, data retention and deletion semantics;
- platform terms/policy URL, policy review date and revalidation deadline;
- credential type and secret-storage reference (never the secret itself);
- test/sandbox mode, health state, known limitations and adapter version.

Default unknown or unreviewed platform capability to `unsupported`. A browser login being possible does not mean automation is permitted. Prefer official APIs and OAuth; use browser automation only where platform policy permits it and preserve user-visible, owner-controlled sessions. Never bypass CAPTCHAs, MFA, anti-bot controls, paywalls, geographic restrictions or identity checks.

## Workflow and approval boundaries

### Safe to automate after setup
- public opportunity discovery within platform access rules;
- market research and comparison;
- drafting profiles, proposals, listings, content and customer replies;
- creating product files in a private workspace;
- test execution, QA, documentation and analytics aggregation;
- tracking deadlines and preparing reports.

### Requires explicit owner approval for each material action
- submitting a proposal/application or accepting a contract;
- publishing or materially editing a listing, post, product or campaign;
- sending messages to customers, leads or platform members;
- committing to scope, delivery dates, prices, refunds or service guarantees;
- purchasing ads, subscriptions, inventory, leads or paid tools;
- changing payout/bank/tax details or withdrawing funds;
- accepting legal terms, identity attestations or tax declarations.

Approval must bind to the exact platform, account, action, target, content/amount, and expiry. Any material change invalidates approval. Use the durable approval and audit infrastructure; do not rely on process-local one-shot approvals for money-moving or externally visible actions.

## Hard safety and integrity rules

- Accounts remain owned and controlled by the human owner; Yuki never fabricates identity, credentials, reviews, experience, qualifications, location or eligibility.
- No fake engagement, fake reviews, click fraud, bot traffic, account farming, referral abuse, scraping against terms, spam, mass unsolicited messaging, impersonation, plagiarism, copyright infringement, or evasion of platform controls.
- Respect platform-specific disclosure requirements for affiliate links, sponsorships, AI-generated content and commercial relationships.
- Use only assets the owner created, licensed or is otherwise authorized to use. Track provenance and licenses.
- No automatic acceptance of contracts, legal terms, paid commitments, refunds or payouts.
- Keep customer data minimal, purpose-limited, access-controlled and subject to deletion/retention rules.
- Never expose API keys, cookies, OAuth tokens, payout data or identity documents to LLM context, logs, traces or agent-to-agent messages.
- Enforce per-platform rate limits, idempotency keys, retry budgets and duplicate-submission protection.
- Provide a global kill switch and per-platform disable/revoke control.

## Income measurement

The revenue ledger must distinguish:
- gross sales / contract value;
- platform fees and payment processing fees;
- refunds, chargebacks and reversals;
- direct costs, advertising spend and subscriptions;
- taxes reserved (not tax advice);
- net realized revenue and payout settlement status;
- hours spent and effective net hourly return.

Never label a proposal, pending order, gross contract value or estimated commission as earned income. Reconcile realized revenue from owner-authorized statements or official APIs. Preserve currency and timestamps; support multi-currency without silently converting historical amounts.

## Architecture sequence

1. Opportunity and platform metadata registry; evidence freshness and policy-review state.
2. Read-only connectors for opportunity discovery and owner-authorized analytics.
3. Typed artifacts: opportunity, business experiment, offer, proposal draft, product asset, listing draft, order, delivery, customer issue, revenue event.
4. Approval-gated write actions with exact-action approvals and immutable audit trail.
5. Isolated product/delivery workspaces, malware scanning and content/license provenance.
6. Revenue ledger and portfolio analytics based on realized settlements.
7. Platform-specific adapters added incrementally after policy and security review.

## Evaluation gates

A platform integration is not production-ready until tests prove:
- owner/account isolation and least-privilege scopes;
- secret redaction across logs, traces, errors and agent context;
- current policy metadata and a fail-closed stale-policy state;
- action approval binds exact target and payload and cannot be replayed;
- idempotency prevents duplicate listings, submissions, messages and charges;
- rate limits and retry behavior respect provider rules;
- webhook signatures and replay protection where webhooks exist;
- pagination, partial outages and reconciliation are handled;
- disconnect/revocation immediately blocks new actions;
- sandbox or test-mode end-to-end tests pass;
- owner can inspect, export and delete integration data.

## Initial release definition

The first useful release should:
- build an owner-specific opportunity pipeline;
- rank opportunities using fit, evidence, time-to-first-test, startup cost, platform risk and expected net return (with uncertainty shown);
- generate a weekly execution plan;
- create ready-to-review proposals, product listings and content drafts;
- track approved work through delivery and settlement;
- report actual net income and time spent.

It must not claim it can earn on every website, bypass platform terms, or guarantee any income. Expand platform coverage by verified adapters and measured reliability, not by an unverified list of logos.
