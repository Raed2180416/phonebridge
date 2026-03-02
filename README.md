# PhoneBridge

PhoneBridge is a unified desktop control plane for managing a phone from Linux.  
It centralizes the tools you already use—KDE Connect, ADB/scrcpy, Syncthing and Tailscale—into a single Qt6 interface.

You can answer and place calls through your laptop, control media playback, sync files, mirror your phone’s screen, view notifications and even toggle radios without juggling multiple applications.

<p align="center">
  <img src="pics/maindash.png" alt="PhoneBridge main dashboard" width="900" />
</p>

---

## ⚠️ Personal Project Notice

PhoneBridge is maintained primarily for my own setup:

- **Phone:** Nothing Phone 3a Pro  
- **Laptop:** Ryzen 7 / RTX 4060  
- **OS:** NixOS  
- **WM:** Hyprland  

I’m sharing the code publicly, but I do **not** guarantee it will work out-of-the-box on every distribution, desktop environment or hardware.

Contributions are welcome.  
Maintenance is best-effort.

---

# Core Capabilities

---

## 🌐 Connectivity Control

- **Tailscale mesh & Syncthing lifecycle**  
  Start/stop Tailscale and Syncthing from the dashboard and view their current status.

- **Bluetooth and Wi-Fi toggles**  
  Toggle Bluetooth or Wi-Fi on the phone via ADB commands with post-state verification.

- **Connectivity health checks**  
  The app polls network state and displays busy flags to prevent conflicting toggles.

<p align="center">
  <img src="pics/network.png" alt="Connectivity controls" width="900" />
</p>

---

## 📞 Calls & Audio Routing

- **Place calls from your PC**  
  Dial contacts or numbers directly; phone audio routes through the laptop and you can switch between phone and laptop mid-call.

- **Incoming call pop-ups**  
  Notifications show caller details with options to answer, deny with SMS or route audio to the laptop.

- **Call audio routing modes**  
  Explicit modes let you:
  - Keep calls on the phone  
  - Switch them to the laptop (using laptop mic + speakers)  
  - Switch back to phone  

- **Device & volume selection**  
  Choose default input/output devices for calls and adjust their volumes from the settings page.

<p align="center">
  <img src="pics/calls.png" alt="Calls interface" width="900" />
</p>
<p align="center">
  <img src="pics/callpopup.png" alt="Incoming call popup" width="520" />
</p>

---

## 🎵 Media Control & Clipboard

- **Now Playing panel**  
  View current media sessions and control playback.  
  If multiple players are active, choose which one to control.

- **Clipboard sharing**  
  Sync phone clipboard events to desktop, with optional auto-share and history sanitization.

---

## 📩 Messages & Notifications

- **Notification mirror**  
  View live phone notifications on your desktop with swipe-to-dismiss and two-way sync.

- **SMS compose**  
  Send text messages from your laptop by selecting a contact or entering a number.

- **Notification actions**  
  Supported notifications expose reply/action buttons forwarded to the phone app.

<p align="center">
  <img src="pics/notifpanel.png" alt="Notification panel" width="900" />
</p>

---

## 📂 File Sync & Transfer

- **Syncthing status & control**  
  Monitor sync progress for each folder, pause/resume transfers and change local storage paths.

- **File transfer**  
  Send files from your laptop to your phone via KDE Connect and add new folders for automatic syncing.

- **Folder management**  
  View all synced folders (default and custom), toggle sync on/off and browse them within the app.

<p align="center">
  <img src="pics/filesync.png" alt="File sync status" width="900" />
</p>
<p align="center">
  <img src="pics/filebrowser.png" alt="File browser" width="900" />
</p>

---

## 📱 Device Controls & Mirroring

- **Ring, lock and DND**  
  Quickly ring or lock your phone, toggle Do-Not-Disturb, and control Wi-Fi, Bluetooth and hotspot.  
  (Hotspot toggle opens the relevant settings screen.)

- **Screen mirror and webcam mode**  
  - Mirror phone screen  
  - Use phone as webcam  
  - Take screenshots  
  - Record screen  
  - Rotate image  
  - Type into phone from laptop  

- **Bluetooth autoconnect**  
  Automatically connect phone via Bluetooth at startup (optional).

<p align="center">
  <img src="pics/screenmirror.png" alt="Screen mirroring view" width="900" />
</p>
<p align="center">
  <img src="pics/webcam.png" alt="Webcam mode" width="900" />
</p>

---

## ⚙️ System Integration & Settings

- **Autostart on login**  
  Enable a user-level systemd service to launch PhoneBridge at login.  
  Disable from settings.

- **Hyprland keybind**  
  Toggle the PhoneBridge panel with a custom keybinding defined in Hyprland config.

- **Self-healing runtime on NixOS**  
  If the app encounters missing `libGL.so.1` or `dbus` modules, it automatically re-execs through `steam-run` to satisfy dependencies.

- **Appearance and behavior settings**
  - Configure call pop-ups  
  - Clipboard auto-share  
  - Bluetooth auto-connect  
  - Sync over mobile data  
  - Call audio devices  
  - Volume levels  

<p align="center">
  <img src="pics/settings.png" alt="Settings page" width="900" />
</p>

---

# 🛠 Known Limits & Trade-Offs

PhoneBridge is designed around a specific environment (NixOS + Hyprland).

Known caveats:

- Hard-coded environment assumptions (paths, commands) may need tuning for other systems.
- CLI commands rely on external tools (`adb`, `tailscale`, `syncthing`, `bluetoothctl`, `wpctl`, etc.) being available in `$PATH`.
- Bluetooth call routing can be flaky; activation is gated on profile + mic path presence and rolls back if audio is unavailable.
- Hotspot toggling opens the phone’s hotspot settings page but does not enable it automatically (work in progress).

---

# 🚀 Quick Start

### 1. Install Dependencies

Ensure the following are available:

- Python 3
- Qt6 bindings (`PyQt6` or `PySide6`)
- `adb`
- `tailscale`
- `syncthing`
- `bluetoothctl`
- `wpctl`

---

### 2. Run the App

```bash
python3 main.py
```

---

### 3. NixOS Virtual Environment

On NixOS using a local venv:

```bash
./run-venv-nix.sh
```

This wrapper:

- Re-executes through `steam-run` on runtime errors  
- Exports system `dbus-python` into the venv process  

---

### 4. Enable Autostart (Optional)

Toggle **Start on Login** in settings.

This creates:

```
~/.config/systemd/user/phonebridge.service
```

The service uses the Nix wrapper for background launch.

---

# 📂 Project Structure

```
main.py                    # Application entrypoint and lifecycle management
backend/                   # System integrations (KDE Connect, ADB, Syncthing, Tailscale, audio routing, connectivity)
ui/                        # Qt UI components, themes and pages
run-venv-nix.sh            # NixOS compatibility wrapper for venv launches
docs/PHONEBRIDGE_DEEP_DIVE.md  # Architectural and feature walkthrough
```
edit: there is no partner mobile app that I developed, you just use the official kde connect app. you configure syncthing and tailscale on your phone and enable wireless and wired usb debugging, the app handles both, prefers wired channel whenever available. to get the call audio routing through your machine, you might need to tinker around on advanced bluetooth settings on your device. this feature is extremely fragile, flaky and not promised for every device. it works on my system tho 
---

# 📜 License

PhoneBridge is licensed under the **Apache License 2.0**.  
See the `LICENSE` file for details.
