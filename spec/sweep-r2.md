## Class: unsynchronised full-file config read/modify/write (concurrent writers revert fields)
Fix at the choke point: Config.save is now atomic (tmp + os.replace), so a reader never sees a half-written file and never falls back to defaults. Remaining risk = last-writer-wins on a whole file; worst case for the new fields is one extra count per day (server HLL dedupes by id) or a re-minted id once.

### Every load/save site checked:
wisprlite/profiles.py:94:    cfg = config.Config.load()
wisprlite/profiles.py:97:        cfg.save()
wisprlite/profiles.py:239:        fresh = config.Config.load()
wisprlite/profiles.py:241:        fresh.save()
wisprlite/updater.py:74:        channel = getattr(config.Config.load(), "update_channel", "stable")
wisprlite/updater.py:156:        return str(getattr(config.Config.load(), "update_channel", "")).lower() == "beta"
wisprlite/prep_window.py:38:    cfg = config.Config.load()
wisprlite/history.py:138:    cfg = config.Config.load()
wisprlite/screenrec_tab.py:49:    cfg = cfg or config.Config.load()
wisprlite/screenrec_tab.py:331:        cfg = config.Config.load()
wisprlite/screenrec_tab.py:435:        destination = str(getattr(config.Config.load(), "screenrec_destination", "") or "").strip()
wisprlite/voices_editor.py:48:    cfg = config.Config.load()
wisprlite/voices_editor.py:211:        fresh = config.Config.load()
wisprlite/voices_editor.py:213:        fresh.save()
wisprlite/mcp_shim.py:13:    return int(getattr(config.Config.load(), "mcp_port", 49518))
wisprlite/meetings_tab.py:924:        cfg = config.Config.load()
wisprlite/meetings_tab.py:961:        cfg = config.Config.load()
wisprlite/meetings_tab.py:1645:        cfg = config.Config.load()
wisprlite/meetings_tab.py:2038:                cfg = config.Config.load()
wisprlite/meetings_tab.py:2042:                cfg.save()
wisprlite/meetings_tab.py:2304:            cfg = config.Config.load()
wisprlite/meetings_tab.py:2365:        destination = (config.Config.load().screenrec_destination or "").strip()
wisprlite/meetings_tab.py:2563:            _mkey = str(getattr(config.Config.load(), "meeting_hotkey", "") or "").strip()
wisprlite/meetings_tab.py:2764:        cfg = config.Config.load()
wisprlite/meetings_tab.py:2802:        cfg = config.Config.load()
wisprlite/meetings_tab.py:2920:        cfg = config.Config.load()
wisprlite/meetings_tab.py:2922:        cfg.save()
wisprlite/meetings_tab.py:2934:        cfg = config.Config.load()
wisprlite/meetings_tab.py:2959:        cfg = config.Config.load()
wisprlite/meetings_tab.py:3024:        cfg = config.Config.load()
wisprlite/meetings_tab.py:3309:        cfg = config.Config.load()
wisprlite/meetings_tab.py:3367:            and provider_ready(config.Config.load().cleanup_provider)
wisprlite/meeting.py:277:        chosen = str(Config.load().meetings_dir or "").strip()
wisprlite/keyprompt.py:138:            cfg.save()
wisprlite/keyprompt.py:147:            cfg.save()
wisprlite/settings.py:626:    cfg = config.Config.load()
wisprlite/settings.py:2131:            cfg.profiles = config.Config.load().profiles
wisprlite/settings.py:2136:            cfg.voices = config.Config.load().voices
wisprlite/settings.py:2192:            disk = config.Config.load()
wisprlite/settings.py:2235:        cfg.knowledge_dir = (config.Config.load().knowledge_dir
wisprlite/settings.py:2244:        cfg.save()
wisprlite/config.py:270:                cfg.save()
wisprlite/config.py:282:            cfg.save()   # marker included, so it never runs twice
wisprlite/welcome.py:55:    cfg = config.Config.load()
wisprlite/star_prompt.py:33:        cfg.save()
wisprlite/transcribe_window.py:128:    cfg = config.Config.load()
wisprlite/app.py:88:        self.cfg = config.Config.load()
wisprlite/app.py:475:        self.cfg.save()
wisprlite/app.py:823:        self.cfg.save()
wisprlite/app.py:829:        self.cfg.save()
wisprlite/app.py:834:        self.cfg.save()
wisprlite/app.py:839:        self.cfg.save()
wisprlite/app.py:849:        self.cfg.save()
wisprlite/app.py:1710:                meeting_mod.transcribe_session(session_dir, config.Config.load())
wisprlite/app.py:1977:        self.cfg.save()
wisprlite/app.py:2195:        old, new = self.cfg, config.Config.load()
wisprlite/app.py:2282:                self.cfg.save()
wisprlite/app.py:2338:                self.cfg.save()
wisprlite/app.py:2389:            config.Config.load()  # write config.json with defaults so we don't re-prompt
wisprlite/app.py:2398:    log.info("Pipevoice starting (engine=%s)", config.Config.load().engine)
wisprlite/usage.py:25:    fresh = config.Config.load()
wisprlite/usage.py:28:    fresh.save()

## Unsynchronised full-file configuration read/modify/write across concurrent processes, allowing a privacy preference to be bypassed or reverted
- usage.py `_save` re-loads the file right before writing and only sets install_id / last_count_day, so it carries the CURRENT usage_count from disk; it can revert "off" only if Settings saves inside the few-ms load→replace window.
- `maybe_send` reads usage_count from the cfg passed at startup; Settings applies live via the 1 s reload watcher. A user who switches it off sends at most the one count already in flight that day.
- Tray/app `self.cfg.save()` sites above can write a stale whole file in the ≤1 s before the watcher reloads; this is pre-existing for every setting (not new), and atomic save removes the half-file → defaults case that could wipe settings.
- Decision: accepted residual (ms/≤1 s windows, impact ≤1 extra count). A full file lock is out of scope for this PR.
