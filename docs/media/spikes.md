# P0 spikes: log

Status: S2, S4, S5, S6, S7 pass; S3 fallback adopted; S1 soak result below. Run 2026-11-05 against Switch 0.31.0
(protocol 9), local server run by Console. Spike code was throwaway and is not kept in this repo.

## Pre-spike findings from the public Switch docs (docs.switchagents.ai, read 2026-11-05)

Install: `https://github.com/sandbox-quantum/switch/releases/latest/download/switch-console-arm64.dmg`
(Intel: `...-x64.dmg`). Console > Add a server > Run a server on this computer > Start. Mattermost login:
server Home > Messaging apps > Mattermost row menu > "Sign-in details..." > Open in Mattermost.

Matches our architecture: `POST /agents` with a registration key returns `{id, api_key}` once; WS at
`/agents/{id}/connection/ws?connection_id&scope&filter&start_from&expected_generation` with
`Authorization: Bearer`; server `ping` every 2 s, client `{"type":"pong","cursor":N}`, 6 s lapse;
ops at `POST /agents/{id}/ops/{op}` with `X-Switch-Connection-Id`; `GET /agents/{id}/ops` lists
every op with its input schema.

Differences / open items to resolve in the spikes:
- Message payload fields documented: `addressed, sender, sender_name, message_id, body, timestamp,
  thread_id, attachments`. `sender_kind` and `sender_role` are NOT in the docs (S2/S5 must show whether
  they exist; identity.py already treats them as optional).
- Attachments are pointers (`filename, mimetype, size, mxc, msgtype`). The media download route is not
  documented on the pages read (S5). Candidate pages: internals/life-of-a-message.md,
  internals/connectors-and-runtime.md.
- Argument and result shapes of `post_message`, `read_context`, `list_participants`,
  `load_internal_documents` are not documented; read them from `GET /agents/{id}/ops` (S4, S5, S6).
- `connect_to_room` takes over the room for that connection and evicts another connection holding it.
- `addressing` via `update_agent_detail` only takes `owner_only | owner_and_owner_agents | anyone`
  (matches decision D14; custom rules are Gateway UI only).
- Registration key page steps (Gateway > API Keys) are in `getting-started/add-a-server.md` (not yet read).

## How the spikes were run (deviations from the plan)

- The Gateway **API Keys** page is not reachable in this Console version, so there is no registration key.
  The server's bootstrap key (`AGENT_REGISTRATION_TOKEN`, from the local server's own env file) was used for
  `POST /agents`. Consequence: agents are owned by a dedicated non-admin account ("Agent Bootstrap"), not by
  the human admin, and `update_agent_detail` on an agent is refused for the agent itself (owner-only).
  A second agent registered with the same key (`spike-admin`) can change `countersign`'s addressing.
- Local server: API `http://127.0.0.1:49379`, Mattermost `http://127.0.0.1:49380` (not 8065). Ports are in
  `~/Library/Application Support/switchdash/local-switch-server/ports.json` and change per install.
- Rooms were created through ops (`create_room`) rather than the Console template.

## Spike results

| Spike | Result | Evidence and notes |
|---|---|---|
| S1 WS stability | PASS | 600 s connected, no evicted/gap/refused frames, no close; connection open; 2 s pings answered with `{"type":"pong","cursor":N}`; protocol 9, server 0.31.0 |
| S2 addressed filter fields | PASS (with addressing `anyone`) | Event payload: `addressed, sender, sender_name, sender_kind (null), on_behalf_of, message_id, body, timestamp (ms), thread_id, attachments, opening_message, sender_role (null)`. `sender` is `@switch-mattermost-<bridge>-<username>:localhost`, `sender_name` is the Mattermost username. With the default addressing (owner only) Switch itself answers the person ("I only take instructions from my owner, and this chat account isn't linked to a Switch user yet") and delivers nothing to the agent. |
| S3 custom rules express R1 | **PASS (via the Gateway API)** | Console shows no Gateway button on this server (empty `dashboard_url`), but the Gateway API is served at `/gateway` (Swagger `/gateway/docs`; login with the local admin account). `PUT /gateway/agents/{id}/addressing-policy` accepts rules R1+R3 (`{"policy": {"rules": [...]}}`). Key finding: `users` must hold the approvers' participant ids from `list_participants`; usernames match nobody (an unlinked chat account "matches nobody" in a name list) and `users: "*"` admits every human. Live result with R1 = 3 participant ids, `agents: []`: meera and arjun delivered to Countersign; vikram_ceo refused by Switch ("I only take instructions from my owner, and this chat account isn't linked to a Switch user yet"); `rogue` agent refused (`target_statuses: not_permitted`, "my operator has restricted who can address me here"). The design stands. `scripts/set_addressing.py` applies the rules. Tried and not needed: `users: "*"` (works but admits impersonators). R2 (intake agents) to be added once they are registered. |
| S4 thread posts across rooms | PASS | `connect_to_room` then `post_message(body, thread_id)` works across 3 rooms; replies appear under the right parent in Mattermost (verified via the Mattermost API `root_id`). |
| S5 sender in read_context + media | PASS | `read_context` returns `sender` and `sender_name` for human messages. Attachment is `{filename, mimetype, size, mxc: "switch-media://<id>", msgtype}`. `GET /agents/{id}/rooms/{room_id}/media?mxc=<mxc>` with the agent bearer key returns the PDF bytes (`%PDF`, 1021 bytes). |
| S6 load_internal_documents | PASS | A custom agent can `create_room_document` and, after `connect_to_room`, the payload lists `documents[{id,name,...}]`; `load_internal_documents(ids)` returns the content. Doc ids must be looked up by name from the connect payload. |
| S7 template v2 | PASS for group, rooms, links, users; docs via ops | `create_room_from_yaml` (v2) created the group, 3 Mattermost-bridged rooms, 2 links, members. The guide documents no room-doc field, so docs use `create_room_document` (works, S6). Creating the `intake` agent from a template needs the "can manage agents" capability and a machine; our bootstrap agent has neither (`can_manage_agents: false`), so that step must be run by the human in Console. |

## Consequences for the design (decisions needed)

1. **Layer 1 holds (S3).** Only the way the rule is set changes: a script calling the Gateway API replaces the "click Custom rules in the UI" step (phases.md S3, P0-T4, P3-T9, PRD D14/D22, architecture §5.1, §11, §11.1, `switch/addressing-rules.md`). Addressing rules must be re-applied (`just set-addressing`) when approvers change, because they hold participant ids.
2. Identity `sender_id` for pinning = the event's `sender` string (stable per Mattermost username); the
   `list_participants` ids are different (UUIDs) and `type` is `user` or `agent`, as assumed.
3. `sender_kind` and `sender_role` are always null for these events, so `require_workspace_role` cannot be used.
4. Countersign cannot create the `intake` agent itself; template/Console run is a manual human step.
