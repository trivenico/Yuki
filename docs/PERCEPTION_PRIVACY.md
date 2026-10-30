# Yuki local perception and desktop assistance

Yuki's perception foundation is deliberately opt-in and local-first.

## Current capabilities

- **One-shot screen capture** from the desktop's Screen & Camera panel. The screenshot is kept in memory for preview and is not saved or sent to a model by this feature.
- **One-shot camera frame** when the user clicks Camera snapshot. The camera device is released immediately; no video recording or background polling is implemented.
- **Active-window metadata** through /observe window on supported Windows desktops. This reads only the foreground window title and prints it locally; it does not capture pixels or send the title to a model.
- **Privacy status** through /observe status.
- **Separate settings** for screen observation, camera access, and desktop control. All three permissions default to false and are saved independently.

## Setup

The base Yuki installation does not require image libraries. On a Windows machine, install the optional dependencies only if you want these features:

```powershell
python -m pip install -r requirements-perception.txt
```

The operating system may still require its own screen-recording or camera permission. Yuki does not attempt to bypass OS privacy controls.

## Privacy controls

- Nothing is captured automatically at startup.
- No continuous screen watching, camera streaming, audio capture, facial recognition, or emotion classification is implemented by this module.
- Frames are not persisted by the capture feature and are not uploaded by the capture feature.
- The camera is released in a finally block even if capture fails.
- Disabling a setting blocks the corresponding API before an optional capture backend is imported.
- Keep cloud-provider privacy in mind before any future feature sends visual content to an AI service. Any such integration must disclose the destination and require a separate explicit action.

## Current limitations and next milestones

This is a capture and permission foundation, not yet a full vision assistant. The current provider interface is text-oriented; captured images are not yet passed to a vision-capable model, and Yuki does not yet generate contextual suggestions from screenshot pixels. The active-window title can be sensitive, so it is not sent to the model by /observe window.

The desktop-control setting is a reserved, off-by-default permission only. Keyboard/mouse automation and remote control are **not implemented by this change**. Future control should use narrowly scoped actions, visible indicators, an emergency stop, allow-listed applications/actions, and confirmation before consequential actions.

Mood inference is also not implemented. Camera images alone cannot reliably establish how a person feels; a future companion should ask how the user is doing rather than claim to know their emotional state from appearance.
