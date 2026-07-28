"""Small, allocation-free acoustic double-snap detector."""

from __future__ import annotations


class SnapDetector:
    """Detect two sharp, high-crest transients in a stream of audio blocks."""

    MIN_GAP = 0.08
    MAX_GAP = 0.60
    REFRACTORY = 0.80
    # A deliberate double-snap is an ISOLATED gesture; typing is a train. Without
    # these two windows, 8 keystrokes at 0.19s apart fire the detector twice —
    # measured — and people type notes all through meetings. Requiring quiet
    # before the pair and quiet after it separates the gesture from the train
    # using no FFT. Confirming late costs nothing because a bookmark is only a
    # timestamp, and `mark_time` reports the moment of the FIRST snap.
    QUIET_BEFORE = 0.45
    CONFIRM_AFTER = 0.40

    def __init__(self, rate, *, sensitivity=0.5):
        self.rate = float(rate)
        self.sensitivity = max(0.0, min(1.0, float(sensitivity)))
        self.baseline = 0.0
        self.samples = 0
        self.last_transient = None
        self.refractory_until = 0.0
        self._cooldown_until = 0.0
        self._pair_start = None      # first snap of a candidate pair
        self._pending_until = None   # decide only once this passes with no further transient
        self.mark_time = None        # audio-clock time of the accepted first snap
        # Diagnostics for the calibration dialog. These thresholds were set against
        # synthetic waveforms, and real microphones disagree — laptop voice-isolation
        # DSP compresses a clap, flattening the very crest factor this keys on. Show
        # the live numbers so they can be tuned against hardware, not guesswork.
        self.transients = 0          # sharp events that passed both thresholds
        self.loudest = 0.0           # loudest peak seen at all
        self.last_peak = 0.0         # peak of the most recent candidate block
        self.last_crest = 0.0        # its peak/rms ratio
        self.need_peak = 0.0         # the peak that block needed to qualify
        self.need_crest = 0.0        # the crest it needed

    def feed(self, block) -> bool:
        """Consume one mono block and return True once for an accepted pair."""
        count = 0
        squares = 0.0
        peak = 0.0
        for value in block:
            # sounddevice supplies one-element rows for a mono stream.
            if getattr(value, "ndim", 0) or isinstance(value, (list, tuple)):
                value = value[0]
            value = float(value)
            magnitude = value if value >= 0.0 else -value
            if magnitude > peak:
                peak = magnitude
            squares += value * value
            count += 1
        if not count:
            return False
        rms = (squares / count) ** 0.5
        now = (self.samples + count * 0.5) / self.rate
        self.samples += count

        # Fast attack and slow release make a loud speech floor reject itself.
        if self.baseline == 0.0 or rms > self.baseline:
            self.baseline += (rms - self.baseline) * 0.20
        else:
            self.baseline += (rms - self.baseline) * 0.01

        # Do NOT widen these without measuring on real hardware. Tried it: taking
        # ratio to 8-5*s and crest to 5-3*s broke five of nine test signals. A
        # LOWER peak threshold makes more blocks qualify as transients, and the
        # isolation rule then rejects the pair for not standing alone — so raising
        # sensitivity made detection WORSE, and typing began firing. The knob is
        # not monotonic; treat it as calibrated, and use the diagnostics below to
        # get real numbers off a real microphone before changing anything.
        ratio = 8.0 - 4.0 * self.sensitivity
        crest = peak / rms if rms > 1e-9 else 0.0
        need_peak = max(0.02, self.baseline * ratio)
        need_crest = 5.0 - 1.5 * self.sensitivity
        if peak > self.loudest:
            self.loudest = peak
        # Record any block loud enough to be a candidate, whether or not it
        # qualified — a clap that fails only on crest looks identical to silence
        # from the outside, and that is precisely what needs to be visible.
        if peak >= need_peak * 0.5:
            self.last_peak = peak
            self.last_crest = crest
            self.need_peak = need_peak
            self.need_crest = need_crest
        transient = (
            now >= self._cooldown_until
            and peak >= need_peak
            and crest >= need_crest
        )
        if transient:
            self.transients += 1
        if not transient:
            # A pair only counts once nothing else follows it. Anything arriving
            # inside the confirm window means this was a train, not a gesture.
            if self._pending_until is not None and now >= self._pending_until:
                self._pending_until = None
                self._pair_start = None
                self.refractory_until = now + self.REFRACTORY
                return True
            return False

        self._cooldown_until = now + 0.06
        previous = self.last_transient
        self.last_transient = now

        # A transient during the confirm window means more than two — typing.
        if self._pending_until is not None:
            self._pending_until = None
            self._pair_start = None
            self.mark_time = None
            return False

        if now < self.refractory_until:
            return False

        gap = None if previous is None else now - previous
        if (
            self._pair_start is not None
            and gap is not None
            and self.MIN_GAP <= gap <= self.MAX_GAP
            and previous == self._pair_start
        ):
            # Second of the pair. Hold the verdict until the gesture proves isolated.
            self.mark_time = self._pair_start
            self._pending_until = now + self.CONFIRM_AFTER
            return False

        # Otherwise this transient can only open a pair, and only if it stands
        # alone — a keystroke in a run of keystrokes never will.
        self._pair_start = now if (previous is None or now - previous > self.QUIET_BEFORE) else None
        return False
