import json
import struct
import uuid


class MessageType:
    LOGIN = "login"
    LOGIN_RESP = "login_resp"
    REGISTER = "register"
    NEW_CONN = "new_conn"
    INIT_CONN = "init_conn"
    PING = "ping"
    PONG = "pong"
    ERROR = "error"
    CLOSE = "close"
    DATA = "data"
    DATA_AUTH = "data_auth"
    DATA_AUTH_RESP = "data_auth_resp"
    # HTTP proxy
    HTTP_NEW_CONN = "http_new_conn"
    HTTP_RESP_REQ = "http_resp_req"
    # STCP
    STCP_REGISTER = "stcp_register"
    STCP_VISITOR_REGISTER = "stcp_visitor_register"
    STCP_VISITOR_REGISTER_RESP = "stcp_visitor_register_resp"
    STCP_NEW_VISITOR = "stcp_new_visitor"
    STCP_VISITOR_READY = "stcp_visitor_ready"
    # FTP
    FTP_NEW_DATA = "ftp_new_data"
    FTP_DATA_READY = "ftp_data_ready"


# String type <-> numeric code mapping for binary framing
_TYPE_TO_CODE = {
    MessageType.LOGIN: 0x01,
    MessageType.LOGIN_RESP: 0x02,
    MessageType.REGISTER: 0x03,
    MessageType.NEW_CONN: 0x04,
    MessageType.INIT_CONN: 0x05,
    MessageType.PING: 0x06,
    MessageType.PONG: 0x07,
    MessageType.ERROR: 0x08,
    MessageType.CLOSE: 0x09,
    MessageType.DATA: 0x0A,
    MessageType.DATA_AUTH: 0x0B,
    MessageType.DATA_AUTH_RESP: 0x0C,
    MessageType.HTTP_NEW_CONN: 0x0D,
    MessageType.HTTP_RESP_REQ: 0x0E,
    MessageType.STCP_REGISTER: 0x0F,
    MessageType.STCP_VISITOR_REGISTER: 0x10,
    MessageType.STCP_VISITOR_REGISTER_RESP: 0x11,
    MessageType.STCP_NEW_VISITOR: 0x12,
    MessageType.STCP_VISITOR_READY: 0x13,
    MessageType.FTP_NEW_DATA: 0x14,
    MessageType.FTP_DATA_READY: 0x15,
}
_CODE_TO_TYPE = {v: k for k, v in _TYPE_TO_CODE.items()}


class Message:
    def __init__(self, msg_type, **kwargs):
        self.type = msg_type
        self.payload = kwargs

    def to_dict(self):
        return {"type": self.type, **self.payload}

    @classmethod
    def from_dict(cls, data):
        msg_type = data.pop("type")
        return cls(msg_type, **data)

    def __repr__(self):
        return f"Message(type={self.type}, payload={self.payload})"


class Protocol:
    """Binary-framed protocol.

    Frame layout (8-byte header + payload):
      ┌────────┬─────────┬──────┬───────┬──────────────┬───────────────┐
      │ Magic  │ Version │ Type │ Flags │ Payload Len  │ Payload       │
      │ 1 byte │ 1 byte  │1 byte│1 byte │ 4 bytes (BE) │ variable      │
      └────────┴─────────┴──────┴───────┴──────────────┴───────────────┘

    Payload encoding:
      - DATA:  binary UUID (16 bytes) + raw data bytes (zero-copy, no hex)
      - others: JSON UTF-8 (small, infrequent control messages)
    """

    MAGIC = 0xAA
    VERSION = 0x01
    HEADER_SIZE = 8
    UUID_SIZE = 16
    _HEADER_FMT = ">BBBBI"  # magic, version, type, flags, length

    @staticmethod
    def encode(message):
        msg_code = _TYPE_TO_CODE.get(message.type)
        if msg_code is None:
            raise ValueError(f"Unknown message type: {message.type}")

        if message.type == MessageType.DATA:
            # Binary payload: conn_id as 16-byte UUID + raw data
            conn_id = message.payload.get("conn_id", "")
            try:
                uuid_bytes = uuid.UUID(conn_id).bytes
            except (ValueError, AttributeError):
                uuid_bytes = uuid.UUID(int=0).bytes
            data_bytes = message.payload.get("data", b"")
            if isinstance(data_bytes, str):
                # Backward-compatible: accept hex string
                data_bytes = bytes.fromhex(data_bytes) if data_bytes else b""
            # Zero-copy: pre-allocate single buffer, avoid intermediate concat
            payload_len = Protocol.UUID_SIZE + len(data_bytes)
            buf = bytearray(Protocol.HEADER_SIZE + payload_len)
            struct.pack_into(
                Protocol._HEADER_FMT, buf, 0,
                Protocol.MAGIC, Protocol.VERSION, msg_code, 0, payload_len,
            )
            buf[Protocol.HEADER_SIZE:Protocol.HEADER_SIZE + Protocol.UUID_SIZE] = uuid_bytes
            buf[Protocol.HEADER_SIZE + Protocol.UUID_SIZE:] = data_bytes
            return buf
        else:
            # JSON payload for control messages
            payload = json.dumps(message.to_dict()).encode("utf-8")
            header = struct.pack(
                Protocol._HEADER_FMT,
                Protocol.MAGIC,
                Protocol.VERSION,
                msg_code,
                0,
                len(payload),
            )
            return header + payload

    @staticmethod
    def decode(data):
        if len(data) < Protocol.HEADER_SIZE:
            return None, data

        magic, version, msg_code, flags, length = struct.unpack(
            Protocol._HEADER_FMT, data[:Protocol.HEADER_SIZE]
        )

        if magic != Protocol.MAGIC:
            # Not a valid frame start; drop one byte and resync
            return None, data[1:]

        if len(data) < Protocol.HEADER_SIZE + length:
            return None, data

        payload = data[Protocol.HEADER_SIZE : Protocol.HEADER_SIZE + length]
        remaining = data[Protocol.HEADER_SIZE + length :]

        msg_type = _CODE_TO_TYPE.get(msg_code)
        if msg_type is None:
            return None, remaining

        if msg_type == MessageType.DATA:
            if len(payload) < Protocol.UUID_SIZE:
                return None, remaining
            conn_id = str(uuid.UUID(bytes=payload[:Protocol.UUID_SIZE]))
            data_bytes = payload[Protocol.UUID_SIZE:]
            message = Message(MessageType.DATA, conn_id=conn_id, data=data_bytes)
        else:
            try:
                d = json.loads(payload.decode("utf-8"))
                message = Message.from_dict(d)
            except (json.JSONDecodeError, ValueError, KeyError):
                return None, remaining

        return message, remaining

    @staticmethod
    def generate_conn_id():
        return str(uuid.uuid4())
