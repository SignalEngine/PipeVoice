"""`--mcp`: a stdio MCP server exposing pipevoice.listen + pipevoice.transcribe.

Spawned on demand by an MCP client (Claude Code etc.). It forwards each call to
the resident tray app over the loopback control bridge, so the heavy MCP
machinery lives only in this ephemeral process — the resident app stays light.
"""

from __future__ import annotations


def _port() -> int:
    from . import config
    return int(getattr(config.Config.load(), "mcp_port", 49518))


def _send(op: str, read_timeout: float = 130.0, **kw) -> dict:
    import socket
    from . import agent_bridge
    try:
        return agent_bridge.send_request(_port(), {"op": op, **kw}, timeout=read_timeout)
    except socket.timeout:
        # connected, but no reply in time (e.g. the user never spoke)
        return {"status": "timeout", "text": "", "error": "no response in time"}
    except OSError:
        # connection refused / reset -> app not running or MCP toggle off
        return {"status": "app_not_running", "text": "",
                "error": "PipeVoice isn't running, or its Agent MCP toggle is off."}


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("pipevoice")

    @mcp.tool()
    def listen(prompt: str = "", timeout_seconds: int = 45, mode: str = "") -> dict:
        """Ask the user to answer by voice and return what they said as text.

        Use when you need information only the user has. PipeVoice shows a prompt,
        the user speaks (push-to-talk by default), and the transcript is returned.
        `mode` may be 'push_to_talk' or 'hands_free' (default: the user's setting).
        """
        return _send("listen", read_timeout=float(timeout_seconds) + 60.0,
                     prompt=prompt, timeout=timeout_seconds, mode=mode)

    @mcp.tool()
    def transcribe(path: str, format: str = "json", language: str = "", model_size: str = "") -> dict:
        """Transcribe a local audio or video file to timed text using local whisper.

        `format`: 'json' returns text + segment/word timestamps; 'srt'/'vtt' return
        a ready-made caption string in `captions`. `language` is an optional ISO
        code (blank = auto-detect). No API key needed; runs offline.
        """
        return _send("transcribe", read_timeout=900.0,
                     path=path, format=format, language=language, model_size=model_size)

    @mcp.tool()
    def record_screen(prompt: str = "", timeout_seconds: int = 300) -> dict:
        """Ask the user to SHOW you the problem, and get back a video plus what they said.

        Use this when words are the wrong medium: a visual bug, a layout that
        looks wrong, a UI that behaves oddly, an animation that stutters, or any
        "it does a weird thing" you would otherwise need three rounds of
        questions to pin down. Also use it when the user is clearly struggling
        to describe something in text.

        The user drags a box over the part of the screen that matters, narrates
        what is wrong, and presses their hotkey to stop. You get:
          video_path      an .mp4 you can read directly if you handle video
          transcript      what they said, already transcribed locally
          transcript_path the same text as a file
        Read `transcript` first - it is usually enough to locate the problem,
        and it costs nothing compared with decoding frames.

        `prompt` is shown to the user, so say what you want to see:
        "show me the checkout page and click the button that does nothing".

        Consent is built in and you cannot bypass it: the user chooses the
        region by hand, and Esc cancels and writes nothing. The file stays on
        their machine - this tool never uploads it anywhere, even if they have
        configured a destination for their own use.

        Returns status 'cancelled' if they pressed Esc, 'timeout' if they are
        still recording after timeout_seconds. Neither is an error; a person
        changed their mind or is taking their time.
        """
        return _send("record_screen", read_timeout=float(timeout_seconds) + 120.0,
                     prompt=prompt, timeout=timeout_seconds)

    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()
