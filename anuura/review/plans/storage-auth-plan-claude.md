# NAS authentication: institute LDAP with lab-owned groups

> **Goal:** authenticate `gaur` (and the compute nodes that will mount it over NFS)
> against the institute directory instead of `gaur`'s self-hosted LDAP, while keeping
> lab-specific groups (`cache-writers`, effective `users` membership) under lab control
> and eliminating manual account add/delete. See [../../resources/storage/README.md](../../resources/storage/README.md)
> for the current setup.

Today `gaur` is simultaneously the LDAP *server* and its own client (base DN
`dc=gaur,dc=iiit,dc=ac,dc=in`). It mints its own uids and owns every group locally.
The consequences are that (a) each account is created by hand in DSM, (b) a person who
leaves IIIT stays valid on `gaur` until someone deletes them, and (c) the lab directory
is an island unrelated to any institute identity.

Both goals reduce to one architectural decision: **make a single directory the
authoritative POSIX source of truth — users, `uidNumber`, groups, `gidNumber`, and
memberships — that `gaur` and every compute node consult.** The NFS-by-numeric-uid model
forces this; the group caveat below is where a half-measure breaks.

The governing principle for keeping groups lab-owned is to **separate authentication
(identity + password → institute) from authorization (group membership → lab).**

---

## Prerequisite: three questions for institute IT (blocks path choice)

Nothing below can be finalised until IT answers these. They decide whether Path A is even
possible.

1. **Directory type.** Is the institute directory **Active Directory** or **OpenLDAP**?
   This changes the join mechanism and whether Kerberos is in play.
2. **POSIX attributes.** Does it expose `uidNumber`, `gidNumber`, `posixAccount` /
   RFC2307 attributes? If it is AD without RFC2307, the nodes must use SSSD algorithmic
   ID-mapping, which produces deterministic-but-different uids (see Migration below).
3. **Delegation.** Will IT (a) grant a **read-only service bind account**, and (b)
   **delegate an OU** (e.g. `ou=ltrc`) where the lab can create and manage its own
   groups? Answer (b) is the deciding factor: **yes → Path A; no → Path B.**

---

## The NFS group caveat (applies to both paths — read first)

NFS resolves group membership on the **client** (the compute node via `sssd`), not on the
NAS. DSM *local* groups — the ones you would create in the DSM UI — are visible to DSM and
SMB but are **invisible to `sssd` on the nodes**. Therefore any group that must grant
access over NFS (`cache-writers`, and the effective membership of `users`) **must live in
the shared directory both sides read**, never as a DSM-local group. This single fact is
what rules out the tempting "bind institute for auth, click local groups in DSM" shortcut.

`research` grants nothing on the NAS (it is only cluster-side GPU quota), so it does not
belong in this directory at all and is out of scope here.

---

## Path A — Institute is the single directory, lab groups in a delegated OU

**Requires:** IT delegates `ou=ltrc` (or equivalent) and provides a read-only service bind
account; the institute directory exposes POSIX attributes.

`gaur` and every compute node bind the institute directory. Identities and passwords are
institute-owned. `cache-writers` and the effective `users` membership live as lab-managed
posixGroups inside the delegated OU, so they are visible to `sssd` on the nodes and satisfy
the NFS caveat. Account lifecycle rides the institute roster: a person leaving IIIT
disappears from the directory and loses access on the next sync, with no lab action.

**Pros**
- Solves both goals at once — institute auth *and* automatic deprovisioning fall out for free.
- One directory, no cross-domain matching, simplest steady-state operation.
- uids are institute-wide and consistent across all of IIIT, not just the lab.

**Cons**
- Depends entirely on IT granting delegation — often the slowest, least certain part.
- Lab is coupled to institute directory availability and its change-management pace.
- Group changes may require going through IT-defined delegation boundaries.

---

## Path B — Institute for authentication only, lab runs FreeIPA for groups

**Requires:** institute is AD (for the trust) or reachable via LDAP for auth; a small
always-on VM/host to run FreeIPA. Use when IT will **not** delegate an OU.

Stand up **FreeIPA** as the lab directory holding only groups, POSIX attributes, home-dir
and host-based-access policy — **no passwords**. Establish a **one-way trust** to the
institute AD so institute users authenticate via Kerberos while FreeIPA owns the POSIX
layer. `gaur` and the nodes enrol as IPA clients. If the institute is OpenLDAP rather than
AD, the equivalent is SSSD stacking two domains (institute for auth+identity, FreeIPA for
supplemental groups) with usernames matched across both.

**Pros**
- No dependency on IT delegation; lab controls groups, automember rules, and access policy end-to-end.
- FreeIPA web UI + automember rules replace all manual DSM clicking and automate home-dir creation.
- Cleanly separates auth (institute) from authorization (lab), the exact split we want.

**Cons**
- More moving parts: a FreeIPA server to run, patch, and back up (needs the ops owner from the infra plan).
- AD trust setup is fiddly; if institute is plain LDAP, cross-domain username matching adds fragility.
- Deprovisioning is only semi-automatic — a departed user still authenticates until removed from institute directory *and* the lab reconciles groups (mitigated by roster automation below).

---

## Migration gotcha (applies to both paths)

Switching identity source **changes `uidNumber`s** unless preserved. Existing files on
`gaur` are owned by the current local-LDAP uids and will orphan at cutover. This holds even
for AD algorithmic ID-mapping — deterministic, but different from today. Plan a maintenance
window, remap, and keep the hourly snapshots as rollback.

---

## Checklist — Path A (delegated OU)

1. [ ] Confirm the three prerequisite answers from IT; obtain the delegated OU DN and the read-only bind account.
2. [ ] Export the current identity map from `gaur`'s LDAP: `username → uidNumber, gidNumber` for every account, plus group memberships.
3. [ ] Reconcile usernames against the institute directory; agree the target `uidNumber` for each existing user with IT.
4. [ ] Create `cache-writers` and the effective `users` group as posixGroups inside the delegated OU, with fixed `gidNumber`s.
5. [ ] In a test scope, join `gaur` to institute LDAP (Control Panel > Domain/LDAP > LDAP), pointing group lookups at the delegated OU; verify a test user logs into DSM and resolves groups.
6. [ ] Configure `sssd` on one compute node against the same directory; confirm `id <user>` returns identical uid/gids on the NAS and the node.
7. [ ] Verify NFS access end-to-end from the node: `hf_cache` read for `users`, read/write for `cache-writers`, private `homes`.
8. [ ] Schedule the cutover window. Announce read-only/downtime; take a fresh snapshot of `homes` and `shared` as the rollback point.
9. [ ] Remap ownership: build an old-uid → new-uid table and `chown -R` across `homes` and `shared` (script it; dry-run first).
10. [ ] Flip `gaur` and all nodes to the institute directory; retire `gaur`'s LDAP Server package (keep an export archived).
11. [ ] Roster automation: track lab members and group membership in a Git-tracked file, applied to the delegated OU via `ldapmodify` (or automember if available) on a schedule.
12. [ ] Deprovisioning policy: rely on institute removal for auth; reconcile lab groups on the same schedule. Disable-then-reap file ownership rather than hard-delete.
13. [ ] Document final mount paths, `HF_HOME`/`TORCH_HOME`, and the new auth flow in `resources/storage/README.md`.

## Checklist — Path B (FreeIPA + institute trust)

1. [ ] Provision an always-on VM/host for FreeIPA reachable from `gaur` and both clusters' nodes; assign the ops owner.
2. [ ] Install and initialise FreeIPA; set the POSIX id range so it does **not** collide with existing or institute uid ranges.
3. [ ] Establish the one-way trust to institute AD (or, for OpenLDAP institutes, configure SSSD two-domain stacking with username matching); verify an institute user authenticates through FreeIPA.
4. [ ] Create `cache-writers` and the effective `users` group in FreeIPA with fixed `gidNumber`s; add existing members.
5. [ ] Export the current `gaur` identity map (`username → uidNumber, gidNumber`, memberships) and decide target uids in FreeIPA.
6. [ ] Enrol one compute node as an IPA client; confirm `id <user>` matches between node and `gaur`, and that institute auth + FreeIPA groups both resolve.
7. [ ] Enrol `gaur` (IPA client, or LDAP client pointed at FreeIPA); verify DSM login and group resolution.
8. [ ] Verify NFS access end-to-end: `hf_cache` read for `users`, read/write for `cache-writers`, private `homes`.
9. [ ] Configure home-dir automation (`pam_mkhomedir`/autofs) and host-based access control in FreeIPA; retire DSM's per-user home toggle.
10. [ ] Schedule the cutover window; announce downtime; snapshot `homes` and `shared` as rollback.
11. [ ] Remap ownership: old-uid → FreeIPA-uid table, `chown -R` across `homes` and `shared` (script; dry-run first).
12. [ ] Flip `gaur` and all nodes to FreeIPA; retire `gaur`'s LDAP Server package (archive an export).
13. [ ] Roster automation: Git-tracked member list driving FreeIPA **automember** rules; deprovisioning removes group membership and disables the account on schedule.
14. [ ] Document final mount paths, `HF_HOME`/`TORCH_HOME`, FreeIPA/trust config, and the new auth flow in `resources/storage/README.md`.

---

### Recommendation

Confirm the three prerequisite questions first. If IT will delegate an OU, take **Path A**
— it is materially simpler and makes deprovisioning automatic. If they will not, take
**Path B (FreeIPA + trust)**, which buys full lab control at the cost of one more service
to run. Both give institute authentication, lab-owned groups, and roster-driven
add/delete; both require the uid remap at cutover.
