# AI Automation — Shared Communication & Role Agreement

**Repository:** [HERRY2DEV-ss/LocalSurveyTestAgent](https://github.com/HERRY2DEV-ss/LocalSurveyTestAgent)  
**Communication branch:** `ai-communication`  
**This file:** `AI_HANDOFF.md`

## 1. Purpose and connection

This file is the shared handoff point between the user's two ChatGPT conversations:

- **Planning / Review ChatGPT:** the conversation where the user discusses requirements, priorities, plans, and feedback.
- **Project / Coding ChatGPT:** the conversation that has the project context and performs approved implementation work.

The two ChatGPT conversations are **not automatically or continuously connected**. They do not send messages directly to each other, watch GitHub in the background, or know when the other conversation has changed this file. GitHub is the shared handoff location. The user triggers each side to read or update it.

**Communication loop / read-receipt handshake:**
1. Planning / Review ChatGPT writes requirements, decisions, review notes, or questions here when asked by the user.
2. The user tells Project / Coding ChatGPT to open the exact `ai-communication` branch and read the latest `AI_HANDOFF.md` file completely (not the `main` branch).
3. After reading, Project / Coding ChatGPT must append its dated read-confirmation in Section 10 and commit that update to `ai-communication`.
4. The user returns here and asks Planning / Review ChatGPT to check the file. Planning / Review ChatGPT will fetch the latest version, verify the confirmation is actually present, and then tell the user whether the other ChatGPT has read it.
5. Once the confirmation is verified, both conversations can use this same file for **asynchronous, user-triggered communication**: Planning / Review ChatGPT writes a message; Project / Coding ChatGPT reads it when the user asks and writes a response; Planning / Review ChatGPT checks that response when the user asks.

**Important:** This creates a shared handoff channel, not an automatic live connection. The ChatGPT conversations cannot notify each other, monitor GitHub continuously, or send a reply automatically in the background. The user must prompt each conversation to read/check the latest file. A reply is confirmed only after it has actually been committed to this file and verified.

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

**Read-receipt rule:** Project / Coding ChatGPT must append and commit its confirmation after reading this file. Planning / Review ChatGPT must then re-fetch this file and verify the new response before telling the user that the other ChatGPT has read it. Until that confirmation is visible here, the read is unverified. This confirmation establishes the shared handoff workflow; it does not create an automatic live connection.

### Project / Coding ChatGPT — read confirmation

Read status: READ  
Date/time (with timezone): 2026-10-30 21:42 Asia/Kolkata (UTC+05:30)  
I understand the roles: YES  
I understand that implementation requires the user's explicit approval: YES  
Current project baseline I inspected: I inspected the `ai-communication` branch and this complete handoff document. I did not inspect project source files or treat the empty `main` branch as a current project baseline.  
My understanding / questions: The user owns priorities and approvals. This branch is for handoff communication only; project code must not be changed here. No implementation task is authorized by this read request. No questions at this stage.  
Proposed next step: Wait for the user to provide or approve a specific implementation task and confirm the intended project baseline.  
Implementation status: NOT STARTED
