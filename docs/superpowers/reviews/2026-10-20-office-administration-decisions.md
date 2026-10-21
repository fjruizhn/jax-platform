# Decisions carried from SDD ledger

2026-10-20 · Codex root · DECISIÓN technical during Fernando-authorized development. Source: signed session ledger; these are design decisions with stated costs, not production claims or execution permission. Refresh this archive at final gate; no ledger deletion.

Ruling: namespace authority limited to Virtual Office, no generic global capability or singleton root — fits Fernando's office requirement and avoids unrelated classic authority — wrong scope needs deliberate future extension.

Ruling: schema/resolver first without exposing routes or enabling external identity — contracts precede capabilities — UI arrives after backend gates.

Ruling: CI=true in dedicated loopback-only disposable DB harness avoids default production governance seed, with GITHUB_ACTIONS absent and all production guard overrides absent — same CI isolation intended by repository — no guarantee of production-scale timings.

Ruling: no new commercial user/invitation quota invented — Fernando requested roles/isolation, not licensing ceilings, and no canonical quota source exists — if commercial limits are later wanted, add explicit product policy; technical rate/concurrency limits remain required and configurable.

Ruling: add OFFICE_INTERNAL account class for new Office staff in canonical jax_users directory — prevents invitation granting unrelated classic tenant/chat/admin access while allowing full Office onboarding — costs an additional checked class and barrier matrix, not another identity store. Sol office_mfa_contract supports this class; Task1 owner informed before Task2.

Ruling: project admins can invite a new restricted OFFICE_INTERNAL collaborator directly into their own project through the trusted platform service, without extra global approval — no authority beyond grants they already control, as Sol verified — depends on classic barriers, atomic inviter reauthorization and professional approval grants; if any invariant fails, invitation must remain disabled.

Ruling: invitation acceptance depends on valid one-time bearer and current authorization, not SMTP_ACCEPTED — real mail can arrive during an UNKNOWN outcome — transport history remains unchanged and no ambiguous send auto-retry is allowed.

Ruling: split migration/runtime DB credentials and prove audit fences behaviorally in rolled-back transactions — runtime DDL enabled TRUNCATE, and MariaDB hides trigger bodies from DML users — costs a deployment oneshot and test fixture separation, protects runtime not a trusted DB administrator. Sol revised contract in office-db-runtime spec. SYSTEM_FENCE records real migrator action without inventing human actor.

Ruling: isolate web and migrator OS identities as well as DB principals, preserving trusted legacy LAS MANOS unchanged — shared uid/group could read legacy DDL credentials despite env overrides — costs dedicated users and storage permission configuration; full live process isolation remains a Task6 gate. Provision fresh explicit DB principals or fail closed on incompatible existing accounts, never revoke an unowned legacy principal. Sol supplied live source/uid/grantee evidence; reverify before activation.

Ruling: root may approve and publish every Office artifact without professional/project grants, while the release records administrative versus evidenced professional basis — honors Fernando's explicit unrestricted access without fabricating a professional qualification — costs separate release provenance and client labels.

Ruling: client can request a no-tools Director/dedicated-agent response using only its own group thread and published artifacts, on the same governed opaque Jacobs adapter — basic client conversation is an explicit product requirement, while internal jobs/content remain forbidden — costs a scoped operation/job kind and configured budget policy; output is unreviewed assistant text, never a professional artifact approval.

Ruling: Office model inputs/outputs stay in scoped Office storage, Jacobs persists only opaque references/receipts through a paired adapter — current Jacobs raw input_ref/save_if_large would leak Office content into classic storage — costs a core executor adapter before enabling Office models.

Ruling: repair consumes the initially accepted total budget and pauses AWAITING_COST_APPROVAL if exceeded — avoids duplicate or unaccepted spend while preserving one bounded repair — can delay completion pending a real cost approval; no commercial ceiling invented.

Ruling: move strict report verification into a pure portable package module before production jobs consume it, sharing the same implementation with the pilot API — the current HTTP adapter cannot be imported by a production-independent package — costs a small separately reviewed extraction; no duplicate truth logic or pilot/FastAPI dependency permitted.

Ruling: inspect structured effective unit properties and compare complete critical command/identity/env/dependency contract, rather than extend a human-output path grep — installed/drop-in units can retain rightpaths while violating migration/secret isolation — costs small verifier/config and faithfulfakeD-Bus tests; no liveunitmutation duringdevelopment.

Ruling: protect Office-enabled target identities from classic administrator credential/email/reset escalation, retaining classic emergency session revocation/deactivation and subject self-service — actor-only barriers leave a confirmed password-reset takeover path into Office grants — costs a permanent Office binding and root-MFA target guard before enabling accounts; no generic classic-admin trust inferred.

Ruling: acceptance uses immutable MFA-backed issuance proof plus current inviter authority/MFA version, without pretending a fresh interactive inviter session at acceptance — an invite may legitimately be accepted24hafterissue — costs proof/event binding and invalidation on MFA change, not every ordinary login.
