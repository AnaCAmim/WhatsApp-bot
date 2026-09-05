from dataclasses import dataclass


@dataclass(frozen=True)
class UnreadChat:
    name: str
    unread_count: int
    label: str

    def to_dict(self):
        return {
            "name": self.name,
            "unread_count": self.unread_count,
            "label": self.label,
        }


@dataclass(frozen=True)
class WhatsAppMessage:
    message_key: str
    source_id: str | None
    chat_name: str
    sender: str | None
    content: str
    raw_metadata: str
    is_from_me: bool
    captured_at: str

    def to_dict(self):
        return {
            "message_key": self.message_key,
            "source_id": self.source_id,
            "chat_name": self.chat_name,
            "sender": self.sender,
            "content": self.content,
            "raw_metadata": self.raw_metadata,
            "is_from_me": self.is_from_me,
            "captured_at": self.captured_at,
        }
