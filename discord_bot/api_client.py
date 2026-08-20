"""
API client for communicating with the getCRAG-ed backend.
Uses a single persistent httpx.AsyncClient so TCP connections are reused across
calls, cutting per-request latency significantly and reducing event-loop pressure.
"""
import httpx
from typing import List, Dict, Any, Optional

import config


class APIClient:
    """Client for interacting with the getCRAG-ed backend API."""

    def __init__(self, base_url: str = config.BACKEND_API_URL):
        self.base_url = base_url
        # Persistent client — one connection pool shared across all calls.
        # httpx.AsyncClient can be safely instantiated outside an async context;
        # the underlying transport connects lazily on first use.
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=config.REQUEST_TIMEOUT,
        )

    async def close(self) -> None:
        """Gracefully close the underlying HTTP connection pool."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, **kwargs) -> Any:
        response = await self._client.get(path, **kwargs)
        response.raise_for_status()
        return response.json()

    async def _post(self, path: str, **kwargs) -> Any:
        response = await self._client.post(path, **kwargs)
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def query(
        self,
        question: str,
        course_bot_id: str,
        user_id: str,
        session_id: Optional[str] = None,
        conversation_history: List[Dict[str, str]] = None,
        learning_mode: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a query to the backend RAG pipeline."""
        if conversation_history is None:
            conversation_history = []
        payload = {
            "query": question,
            "course_bot_id": course_bot_id,
            "user_id": user_id,
            "conversation_history": conversation_history,
            "top_k": 5,
        }
        if session_id:
            payload["session_id"] = session_id
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if learning_mode:
            payload["learning_mode"] = learning_mode
        try:
            return await self._post("/query", json=payload)
        except httpx.TimeoutException:
            raise Exception("Request to backend timed out. Please try again.")
        except httpx.HTTPStatusError as e:
            raise Exception(f"Backend returned error: {e.response.status_code}")
        except Exception as e:
            raise Exception(f"Failed to query backend: {str(e)}")

    def _research_headers(self) -> Dict[str, str]:
        return {
            "X-Discord-Bot-Secret": config.DISCORD_BACKEND_SHARED_SECRET,
        }

    async def start_research_conversation(
        self, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Start or idempotently recover one durable Discord conversation."""
        try:
            return await self._post(
                "/research/discord/conversations",
                json=payload,
                headers=self._research_headers(),
            )
        except httpx.HTTPStatusError as e:
            raise Exception(
                f"Research conversation capture failed: {e.response.status_code}"
            )

    async def append_research_message(
        self, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Append one canonical Discord transcript message."""
        try:
            return await self._post(
                "/research/discord/messages",
                json=payload,
                headers=self._research_headers(),
            )
        except httpx.HTTPStatusError as e:
            raise Exception(
                f"Research message capture failed: {e.response.status_code}"
            )

    async def record_research_interaction(
        self, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Append one allow-listed Discord interaction."""
        try:
            return await self._post(
                "/research/discord/interactions",
                json=payload,
                headers=self._research_headers(),
            )
        except httpx.HTTPStatusError as e:
            raise Exception(
                f"Research interaction capture failed: {e.response.status_code}"
            )

    async def consume_lms_link_code(
        self, code: str, discord_user_id: str, discord_display_name: str
    ) -> Dict[str, Any]:
        """Redeem one LMS-issued code for the current Discord account."""
        try:
            return await self._post(
                "/identity/discord-link/consume",
                json={
                    "code": code,
                    "discord_user_id": discord_user_id,
                    "discord_display_name": discord_display_name,
                },
                headers=self._research_headers(),
            )
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json().get("detail")
            except Exception:
                detail = None
            raise Exception(
                detail or f"LMS account linking failed: {e.response.status_code}"
            )

    async def generate_lms_recovery_code(
        self, discord_user_id: str, discord_display_name: str
    ) -> Dict[str, Any]:
        """Create private one-time recovery code for a linked Discord user."""
        try:
            return await self._post(
                "/identity/discord-recovery-code",
                json={
                    "discord_user_id": discord_user_id,
                    "discord_display_name": discord_display_name,
                },
                headers=self._research_headers(),
            )
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json().get("detail")
            except Exception:
                detail = None
            raise Exception(
                detail or f"LMS account recovery failed: {e.response.status_code}"
            )

    async def get_course_bots(self) -> List[Dict[str, Any]]:
        """Fetch all available course bots."""
        try:
            data = await self._get("/course-bots")
            return data.get("course_bots", [])
        except Exception as e:
            raise Exception(f"Failed to fetch course bots: {str(e)}")

    async def get_channel_access(
        self, channel_id: str, discord_user_id: str
    ) -> Dict[str, Any]:
        """
        Combined check for server channels: is the channel linked to a course,
        and is this user approved for it?

        Returns dict with keys: course_bot_id, course_name, reason.
        reason values: "approved" | "channel_not_linked" | "not_registered" | "pending" | "rejected"
        """
        try:
            return await self._get(
                f"/discord/channel/{channel_id}/user/{discord_user_id}/access"
            )
        except Exception as e:
            raise Exception(f"Failed to check channel access: {str(e)}")

    async def register_student(
        self,
        discord_user_id: str,
        discord_username: str,
        real_name: str,
        student_id: str,
        join_code: str,
        channel_id: Optional[str] = None,
        guild_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Register a student for a course via /auth command.
        Student is stored as 'pending' until a tutor approves them.
        If channel_id is provided (server channel), that channel is auto-linked to the course.
        """
        try:
            payload: Dict[str, Any] = {
                "discord_user_id": discord_user_id,
                "discord_username": discord_username,
                "real_name": real_name,
                "student_id": student_id,
                "join_code": join_code,
            }
            if channel_id:
                payload["channel_id"] = channel_id
            if guild_id:
                payload["guild_id"] = guild_id
            return await self._post("/discord/auth", json=payload)
        except httpx.HTTPStatusError as e:
            detail = e.response.json().get("detail", "Registration failed.")
            raise Exception(detail)
        except Exception as e:
            raise Exception(f"Failed to register: {str(e)}")

    async def get_linked_course(self, discord_user_id: str) -> Optional[Dict[str, Any]]:
        """Get the course linked to a Discord user (DM flow), or None if not linked."""
        try:
            data = await self._get(f"/discord/user/{discord_user_id}/course")
            if data.get("course_bot_id"):
                return data
            return None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Quiz Methods
    # ------------------------------------------------------------------

    async def generate_quiz(
        self,
        user_id: str,
        course_bot_id: str,
        num_questions: int = 5,
        difficulty: str = "medium",
        question_types: List[str] = None,
        learning_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a new quiz for a user."""
        if question_types is None:
            question_types = ["multiple_choice", "true_false"]
        payload = {
            "user_id": user_id,
            "course_bot_id": course_bot_id,
            "num_questions": num_questions,
            "difficulty": difficulty,
            "question_types": question_types,
            "is_practice": True,
            "learning_mode": learning_mode,
        }
        try:
            return await self._post("/generate-quiz", json=payload)
        except Exception as e:
            raise Exception(f"Failed to generate quiz: {str(e)}")

    async def get_quiz(self, quiz_id: str) -> Dict[str, Any]:
        """Fetch a specific quiz by ID."""
        try:
            return await self._get(f"/quizzes/{quiz_id}")
        except Exception as e:
            raise Exception(f"Failed to fetch quiz: {str(e)}")

    async def submit_quiz(
        self,
        user_id: str,
        quiz_id: str,
        answers: Dict[str, Any],
        time_spent_seconds: Optional[int] = None,
        learning_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit quiz answers for grading."""
        payload = {
            "user_id": user_id,
            "quiz_id": quiz_id,
            "answers": answers,
            "time_spent_seconds": time_spent_seconds,
            "learning_mode_used": learning_mode,
        }
        try:
            return await self._post("/submit-quiz", json=payload)
        except Exception as e:
            raise Exception(f"Failed to submit quiz: {str(e)}")

    async def get_quiz_suggestions(
        self, user_id: str, course_bot_id: str
    ) -> List[Dict[str, Any]]:
        """Get adaptive quiz suggestions for a user."""
        try:
            return await self._get(f"/quiz-suggestions/{user_id}/{course_bot_id}")
        except Exception as e:
            raise Exception(f"Failed to fetch quiz suggestions: {str(e)}")

    async def get_topic_mastery(
        self, user_id: str, course_bot_id: str
    ) -> Dict[str, Any]:
        """Get topic mastery records for a user."""
        try:
            return await self._get(f"/topic-mastery/{user_id}/{course_bot_id}")
        except Exception as e:
            raise Exception(f"Failed to fetch topic mastery: {str(e)}")
