# Implementation plan: team workspaces

Status: approved. Target: ship behind a feature flag, then migrate existing accounts.

Today every account is a single user with their own projects. We are introducing workspaces: a
container that holds projects and members, with roles, invitations, and per-seat billing.

## Phase 1 — data model

1. Add `workspaces` and `workspace_members` tables. A member row carries a role: `owner`,
   `admin`, or `member`.
2. Add `workspace_id` to `projects`, nullable for now so existing rows stay valid.
3. Write the forward migration and the rollback. Nothing reads the new columns yet.

## Phase 2 — API surface

4. `POST /workspaces`, `GET /workspaces`, `PATCH /workspaces/:id`, `DELETE /workspaces/:id`.
5. `GET /workspaces/:id/members`, `DELETE /workspaces/:id/members/:userId`.
6. Every response uses the existing envelope. Deleting a workspace with projects returns 409.

## Phase 3 — permissions

7. Resolve the acting member on every request that carries a workspace id.
8. Enforce the role matrix: only `owner` may delete a workspace or change billing; `admin` may
   invite and remove members; `member` may read.
9. A user with no membership must get 404, not 403 — we do not confirm that a workspace exists to
   someone who is not in it.

## Phase 4 — invitations

10. `POST /workspaces/:id/invitations` sends an email with a signed, single-use link.
11. Accepting an invitation creates the membership and marks the invitation used.
12. Invitations expire after 7 days. An expired or reused link gets an explicit message, not a
    generic error.

## Phase 5 — billing

13. Price per active seat, counted daily. Adding a member mid-cycle is prorated.
14. Removing a member frees the seat at the end of the cycle, not immediately.
15. The workspace owner sees the seat count and the next invoice estimate on the billing page.

## Phase 6 — interface

16. Workspace switcher in the top bar: current workspace, list to switch, "create workspace".
17. Members screen: list with roles, invite form, remove action, pending invitations with their
    expiry.

## Phase 7 — migration and release

18. Every existing account becomes a workspace of one, with that user as `owner`, and their
    projects reassigned to it.
19. Backfill runs in batches and is resumable — it will run against production data.
20. Acceptance pass: the flag off means the old behaviour is untouched; the flag on means every
    screen, endpoint, and email works end to end.

## Open questions

- Should the seat count include members whose invitation is still pending? (Phase 5)
- Wording of the expired-invitation message. (Phase 4)
- Does removing the last owner get blocked, or does it promote the oldest admin? (Phase 3)
