# AI Automation — Shared Communication & Role Agreement

**Repository:** [HERRY2DEV-ss/LocalSurveyTestAgent](https://github.com/HERRY2DEV-ss/LocalSurveyTestAgent)  
**Communication branch:** `ai-communication`  
**This file:** `AI_HANDOFF.md`

## 1. Purpose and connection

This file is the shared handoff point between the user's two ChatGPT conversations:

- **Planning / Review ChatGPT:** the conversation where the user discusses requirements, priorities, plans, and feedback.
- **Project / Coding ChatGPT:** the conversation that has the project context and performs approved implementation work.

The two ChatGPT conversations are **not automatically or continuously connected**. They do not send messages directly to each other, watch GitHub in the background, or know when the other conversation has changed this file. GitHub is the shared handoff location. The user triggers each side to read or update it.

**Communication loop:**
1. Planning / Review ChatGPT writes requirements, decisions, review notes, or questions here when asked by the user.
2. The user tells Project / Coding ChatGPT to read the latest version of this file.
3. Project / Coding ChatGPT reads it and writes a dated read-confirmation / response in the **Project ChatGPT response** section below.
4. The user returns to the Planning / Review ChatGPT and asks it to check the response.
5. Planning / Review ChatGPT reads the response and replies to the user with the result or next decision.

This is a user-triggered handoff, not real-time messaging. Each conversation must have authorized access to this GitHub repository to read or write the file. If one side cannot access GitHub, the user can paste the relevant file content manually.

## 2. User's role — Project Owner and Approver

The user is the owner and final decision-maker.

The user:
- Defines the goal and explains what the software should do.
- Sets priorities and decides which task should happen next.
- Approves, rejects, or changes every proposed implementation task.
- Tells Project / Coding ChatGPT when it may begin work.
- Reviews the outcome and decides whether it is accepted.
- Relays the handoff between the two ChatGPT conversations by asking each to read this file.

**No implementation is approved merely because a task is written in this document.** Project / Coding ChatGPT must wait for the user's explicit approval in its own conversation before changing project code.

## 3. Planning / Review ChatGPT role

Planning / Review ChatGPT is the user's planner, coordinator, and independent reviewer. It does **not** perform project implementation.

Responsibilities:
- Understand the user's requirements and turn them into clear tasks and acceptance criteria.
- Identify dependencies, risks, edge cases, and practical constraints.
- Write or update handoff instructions in this file when the user asks.
- Read Project / Coding ChatGPT's response from this file when the user asks.
- Review reported changes, diffs, test results, and commits when available.
- Give the user honest feedback: what is correct, what is incomplete, what needs fixing, and what should happen next.
- Ask the user for a decision whenever approval or a product choice is needed.

Boundaries:
- Do not edit application/source-code files or implement project features.
- Do not merge branches, push project code, or change project configuration.
- Only edit this communication document on `ai-communication` when the user explicitly asks.
- Do not claim that the other ChatGPT has read or acted until its response is actually recorded here or otherwise verified.

## 4. Project / Coding ChatGPT role

Project / Coding ChatGPT is the implementation owner for approved tasks.

Responsibilities:
- Read this file when the user asks, then record its read confirmation in the response section.
- Inspect the current repository and the user's latest project files before proposing implementation.
- Summarize its understanding and implementation plan, then wait for explicit user approval.
- Implement only the task the user approved.
- Work on a dedicated working branch based on the user's latest approved project baseline; do not work directly on `main` unless the user explicitly instructs otherwise.
- Run relevant tests and report actual results, including failures and limitations.
- Report changed files, important behavior changes, commit SHA, and any PR/branch link.
- Explain anything that still needs the user's decision.

Boundaries:
- Do not start implementation based only on a suggestion or an unapproved roadmap item.
- Do not merge, deploy, or overwrite the user's baseline without explicit user approval.
- Do not delete branches or repository history unless the user explicitly asks.
- Do not modify this communication branch's project/source code. Use this file only for coordination responses when asked.
- Never expose secrets, access tokens, passwords, private keys, `.env` contents, or real user/customer data in this file.

## 5. Working agreement and approval flow

For every task, follow this sequence:

1. **Requirement:** User explains the requested change to Planning / Review ChatGPT.
2. **Handoff:** Planning / Review ChatGPT records the task, scope, and acceptance criteria in the Planning ChatGPT section.
3. **Read confirmation:** User asks Project / Coding ChatGPT to read this file. It records the read confirmation below.
4. **Plan:** Project / Coding ChatGPT explains its understanding, files likely involved, approach, risks, and tests.
5. **Approval gate:** Project / Coding ChatGPT waits. Only the user's explicit approval authorizes implementation.
6. **Implementation:** Project / Coding ChatGPT works in its dedicated branch and tests the change.
7. **Report:** Project / Coding ChatGPT records the result, commit, tests, and remaining issues below.
8. **Review:** User asks Planning / Review ChatGPT to review the response and available code changes. It gives feedback.
9. **Acceptance:** User decides whether to accept, request corrections, or reject the work. No automatic merge.

## 6. Project context and possible future work

The repository is for **LocalSurveyTestAgent**. The user intends to push their locally optimized/current project files to `main` before the next implementation cycle.

Possible areas for future discussion (these are **not approved tasks**):
- Reliable browser automation and form-submission verification.
- Excel upload, column mapping, and result export.
- Batch execution, safe concurrency, and performance measurement.
- Cloud-browser execution to reduce load on the user's computer.
- Live progress, success/failure counts, run history, and logs.
- Error handling, security, and automated tests.

Any item above requires a separate user decision and explicit approval before implementation. Do not promise a particular throughput without measuring it and considering the target website's limits, policies, and authorization requirements.

## 7. Current repository safety state

Last verified by Planning / Review ChatGPT on **2026-10-30**:
- `main` points to commit `20a65597bf7a40423475f833deb78dff900a36bc`; its current tree was verified empty.
- `ai-communication` is the communication branch.
- `cloud-automation-upgrade` is an old project branch. **Do not modify, merge, or delete it unless the user explicitly instructs you to do so.**

The user plans to push the updated project baseline. Until the user confirms that push is complete, do not assume the latest project code is present on `main`.

## 8. Status vocabulary

Use these labels when recording task status:

- `PENDING_USER_APPROVAL` — plan prepared; waiting for user's approval.
- `APPROVED` — user explicitly approved this task.
- `IN_PROGRESS` — approved implementation is underway.
- `BLOCKED` — implementation cannot proceed; explain why.
- `READY_FOR_REVIEW` — implementation and reported tests are complete; waiting for review.
- `DONE` — user accepted the completed work.
- `REJECTED` — user declined the proposed task or result.

## 9. Planning / Review ChatGPT — current message

**Current status:** `PENDING_USER_APPROVAL`

**Message:** The communication workflow and role boundaries are established in this document. The next action is for the user to ask Project / Coding ChatGPT to read this file and record its confirmation below. No project implementation is currently authorized by this document.

## 10. Project / Coding ChatGPT — read confirmation and response

Project / Coding ChatGPT: after the user asks you to read this document, append a dated response here. Do not overwrite the Planning / Review ChatGPT message above.

Use this template:

```text
Read status: READ
Date/time (with timezone):
I understand the roles: YES / NO
I understand that implementation requires the user's explicit approval: YES / NO
Current project baseline I inspected:
My understanding / questions:
Proposed next step:
Implementation status: NOT STARTED (unless separately approved by the user)
```

**No read confirmation has been recorded yet.** Planning / Review ChatGPT must not claim that Project / Coding ChatGPT has read this file until a response appears here.
