"""
Session manager for tracking user conversations and course bot selections.
"""
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import config

class ConversationSession:
    """Represents a user's conversation session."""
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.session_id: Optional[str] = None
        self.course_bot_id: Optional[str] = None
        self.course_bot_name: Optional[str] = None
        self.channel_id: Optional[str] = None
        self.learning_mode: str = "standard"
        self.root_response_message_id: Optional[str] = None
        self.research_conversation_id: Optional[str] = None
        self.follow_up_count: int = 0
        self.conversation_history: List[Dict[str, str]] = []
        self.last_activity = datetime.now()
        
    def add_message(self, role: str, content: str):
        """Add a message to conversation history."""
        self.conversation_history.append({
            "role": role,
            "content": content
        })
        self.last_activity = datetime.now()
        
    def set_course_bot(self, course_bot_id: str, course_bot_name: str, channel_id: Optional[str] = None):
        """Set the active course bot for this session."""
        self.course_bot_id = course_bot_id
        self.course_bot_name = course_bot_name
        if channel_id:
            self.channel_id = channel_id
        self.last_activity = datetime.now()
        
    def record_follow_up(self) -> None:
        """Record one student reply within this conversation chain."""
        self.follow_up_count += 1
        self.last_activity = datetime.now()
        
    def is_expired(self) -> bool:
        """Check if session has expired."""
        expiry_time = timedelta(seconds=config.SESSION_TIMEOUT)
        return datetime.now() - self.last_activity > expiry_time


class SessionManager:
    """Manages user sessions, keyed by (user_id, channel_id) for server channels or user_id for DMs."""

    def __init__(self):
        self.sessions: Dict[str, ConversationSession] = {}
        self.response_sessions: Dict[str, ConversationSession] = {}
        self.response_parent_message_ids: Dict[str, str] = {}

    @staticmethod
    def _session_key(user_id: str, channel_id: Optional[str] = None) -> str:
        return f"{user_id}:{channel_id}" if channel_id else user_id

    def get_session(self, user_id: str, channel_id: Optional[str] = None) -> ConversationSession:
        """
        Get or create a session.
        - Server channel: keyed by "user_id:channel_id" so history is per-channel.
        - DM (channel_id=None): keyed by user_id only.
        """
        self._cleanup_expired_sessions()
        key = self._session_key(user_id, channel_id)
        if key not in self.sessions:
            self.sessions[key] = ConversationSession(user_id)
        return self.sessions[key]

    def start_conversation(
        self,
        user_id: str,
        channel_id: Optional[str] = None,
    ) -> ConversationSession:
        """Create a new reply-chain conversation from the user's current context."""
        context = self.get_session(user_id, channel_id)
        conversation = ConversationSession(user_id)
        conversation.course_bot_id = context.course_bot_id
        conversation.course_bot_name = context.course_bot_name
        conversation.channel_id = context.channel_id
        conversation.learning_mode = context.learning_mode
        return conversation

    def bind_response(
        self,
        response_message_id: str,
        session: ConversationSession,
        parent_message_id: Optional[str] = None,
    ) -> None:
        """Associate a bot answer with the student's active conversation."""
        message_id = str(response_message_id)
        if session.root_response_message_id is None:
            session.root_response_message_id = message_id
        self.response_sessions[message_id] = session
        if parent_message_id:
            self.response_parent_message_ids[message_id] = parent_message_id

    def get_session_for_response(
        self,
        response_message_id: str,
        user_id: str,
    ) -> Optional[ConversationSession]:
        """Resolve a replied-to bot answer, restricted to its original student."""
        session = self.response_sessions.get(str(response_message_id))
        if not session:
            return None
        if session.user_id != user_id or session.is_expired():
            return None
        return session

    def get_parent_message_for_response(
        self, response_message_id: str
    ) -> Optional[str]:
        """Return canonical CRAG message referenced by a Discord bot answer."""
        return self.response_parent_message_ids.get(str(response_message_id))
    
    def _cleanup_expired_sessions(self):
        """Remove expired sessions."""
        expired_users = [
            user_id for user_id, session in self.sessions.items()
            if session.is_expired()
        ]
        for user_id in expired_users:
            del self.sessions[user_id]
        self.response_sessions = {
            message_id: session
            for message_id, session in self.response_sessions.items()
            if not session.is_expired()
        }
        live_message_ids = set(self.response_sessions)
        self.response_parent_message_ids = {
            message_id: parent_message_id
            for message_id, parent_message_id in self.response_parent_message_ids.items()
            if message_id in live_message_ids
        }
