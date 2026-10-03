# MEETIQ WebRTC TURN Configuration & NAT Traversal

This document describes the TURN relay server integration implemented for the **MEETIQ** virtual meeting platform to enable reliable WebRTC audio/video communication across restrictive NATs, corporate firewalls, and VPNs.

---

## 1. Current WebRTC Architecture

MeetIQ establishes peer-to-peer (P2P) audio/video sessions between meeting participants using HTML5 `RTCPeerConnection` and Socket.IO signaling. Socket.IO manages WebRTC offer, answer, and ICE candidate exchange scoped strictly to isolated meeting rooms.

---

## 2. STUN Configuration

By default, MeetIQ includes Google's public STUN server:
```json
{
  "urls": ["stun:stun.l.google.com:19302"]
}
```
STUN allows endpoints behind non-symmetric NATs to discover their public IP address and port mapping for direct P2P media streaming.

---

## 3. Why TURN Is Required

Direct P2P WebRTC connection via STUN fails in restrictive network environments, including:
- **Symmetric NATs**: Devices where public mapping changes per destination address.
- **Enterprise Firewalls**: Blocked peer-to-peer UDP traffic.
- **Corporate VPNs & Mobile Gateways**: Restricted inbound UDP connections.

A TURN (Traversal Using Relays around NAT) server acts as a media relay server in the cloud, receiving media packets from one peer and relaying them to the remote peer when direct P2P connections cannot be established.

---

## 4. ICE Server Configuration

ICE (Interactive Connectivity Establishment) gathers candidate address pairs in order of preference:
1. **Host candidates**: Direct local IP addresses.
2. **Server reflexive candidates**: Public IP addresses retrieved via STUN.
3. **Relay candidates**: Relayed addresses provided by TURN.

When `TURN_ENABLED=True`, MeetIQ dynamically returns both STUN and TURN configurations to client browsers:
```json
[
  {
    "urls": ["stun:stun.l.google.com:19302"]
  },
  {
    "urls": [
      "turn:turn.meetiq.com:3478?transport=udp",
      "turn:turn.meetiq.com:3478?transport=tcp",
      "turns:turn.meetiq.com:5349?transport=tcp"
    ],
    "username": "meetiq_user",
    "credential": "meetiq_secret"
  }
]
```

---

## 5. Environment Variables

TURN configuration is completely externalized and environment-driven in `.env`:

```env
TURN_ENABLED=True
TURN_SERVER_URL=turn:turn.meetiq.com:3478?transport=udp,turn:turn.meetiq.com:3478?transport=tcp,turns:turn.meetiq.com:5349?transport=tcp
TURN_USERNAME=meetiq_user
TURN_PASSWORD=meetiq_secret
```

- If `TURN_ENABLED=False` (default), client browsers receive STUN-only configuration without credentials.
- Secrets and credentials are never hardcoded in client-side HTML or JavaScript source files.

---

## 6. Backend ICE Configuration API

- **Endpoint**: `GET /api/webrtc/ice-servers?meeting_id=<meeting_id>`
- **Method**: `GET`
- **Response (200 OK)**:
  ```json
  {
    "success": true,
    "ice_servers": [
      {
        "urls": ["stun:stun.l.google.com:19302"]
      },
      {
        "urls": ["turn:turn.meetiq.com:3478?transport=udp"],
        "username": "meetiq_user",
        "credential": "meetiq_secret"
      }
    ]
  }
  ```

---

## 7. Authorization

The `/api/webrtc/ice-servers` endpoint enforces strict access boundaries:
- **Unauthenticated user**: Returns HTTP 401 Unauthorized.
- **Unauthorized participant / admin**: Returns HTTP 403 Forbidden if user is not the meeting creator or an invited participant.
- **Non-existent meeting**: Returns HTTP 404 Not Found.
- No internal stack traces or database connection strings are exposed on error.

---

## 8. Coturn Deployment Requirements

For self-hosted TURN infrastructure, Coturn (an open-source TURN/STUN server) is recommended:

### `turnserver.conf` Configuration:
```ini
listening-port=3478
tls-listening-port=5349
fingerprint
lt-cred-mech
user=meetiq_user:meetiq_secret
realm=meetiq.com
total-quota=100
bps-capacity=0
stale-nonce
no-loopback-peers
no-multicast-peers
```

---

## 9. Firewall / Port Requirements

To allow TURN media relay traffic across firewalls:
- **UDP 3478**: Standard TURN traffic.
- **TCP 3478**: Standard TURN TCP fallback.
- **TCP 443 / TLS 5349**: Encrypted TURN over TLS (traverses strict outbound HTTP/HTTPS firewalls).
- **UDP 49152–65535**: Dynamic media relay port range.

---

## 10. Browser Validation

Client templates (`host_meeting.html` and `meeting.html`) fetch ICE configurations dynamically on meeting initialization:
```javascript
const response = await fetch("/api/webrtc/ice-servers?meeting_id=" + meetingId);
const data = await response.json();
const peerConnection = new RTCPeerConnection({ iceServers: data.ice_servers });
```

---

## 11. Failure / Fallback Behavior

- **TURN Offline / Network Timeout**: If `/api/webrtc/ice-servers` fetch fails or TURN server is unreachable, client browsers log a warning and fall back cleanly to `[{ urls: "stun:stun.l.google.com:19302" }]`.
- Meeting calls and WebRTC connections continue operating in STUN mode without application crashes.

---

## 12. Known Limitations

- **Live TURN Relay Verification**: Live media relay verification requires active Coturn infrastructure provisioned with valid public DNS / IP routing.
- **NAT Traversal Testing**: True TURN relay verification requires testing between two physical machines behind different restrictive symmetric NAT subnets.
