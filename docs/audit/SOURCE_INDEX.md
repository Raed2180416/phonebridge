# PhoneBridge Audit Source Index

Accessed on: 2026-03-02 (local timezone)

## Source Policy
- Priority order: official/vendor documentation first.
- Community sources are only used when official docs are silent or ambiguous.
- This audit run did not require community sources for core claims.

## Official Sources

| ID | URL | Authority | Used For | Why Included | Confidence |
| --- | --- | --- | --- | --- | --- |
| WEB-01 | https://docs.syncthing.net/dev/rest.html | Official (Syncthing docs) | Syncthing REST authentication model (`X-API-Key` header), API endpoint patterns | Validates backend `http://127.0.0.1:8384/rest/...` usage and key header expectation | High |
| WEB-02 | https://docs.syncthing.net/rest/system-ping-get.html | Official (Syncthing docs) | `/rest/system/ping` semantics (`pong` response) | Confirms what `Syncthing.is_running()` actually checks in current code | High |
| WEB-03 | https://tailscale.com/kb/1241/tailscale-up | Official (Tailscale docs) | `tailscale up` behavior and `--operator` option | Validates permission guidance surfaced by `backend/tailscale.py` | High |
| WEB-04 | https://tailscale.com/kb/1278/tailscaled | Official (Tailscale docs) | `tailscaled` daemon role and root-owned service model | Supports diagnosis of daemon/operator permission failure modes | High |
| WEB-05 | https://raw.githubusercontent.com/Genymobile/scrcpy/master/doc/audio.md | Official (scrcpy upstream docs) | Audio forwarding capabilities, `--audio-source`, codec defaults, Android version constraints | Validates code use of `--audio-source=output` and `--audio-source=voice-call` | High |
| WEB-06 | https://developer.android.com/reference/android/media/MediaRecorder.AudioSource | Official (Android docs) | Android audio source categories and platform constraints | Supports risk analysis for call-audio source behavior on OEM devices | Medium |
| WEB-07 | https://man7.org/linux/man-pages/man1/systemctl.1.html | Primary reference (man page mirror) | `systemctl is-enabled` state outputs, including `linked-runtime` | Explains runtime evidence showing `linked-runtime` for Syncthing unit | High |
| WEB-08 | https://man7.org/linux/man-pages/man5/org.freedesktop.systemd1.5.html | Primary reference (D-Bus/systemd interface man page mirror) | `ActiveState`, `SubState`, `UnitFileState` semantics | Supports distinction between API reachability and unit activity state | High |

## Community Sources

No community sources were required for core technical claims in this audit.

## Source-to-Claim Mapping Notes
- Syncthing findings reference WEB-01, WEB-02, WEB-07, WEB-08.
- Tailscale operator/permission findings reference WEB-03, WEB-04.
- scrcpy/call audio capability and limits reference WEB-05, WEB-06.
