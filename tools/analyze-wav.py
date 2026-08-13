#!/usr/bin/env python3
"""Validate a HIL tone capture using only the Python standard library."""

from __future__ import annotations

import argparse
from array import array
import json
import math
from pathlib import Path
import sys
import wave

EXPECTED_CHANNELS = 1
EXPECTED_SAMPLE_WIDTH = 2
EXPECTED_RATE = 48_000
MAX_FFT_SAMPLES = 65_536
MIN_FFT_SAMPLES = 1_024


class AnalysisError(ValueError):
    """The WAV file does not satisfy the declared HIL gate."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Require mono S16_LE 48 kHz PCM, expected duration, and a dominant "
            "tone within the requested tolerance."
        )
    )
    parser.add_argument("wav", type=Path)
    parser.add_argument("--expect-duration-seconds", type=positive_float, required=True)
    parser.add_argument(
        "--duration-tolerance-seconds", type=nonnegative_float, default=0.10
    )
    parser.add_argument("--expect-frequency-hz", type=positive_float, required=True)
    parser.add_argument(
        "--frequency-tolerance-hz", type=nonnegative_float, default=20.0
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser.parse_args()


def positive_float(text: str) -> float:
    value = finite_float(text)
    if value <= 0.0:
        raise argparse.ArgumentTypeError("value must be positive")
    return value


def nonnegative_float(text: str) -> float:
    value = finite_float(text)
    if value < 0.0:
        raise argparse.ArgumentTypeError("value must be nonnegative")
    return value


def finite_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be a number") from error
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError("value must be finite")
    return value


def read_pcm(path: Path) -> tuple[array, int, float]:
    try:
        with wave.open(str(path), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            rate = source.getframerate()
            frame_count = source.getnframes()
            compression = source.getcomptype()
            if channels != EXPECTED_CHANNELS:
                raise AnalysisError(f"expected mono WAV, found {channels} channels")
            if sample_width != EXPECTED_SAMPLE_WIDTH:
                raise AnalysisError(
                    f"expected signed 16-bit samples, found {sample_width * 8}-bit"
                )
            if rate != EXPECTED_RATE:
                raise AnalysisError(f"expected 48000 Hz, found {rate} Hz")
            if compression != "NONE":
                raise AnalysisError(f"expected uncompressed PCM, found {compression}")
            raw = source.readframes(frame_count)
            if source.readframes(1):
                raise AnalysisError("WAV contains data beyond its declared frame count")
    except (OSError, EOFError, wave.Error) as error:
        raise AnalysisError(f"cannot read WAV: {error}") from error

    expected_bytes = frame_count * EXPECTED_CHANNELS * EXPECTED_SAMPLE_WIDTH
    if len(raw) != expected_bytes:
        raise AnalysisError(
            f"truncated PCM: expected {expected_bytes} bytes, read {len(raw)}"
        )
    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        raise AnalysisError("WAV contains no samples")
    return samples, rate, frame_count / rate


def dominant_frequency(samples: array, rate: int) -> tuple[float, int]:
    count = min(len(samples), MAX_FFT_SAMPLES)
    size = 1 << (count.bit_length() - 1)
    if size < MIN_FFT_SAMPLES:
        raise AnalysisError(
            f"capture is too short for frequency analysis ({len(samples)} samples)"
        )
    start = (len(samples) - size) // 2
    scale = 2.0 * math.pi / (size - 1)
    spectrum = [
        complex(samples[start + index] * (0.5 - 0.5 * math.cos(scale * index)), 0.0)
        for index in range(size)
    ]
    fft_in_place(spectrum)

    first_bin = max(1, math.ceil(20.0 * size / rate))
    last_bin = size // 2 - 1
    peak_bin = max(range(first_bin, last_bin + 1), key=lambda index: abs(spectrum[index]))
    peak = abs(spectrum[peak_bin])
    if peak == 0.0:
        raise AnalysisError("capture is silent; dominant frequency is undefined")

    left = abs(spectrum[peak_bin - 1])
    right = abs(spectrum[peak_bin + 1])
    denominator = left - 2.0 * peak + right
    correction = 0.0
    if denominator != 0.0:
        correction = 0.5 * (left - right) / denominator
        correction = max(-0.5, min(0.5, correction))
    return (peak_bin + correction) * rate / size, size


def fft_in_place(values: list[complex]) -> None:
    """Iterative radix-2 Cooley-Tukey FFT with no third-party dependency."""

    size = len(values)
    target = 0
    for source in range(1, size):
        bit = size >> 1
        while target & bit:
            target ^= bit
            bit >>= 1
        target ^= bit
        if source < target:
            values[source], values[target] = values[target], values[source]

    span = 2
    while span <= size:
        angle = -2.0 * math.pi / span
        root = complex(math.cos(angle), math.sin(angle))
        half = span // 2
        for base in range(0, size, span):
            twiddle = 1.0 + 0.0j
            for offset in range(half):
                even = values[base + offset]
                odd = values[base + offset + half] * twiddle
                values[base + offset] = even + odd
                values[base + offset + half] = even - odd
                twiddle *= root
        span <<= 1


def rms_dbfs(samples: array) -> float:
    mean_square = math.fsum(float(sample) * float(sample) for sample in samples) / len(samples)
    if mean_square == 0.0:
        return -math.inf
    return 20.0 * math.log10(math.sqrt(mean_square) / 32768.0)


def analyze(args: argparse.Namespace) -> dict[str, int | float | str]:
    samples, rate, duration = read_pcm(args.wav)
    duration_error = abs(duration - args.expect_duration_seconds)
    if duration_error > args.duration_tolerance_seconds:
        raise AnalysisError(
            f"duration {duration:.6f}s differs from expected "
            f"{args.expect_duration_seconds:.6f}s by {duration_error:.6f}s "
            f"(limit {args.duration_tolerance_seconds:.6f}s)"
        )

    frequency, fft_samples = dominant_frequency(samples, rate)
    frequency_error = abs(frequency - args.expect_frequency_hz)
    if frequency_error > args.frequency_tolerance_hz:
        raise AnalysisError(
            f"dominant frequency {frequency:.3f} Hz differs from expected "
            f"{args.expect_frequency_hz:.3f} Hz by {frequency_error:.3f} Hz "
            f"(limit {args.frequency_tolerance_hz:.3f} Hz)"
        )

    return {
        "path": str(args.wav),
        "channels": EXPECTED_CHANNELS,
        "sample_width_bits": EXPECTED_SAMPLE_WIDTH * 8,
        "sample_rate_hz": rate,
        "frames": len(samples),
        "duration_seconds": duration,
        "dominant_frequency_hz": frequency,
        "frequency_error_hz": frequency_error,
        "fft_samples": fft_samples,
        "rms_dbfs": rms_dbfs(samples),
    }


def main() -> int:
    args = parse_args()
    try:
        result = analyze(args)
    except AnalysisError as error:
        print(f"analyze-wav: {error}", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    else:
        print(
            "PASS: mono S16_LE 48000 Hz, "
            f"duration={result['duration_seconds']:.6f}s, "
            f"dominant={result['dominant_frequency_hz']:.3f} Hz, "
            f"rms={result['rms_dbfs']:.2f} dBFS"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
