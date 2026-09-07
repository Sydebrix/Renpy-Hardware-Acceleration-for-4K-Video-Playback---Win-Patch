# Copyright 2004-2025 Tom Rothamel <pytom@bishoujo.us>
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

from __future__ import division, absolute_import, with_statement, print_function, unicode_literals  # type: ignore
from renpy.compat import PY2, basestring, bchr, bord, chr, open, pystr, range, round, str, tobytes, unicode  # *

import ctypes
import os
import subprocess
import time

from ctypes import wintypes

import renpy
from renpy.exports.commonexports import renpy_pure


# Game-local mpv movie_cutscene replacement.
#
# Expected layout:
#
#   game/
#       mpv/
#           mpv.exe
#
# Cutscene input is handled directly through Win32 by this module.

_MPV_SKIP_EXIT_CODE = 42
_MPV_POLL_INTERVAL = 0.01

# User-editable mpv volume. 0 = mute, 100 = normal/full volume.
_MPV_VOLUME = 50


def _mpv_log_path():
    return os.path.join(renpy.config.gamedir, "mpv_movie_patch.log")


def _mpv_player_log_path():
    return os.path.join(renpy.config.gamedir, "mpv_movie_patch_mpv.log")


def _mpv_dir():
    return os.path.join(renpy.config.gamedir, "mpv")


def _mpv_exe():
    return os.path.join(_mpv_dir(), "mpv.exe")


def _mpv_input_conf():
    return os.path.join(_mpv_dir(), "input.conf")


def _mpv_log(message):
    try:
        with open(_mpv_log_path(), "a", encoding="utf-8") as f:
            f.write("[{}] {}\n".format(
                time.strftime("%Y-%m-%d %H:%M:%S"),
                message,
            ))
    except Exception:
        pass


def _mpv_find_renpy_hwnd():
    """
    Finds the largest visible top-level Win32 window belonging to this process.
    """

    if os.name != "nt":
        raise RuntimeError("mpv cutscene backend currently supports Windows only.")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    pid = os.getpid()
    candidates = []

    enum_proc_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )

    user32.EnumWindows.argtypes = [enum_proc_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL

    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL

    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    user32.GetClientRect.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.RECT),
    ]
    user32.GetClientRect.restype = wintypes.BOOL

    @enum_proc_type
    def enum_window(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True

        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))

        if window_pid.value != pid:
            return True

        rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return True

        width = max(0, rect.right - rect.left)
        height = max(0, rect.bottom - rect.top)
        area = width * height

        if area > 0:
            # mpv's Win32 --wid interface expects HWND cast to uint32_t.
            hwnd_value = ctypes.cast(hwnd, ctypes.c_void_p).value
            if hwnd_value is not None:
                candidates.append((area, int(hwnd_value) & 0xFFFFFFFF))

        return True

    if not user32.EnumWindows(enum_window, 0):
        error = ctypes.get_last_error()
        if error:
            raise ctypes.WinError(error)

    if not candidates:
        raise RuntimeError("Could not locate Ren'Py's top-level window.")

    candidates.sort(reverse=True)
    return candidates[0][1]


def _mpv_pump_windows_messages():
    """
    Keep the Ren'Py parent window responsive while mpv runs, but do NOT
    dispatch keyboard/mouse input into Ren'Py.

    Dispatching those messages would let SDL queue them, causing game-menu
    actions/clicks to fire immediately after the cutscene ends.
    """

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    PM_REMOVE = 0x0001

    WM_INPUT = 0x00FF

    WM_KEYFIRST = 0x0100
    WM_KEYLAST = 0x0109

    WM_MOUSEFIRST = 0x0200
    WM_MOUSELAST = 0x020E

    msg = wintypes.MSG()

    while user32.PeekMessageW(
        ctypes.byref(msg),
        None,
        0,
        0,
        PM_REMOVE,
    ):
        message = int(msg.message)

        is_input = (
            message == WM_INPUT
            or WM_KEYFIRST <= message <= WM_KEYLAST
            or WM_MOUSEFIRST <= message <= WM_MOUSELAST
        )

        if is_input:
            # Consume it here instead of forwarding it to Ren'Py/SDL.
            continue

        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def _mpv_read_skip_inputs():
    """
    Read physical skip inputs directly from Windows, independent of whether
    the Ren'Py parent or mpv child currently owns keyboard focus.

    Returns a tuple:
        (left mouse, Enter, Space, Escape)
    """

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    VK_LBUTTON = 0x01
    VK_RETURN = 0x0D
    VK_SPACE = 0x20
    VK_ESCAPE = 0x1B

    def down(vk):
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    return (
        down(VK_LBUTTON),
        down(VK_RETURN),
        down(VK_SPACE),
        down(VK_ESCAPE),
    )


def _mpv_skip_pressed(previous, current):
    """
    True only on a new press, so a mouse/key already held when the movie
    begins does not instantly skip it.
    """

    return any(now and not before for before, now in zip(previous, current))


def _mpv_stop_process(process):
    if process is None or process.poll() is not None:
        return

    try:
        process.terminate()
        process.wait(timeout=1.0)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _mpv_resolve_movie(filename):
    """
    Applies the same fullscreen oversampling lookup used by video.py, then
    converts the Ren'Py filename into a physical path for the external player.
    """

    if not isinstance(filename, str):
        raise TypeError("mpv movie_cutscene backend requires a string filename.")

    resolved = renpy.display.video.find_oversampled_filename(filename)
    physical = renpy.loader.transfn(resolved)

    if not os.path.isfile(physical):
        raise IOError("Resolved movie is not a loose physical file: {!r}".format(physical))

    return resolved, physical


def _mpv_build_command(hwnd, physical_path, loops):
    command = [
        _mpv_exe(),
        "--no-config",
        "--wid={}".format(hwnd),

        # Windows hardware decode/render path.
        "--vo=gpu-next",
        "--gpu-api=d3d11",
        "--gpu-context=d3d11",
        "--hwdec=d3d11va",
        "--volume={}".format(_MPV_VOLUME),

        # Keep the embedded window visually minimal.
        "--osc=no",
        "--osd-level=0",
        "--input-default-bindings=no",
        "--input-builtin-bindings=no",
        "--input-cursor=yes",
        "--cursor-autohide=1000",
        "--keep-open=no",
        "--idle=no",

        # Useful diagnostics while we test this.
        "--msg-level=all=warn,vd=info,vo=info",
    ]

    # Ren'Py's loops means "extra playthroughs".
    # mpv's --loop-file=N means "seek back N times", which is equivalent.
    if loops == -1:
        command.append("--loop-file=inf")
    elif loops > 0:
        command.append("--loop-file={}".format(int(loops)))
    else:
        command.append("--loop-file=no")

    command.append(physical_path)
    return command


def _renpy_movie_cutscene(filename, delay=None, loops=0, stop_music=True):
    """
    Original Ren'Py 8.4 movie_cutscene implementation, retained as fallback.
    """

    renpy.exports.mode("movie")

    if stop_music:
        renpy.audio.audio.set_force_stop("music", True)

    renpy.exports.movie_start_fullscreen(filename, loops=loops)

    renpy.ui.saybehavior()

    if delay is None or delay < 0:
        renpy.ui.soundstopbehavior("movie")
    else:
        renpy.ui.pausebehavior(delay, False)

    if renpy.game.log.forward:
        roll_forward = True
    else:
        roll_forward = None

    rv = renpy.ui.interact(suppress_overlay=True, roll_forward=roll_forward)

    renpy.exports.movie_stop()

    if stop_music:
        renpy.audio.audio.set_force_stop("music", False)

    return rv


def movie_cutscene(filename, delay=None, loops=0, stop_music=True):
    """
    :doc: movie_cutscene

    Game-patched movie_cutscene implementation.

    Fullscreen cutscenes are played by an external mpv process embedded as a
    child of the Ren'Py window. If that backend cannot be started, playback
    falls back to Ren'Py's original implementation.

    The public API/return convention remains the same:
      - True if the player dismisses the cutscene.
      - False on normal completion or delay expiry.
    """

    process = None
    mpv_log_file = None
    music_forced_stopped = False

    try:
        if os.name != "nt":
            raise RuntimeError("Not running on Windows.")

        if not os.path.isfile(_mpv_exe()):
            raise IOError("mpv.exe not found at {!r}".format(_mpv_exe()))

        resolved_name, physical_path = _mpv_resolve_movie(filename)
        hwnd = _mpv_find_renpy_hwnd()

        _mpv_log(
            "Starting {!r} -> {!r}; HWND={}; loops={!r}; delay={!r}".format(
                filename,
                resolved_name,
                hwnd,
                loops,
                delay,
            )
        )

        renpy.exports.mode("movie")

        if stop_music:
            renpy.audio.audio.set_force_stop("music", True)
            music_forced_stopped = True

        command = _mpv_build_command(hwnd, physical_path, loops)

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        mpv_log_file = open(
            _mpv_player_log_path(),
            "a",
            encoding="utf-8",
            errors="replace",
        )

        mpv_log_file.write(
            "\n\n===== {} : {} =====\n".format(
                time.strftime("%Y-%m-%d %H:%M:%S"),
                physical_path,
            )
        )
        mpv_log_file.write("COMMAND: {!r}\n".format(command))
        mpv_log_file.flush()

        process = subprocess.Popen(
            command,
            cwd=_mpv_dir(),
            stdin=subprocess.DEVNULL,
            stdout=mpv_log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )

        deadline = None
        if delay is not None and delay >= 0:
            deadline = time.monotonic() + float(delay)

        timed_out = False
        skipped = False

        # Snapshot input state so a button already held when playback starts
        # does not immediately dismiss the movie.
        previous_skip_inputs = _mpv_read_skip_inputs()

        while process.poll() is None:
            _mpv_pump_windows_messages()

            current_skip_inputs = _mpv_read_skip_inputs()
            if _mpv_skip_pressed(previous_skip_inputs, current_skip_inputs):
                skipped = True
                _mpv_stop_process(process)
                break

            previous_skip_inputs = current_skip_inputs

            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                _mpv_stop_process(process)
                break

            time.sleep(_MPV_POLL_INTERVAL)

        # Discard any input messages that arrived on the same press/frame.
        _mpv_pump_windows_messages()

        return_code = process.poll()

        _mpv_log(
            "mpv exited: code={!r}; timed_out={!r}; skipped={!r}; file={!r}".format(
                return_code,
                timed_out,
                skipped,
                resolved_name,
            )
        )

        if skipped:
            return True

        if timed_out:
            return False

        # Retained for compatibility if mpv somehow exits through an older
        # input.conf with our historical skip exit code.
        if return_code == _MPV_SKIP_EXIT_CODE:
            return True

        if return_code == 0:
            return False

        raise RuntimeError(
            "mpv exited with unexpected code {!r}; see {!r}".format(
                return_code,
                _mpv_player_log_path(),
            )
        )

    except Exception as e:
        _mpv_log(
            "mpv backend failed for {!r}: {!r}. Falling back to Ren'Py.".format(
                filename,
                e,
            )
        )

        _mpv_stop_process(process)

        # Restore the state before calling the original function; the original
        # implementation will apply stop_music itself.
        if music_forced_stopped:
            try:
                renpy.audio.audio.set_force_stop("music", False)
            except Exception:
                pass
            music_forced_stopped = False

        return _renpy_movie_cutscene(
            filename,
            delay=delay,
            loops=loops,
            stop_music=stop_music,
        )

    finally:
        _mpv_stop_process(process)

        if mpv_log_file is not None:
            try:
                mpv_log_file.close()
            except Exception:
                pass

        if music_forced_stopped:
            try:
                renpy.audio.audio.set_force_stop("music", False)
            except Exception:
                pass


def toggle_music():
    """
    :undocumented:
    Does nothing.
    """


def music_start(filename, loops=True, fadeout=None, fadein=0):
    """
    Deprecated music start function, retained for compatibility. Use
    renpy.music.play() or .queue() instead.
    """

    renpy.audio.music.play(filename, loop=loops, fadeout=fadeout, fadein=fadein)


def music_stop(fadeout=None):
    """
    Deprecated music stop function, retained for compatibility. Use
    renpy.music.stop() instead.
    """

    renpy.audio.music.stop(fadeout=fadeout)


def play(filename, channel=None, **kwargs):
    """
    :doc: audio

    Plays a sound effect. If `channel` is None, it defaults to
    :var:`config.play_channel`. This is used to play sounds defined in
    styles, :propref:`hover_sound` and :propref:`activate_sound`.
    """

    if filename is None:
        return

    if channel is None:
        channel = renpy.config.play_channel

    renpy.audio.music.play(filename, channel=channel, loop=False, **kwargs)
