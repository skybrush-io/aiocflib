class Connection:
    def recv(self) -> Packet: ...
    def send(self, packet: Packet) -> None: ...

class Packet:
    valid: bool
    port: int
    channel: int
    payload: bytes
