"""
V380 Live Streaming

Stream and decrypt live video/audio from V380 cameras.
"""

import struct
import socket
import signal
import time
import os
from datetime import datetime
from typing import Optional, Tuple

from .client import V380Client
from .crypto import decrypt_64_80, decrypt_audio

# Global flag for Ctrl-C handling
_stop_recording = False


def _signal_handler(sig, frame):
    global _stop_recording
    _stop_recording = True
    print("\n[!] Ctrl-C detected - stopping recording...")


class StreamRecorder:
    """Record live video/audio streams from V380 camera"""

    HEADER_SIZE = 12
    KEEPALIVE_PACKET = bytes.fromhex("01210000000000000010000000000000")

    def __init__(self, client: V380Client, enable_audio: bool = True):
        self.client = client
        self.enable_audio = enable_audio

        # Frame reassembly state
        self._frame_chunks = {}
        self._current_frame_start = None
        self._current_total = 0
        self._current_is_iframe = False

    def record(self, duration: int = 60, output_dir: str = "recordings",
               output_prefix: str = "v380", rtsp_server=None) -> Optional[str]:
        """
        Record video stream for specified duration.

        Args:
            duration: Recording duration in seconds
            output_dir: Directory for output files (created if needed)
            output_prefix: Prefix for output filenames
            rtsp_server: Optional RTSP server for live streaming

        Returns:
            Path to recorded .h265 file, or None on failure
        """
        stream_sock = self.client.create_stream_socket()
        if not stream_sock:
            return None

        # Create output directory
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        video_file = os.path.join(output_dir, f"{output_prefix}_{timestamp}.h265")

        record_audio = self.client.audio_supported and self.enable_audio
        audio_file = video_file.replace('.h265', '.aac') if record_audio else None

        print(f"[*] Recording video to {video_file}")
        if record_audio:
            print(f"[*] Recording audio to {audio_file}")
        elif not self.client.audio_supported:
            print(f"[*] Audio: not supported by camera")
        else:
            print(f"[*] Audio: disabled")

        # Debug raw stream
        raw_file = video_file.replace('.h265', '_raw.bin') if self.client.debug else None
        if raw_file:
            print(f"[*] Saving raw stream to {raw_file}")

        # Set up signal handler
        global _stop_recording
        _stop_recording = False
        old_handler = signal.signal(signal.SIGINT, _signal_handler)

        start_time = time.time()
        video_bytes = 0
        audio_bytes = 0
        buffer = bytearray()

        try:
            video_f = open(video_file, 'wb')
            audio_f = open(audio_file, 'wb') if record_audio else None
            raw_f = open(raw_file, 'wb') if raw_file else None

            try:
                while (time.time() - start_time) < duration and not _stop_recording:
                    try:
                        data = stream_sock.recv(65536)
                        if not data:
                            break

                        buffer.extend(data)

                        if raw_f:
                            raw_f.write(data)

                        video, audio, remaining = self._process_stream_data(
                            bytes(buffer), record_audio
                        )
                        buffer = bytearray(remaining)

                        if video:
                            video_f.write(video)
                            video_bytes += len(video)
                            if rtsp_server:
                                rtsp_server.send_frame(video)

                        if audio and audio_f:
                            audio_f.write(audio)
                            audio_bytes += len(audio)

                        # Periodic keepalive
                        if int(time.time()) % 5 == 0:
                            stream_sock.sendall(self.KEEPALIVE_PACKET)

                        # Progress update
                        elapsed = time.time() - start_time
                        total = video_bytes + audio_bytes
                        if total % 50000 < 5000:
                            if record_audio:
                                print(f"  {elapsed:.0f}s - Video: {video_bytes/1024:.1f}KB, Audio: {audio_bytes/1024:.1f}KB")
                            else:
                                print(f"  {elapsed:.0f}s - Video: {video_bytes/1024:.1f}KB")

                    except socket.timeout:
                        stream_sock.sendall(self.KEEPALIVE_PACKET)

            finally:
                video_f.close()
                if audio_f:
                    audio_f.close()
                if raw_f:
                    raw_f.close()

        except Exception as e:
            print(f"[!] Stream error: {e}")
            if self.client.debug:
                import traceback
                traceback.print_exc()
            return None

        finally:
            signal.signal(signal.SIGINT, old_handler)
            stream_sock.close()

        status = "stopped by user" if _stop_recording else "complete"
        print(f"\n[+] Recording {status}!")
        print(f"    Video: {video_bytes/1024:.1f} KB -> {video_file}")
        if record_audio:
            print(f"    Audio: {audio_bytes/1024:.1f} KB -> {audio_file}")

        return video_file

    def _process_stream_data(self, data: bytes, record_audio: bool) -> Tuple[bytes, bytes, bytes]:
        """Process and decrypt stream packets"""
        video_result = bytearray()
        audio_result = bytearray()
        pos = 0

        while pos < len(data):
            # Video packet (0x7f28 = I-frame, 0x7f29 = P-frame)
            if data[pos] == 0x7f and pos + 1 < len(data) and data[pos+1] in [0x28, 0x29]:
                if pos + self.HEADER_SIZE > len(data):
                    break

                is_iframe = (data[pos+1] == 0x28)
                total_frame = struct.unpack('<H', data[pos+3:pos+5])[0]
                cur_frame = struct.unpack('<H', data[pos+5:pos+7])[0]
                pkt_len = struct.unpack('<H', data[pos+7:pos+9])[0]
                packet_end = pos + self.HEADER_SIZE + pkt_len

                if packet_end > len(data):
                    break

                payload = data[pos+12:packet_end]

                if cur_frame == 0:
                    # Process previous complete frame
                    if self._current_frame_start is not None and 'current' in self._frame_chunks:
                        if len(self._frame_chunks['current']) >= self._current_total:
                            decrypted = self._decrypt_frame(
                                self._frame_chunks['current'],
                                self._current_is_iframe
                            )
                            video_result.extend(decrypted)
                        self._frame_chunks.pop('current', None)

                    # Start new frame
                    self._current_frame_start = pos
                    self._current_total = total_frame
                    self._current_is_iframe = is_iframe
                    self._frame_chunks['current'] = [(cur_frame, payload)]
                else:
                    if 'current' in self._frame_chunks:
                        self._frame_chunks['current'].append((cur_frame, payload))

                        if len(self._frame_chunks['current']) >= self._current_total:
                            decrypted = self._decrypt_frame(
                                self._frame_chunks['current'],
                                self._current_is_iframe
                            )
                            video_result.extend(decrypted)
                            self._frame_chunks.pop('current', None)
                            self._current_frame_start = None

                pos = packet_end

            # Audio packet (0x7f18)
            elif data[pos] == 0x7f and pos + 1 < len(data) and data[pos+1] == 0x18:
                if pos + self.HEADER_SIZE > len(data):
                    break

                total_frame = struct.unpack('<H', data[pos+3:pos+5])[0]
                cur_frame = struct.unpack('<H', data[pos+5:pos+7])[0]
                pkt_len = struct.unpack('<H', data[pos+7:pos+9])[0]
                packet_end = pos + self.HEADER_SIZE + pkt_len

                # Sanity check for false audio headers
                if pkt_len > 1000 or total_frame > 10 or packet_end > len(data):
                    pos += 1
                    continue

                if not record_audio:
                    pos = packet_end
                    continue

                audio_payload = data[pos+12:packet_end]
                if cur_frame == 0 and len(audio_payload) > 16:
                    audio_payload = audio_payload[16:]  # Skip metadata

                decrypted = decrypt_audio(audio_payload, self.client.cipher)
                audio_result.extend(decrypted)

                pos = packet_end
            else:
                pos += 1

        # Process any remaining complete frame
        if self._current_frame_start is not None and 'current' in self._frame_chunks:
            if len(self._frame_chunks['current']) >= self._current_total:
                decrypted = self._decrypt_frame(
                    self._frame_chunks['current'],
                    self._current_is_iframe
                )
                video_result.extend(decrypted)
                self._frame_chunks.pop('current', None)
                self._current_frame_start = None

        return bytes(video_result), bytes(audio_result), data[pos:]

    def _decrypt_frame(self, chunks: list, is_iframe: bool) -> bytes:
        """Decrypt and reassemble video frame"""
        chunks.sort(key=lambda x: x[0])

        # Reassemble frame data
        frame_data = bytearray()
        for cur_frame, payload in chunks:
            if cur_frame == 0:
                frame_data.extend(payload[16:])  # Skip metadata
            else:
                frame_data.extend(payload)

        # Decrypt based on frame type and size
        if is_iframe or len(frame_data) >= 64:
            return decrypt_64_80(bytes(frame_data), self.client.cipher)
        else:
            return bytes(frame_data)
