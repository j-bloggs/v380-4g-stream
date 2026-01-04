#!/usr/bin/env python3
"""
RTSP Server for V380 camera streams

Provides live RTSP streaming of decrypted V380 video.
Connect with: vlc rtsp://localhost:8554/stream
"""

import socket
import threading
import struct
import time
import random
from typing import Optional


class RTPPacketizer:
    """Packetize H.265 NAL units into RTP packets"""

    def __init__(self, ssrc: int = None):
        self.ssrc = ssrc or random.randint(0, 0xFFFFFFFF)
        self.sequence = random.randint(0, 0xFFFF)
        self.timestamp = random.randint(0, 0xFFFFFFFF)
        self.payload_type = 96

    def packetize_nal(self, nal_data: bytes, is_last: bool = True) -> list:
        """Convert a NAL unit to RTP packets"""
        packets = []
        max_payload = 1400

        if len(nal_data) <= max_payload:
            packets.append(self._make_rtp_packet(nal_data, is_last))
        else:
            nal_type = (nal_data[0] >> 1) & 0x3F
            fu_header1 = (nal_data[0] & 0x81) | (49 << 1)
            fu_header2 = nal_data[1]

            offset = 2
            first = True

            while offset < len(nal_data):
                chunk_size = min(max_payload - 3, len(nal_data) - offset)
                last_fragment = (offset + chunk_size >= len(nal_data))

                fu_indicator = bytes([fu_header1, fu_header2])

                fu_type = nal_type
                if first:
                    fu_header = 0x80 | fu_type
                    first = False
                elif last_fragment:
                    fu_header = 0x40 | fu_type
                else:
                    fu_header = fu_type

                payload = fu_indicator + bytes([fu_header]) + nal_data[offset:offset + chunk_size]
                packets.append(self._make_rtp_packet(payload, is_last and last_fragment))
                offset += chunk_size

        return packets

    def _make_rtp_packet(self, payload: bytes, marker: bool) -> bytes:
        """Create an RTP packet with header"""
        byte0 = 0x80
        byte1 = (0x80 if marker else 0) | self.payload_type

        header = struct.pack('>BBHII',
            byte0,
            byte1,
            self.sequence & 0xFFFF,
            self.timestamp & 0xFFFFFFFF,
            self.ssrc
        )

        self.sequence = (self.sequence + 1) & 0xFFFF
        return header + payload

    def advance_timestamp(self, ticks: int = 3600):
        """Advance timestamp (90kHz clock)"""
        self.timestamp = (self.timestamp + ticks) & 0xFFFFFFFF


class RTSPServer:
    """Simple RTSP server for live streaming"""

    def __init__(self, port: int = 8554):
        self.port = port
        self.server_socket = None
        self.clients = []
        self.running = False
        self.lock = threading.Lock()
        self.packetizer = RTPPacketizer()
        self.session_id = str(random.randint(10000000, 99999999))

        self.width = 640
        self.height = 720
        self.vps = None
        self.sps = None
        self.pps = None

    def start(self):
        """Start the RTSP server"""
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(('0.0.0.0', self.port))
        self.server_socket.listen(5)
        self.running = True

        self.accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.accept_thread.start()

        print(f"[RTSP] Server started on rtsp://localhost:{self.port}/stream")

    def stop(self):
        """Stop the RTSP server"""
        self.running = False
        if self.server_socket:
            self.server_socket.close()
        with self.lock:
            for client_sock, rtp_sock, _, _ in self.clients:
                try:
                    client_sock.close()
                    rtp_sock.close()
                except:
                    pass
            self.clients.clear()

    def set_stream_params(self, vps: bytes, sps: bytes, pps: bytes, width: int, height: int):
        """Set stream parameters from first I-frame"""
        self.vps = vps
        self.sps = sps
        self.pps = pps
        self.width = width
        self.height = height

    def send_frame(self, frame_data: bytes):
        """Send a video frame to all connected clients"""
        if not self.clients:
            return

        nal_units = self._parse_nal_units(frame_data)

        with self.lock:
            dead_clients = []

            for i, (client_sock, rtp_sock, client_addr, rtp_port) in enumerate(self.clients):
                try:
                    for j, nal in enumerate(nal_units):
                        is_last = (j == len(nal_units) - 1)
                        packets = self.packetizer.packetize_nal(nal, is_last)
                        for packet in packets:
                            rtp_sock.sendto(packet, (client_addr[0], rtp_port))
                except Exception:
                    dead_clients.append(i)

            for i in reversed(dead_clients):
                try:
                    self.clients[i][0].close()
                    self.clients[i][1].close()
                except:
                    pass
                self.clients.pop(i)

        self.packetizer.advance_timestamp(3600)

    def _parse_nal_units(self, data: bytes) -> list:
        """Parse NAL units from Annex B format"""
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
            while j < len(data) - 3:
                if data[j:j+4] == b'\x00\x00\x00\x01' or data[j:j+3] == b'\x00\x00\x01':
                    end = j
                    break
                j += 1

            if start < end:
                nal_units.append(data[start:end])
            i = end

        return nal_units

    def _accept_loop(self):
        """Accept incoming RTSP connections"""
        while self.running:
            try:
                self.server_socket.settimeout(1.0)
                client_sock, client_addr = self.server_socket.accept()
                print(f"[RTSP] Client connected from {client_addr}")

                thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock, client_addr),
                    daemon=True
                )
                thread.start()
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"[RTSP] Accept error: {e}")
                break

    def _handle_client(self, client_sock: socket.socket, client_addr: tuple):
        """Handle RTSP client requests"""
        rtp_sock = None
        rtp_port = None

        try:
            while self.running:
                client_sock.settimeout(30.0)
                data = client_sock.recv(4096)
                if not data:
                    break

                request = data.decode('utf-8', errors='ignore')
                lines = request.split('\r\n')
                if not lines:
                    continue

                parts = lines[0].split(' ')
                if len(parts) < 2:
                    continue

                method = parts[0]
                cseq = self._get_header(lines, 'CSeq') or '0'

                if method == 'OPTIONS':
                    response = self._make_response(200, cseq, {
                        'Public': 'OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN'
                    })
                    client_sock.send(response.encode())

                elif method == 'DESCRIBE':
                    sdp = self._generate_sdp()
                    response = self._make_response(200, cseq, {
                        'Content-Type': 'application/sdp',
                        'Content-Length': str(len(sdp))
                    }, sdp)
                    client_sock.send(response.encode())

                elif method == 'SETUP':
                    transport = self._get_header(lines, 'Transport') or ''

                    rtp_port = 0
                    for part in transport.split(';'):
                        if part.startswith('client_port='):
                            ports = part.split('=')[1]
                            rtp_port = int(ports.split('-')[0])
                            break

                    if rtp_port == 0:
                        rtp_port = 5000

                    rtp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    server_rtp_port = rtp_sock.getsockname()[1] or 6970

                    response = self._make_response(200, cseq, {
                        'Transport': f'RTP/AVP;unicast;client_port={rtp_port}-{rtp_port+1};server_port={server_rtp_port}-{server_rtp_port+1}',
                        'Session': self.session_id
                    })
                    client_sock.send(response.encode())

                elif method == 'PLAY':
                    response = self._make_response(200, cseq, {
                        'Session': self.session_id,
                        'Range': 'npt=0.000-'
                    })
                    client_sock.send(response.encode())

                    if rtp_sock and rtp_port:
                        with self.lock:
                            self.clients.append((client_sock, rtp_sock, client_addr, rtp_port))
                        print(f"[RTSP] Streaming to {client_addr[0]}:{rtp_port}")

                elif method == 'TEARDOWN':
                    response = self._make_response(200, cseq, {
                        'Session': self.session_id
                    })
                    client_sock.send(response.encode())
                    break

        except Exception as e:
            print(f"[RTSP] Client error: {e}")
        finally:
            with self.lock:
                self.clients = [(cs, rs, ca, rp) for cs, rs, ca, rp in self.clients
                               if cs != client_sock]
            try:
                client_sock.close()
                if rtp_sock:
                    rtp_sock.close()
            except:
                pass
            print(f"[RTSP] Client disconnected: {client_addr}")

    def _get_header(self, lines: list, name: str) -> Optional[str]:
        """Get header value from request lines"""
        for line in lines:
            if line.lower().startswith(name.lower() + ':'):
                return line.split(':', 1)[1].strip()
        return None

    def _make_response(self, code: int, cseq: str, headers: dict = None, body: str = '') -> str:
        """Create RTSP response"""
        status = {200: 'OK', 400: 'Bad Request', 404: 'Not Found', 500: 'Internal Server Error'}
        response = f'RTSP/1.0 {code} {status.get(code, "Unknown")}\r\n'
        response += f'CSeq: {cseq}\r\n'

        if headers:
            for key, value in headers.items():
                response += f'{key}: {value}\r\n'

        response += '\r\n'
        if body:
            response += body

        return response

    def _generate_sdp(self) -> str:
        """Generate SDP for the stream"""
        import base64

        vps_b64 = base64.b64encode(self.vps).decode() if self.vps else ''
        sps_b64 = base64.b64encode(self.sps).decode() if self.sps else ''
        pps_b64 = base64.b64encode(self.pps).decode() if self.pps else ''

        sprop = ''
        if vps_b64 and sps_b64 and pps_b64:
            sprop = f';sprop-vps={vps_b64};sprop-sps={sps_b64};sprop-pps={pps_b64}'

        sdp = f'''v=0
o=- {int(time.time())} 1 IN IP4 127.0.0.1
s=V380 Camera Stream
t=0 0
m=video 0 RTP/AVP 96
c=IN IP4 0.0.0.0
a=rtpmap:96 H265/90000
a=fmtp:96 profile-id=1{sprop}
a=control:streamid=0
'''
        return sdp


def create_rtsp_server(port: int = 8554) -> RTSPServer:
    """Create and return an RTSP server instance"""
    return RTSPServer(port)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='RTSP Server for V380 streams')
    parser.add_argument('--port', type=int, default=8554, help='RTSP port (default: 8554)')
    args = parser.parse_args()

    server = RTSPServer(args.port)
    server.start()

    print(f"RTSP server running. Connect with: vlc rtsp://localhost:{args.port}/stream")
    print("Press Ctrl+C to stop")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.stop()
