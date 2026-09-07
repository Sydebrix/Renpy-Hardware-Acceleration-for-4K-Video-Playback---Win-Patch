# Renpy Hardware Acceleration for 4K Video Playback- Windows-Patch

## What it does
Replaces the native Renpy rendering pipeline with calls to mpv.exe, to enable smooth playback of high-resolution videos, 
or smooth video playback in general, in case you run games on a weak CPU. 

It patches Renpy itself, so the original game-files and assets stay untouched, and no de-compilation is necessary. 

## Compatibility
- Based on Ren'Py 8.4.1.25072401
- Earlier 8.x version should work, but are untested.
- Versions prior to 8 are unknown. If you try it, let me know how it went. 
- You can find the Renpy version of a given game in the `SomeGame-v1\log.txt` file


## How to install
- Navigate to your game folder
- Create a copy/a backup of `SomeGame-v1\renpy\exports\mediaexports.py`
- Click on the "Code" button above on this github page and download the repository as a zip file
- Extract it
- Copy/Move the contents to the root folder of your Renpy game, e.g. `SomeGame-v1\`
- It will ask you whether it should overwrite `mediaexports.py` - click yes.
- Done

## Features
- Looks and feels like the normal player
- Supports skipping by clicking/pressing a button
- Supports responsive window sizes
- Supports over-sampling behavior equivalent to Renpy's native one, e.g. video@2.webm will be handled as a video with double the size in x and y

## Caveat
- Works only on Windows
- Volume isn't taken from Renpy and has to be set manually. It is 50% by default, and can be simply changed directly in the `mediaexports.py` on line 50:
```python
# User-editable mpv volume. 0 = mute, 100 = normal/full volume.
_MPV_VOLUME = 50
```

## Sources
- mpv-v0.41.0-x86_64-pc-windows-msvc: https://github.com/mpv-player/mpv
- Renpy mediaexports.py: https://github.com/renpy/renpy/blob/master/renpy/exports/mediaexports.py

## Security Tip
- If you are cautious of the bundled binaries, you can only take the `mediaexports.py` from this repo and then add the mpv files yourself, it doesn't matter. 