# Countersign addressing rules (layer 1)

Switch's addressing policy decides who can reach Countersign at all, before Countersign sees a message.
These are the rules (architecture.md §11.1). Countersign re-checks them at startup and every 5 minutes.

| Rule | rooms | users | agents | owner | platform |
|---|---|---|---|---|---|
| R1 approvals | `#ap-approvals` | meera, arjun, kabir | none | no | no |
| R2 intake | `#ap-intake` | none | `intake`, `scripted-intake` | no | no |
| R3 owner ops | all 3 rooms | none | none | yes | no |

`users` takes the approvers' **participant ids** (the `id` of each `type: user` entry in `list_participants`
for `#ap-approvals`), not usernames: an unlinked chat account matches no name, but its participant id does.
Verified on Switch 0.31.0 (`docs/media/spikes.md`, S3): approvers are delivered, `vikram_ceo` and the
`rogue` agent are refused by Switch, which posts its own refusal in the room.

## Apply

```bash
just set-addressing        # prompts for the Gateway admin password
uv run python scripts/set_addressing.py --dry-run   # print the rules, change nothing
```

The script resolves room, approver and agent ids from the running server and calls
`PUT <SWITCH_URL>/gateway/agents/<countersign id>/addressing-policy`. Run it again after adding an
approver (edit `policy.yaml`, add the person to the room) or registering an intake agent.

Console-run local servers have no Gateway page, but serve the API at `<SWITCH_URL>/gateway`
(Swagger at `/gateway/docs`). The local admin email is `admin@switch.local`; its password is the
`GATEWAY_ADMIN_PASSWORD` line in the server's `switch-core.env`
(`~/Library/Application Support/switchdash/local-switch-server/native/run/` on macOS Console).
If a Gateway UI exists, the same rules can be entered under the agent's Custom rules.

## Switch's refusal wording

Switch writes the layer-1 refusal itself. For an unlisted person it reads "I only take instructions from my
owner, and this chat account isn't linked to a Switch user yet"; for an agent, "You're not permitted to
direct messages to me in this room". The README shows both as screenshots and explains them.
