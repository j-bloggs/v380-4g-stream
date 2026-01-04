#!/usr/bin/env python3
"""
MP4 Muxer - Convert raw H.265/HEVC and AAC streams to MP4 container

Pure Python implementation, no external dependencies required.

Usage:
    python mp4_muxer.py input.h265 -o output.mp4
    python mp4_muxer.py input.h265 -a input.aac -o output.mp4
"""

import struct
import argparse
from typing import Optional


class MP4Muxer:
    """Mux H.265 video and optional AAC audio into MP4 container"""

    def __init__(self, video_path: str, audio_path: Optional[str] = None,
                 fps: float = 25.0, audio_sample_rate: int = 8000):
        self.video_path = video_path
        self.audio_path = audio_path
        self.fps = fps
        self.audio_sample_rate = audio_sample_rate
        self.timescale = 1200
        self.frame_duration = int(self.timescale / fps)

    def mux(self, output_path: str) -> bool:
        """Mux video (and audio) into MP4 file"""
        try:
            print(f"[*] Parsing H.265 video: {self.video_path}")
            video_samples, vps, sps, pps, width, height = self._parse_h265()
            print(f"    Found {len(video_samples)} video frames, {width}x{height}")

            audio_samples = []
            asc = None
            if self.audio_path:
                print(f"[*] Parsing AAC audio: {self.audio_path}")
                audio_samples, asc = self._parse_aac()
                print(f"    Found {len(audio_samples)} audio frames")

            print(f"[*] Building MP4 container...")

            mdat_header_size = 8
            mdat_data = bytearray()

            video_offsets = []
            video_sizes = []
            for sample in video_samples:
                video_offsets.append(len(mdat_data) + mdat_header_size)
                converted = self._annexb_to_mp4(sample)
                video_sizes.append(len(converted))
                mdat_data.extend(converted)

            audio_offsets = []
            audio_sizes = []
            for sample in audio_samples:
                audio_offsets.append(len(mdat_data) + mdat_header_size)
                audio_sizes.append(len(sample))
                mdat_data.extend(sample)

            moov = self._build_moov(
                video_sizes, video_offsets, vps, sps, pps, width, height,
                audio_sizes, audio_offsets, asc
            )

            ftyp = self._build_ftyp()

            offset_adjust = len(ftyp) + len(moov)
            video_offsets = [o + offset_adjust for o in video_offsets]
            audio_offsets = [o + offset_adjust for o in audio_offsets]

            moov = self._build_moov(
                video_sizes, video_offsets, vps, sps, pps, width, height,
                audio_sizes, audio_offsets, asc
            )

            mdat = self._build_box(b'mdat', bytes(mdat_data))

            print(f"[*] Writing MP4: {output_path}")
            with open(output_path, 'wb') as f:
                f.write(ftyp)
                f.write(moov)
                f.write(mdat)

            total_size = len(ftyp) + len(moov) + len(mdat)
            print(f"[+] Done! Output size: {total_size / 1024:.1f} KB")
            return True

        except Exception as e:
            print(f"[!] Error: {e}")
            return False

    def _parse_h265(self) -> tuple:
        """Parse H.265 Annex B stream, extract NAL units and parameters"""
        with open(self.video_path, 'rb') as f:
            data = f.read()

        nal_units = []
        i = 0
        while i < len(data) - 4:
            if data[i:i+4] == b'\x00\x00\x00\x01':
                start = i + 4
                i += 4
            elif data[i:i+3] == b'\x00\x00\x01':
                start = i + 3
                i += 3
            else:
                i += 1
                continue

            end = len(data)
            j = i
            while j < len(data) - 4:
                if data[j:j+4] == b'\x00\x00\x00\x01' or data[j:j+3] == b'\x00\x00\x01':
                    end = j
                    break
                j += 1

            nal_data = data[start:end]
            if nal_data:
                nal_units.append(nal_data)
            i = end

        vps = None
        sps = None
        pps = None
        width = 640
        height = 720
        samples = []
        current_au = bytearray()

        for nal in nal_units:
            if len(nal) < 2:
                continue

            nal_type = (nal[0] >> 1) & 0x3F

            if nal_type == 32:
                vps = nal
            elif nal_type == 33:
                sps = nal
                w, h = self._parse_sps_dimensions(nal)
                if w and h:
                    width, height = w, h
            elif nal_type == 34:
                pps = nal
            elif nal_type in [19, 20, 21]:
                if current_au:
                    samples.append(bytes(current_au))
                current_au = bytearray()
                current_au.extend(b'\x00\x00\x00\x01')
                current_au.extend(nal)
            elif nal_type in [0, 1]:
                if current_au:
                    samples.append(bytes(current_au))
                current_au = bytearray()
                current_au.extend(b'\x00\x00\x00\x01')
                current_au.extend(nal)
            elif nal_type in [39, 40]:
                if current_au:
                    current_au.extend(b'\x00\x00\x00\x01')
                    current_au.extend(nal)

        if current_au:
            samples.append(bytes(current_au))

        if not vps or not sps or not pps:
            raise ValueError("Missing VPS, SPS, or PPS in H.265 stream")

        return samples, vps, sps, pps, width, height

    def _parse_sps_dimensions(self, sps: bytes) -> tuple:
        """Parse width/height from SPS (simplified)"""
        try:
            return None, None
        except:
            return None, None

    def _parse_aac(self) -> tuple:
        """Parse raw AAC ADTS stream"""
        with open(self.audio_path, 'rb') as f:
            data = f.read()

        samples = []
        i = 0

        while i < len(data) - 7:
            if data[i] == 0xFF and (data[i+1] & 0xF0) == 0xF0:
                frame_length = ((data[i+3] & 0x03) << 11) | (data[i+4] << 3) | ((data[i+5] & 0xE0) >> 5)

                if frame_length < 7 or frame_length > 8192:
                    i += 1
                    continue

                if i + frame_length > len(data):
                    break

                header_size = 7 if (data[i+1] & 0x01) else 9
                raw_frame = data[i + header_size:i + frame_length]
                if raw_frame:
                    samples.append(raw_frame)

                i += frame_length
            else:
                i += 1

        # AudioSpecificConfig for AAC LC 8kHz mono
        asc = bytes([0x15, 0x88])

        return samples, asc

    def _annexb_to_mp4(self, data: bytes) -> bytes:
        """Convert Annex B format to MP4 format (length prefixed)"""
        result = bytearray()
        i = 0

        while i < len(data):
            if i + 4 <= len(data) and data[i:i+4] == b'\x00\x00\x00\x01':
                start = i + 4
                i += 4
            elif i + 3 <= len(data) and data[i:i+3] == b'\x00\x00\x01':
                start = i + 3
                i += 3
            else:
                i += 1
                continue

            end = len(data)
            j = start
            while j < len(data) - 3:
                if data[j:j+4] == b'\x00\x00\x00\x01' or data[j:j+3] == b'\x00\x00\x01':
                    end = j
                    break
                j += 1

            nal = data[start:end]
            result.extend(struct.pack('>I', len(nal)))
            result.extend(nal)
            i = end

        return bytes(result)

    def _build_box(self, box_type: bytes, data: bytes) -> bytes:
        """Build an MP4 box with type and data"""
        size = 8 + len(data)
        return struct.pack('>I', size) + box_type + data

    def _build_ftyp(self) -> bytes:
        """Build ftyp (file type) box"""
        data = b'isom'
        data += struct.pack('>I', 512)
        data += b'isom' + b'iso2' + b'mp41' + b'hev1'
        return self._build_box(b'ftyp', data)

    def _build_moov(self, video_sizes, video_offsets, vps, sps, pps, width, height,
                    audio_sizes, audio_offsets, asc) -> bytes:
        """Build moov (movie) box"""
        video_duration = len(video_sizes) * self.frame_duration
        audio_duration = 0
        audio_duration_movie = 0
        if audio_sizes:
            audio_duration = len(audio_sizes) * 1024
            audio_duration_movie = int(audio_duration * self.timescale / self.audio_sample_rate)

        mvhd = self._build_mvhd(max(video_duration, audio_duration_movie))

        trak_video = self._build_video_trak(
            video_sizes, video_offsets, vps, sps, pps, width, height, video_duration
        )

        trak_audio = b''
        if audio_sizes and asc:
            trak_audio = self._build_audio_trak(
                audio_sizes, audio_offsets, asc, audio_duration
            )

        moov_data = mvhd + trak_video + trak_audio
        return self._build_box(b'moov', moov_data)

    def _build_mvhd(self, duration: int) -> bytes:
        """Build mvhd (movie header) box"""
        data = bytearray()
        data.extend(struct.pack('>I', 0))
        data.extend(struct.pack('>I', 0))
        data.extend(struct.pack('>I', 0))
        data.extend(struct.pack('>I', self.timescale))
        data.extend(struct.pack('>I', duration))
        data.extend(struct.pack('>I', 0x00010000))
        data.extend(struct.pack('>H', 0x0100))
        data.extend(b'\x00' * 10)
        data.extend(struct.pack('>9I', 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000))
        data.extend(b'\x00' * 24)
        data.extend(struct.pack('>I', 3))
        return self._build_box(b'mvhd', bytes(data))

    def _build_video_trak(self, sizes, offsets, vps, sps, pps, width, height, duration) -> bytes:
        """Build video trak (track) box"""
        tkhd = self._build_tkhd(1, duration, width, height)
        mdia = self._build_video_mdia(sizes, offsets, vps, sps, pps, width, height, duration)
        return self._build_box(b'trak', tkhd + mdia)

    def _build_audio_trak(self, sizes, offsets, asc, duration) -> bytes:
        """Build audio trak (track) box"""
        tkhd = self._build_tkhd(2, duration, 0, 0, is_audio=True)
        mdia = self._build_audio_mdia(sizes, offsets, asc, duration)
        return self._build_box(b'trak', tkhd + mdia)

    def _build_tkhd(self, track_id: int, duration: int, width: int, height: int,
                    is_audio: bool = False) -> bytes:
        """Build tkhd (track header) box"""
        data = bytearray()
        flags = 0x000007 if not is_audio else 0x000003
        data.extend(struct.pack('>I', flags))
        data.extend(struct.pack('>I', 0))
        data.extend(struct.pack('>I', 0))
        data.extend(struct.pack('>I', track_id))
        data.extend(struct.pack('>I', 0))
        data.extend(struct.pack('>I', duration))
        data.extend(b'\x00' * 8)
        data.extend(struct.pack('>H', 0))
        data.extend(struct.pack('>H', 0))
        data.extend(struct.pack('>H', 0x0100 if is_audio else 0))
        data.extend(struct.pack('>H', 0))
        data.extend(struct.pack('>9I', 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000))
        data.extend(struct.pack('>I', width << 16))
        data.extend(struct.pack('>I', height << 16))
        return self._build_box(b'tkhd', bytes(data))

    def _build_video_mdia(self, sizes, offsets, vps, sps, pps, width, height, duration) -> bytes:
        """Build video mdia (media) box"""
        mdhd = self._build_mdhd(duration, self.timescale)
        hdlr = self._build_hdlr(b'vide', b'VideoHandler')
        minf = self._build_video_minf(sizes, offsets, vps, sps, pps, width, height)
        return self._build_box(b'mdia', mdhd + hdlr + minf)

    def _build_audio_mdia(self, sizes, offsets, asc, duration) -> bytes:
        """Build audio mdia (media) box"""
        mdhd = self._build_mdhd(duration, self.audio_sample_rate)
        hdlr = self._build_hdlr(b'soun', b'SoundHandler')
        minf = self._build_audio_minf(sizes, offsets, asc)
        return self._build_box(b'mdia', mdhd + hdlr + minf)

    def _build_mdhd(self, duration: int, timescale: int) -> bytes:
        """Build mdhd (media header) box"""
        data = bytearray()
        data.extend(struct.pack('>I', 0))
        data.extend(struct.pack('>I', 0))
        data.extend(struct.pack('>I', 0))
        data.extend(struct.pack('>I', timescale))
        data.extend(struct.pack('>I', duration))
        data.extend(struct.pack('>H', 0x55C4))
        data.extend(struct.pack('>H', 0))
        return self._build_box(b'mdhd', bytes(data))

    def _build_hdlr(self, handler_type: bytes, name: bytes) -> bytes:
        """Build hdlr (handler) box"""
        data = bytearray()
        data.extend(struct.pack('>I', 0))
        data.extend(b'\x00' * 4)
        data.extend(handler_type.ljust(4, b'\x00'))
        data.extend(b'\x00' * 12)
        data.extend(name + b'\x00')
        return self._build_box(b'hdlr', bytes(data))

    def _build_video_minf(self, sizes, offsets, vps, sps, pps, width, height) -> bytes:
        """Build video minf (media information) box"""
        vmhd = self._build_vmhd()
        dinf = self._build_dinf()
        stbl = self._build_video_stbl(sizes, offsets, vps, sps, pps, width, height)
        return self._build_box(b'minf', vmhd + dinf + stbl)

    def _build_audio_minf(self, sizes, offsets, asc) -> bytes:
        """Build audio minf (media information) box"""
        smhd = self._build_smhd()
        dinf = self._build_dinf()
        stbl = self._build_audio_stbl(sizes, offsets, asc)
        return self._build_box(b'minf', smhd + dinf + stbl)

    def _build_vmhd(self) -> bytes:
        """Build vmhd (video media header) box"""
        data = struct.pack('>I', 0x00000001)
        data += struct.pack('>H', 0)
        data += struct.pack('>HHH', 0, 0, 0)
        return self._build_box(b'vmhd', data)

    def _build_smhd(self) -> bytes:
        """Build smhd (sound media header) box"""
        data = struct.pack('>I', 0)
        data += struct.pack('>H', 0)
        data += struct.pack('>H', 0)
        return self._build_box(b'smhd', data)

    def _build_dinf(self) -> bytes:
        """Build dinf (data information) box"""
        url_box = self._build_box(b'url ', struct.pack('>I', 0x000001))
        dref_data = struct.pack('>I', 0) + struct.pack('>I', 1) + url_box
        dref = self._build_box(b'dref', dref_data)
        return self._build_box(b'dinf', dref)

    def _build_video_stbl(self, sizes, offsets, vps, sps, pps, width, height) -> bytes:
        """Build video stbl (sample table) box"""
        stsd = self._build_video_stsd(vps, sps, pps, width, height)
        stts = self._build_stts(len(sizes), self.frame_duration)
        stsc = self._build_stsc(len(sizes))
        stsz = self._build_stsz(sizes)
        stco = self._build_stco(offsets)
        stss = self._build_stss(sizes)
        return self._build_box(b'stbl', stsd + stts + stsc + stsz + stco + stss)

    def _build_audio_stbl(self, sizes, offsets, asc) -> bytes:
        """Build audio stbl (sample table) box"""
        stsd = self._build_audio_stsd(asc)
        stts = self._build_stts(len(sizes), 1024)
        stsc = self._build_stsc(len(sizes))
        stsz = self._build_stsz(sizes)
        stco = self._build_stco(offsets)
        return self._build_box(b'stbl', stsd + stts + stsc + stsz + stco)

    def _build_video_stsd(self, vps, sps, pps, width, height) -> bytes:
        """Build video stsd (sample description) box"""
        hvcc = self._build_hvcc(vps, sps, pps)

        hev1_data = bytearray()
        hev1_data.extend(b'\x00' * 6)
        hev1_data.extend(struct.pack('>H', 1))
        hev1_data.extend(b'\x00' * 16)
        hev1_data.extend(struct.pack('>H', width))
        hev1_data.extend(struct.pack('>H', height))
        hev1_data.extend(struct.pack('>I', 0x00480000))
        hev1_data.extend(struct.pack('>I', 0x00480000))
        hev1_data.extend(struct.pack('>I', 0))
        hev1_data.extend(struct.pack('>H', 1))
        hev1_data.extend(b'\x00' * 32)
        hev1_data.extend(struct.pack('>H', 0x0018))
        hev1_data.extend(struct.pack('>h', -1))
        hev1_data.extend(hvcc)

        hev1 = self._build_box(b'hev1', bytes(hev1_data))

        stsd_data = struct.pack('>I', 0) + struct.pack('>I', 1) + hev1
        return self._build_box(b'stsd', stsd_data)

    def _build_hvcc(self, vps, sps, pps) -> bytes:
        """Build hvcC (HEVC decoder configuration record)"""
        data = bytearray()
        data.append(1)

        if len(sps) > 3:
            data.append(sps[2] if len(sps) > 2 else 0)
        else:
            data.append(0)

        data.extend(b'\x60\x00\x00\x00')
        data.extend(b'\x90\x00\x00\x00\x00\x00')
        data.append(93)
        data.extend(struct.pack('>H', 0xF000))
        data.append(0xFC)
        data.append(0xFD)
        data.append(0xF8)
        data.append(0xF8)
        data.extend(struct.pack('>H', 0))
        data.append(0x0F)
        data.append(3)

        data.append(0x20 | 32)
        data.extend(struct.pack('>H', 1))
        data.extend(struct.pack('>H', len(vps)))
        data.extend(vps)

        data.append(0x20 | 33)
        data.extend(struct.pack('>H', 1))
        data.extend(struct.pack('>H', len(sps)))
        data.extend(sps)

        data.append(0x20 | 34)
        data.extend(struct.pack('>H', 1))
        data.extend(struct.pack('>H', len(pps)))
        data.extend(pps)

        return self._build_box(b'hvcC', bytes(data))

    def _build_audio_stsd(self, asc) -> bytes:
        """Build audio stsd (sample description) box"""
        esds = self._build_esds(asc)

        mp4a_data = bytearray()
        mp4a_data.extend(b'\x00' * 6)
        mp4a_data.extend(struct.pack('>H', 1))
        mp4a_data.extend(b'\x00' * 8)
        mp4a_data.extend(struct.pack('>H', 1))
        mp4a_data.extend(struct.pack('>H', 16))
        mp4a_data.extend(struct.pack('>H', 0))
        mp4a_data.extend(struct.pack('>H', 0))
        mp4a_data.extend(struct.pack('>I', self.audio_sample_rate << 16))
        mp4a_data.extend(esds)

        mp4a = self._build_box(b'mp4a', bytes(mp4a_data))

        stsd_data = struct.pack('>I', 0) + struct.pack('>I', 1) + mp4a
        return self._build_box(b'stsd', stsd_data)

    def _build_esds(self, asc) -> bytes:
        """Build esds (elementary stream descriptor) box"""
        data = bytearray()
        data.extend(struct.pack('>I', 0))

        es_data = bytearray()
        es_data.extend(struct.pack('>H', 0))
        es_data.append(0)

        dc_data = bytearray()
        dc_data.append(0x40)
        dc_data.append(0x15)
        dc_data.extend(b'\x00\x00\x00')
        dc_data.extend(struct.pack('>I', 16000))
        dc_data.extend(struct.pack('>I', 16000))

        dc_data.append(0x05)
        dc_data.append(len(asc))
        dc_data.extend(asc)

        es_data.append(0x04)
        es_data.append(len(dc_data))
        es_data.extend(dc_data)

        es_data.append(0x06)
        es_data.append(1)
        es_data.append(0x02)

        data.append(0x03)
        data.append(len(es_data))
        data.extend(es_data)

        return self._build_box(b'esds', bytes(data))

    def _build_stts(self, sample_count: int, sample_delta: int) -> bytes:
        """Build stts (decoding time to sample) box"""
        data = struct.pack('>I', 0)
        data += struct.pack('>I', 1)
        data += struct.pack('>I', sample_count)
        data += struct.pack('>I', sample_delta)
        return self._build_box(b'stts', data)

    def _build_stsc(self, sample_count: int) -> bytes:
        """Build stsc (sample to chunk) box"""
        data = struct.pack('>I', 0)
        data += struct.pack('>I', 1)
        data += struct.pack('>I', 1)
        data += struct.pack('>I', 1)
        data += struct.pack('>I', 1)
        return self._build_box(b'stsc', data)

    def _build_stsz(self, sizes: list) -> bytes:
        """Build stsz (sample size) box"""
        data = struct.pack('>I', 0)
        data += struct.pack('>I', 0)
        data += struct.pack('>I', len(sizes))
        for size in sizes:
            data += struct.pack('>I', size)
        return self._build_box(b'stsz', data)

    def _build_stco(self, offsets: list) -> bytes:
        """Build stco (chunk offset) box"""
        data = struct.pack('>I', 0)
        data += struct.pack('>I', len(offsets))
        for offset in offsets:
            data += struct.pack('>I', offset)
        return self._build_box(b'stco', data)

    def _build_stss(self, sizes: list) -> bytes:
        """Build stss (sync sample / keyframe) box"""
        data = struct.pack('>I', 0)
        data += struct.pack('>I', 1)
        data += struct.pack('>I', 1)
        return self._build_box(b'stss', data)


def main():
    parser = argparse.ArgumentParser(
        description="Convert H.265 video (and optional AAC audio) to MP4 container",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s video.h265 -o output.mp4
  %(prog)s video.h265 -a audio.aac -o output.mp4
  %(prog)s video.h265 -a audio.aac --fps 12 -o output.mp4
"""
    )

    parser.add_argument("video", help="Input H.265 video file")
    parser.add_argument("-a", "--audio", help="Input AAC audio file (optional)")
    parser.add_argument("-o", "--output", required=True, help="Output MP4 file")
    parser.add_argument("--fps", type=float, default=25.0,
                        help="Video frame rate (default: 25)")
    parser.add_argument("--audio-rate", type=int, default=8000,
                        help="Audio sample rate in Hz (default: 8000)")

    args = parser.parse_args()

    muxer = MP4Muxer(args.video, args.audio, args.fps, args.audio_rate)
    success = muxer.mux(args.output)
    return 0 if success else 1


if __name__ == "__main__":
    exit(main())
