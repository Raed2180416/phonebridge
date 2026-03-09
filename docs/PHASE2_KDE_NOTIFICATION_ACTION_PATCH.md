# Phase 2 (Optional): KDE Connect Action Metadata Patch

PhoneBridge Phase 1 intentionally assumes current KDE Connect D-Bus notification objects do not expose full quick-action metadata. This document defines the optional Phase 2 path to add full action parity.

## Goal

Expose full notification action metadata from KDE Connect over D-Bus so PhoneBridge can render and forward real per-notification actions (not just reply/copy/dismiss/open).

## Upstream Area

- Repository: `network/kdeconnect-kde`
- Plugin: `plugins/notifications`
- Key files:
  - `notification.h`
  - `notification.cpp`

## Required Patch Shape

1. Add a D-Bus-exposed property or method on `org.kde.kdeconnect.device.notifications.notification` for action IDs/labels.
2. Populate it from existing parsed notification actions (`m_actions` in current code path).
3. Preserve existing behavior for `dismiss`, `reply`, and `sendReply`.

## Packaging / Rollout

1. Ship patched `kdeconnect-kde` via a Nix overlay/custom package.
2. Keep PhoneBridge runtime capability detection:
   - patched build: use full action metadata
   - unpatched build: keep Phase 1 reliable subset fallback
3. Pin and track KDE Connect version updates so patch drift is managed explicitly.

## Validation Checklist

- Introspection of `...notifications/<id>` shows the new actions property/method.
- PhoneBridge action buttons match source app actions for supported notifications.
- Unknown/missing action metadata still gracefully falls back to reliable subset.
