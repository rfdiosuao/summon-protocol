<p align="right"><a href="EVOX-INTEGRATION.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Local EvoX integration

SUMMON can use a local EvoX process as the planner behind an existing Passport nameplate. Speech recognition, nameplate routing, desktop execution, voice response, and experience upload continue to use the established SUMMON path.

```text
Passport audio → Hub speech recognition → local EvoX on the nameplate's PC
  → SUMMON action.request → authorized desktop shell and evidence
  → EvoX observes the receipt → Hub voice response and cloud experience
```

The adapter starts a short-lived CLI with tools, session persistence, extensions, and memory disabled. EvoX proposes one action at a time; it cannot execute commands directly on the adapter PC. Actions still require the active SUMMON lease and the target shell's granted capability. An `UNKNOWN` result stops the turn without retry.

## Requirements

- Windows with the EvoX CLI and a usable private provider/model configuration.
- A successful real text request through the selected EvoX model.
- A private credential file for the existing Passport Agent identity. Reuse this identity; do not register a second Agent.
- An online SUMMON desktop client for the target shell.

Keep the private JSON configuration and credentials outside Git and shared folders. Configure ACLs so only the user and SYSTEM can read them. The optional `credential_file` JSON contains `api_key`; the adapter injects it into the EvoX child process environment, never its command line. Omit it to use EvoX's existing authentication.

Configure `model` with `backend: "evox"`, the CLI executable, private EvoX runtime directory, provider name, and model ID. Start the existing Agent identity:

```powershell
python -m hub.passport_agent --config C:/path/private/evox-agent.json --credentials C:/path/private/passport-agent-credentials.json
```

Only one active connection may use an Agent token at a time. Stop the server-side Agent before switching to this local process. Verify the existing nameplate, `mode: evox`, and `/v1/agents/me` connected state. Do not run a second Passport Agent with the same identity.

## Verification

Confirm the target shell is online. Send a low-risk observation request from Passport and correlate the EvoX plan, shell receipt, final response, Passport `play.done`, and cloud experience record. A task is complete only after its real receipt and audible response are confirmed. Never replay an unknown action.

The integration invokes EvoX as an ephemeral CLI planner. It does not attach to EvoX's desktop conversation, long-lived history, or cross-session memory. SUMMON experiences remain uploaded by the desktop client. A turn is bounded to four actions and each EvoX call to 12 seconds. A timeout stops that child process. Results depend on the target machine's granted capabilities and execution policy.

## Acceptance status

- [x] Local EvoX CLI returned a real response from its configured model service.
- [x] The existing Hub Agent identity connected in EvoX mode.
- [x] The user confirmed Wi-Fi scanning and saving work.
- [x] The build with unused BLE components disabled passed GitHub Firmware and Static checks and was flashed; a follow-up display-buffer reduction is building.
- [ ] Repeat the full Passport Wi-Fi speech, EvoX planning, online shell execution, spoken receipt, and cloud experience flow with stable device connectivity.
