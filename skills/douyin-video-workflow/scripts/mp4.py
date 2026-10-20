"""MP4 sample-table inspection; avoids treating stale mdhd as playback duration."""
import struct
import subprocess
import hashlib
import itertools
import math
import imageio_ffmpeg

def boxes(f, start, end):
    while start + 8 <= end:
        f.seek(start)
        raw = f.read(8)
        if len(raw) != 8:
            break
        size, kind = struct.unpack(">I4s", raw)
        header = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            header = 16
        elif size == 0:
            size = end - start
        if size < header or start + size > end:
            raise ValueError("Invalid MP4 box")
        yield kind, start + header, start + size
        start += size


def media_info(path):
    tracks = []
    movie_timescale = None
    with path.open("rb") as f:
        for kind, start, end in boxes(f, 0, path.stat().st_size):
            if kind != b"moov":
                continue
            for kind, start, end in boxes(f, start, end):
                if kind == b"mvhd":
                    f.seek(start)
                    raw = f.read(32)
                    movie_timescale = struct.unpack(">I", raw[20:24] if raw[0] else raw[12:16])[0]
                if kind != b"trak":
                    continue
                track = {}
                stts = []
                ctts = []
                edits = []

                def walk(a, b):
                    for k, s, e in boxes(f, a, b):
                        if k == b"tkhd":
                            f.seek(e - 8)
                            w, h = struct.unpack(">II", f.read(8))
                            track["width"], track["height"] = w // 65536, h // 65536
                        elif k == b"hdlr":
                            f.seek(s + 8)
                            track["type"] = f.read(4).decode("ascii")
                        elif k == b"mdhd":
                            f.seek(s)
                            raw = f.read(32)
                            if raw[0] == 1:
                                scale, duration = struct.unpack(">IQ", raw[20:32])
                            else:
                                scale, duration = struct.unpack(">II", raw[12:20])
                            track["timescale"] = scale
                            track["duration"] = duration / scale
                        elif k == b"stsd":
                            track["codecs"] = [t.decode("ascii") for t, _, _ in boxes(f, s + 8, e)]
                        elif k == b"stsz":
                            f.seek(s + 8)
                            track["samples"] = struct.unpack(">I", f.read(4))[0]
                            f.seek(s + 4)
                            sample_size = struct.unpack(">I", f.read(4))[0]
                            f.seek(s + 12)
                            track["data_bytes"] = (sample_size * track["samples"] if sample_size else
                                sum(value[0] for value in struct.iter_unpack(">I", f.read(track["samples"] * 4))))
                        elif k == b"stts":
                            f.seek(s + 8)
                            stts.extend(struct.iter_unpack(">II", f.read(e - s - 8)))
                        elif k == b"ctts":
                            f.seek(s)
                            raw = f.read(e - s)
                            ctts.extend(struct.iter_unpack(">Ii" if raw[0] == 1 else ">II", raw[8:]))
                        elif k == b"elst":
                            f.seek(s)
                            raw = f.read(e - s)
                            version = raw[0]
                            entry_size = 20 if version else 12
                            fmt = ">Q" if version else ">I"
                            count = struct.unpack(">I", raw[4:8])[0]
                            ticks = sum(struct.unpack_from(fmt, raw, 8 + i * entry_size)[0]
                                        for i in range(count))
                            track["presentation_duration"] = ticks / movie_timescale
                            for i in range(count):
                                offset = 8 + i * entry_size
                                duration, media, rate_i, rate_f = struct.unpack_from(
                                    ">Qqhh" if version else ">Iihh", raw, offset)
                                edits.append((duration / movie_timescale, media, rate_i, rate_f))
                        elif k in (b"mdia", b"minf", b"stbl", b"edts"):
                            walk(s, e)

                walk(start, end)
                if stts:
                    offsets = (itertools.chain.from_iterable(itertools.repeat(offset, count) for count, offset in ctts)
                               if ctts else itertools.repeat(0))
                    pts = []
                    dts = 0
                    for count, delta in stts:
                        for _ in range(count):
                            pts.append(dts + next(offsets))
                            dts += delta
                    if len(pts) != track["samples"]:
                        raise ValueError("Timing table sample count differs from sample-size table")
                    pts.sort()
                    first = pts[0]
                    scale = track["timescale"]
                    digest = hashlib.sha256()
                    for timestamp in pts:
                        normalized = timestamp - first
                        divisor = math.gcd(normalized, scale)
                        digest.update(struct.pack(">qq", normalized // divisor, scale // divisor))
                    track["normalized_pts_sha256"] = digest.hexdigest()
                    track["timing_duration"] = dts / scale
                    # Offsets are relative to first presentation sample, so
                    # different encoder B-frame reorder delays cancel out.
                    track["normalized_edits"] = [
                        [duration, None if media == -1 else (media - first) / scale, ri, rf]
                        for duration, media, ri, rf in edits
                    ] or [[dts / scale, 0.0, 1, 0]]
                tracks.append(track)
    if not tracks:
        raise ValueError("Missing media tracks")
    return tracks


def verify(source, target):
    original, result = media_info(source), media_info(target)
    sv = next(t for t in original if t["type"] == "vide")
    dv = next(t for t in result if t["type"] == "vide")
    assert dv["width"] * 3 == dv["height"] * 4
    assert dv["height"] == sv["height"]
    assert dv["codecs"] == ["avc1"]
    assert dv["samples"] == sv["samples"], "Video frame count changed"
    assert abs(dv["duration"] - sv["duration"]) < 0.05
    assert dv["normalized_pts_sha256"] == sv["normalized_pts_sha256"], "Video presentation timing changed"
    sa = [t for t in original if t["type"] == "soun"]
    da = [t for t in result if t["type"] == "soun"]
    assert len(sa) == len(da)
    for audio_index, (a, b) in enumerate(zip(sa, da)):
        assert a["codecs"] == b["codecs"]
        assert a["samples"] == b["samples"], "Audio sample count changed"
        assert a["normalized_pts_sha256"] == b["normalized_pts_sha256"], "Audio presentation timing changed"
        # mdhd can contain a stale raw duration in the source. The edit list
        # defines the actual presentation duration. Confirm identical encoded
        # audio bytes as well when that raw duration changes during remuxing.
        assert abs(a.get("presentation_duration", a["timing_duration"]) -
                   b.get("presentation_duration", b["timing_duration"])) < 0.05
        if abs(a["duration"] - b["duration"]) >= 0.05:
            def audio_hash(path):
                result = subprocess.run([
                    imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner",
                    "-loglevel", "error", "-i", str(path), "-map", f"0:a:{audio_index}",
                    "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-"
                ], capture_output=True, check=True, text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return result.stdout.strip()
            assert audio_hash(source) == audio_hash(target), "Audio payload changed"
    for a, b in [(sv, dv), *zip(sa, da)]:
        assert len(a["normalized_edits"]) == len(b["normalized_edits"]), "Edit list changed"
        for ae, be in zip(a["normalized_edits"], b["normalized_edits"]):
            assert abs(ae[0] - be[0]) < 0.05, "Edit duration changed"
            assert (ae[1] is None) == (be[1] is None), "Empty edit changed"
            if ae[1] is not None:
                assert abs(ae[1] - be[1]) <= max(1 / a["timescale"], 1 / b["timescale"]), "Track playback offset changed"
            assert ae[2:] == be[2:], "Edit playback rate changed"
    return result
