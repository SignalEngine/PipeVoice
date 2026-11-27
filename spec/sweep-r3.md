## Authorization based on a stale configuration snapshot plus unsynchronised whole-file read/modify/write
- Fixed at the source (3c08c47, 5ebeddb): usage.py no longer writes config.json at all (state moved to usage.json), and the switch is re-read raw from config.json on disk immediately before urlopen; unreadable/damaged/missing = no send.
- The startup cfg check at maybe_send is only an early exit; it can never authorise a send by itself.

### Every config read/write in usage.py:
23:    return config.CONFIG_PATH.with_name("usage.json")
26:def _read() -> dict:
34:def _write(data: dict) -> None:
44:        state = _read()
45:        if not cfg.usage_count or state.get("last_count_day") == today:
49:            _write(state)
55:        # Raw read: Config.load() saves defaults (count on) over a damaged file.
57:        if json.loads(config.CONFIG_PATH.read_text(encoding="utf-8")).get("usage_count", True) is not True:
62:        _write(state)

### Other privacy-switch readers in the app:
wisprlite/settings.py:943:    usage_count_var = tk.BooleanVar(value=cfg.usage_count)
wisprlite/settings.py:1862:    check(c, "Send an anonymous daily count", usage_count_var,
wisprlite/settings.py:2189:        cfg.usage_count = bool(usage_count_var.get())
wisprlite/config.py:171:    usage_count: bool = True         # send one anonymous daily count: version + random install id
