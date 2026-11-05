# BrasilMart support agent: backend and data plan

## 1. Working assumptions and the key limitation

Build a **training demo** in Microsoft Copilot Studio's **standard harness** (`New experience` off), publish it to Microsoft Teams, and use approved training-tenant connectors only. Use Dataverse for operational records if it is available in the tenant. Use classic, topic-led orchestration so order access and return decisions follow explicit branches. The Olist CSVs are historical anonymised data, so every order answer must say it is a **dataset record**, not a live carrier update.

Use a fixed **demo business date of 2018-09-01** for lateness and return-window examples. Store it as one environment/configuration value, show it in the conversation, and never use the host's current date for these calculations. This date is a simulation setting, **not an asserted Olist snapshot date**. The CSV has final recorded order statuses and milestone timestamps, but no complete history of status changes, cancellations, or tracking scans. For the live demo, choose cases with purchase and all recorded milestones on or before the demo date. Present `order_status` as the recorded dataset status. Do not claim to know an order's status at an earlier instant or its current status in 2026.

The Olist file has no email address, Teams account, or verified customer identity. Create a small **demo identity mapping** between two real training-tenant Teams test users and distinct Olist `customer_unique_id` values. Provision this mapping before the demo. Never accept a typed customer ID as proof of ownership.

## 2. Data inventory and preprocessing

The nine CSVs already in this workspace are the [Olist Brazilian E-Commerce Public Dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce). The Olist data card describes the data as anonymised and licensed CC BY-NC-SA 4.0. Keep the source and its derivative in the training tenant for this exercise.

| Source file | Local count | Use |
| --- | ---: | --- |
| `olist_customers_dataset.csv` | 99,441 | Required: order-to-customer join; `customer_unique_id` identifies repeat purchasers. |
| `olist_orders_dataset.csv` | 99,441 | Required: recorded status, purchase, approval, carrier handover, actual delivery, estimate. |
| `olist_order_items_dataset.csv` | 112,650 | Required for item-specific returns and exact item price and freight. |
| `olist_order_payments_dataset.csv` | 103,886 | Use for payment methods, instalments, and recorded payment totals when discussing amounts. |
| `olist_products_dataset.csv` and `product_category_name_translation.csv` | 32,951 and 71 | Optional for item category labels; Olist does not provide reliable customer-facing product names. |
| `olist_order_reviews_dataset.csv` | 99,224 | Optional for fixture selection or analytics; a review is **not** a complaint ticket. |
| `olist_sellers_dataset.csv` and `olist_geolocation_dataset.csv` | 3,095 and 1,000,163 | Exclude from the first operational load; they do not support the required conversations. |

Local checks found 96,096 distinct `customer_unique_id` values and 2,997 repeat buyers. Orders have eight recorded status values: delivered 96,478; shipped 1,107; canceled 625; unavailable 609; invoiced 314; processing 301; created 5; approved 2. There are eight `delivered` rows without an actual delivery timestamp. The earliest purchase is 2016-09-04 and the latest is 2018-10-17. As an initial selection check for the 2018-09-01 demo date, 3,656 delivered orders have a delivery date within the proposed 14 day window; this count is **not** a claim that all their statuses are independently known as of that date.

Preprocessing contract for the teammate:

1. Preserve raw CSVs unmodified. Record file name, row count, checksum, and import batch ID.
2. Parse CSV as UTF-8 and dates as Olist local date/time values. Keep the original timestamp text or source value alongside the parsed date/time. Date-only policy comparisons use the first ten characters (`YYYY-MM-DD`) consistently; do not silently apply a timezone shift.
3. Keep all Olist IDs as strings. Parse money into decimal values with two fractional digits and `BRL` currency; never use floating point for an authoritative total.
4. Validate uniqueness of `order_id`, `customer_id`, `(order_id, order_item_id)`, and `(order_id, payment_sequential)`; validate foreign keys and flag missing required joins. Quarantine anomalies instead of guessing missing data.
5. Join `orders.customer_id` to `customers.customer_id`, then use `customer_unique_id` as the stable customer key. Join items and payments by `order_id`. Aggregate payments per order only after validating the original payment rows. Do not substitute `SUM(item price + freight)` for a missing payment total; they represent different source fields.
6. Import only fields the agent needs. Keep `source_order_id` and source row identifiers for traceability. The first demo load can use a **curated subset** spanning eligible and ineligible returns, late and on-time delivery, open and canceled orders, repeat buyers, missing data, and two customers for isolation proof. A later load can cover all valid rows.
7. Generate a validation report: source and loaded row counts, rejected records and reasons, null dates, unmatched joins, money reconciliation differences, and selected demo case IDs with expected calculations.

## 3. Operational tables

Default storage: Dataverse custom tables in the training environment. The chatbot reads these via **agent flows**, not by adding private order tables as a knowledge source. This keeps the ownership check in every read/write path. Table names below are logical names; use the environment's publisher prefix in Dataverse.

| Table | Key fields | Relationships and purpose |
| --- | --- | --- |
| `Customer` | `customer_unique_id` unique; city/state optional | One customer to many orders. Keep location minimal. |
| `CustomerIdentity` | `tenant_id`, authenticated `User.Id`, `customer_unique_id`, `enabled` | Unique `(tenant_id, User.Id)` mapping. Provision two demo identities out of band. The user never chooses this link in chat. |
| `Order` | `order_id` unique, `customer_id` source, `customer_unique_id`, `order_status`, purchase/approval/carrier/actual/estimated dates, `import_batch_id` | Many orders to one customer. Source status and timestamps remain exact. |
| `OrderItem` | `(order_id, order_item_id)` unique, `product_id`, category label optional, `price_brl`, `freight_brl`, `shipping_limit_date` | Many items per order; return requests select a specific item. |
| `OrderPayment` | `(order_id, payment_sequential)` unique, type, instalments, `payment_value_brl` | Many payment records per order. A flow can return the exact sum and label it “recorded payment total.” |
| `ReturnRequest` | generated ID, `order_id`, `order_item_id`, requester `User.Id`, reason, requested date, policy version, decision, refusal reason, state, created timestamp, idempotency key | New durable record. The flow checks for an existing active request for the item and uses a unique idempotency key to return the same result on retry. Do not equate acceptance with a completed refund. |
| `Complaint` | generated ID, `order_id` nullable, requester `User.Id`, category, description, priority, status, created timestamp | New durable record visible to support. Priority rules below. |
| `SupportCase` | generated ID, requester `User.Id`, order/return/complaint IDs nullable, conversation ID, issue summary, last known status/dates, reason for handoff, queue state, created timestamp | Durable handoff context and support queue item. Store a concise factual summary, not an unreviewed claim of a live transfer. |
| `ImportBatch` | batch ID, source checksums, import date, counts, validation result | Trace each displayed order fact back to the training import. |
| `DemoConfig` | `business_date`, policy version, source notice | One controlled value for calculations and display. |

Relationships: `CustomerIdentity → Customer → Order → {OrderItem, OrderPayment}`; `OrderItem → ReturnRequest`; `Order → Complaint/SupportCase`. Every order, return, complaint, and support-case flow first resolves the **authenticated** `User.Id` to exactly one `Customer`. It then checks `Order.customer_unique_id` against that customer before returning details or creating an order-linked record. A missing, disabled, or ambiguous mapping fails closed.

## 4. Deterministic business rules

**Order lookup.** Inputs: authenticated `User.Id`, optional order ID. If no order ID is provided, return a short list of that user's orders for selection. If an ID is provided, verify ownership in the flow before returning any fields. A foreign or nonexistent order gets the same generic response: “I couldn't find that order in your account.” The agent must not reveal which condition occurred.

**Delivery.** Return recorded source status, estimated date, actual date only when present, and carrier handover date only when present. No carrier tracking URL or future delivery prediction exists in Olist. Compute integer calendar days using `DATE(actual_delivery) - DATE(estimated_delivery)` for delivered orders; use `DATE(demo_business_date) - DATE(estimated_delivery)` for open orders. Clamp at zero. Do not call canceled or unavailable orders “late.” If a delivered row lacks the actual date, say it is missing and offer handoff; do not calculate lateness. For an open order, say “overdue by N days as of 1 Sep 2018,” not “delivered N days late.”

**Proposed BrasilMart return policy for the demo.** An item can be requested for return within **14 calendar days after its recorded delivery date**, inclusive. It must be in an order with `delivered` status, have a valid actual delivery date, and have no active return request for the same item. A request exactly on day 14 is accepted; day 15 is refused with the specific reason. A customer may explain damage or wrong item, but the automated rule still records a complaint or support case for human review if outside the standard window. An accepted request means “request recorded for review/processing”; it does not assert that pickup or refund has happened. The flow rechecks ownership and eligibility immediately before the write and returns the saved request ID.

**Proposed complaint priority.** `P1` for “marked delivered but not received,” payment dispute, or an open order overdue by 7+ days; `P2` for an open order overdue by 1–6 days, a return-policy dispute, or damaged/wrong item; `P3` for other complaints. The flow sets the priority from structured answers and source facts; the user cannot set it directly. Record the complaint first, then notify support. A failed notification leaves the case queued and visible.

**Proposed policy knowledge.** Draft four short versioned documents: Delivery, Payments, Returns, Complaints. State the historical dataset and demo date in the delivery document. Keep all store policy wording authored by the team; do not present it as Olist policy. Upload these files as the agent's policy-only knowledge source, or use an approved SharePoint folder if the training tenant permits it. Keep order facts out of knowledge retrieval.

## 5. How the standard harness connects to the backend

```text
Customer in Teams (authenticated User.Id)
    → Copilot Studio standard-harness topic
    → question/entity and topic variables
    → agent flow (Dataverse query/decision/write)
    → structured flow result
    → condition + Power Fx + Adaptive Card/message
    → optional SupportCase + Teams support notification

General policy question → policy topic → policy-only knowledge source
```

Use these topic/flow contracts:

| Topic and sample triggers | Topic inputs/outputs | Flow action |
| --- | --- | --- |
| **My orders**: “show my orders”, “my purchases” | `User.Id` → owned order IDs and purchase dates | `ListMyOrders` |
| **Order status / late**: “where is my order”, “is it late”, “delivery date” | order-ID entity or selected card ID, `User.Id` → owned status, exact dates, `lateDays`, `lateKind` | `GetMyOrder` |
| **Return item**: “return an item”, “send it back”, “refund request” | owned order/item choice, reason, `User.Id` → accepted/refused, reason, request ID | `CreateReturnRequest` |
| **Complaint**: “complain”, “my parcel is missing”, “payment problem” | category entity, text, optional owned order, `User.Id` → saved ID/priority | `CreateComplaint` |
| **Human help**: “speak to a person”, “agent please” | issue summary, related IDs, `User.Id` → support case ID/queue state | `CreateSupportCase` |
| **Policy**: “return policy”, “how can I pay”, “delivery policy” | policy question → sourced answer or fallback | Policy-only knowledge node; no order flow |

Use a **Question** node for order ID (text entity), return reason/category (choice entities), and complaint details. Store `Topic.OrderId`, `Topic.ItemId`, `Topic.ReturnReason`, `Topic.ComplaintCategory`, and flow outputs as topic variables; use a global conversation variable only for an already verified selected order ID. A selected ID is still reauthorised by every flow call. Use **Conditions** for `found/owned`, missing dates, return decision, duplicate request, and handoff. Use **Power Fx** for clear presentation, for example `If(Topic.LateDays > 0, "Overdue by " & Text(Topic.LateDays) & " days", "Not overdue")`; keep the authoritative day calculation and all writes in the flow. Use **Adaptive Cards** (Teams schema 1.5) for the owned-order picker, item picker, return confirmation, and complaint receipt. Validate card submission through the same flow because a card value can be edited by a caller.

Each agent flow uses `When an agent calls the flow` and `Respond to the agent`, with synchronous response within the documented 100 second action limit. Return small typed results, such as `outcome`, `displayStatus`, `estimatedDate`, `actualDate`, `lateDays`, and record ID. Catch connector errors; return `unavailable` and offer support rather than inventing data. Use an idempotency key for write flows (conversation ID + action + selected order/item) so retries do not create duplicates.

Set agent authentication to **Authenticate with Microsoft**. Use the authenticated `User.Id` variable as the lookup key, scoped to the tenant. Do not trust typed names, email addresses, order IDs, or a model-generated summary as identity. Use the Teams + Microsoft 365 channel and publish before installing the agent in the two test users' Teams clients. Restrict the installation to the training tenant.

Turn off **Web Search** and **general knowledge/ungrounded responses** in agent settings and in the policy generative-answers node. Scope that node to the approved policy source only. In **system topics**, configure Greeting/Conversation Start to explain the historical demo, Fallback to a factual “I don't have that information” and offer support, Escalate to create a support case, and End of Conversation to ask for feedback. Avoid broad Conversational Boosting over private order tables.

## 6. Human handoff: choose the honest implementation

The minimum training-tenant implementation is a **durable asynchronous handoff**: `CreateSupportCase` saves the context, a Teams connector posts the case ID and factual summary to a support-team channel, and a human uses that record to contact the customer. The customer sees “A support teammate will follow up” and the case ID. The notification can contain order ID, source status, dates, complaint/return ID, user identity key, and the customer's stated issue; exclude unrelated order details. Record notification success/failure separately from case creation.

If the tenant has an approved engagement hub such as Dynamics 365 Customer Service/Omnichannel and a live transfer is required, configure the **Escalate** system topic's Transfer conversation node and pass the case ID and context. A Teams channel notification alone does **not** let a human take over the same bot chat. This is a demo decision gate, not a claim that live transfer is already available.

## 7. Build sequence and proof

1. **Data owner:** choose two distinct repeatable customer fixtures and at least one eligible return, day-14 boundary, day-15 refusal, open overdue order, delivered late order, canceled/unavailable order, and missing-date anomaly. Produce a fixture manifest with source IDs, expected dates, and expected decisions. Prepare validation report and Dataverse import.
2. **Backend owner:** create tables, unique keys/relationships, identity mapping, import, and five agent flows. Test each flow with both users and a foreign order ID before connecting topics.
3. **Agent owner:** create standard-harness topics, questions/entities, variables, conditions, Power Fx display logic, Teams-compatible cards, policy documents/knowledge node, and system-topic behavior. Disable web and general knowledge. Wire to flows.
4. **Integration:** install in Teams for both test users, verify end-to-end records in Dataverse, support channel notification, and actual customer-facing wording. Capture screenshots of the same order ID denied to the other user and absence of cross-customer results in “my orders.”
5. **Analytics/demo:** use Copilot Studio Monitor for topic use, resolved/escalated/abandoned sessions, and satisfaction; use Dataverse record counts for return, complaint, and support-case completion. Demonstrate one success and one failure path for each major topic in a 15 minute script.

### Conversation test list to execute later

Mark each test **pass/fail with evidence after the build**; none has been run in Teams yet.

| # | Customer utterance / path | Expected check |
| ---: | --- | --- |
| 1 | “Show my orders” as User A | Only A's mapped customer orders. |
| 2 | “Show my orders” as User B | Only B's mapped customer orders. |
| 3 | A asks for A's delivered order | Exact recorded status and dates. |
| 4 | A asks for B's order ID | Generic denial; no B details. |
| 5 | B pastes A's order ID into the card response | Flow denies it again. |
| 6 | A asks for an unknown order ID | Same generic response as foreign ID. |
| 7 | A asks “Is it late?” for an open overdue order | Exact days as of demo date, labeled overdue. |
| 8 | A asks for an on-time delivered order | Zero late days, exact actual date. |
| 9 | A asks for a late delivered order | Exact delivered-late days. |
| 10 | A asks for canceled/unavailable order | Status shown; no overdue claim. |
| 11 | A asks for delivered row with missing actual date | Missing-date message, no invented date or days. |
| 12 | A requests return of item within 14 days | Saved accepted request ID. |
| 13 | A requests return exactly on day 14 | Accepted. |
| 14 | A requests return on day 15 | Refused with window reason and saved decision. |
| 15 | A requests return of undelivered item | Refused with status reason and saved decision. |
| 16 | A repeats accepted return submission | No duplicate active request. |
| 17 | B attempts return for A's item | Generic denial; no record linked to A. |
| 18 | A reports “marked delivered but missing” | Complaint saved as P1, support case offered. |
| 19 | A reports a minor delivery complaint | Complaint saved with rule-based priority. |
| 20 | A asks payment, delivery, return, and complaint policy | Answers only from versioned policy source. |
| 21 | A asks unrelated general-knowledge question | Fallback; no web/general answer. |
| 22 | A asks for a human after an order discussion | Support case contains order/context and support notification. |
| 23 | Support notification fails | Case remains saved and visible; no false transfer claim. |
| 24 | Unmapped Teams user asks for orders | Closed failure, no order details. |

## 8. Decisions to verify in the tenant

- Dataverse custom tables and agent-flow Dataverse actions are approved and licensed; Teams connector and Teams publishing are allowed.
- Two distinct authenticated Teams test users are available for isolation proof.
- Decide whether an asynchronous support queue satisfies the handoff rubric; a same-chat live transfer requires an approved engagement hub.
- Confirm the team's preferred policy language (Portuguese for the demo is recommended), then create a matching versioned English translation only if useful.


