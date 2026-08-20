"""
Discord bot for getCRAG-ed - Main entry point.
Channel-first setup: each server channel is linked to one course via /auth.
DMs use per-user links. Students must /auth and be approved before asking.
"""
import discord
from discord import app_commands
from discord.ext import commands
import hashlib
import hmac
import logging
from typing import Optional, Tuple

import config
from session_manager import ConversationSession
from api_client import APIClient
from session_manager import SessionManager
from quiz_views import QuizOrchestrator

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------
class CRAGBot(commands.Bot):
    """Syncs slash commands on startup."""

    async def setup_hook(self) -> None:
        try:
            synced = await self.tree.sync()
            logger.info(
                "Synced %d global command(s): %s",
                len(synced),
                ", ".join(command.name for command in synced),
            )
            if config.DISCORD_GUILD_ID:
                guild = discord.Object(id=config.DISCORD_GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                guild_synced = await self.tree.sync(guild=guild)
                logger.info(
                    "Synced %d command(s) to guild %s: %s",
                    len(guild_synced),
                    config.DISCORD_GUILD_ID,
                    ", ".join(command.name for command in guild_synced),
                )
        except Exception as e:
            logger.exception("Failed to sync commands: %s", e)


intents = discord.Intents.default()
intents.message_content = True
bot = CRAGBot(
    command_prefix="!",
    intents=intents,
    allowed_contexts=app_commands.AppCommandContext(
        guild=True,
        dm_channel=True,
        private_channel=True,
    ),
    allowed_installs=app_commands.AppInstallationType(
        guild=True,
        user=True,
    ),
)

api_client = APIClient()
session_manager = SessionManager()

course_bots_cache = None


def _discord_surface(channel) -> Tuple[str, Optional[str], Optional[str]]:
    """Return research surface, parent channel ID and thread ID."""
    if isinstance(channel, discord.DMChannel):
        return "dm", str(channel.id), None
    if isinstance(channel, discord.Thread):
        surface = (
            "private_thread"
            if channel.type == discord.ChannelType.private_thread
            else "public_thread"
        )
        return surface, str(channel.parent_id), str(channel.id)
    return "guild_channel", str(channel.id), None


def _research_session_digest(
    user_id: str, channel_id: str, root_interaction_id: str
) -> str:
    raw_key = f"discord:{user_id}:{channel_id}:{root_interaction_id}"
    return hmac.new(
        config.DISCORD_BACKEND_SHARED_SECRET.encode("utf-8"),
        raw_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


async def _capture_student_turn(
    *,
    session: ConversationSession,
    question: str,
    course_bot_id: str,
    user_id: str,
    channel,
    interaction_id: str,
    occurred_at,
    interaction_surface: str,
    started_by: Optional[str] = None,
    discord_message_id: Optional[str] = None,
    target_discord_message_id: Optional[str] = None,
    parent_discord_message_id: Optional[str] = None,
    parent_message_id: Optional[str] = None,
) -> Optional[str]:
    """Capture canonical student message and Discord interaction before RAG."""
    if not config.RESEARCH_CAPTURE_ENABLED:
        return None

    surface, research_channel_id, thread_id = _discord_surface(channel)
    occurred_at_iso = occurred_at.isoformat()

    if not session.research_conversation_id:
        start = await api_client.start_research_conversation(
            {
                "course_bot_id": course_bot_id,
                "session_key_digest": _research_session_digest(
                    user_id, str(channel.id), interaction_id
                ),
                "surface": surface,
                "started_by": started_by or "mention",
                "activity_type": "natural",
                "crag_mode": session.learning_mode,
                "start_interaction_id": interaction_id,
                "guild_id": str(channel.guild.id) if getattr(channel, "guild", None) else None,
                "channel_id": research_channel_id,
                "thread_id": thread_id,
                "anchor_message_id": discord_message_id or interaction_id,
                "occurred_at": occurred_at_iso,
            }
        )
        session.research_conversation_id = start["conversation_id"]

    client_message_id = (
        f"discord:message:{discord_message_id}"
        if discord_message_id
        else f"discord:interaction:{interaction_id}"
    )
    message_result = await api_client.append_research_message(
        {
            "conversation_id": session.research_conversation_id,
            "sender": "student",
            "message_text": question,
            "client_message_id": client_message_id,
            "parent_message_id": parent_message_id,
            "sent_at": occurred_at_iso,
        }
    )

    interaction_payload = {
        "interaction_id": interaction_id,
        "conversation_id": session.research_conversation_id,
        "interaction_surface": interaction_surface,
        "action_origin": "free_text",
        "elicitation_condition": "spontaneous",
        "message_id": message_result["message_id"],
        "discord_message_id": discord_message_id,
        "target_discord_message_id": target_discord_message_id,
        "parent_discord_message_id": parent_discord_message_id,
        "command_name": "ask" if interaction_surface == "slash_command" else None,
        "crag_mode": session.learning_mode,
        "occurred_at": occurred_at_iso,
        "sanitised_payload": {"content_length": len(question)},
    }
    await api_client.record_research_interaction(interaction_payload)
    return message_result["message_id"]


async def _capture_crag_turn(
    *,
    session: ConversationSession,
    sender: str,
    content: str,
    discord_message,
    parent_message_id: Optional[str],
) -> Optional[str]:
    if not config.RESEARCH_CAPTURE_ENABLED or not session.research_conversation_id:
        return None
    result = await api_client.append_research_message(
        {
            "conversation_id": session.research_conversation_id,
            "sender": sender,
            "message_text": content,
            "client_message_id": f"discord:message:{discord_message.id}",
            "parent_message_id": parent_message_id,
            "backend_chat_session_id": session.session_id,
            "sent_at": discord_message.created_at.isoformat(),
        }
    )
    return result["message_id"]


async def get_course_name(course_bot_id: str) -> str:
    global course_bots_cache
    if course_bots_cache is None:
        try:
            course_bots_cache = await api_client.get_course_bots()
        except Exception as e:
            logger.error(f"Failed to fetch course bots: {e}")
            return "Unknown Course"
    for bot_data in course_bots_cache:
        if bot_data.get("id") == course_bot_id:
            return bot_data.get("name", "Unknown Course")
    return "Unknown Course"


# ---------------------------------------------------------------------------
# Auth / access messages
# ---------------------------------------------------------------------------
_NOT_REGISTERED_MSG = (
    "❌ You are not registered for a course yet.\n\n"
    "Run `/auth name:<your name> student_id:<id> code:<join code>` with the code from your tutor.\n"
    "Once approved, you can use `/ask` straight away."
)

_PENDING_MSG = (
    "⏳ Your registration is pending tutor approval.\n"
    "You'll be able to ask questions once approved."
)

_REJECTED_MSG = (
    "❌ Your registration was not approved. Please contact your tutor."
)

_LINK_STATUS_MESSAGES = {
    "linked": "✅ LMS account linked to your Discord account.",
    "already_linked": "✅ This LMS account is already linked to your Discord account.",
    "invalid": "❌ Invalid link code. Generate a new code from the LMS and try again.",
    "used": "❌ This link code has already been used. Generate a new code from the LMS.",
    "expired": "❌ This link code expired. Generate a new code from the LMS.",
    "locked": "❌ This link code is locked after too many attempts. Generate a new code.",
    "rate_limited": "⏳ Too many attempts. Wait five minutes, then try again.",
    "discord_already_linked": (
        "❌ Your Discord account is already linked to a different LMS account. "
        "Unlink it from that LMS account first."
    ),
    "lms_already_linked": (
        "❌ This LMS account is already linked to a different Discord account. "
        "Unlink it from the LMS first."
    ),
}

_RECOVERY_STATUS_MESSAGES = {
    "not_linked": (
        "❌ Discord is not linked to an LMS account yet. Open LMS Account settings, "
        "generate a link code, then run `/link`."
    ),
    "rate_limited": "⏳ Too many recovery codes requested. Wait 15 minutes and retry.",
}


# ---------------------------------------------------------------------------
# Course resolution helpers
# ---------------------------------------------------------------------------
async def _resolve_user_course(
    user_id: str,
    session: ConversationSession,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve course for a DM user. Checks backend link, env mapping, then session."""
    try:
        linked = await api_client.get_linked_course(user_id)
        if linked and linked.get("course_bot_id"):
            return linked["course_bot_id"], linked.get("course_name", "Unknown"), None
    except Exception:
        pass

    course_bot_id = config.USER_COURSE_MAPPING.get(str(user_id))
    if course_bot_id:
        name = await get_course_name(course_bot_id)
        return course_bot_id, name, None

    if session.course_bot_id:
        name = session.course_bot_name or await get_course_name(session.course_bot_id)
        return session.course_bot_id, name, None

    return None, None, None


async def get_course_for_context(
    user_id: str,
    channel_id: str,
    is_dm: bool,
    session: ConversationSession,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (course_bot_id, course_name, error_msg). error_msg is set when access is denied."""
    if not is_dm:
        try:
            access = await api_client.get_channel_access(channel_id, user_id)
            reason = access.get("reason")

            if reason == "approved":
                return access.get("course_bot_id"), access.get("course_name"), None
            if reason == "pending":
                return None, None, _PENDING_MSG
            if reason == "rejected":
                return None, None, _REJECTED_MSG
        except Exception as e:
            logger.error(f"Error checking channel access: {e}")

        return None, None, _NOT_REGISTERED_MSG

    course_bot_id, course_name, _ = await _resolve_user_course(user_id, session)
    if course_bot_id:
        return course_bot_id, course_name, None

    return None, None, _NOT_REGISTERED_MSG


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    logger.info(f"Bot logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"Connected to {len(bot.guilds)} guild(s)")
    if not config.RESEARCH_CAPTURE_ENABLED:
        logger.error(
            "Discord research capture disabled: DISCORD_BACKEND_SHARED_SECRET "
            "must be configured with at least 32 characters"
        )


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    user_id = str(message.author.id)
    channel_id = str(message.channel.id)
    is_dm = isinstance(message.channel, discord.DMChannel)
    session_channel_id = None if is_dm else channel_id

    replied_session = None
    replied_discord_message_id = None
    parent_research_message_id = None
    if message.reference and message.reference.message_id:
        replied_discord_message_id = str(message.reference.message_id)
        replied_session = session_manager.get_session_for_response(
            replied_discord_message_id, user_id
        )
        parent_research_message_id = session_manager.get_parent_message_for_response(
            replied_discord_message_id
        )

    is_mention = bot.user in message.mentions
    if not is_mention and replied_session is None:
        return

    question = (
        message.content
        .replace(f"<@{bot.user.id}>", "")
        .replace(f"<@!{bot.user.id}>", "")
        .strip()
    )
    if not question:
        await message.reply("Hi! Ask me a question about your course materials! 📚")
        return

    context_session = replied_session or session_manager.get_session(
        user_id, session_channel_id
    )

    course_bot_id, course_bot_name, error_msg = await get_course_for_context(
        user_id, channel_id, is_dm, context_session
    )

    if not course_bot_id:
        await message.reply(error_msg or _NOT_REGISTERED_MSG)
        return

    context_session.set_course_bot(course_bot_id, course_bot_name, channel_id)
    session = replied_session or session_manager.start_conversation(
        user_id, session_channel_id
    )
    session.set_course_bot(course_bot_id, course_bot_name, channel_id)
    if replied_session:
        session.record_follow_up()
    session.add_message("user", question)
    user_research_message_id = None

    async with message.channel.typing():
        try:
            user_research_message_id = await _capture_student_turn(
                session=session,
                question=question,
                course_bot_id=course_bot_id,
                user_id=user_id,
                channel=message.channel,
                interaction_id=str(message.id),
                occurred_at=message.created_at,
                interaction_surface="message_reply" if replied_session else "mention",
                started_by=None if replied_session else "mention",
                discord_message_id=str(message.id),
                target_discord_message_id=replied_discord_message_id,
                parent_discord_message_id=replied_discord_message_id,
                parent_message_id=parent_research_message_id,
            )
            response = await api_client.query(
                question=question,
                course_bot_id=course_bot_id,
                user_id=user_id,
                session_id=session.session_id,
                conversation_history=session.conversation_history[:-1],
                learning_mode=session.learning_mode,
                conversation_id=session.research_conversation_id,
            )

            answer = response.get("answer", "No answer provided")
            sources = response.get("sources", [])
            is_verified = response.get("is_verified", False)
            
            if response.get("session_id"):
                session.session_id = response.get("session_id")

            session.add_message("assistant", answer)

            embed = _build_answer_embed(
                course_bot_name, question, answer, sources, is_verified,
                asked_by=message.author.display_name,
                learning_mode=session.learning_mode,
                follow_up_count=session.follow_up_count,
            )
            answer_message = await message.reply(embed=embed)
            assistant_research_message_id = None
            try:
                assistant_research_message_id = await _capture_crag_turn(
                    session=session,
                    sender="crag",
                    content=answer,
                    discord_message=answer_message,
                    parent_message_id=user_research_message_id,
                )
            except Exception as capture_error:
                logger.error("Failed to capture CRAG Discord turn: %s", capture_error)
            session_manager.bind_response(
                str(answer_message.id), session, assistant_research_message_id
            )

        except Exception as e:
            logger.error(f"Error processing question: {e}")
            error_message = await message.reply(f"❌ Error: {str(e)}")
            try:
                await _capture_crag_turn(
                    session=session,
                    sender="error",
                    content="Discord query processing failed",
                    discord_message=error_message,
                    parent_message_id=user_research_message_id,
                )
            except Exception as capture_error:
                logger.error("Failed to capture Discord error turn: %s", capture_error)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------
@bot.tree.command(name="link", description="Link your LMS account using a one-time code")
@app_commands.describe(code="One-time code generated in the LMS (for example CRAG-ABCDE-23456)")
async def link_lms_account(interaction: discord.Interaction, code: str):
    """Persistently link authenticated LMS identity to this Discord account."""
    logger.info(
        "Received /link from Discord user %s in guild %s",
        interaction.user.id,
        interaction.guild_id,
    )
    await interaction.response.defer(ephemeral=True)
    try:
        result = await api_client.consume_lms_link_code(
            code=code,
            discord_user_id=str(interaction.user.id),
            discord_display_name=interaction.user.display_name,
        )
        status_value = result.get("status", "invalid")
        message_text = _LINK_STATUS_MESSAGES.get(
            status_value,
            "❌ LMS account linking returned an unknown result. Generate a new code and retry.",
        )
        await interaction.followup.send(message_text, ephemeral=True)
    except Exception as exc:
        logger.error("LMS-to-Discord linking failed: %s", exc)
        await interaction.followup.send(
            f"❌ Could not link LMS account: {exc}",
            ephemeral=True,
        )


@bot.tree.command(name="generate", description="Generate a one-time LMS account recovery code")
async def generate_lms_recovery_code(interaction: discord.Interaction):
    """Privately prove linked Discord ownership for LMS account recovery."""
    logger.info(
        "Received /generate from Discord user %s in guild %s",
        interaction.user.id,
        interaction.guild_id,
    )
    await interaction.response.defer(ephemeral=True)
    try:
        result = await api_client.generate_lms_recovery_code(
            discord_user_id=str(interaction.user.id),
            discord_display_name=interaction.user.display_name,
        )
        if not result.get("issued"):
            await interaction.followup.send(
                _RECOVERY_STATUS_MESSAGES.get(
                    result.get("status"),
                    "❌ Recovery code could not be generated.",
                ),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            "🔐 **LMS recovery code**\n"
            f"`{result['code']}`\n\n"
            "Enter this in LMS Account settings within 10 minutes. "
            "Code works once and should not be shared.",
            ephemeral=True,
        )
    except Exception as exc:
        logger.error("LMS recovery-code generation failed: %s", exc)
        await interaction.followup.send(
            f"❌ Could not generate recovery code: {exc}",
            ephemeral=True,
        )


@bot.tree.command(name="auth", description="Register for a course with your tutor's join code")
@app_commands.describe(
    name="Your full real name (e.g. John Smith)",
    student_id="Your student ID (e.g. A0123456B)",
    code="The join code from your tutor's share link (e.g. ABC12XYZ)",
)
async def auth_command(interaction: discord.Interaction, name: str, student_id: str, code: str):
    await interaction.response.defer(ephemeral=True)
    try:
        is_dm = isinstance(interaction.channel, discord.DMChannel)
        result = await api_client.register_student(
            discord_user_id=str(interaction.user.id),
            discord_username=interaction.user.display_name,
            real_name=name,
            student_id=student_id,
            join_code=code,
            channel_id=None if is_dm else str(interaction.channel_id),
            guild_id=str(interaction.guild_id) if interaction.guild_id else None,
        )
        course_name = result.get("course_name", "your course")
        await interaction.followup.send(
            f"✅ Registration submitted for **{course_name}**.\n\n"
            f"A tutor will review your details. Once approved, you can use `/ask` straight away — no extra steps needed.",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)


@bot.tree.command(name="ask", description="Ask a question about course materials")
@app_commands.describe(question="Your question about the course material")
async def ask(interaction: discord.Interaction, question: str):
    await interaction.response.defer()
    session = None
    user_research_message_id = None
    try:
        user_id = str(interaction.user.id)
        channel_id = str(interaction.channel_id)
        is_dm = isinstance(interaction.channel, discord.DMChannel)
        session_channel_id = None if is_dm else channel_id
        context_session = session_manager.get_session(user_id, session_channel_id)

        course_bot_id, course_bot_name, error_msg = await get_course_for_context(
            user_id, channel_id, is_dm, context_session
        )

        if not course_bot_id:
            await interaction.followup.send(error_msg or _NOT_REGISTERED_MSG)
            return

        context_session.set_course_bot(course_bot_id, course_bot_name, channel_id)
        session = session_manager.start_conversation(user_id, session_channel_id)
        session.set_course_bot(course_bot_id, course_bot_name, channel_id)
        session.add_message("user", question)

        user_research_message_id = await _capture_student_turn(
            session=session,
            question=question,
            course_bot_id=course_bot_id,
            user_id=user_id,
            channel=interaction.channel,
            interaction_id=str(interaction.id),
            occurred_at=interaction.created_at,
            interaction_surface="slash_command",
            started_by="slash_command",
        )

        response = await api_client.query(
            question=question,
            course_bot_id=course_bot_id,
            user_id=user_id,
            session_id=session.session_id,
            conversation_history=session.conversation_history[:-1],
            learning_mode=session.learning_mode,
            conversation_id=session.research_conversation_id,
        )

        answer = response.get("answer", "No answer provided")
        sources = response.get("sources", [])
        is_verified = response.get("is_verified", False)

        if response.get("session_id"):
            session.session_id = response.get("session_id")

        session.add_message("assistant", answer)

        mode_label = _MODE_LABELS.get(session.learning_mode, "📘 Standard")
        footer = f"Asked by {interaction.user.display_name} • {course_bot_name} • {mode_label}"

        embed = _build_answer_embed(
            course_bot_name, question, answer, sources, is_verified,
            asked_by=interaction.user.display_name,
            footer_override=footer,
            learning_mode=session.learning_mode,
            follow_up_count=session.follow_up_count,
        )

        answer_message = await interaction.followup.send(embed=embed, wait=True)
        assistant_research_message_id = None
        try:
            assistant_research_message_id = await _capture_crag_turn(
                session=session,
                sender="crag",
                content=answer,
                discord_message=answer_message,
                parent_message_id=user_research_message_id,
            )
        except Exception as capture_error:
            logger.error("Failed to capture CRAG Discord turn: %s", capture_error)
        session_manager.bind_response(
            str(answer_message.id), session, assistant_research_message_id
        )

    except Exception as e:
        logger.error(f"Error processing /ask: {e}")
        error_message = await interaction.followup.send(
            f"❌ Error: {str(e)}", wait=True
        )
        if session:
            try:
                await _capture_crag_turn(
                    session=session,
                    sender="error",
                    content="Discord query processing failed",
                    discord_message=error_message,
                    parent_message_id=user_research_message_id,
                )
            except Exception as capture_error:
                logger.error("Failed to capture Discord error turn: %s", capture_error)


_MODE_LABELS = {"standard": "📘 Standard", "guided": "📗 Guided", "socratic": "📙 Socratic"}

@bot.tree.command(name="mode", description="Switch your learning mode")
@app_commands.describe(mode="Choose a learning mode")
@app_commands.choices(mode=[
    app_commands.Choice(name="📘 Standard — clear, direct answers", value="standard"),
    app_commands.Choice(name="📗 Guided — step-by-step explanations", value="guided"),
    app_commands.Choice(name="📙 Socratic — guided discovery through questions", value="socratic"),
])
async def mode_command(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    user_id = str(interaction.user.id)
    channel_id = str(interaction.channel_id)
    is_dm = isinstance(interaction.channel, discord.DMChannel)
    session = session_manager.get_session(user_id, None if is_dm else channel_id)

    course_bot_id, _, error_msg = await get_course_for_context(user_id, channel_id, is_dm, session)
    if not course_bot_id:
        await interaction.response.send_message(error_msg or _NOT_REGISTERED_MSG, ephemeral=True)
        return

    session.learning_mode = mode.value
    await interaction.response.send_message(
        f"✅ Learning mode switched to **{_MODE_LABELS[mode.value]}**\n"
        f"All your future answers will use this mode.",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# /quiz
# ---------------------------------------------------------------------------

@bot.tree.command(name="quiz", description="Start a practice quiz based on your course material")
@app_commands.describe(
    questions="Number of questions (1–20, default 5)",
    difficulty="Quiz difficulty",
    type="Question type",
)
@app_commands.choices(
    difficulty=[
        app_commands.Choice(name="🟢 Easy", value="easy"),
        app_commands.Choice(name="🟡 Medium", value="medium"),
        app_commands.Choice(name="🔴 Hard", value="hard"),
    ],
    type=[
        app_commands.Choice(name="🔤 Multiple choice", value="multiple_choice"),
        app_commands.Choice(name="✅ True / False", value="true_false"),
        app_commands.Choice(name="✍️ Short answer", value="short_answer"),
        app_commands.Choice(name="📝 Fill in the blank", value="fill_blank"),
        app_commands.Choice(name="🧠 Application", value="application"),
        app_commands.Choice(name="🎲 Mixed (MCQ + T/F)", value="mixed"),
    ],
)
async def quiz_command(
    interaction: discord.Interaction,
    difficulty: app_commands.Choice[str] = None,
    questions: app_commands.Range[int, 1, 20] = 5,
    type: app_commands.Choice[str] = None,
):
    try:
        await interaction.response.defer()
    except discord.HTTPException:
        # Interaction already acknowledged or expired — ignore stale duplicates
        return

    user_id = str(interaction.user.id)
    channel_id = str(interaction.channel_id)
    is_dm = isinstance(interaction.channel, discord.DMChannel)
    session = session_manager.get_session(user_id, None if is_dm else channel_id)

    course_bot_id, course_bot_name, error_msg = await get_course_for_context(
        user_id, channel_id, is_dm, session
    )
    if not course_bot_id:
        await interaction.followup.send(error_msg or _NOT_REGISTERED_MSG)
        return

    # Acknowledge only after auth passes — the orchestrator will edit this message
    # in-place once quiz generation completes
    ack_message = await interaction.followup.send("⏳ Generating your quiz, please wait…", wait=True)

    diff = difficulty.value if difficulty else "medium"
    q_type = type.value if type else "mixed"
    question_types = (
        ["multiple_choice", "true_false"] if q_type == "mixed" else [q_type]
    )

    try:
        orchestrator = QuizOrchestrator(
            interaction=interaction,
            api_client=api_client,
            user_id=user_id,
            course_bot_id=str(course_bot_id),
            learning_mode=session.learning_mode,
            ack_message=ack_message,
        )
        await orchestrator.start(
            num_questions=questions,
            difficulty=diff,
            question_types=question_types,
        )
    except Exception as e:
        logger.error(f"Error starting quiz: {e}")

        await interaction.followup.send(f"❌ Failed to generate quiz: {str(e)}")


# ---------------------------------------------------------------------------
# /quiz-suggest
# ---------------------------------------------------------------------------


@bot.tree.command(name="help", description="Show how to use the bot")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="getCRAG-ed Bot Help",
        description=(
            "Your course-grounded study assistant. I answer from materials provided for "
            "your course. Register and get tutor approval before asking."
        ),
        color=discord.Color.green(),
    )

    embed.add_field(
        name="Get access first",
        value=(
            "1. Get your tutor's join code.\n"
            "2. Run `/auth name:<your name> student_id:<id> code:<join code>`.\n"
            "3. Wait for tutor approval, then use `/ask`.\n\n"
            "No access? Check your join code, approval status, and channel with your tutor."
        ),
        inline=False,
    )

    embed.add_field(
        name="Start a new question",
        value=(
            "`/ask question:<your question>`\n"
            "or mention me: `@CRAG Explain recursion with an example`\n\n"
            "Use specific questions. Include topic, what you have tried, and where you are stuck "
            "for best help. Each `/ask` or new mention begins a fresh conversation."
        ),
        inline=False,
    )

    embed.add_field(
        name="Continue a conversation",
        value=(
            "Reply directly to one of my answers using Discord's **Reply** action. Write your "
            "follow-up normally — no mention and no `/ask` needed.\n\n"
            "Reply to latest answer to keep context, e.g. `Can you show another example?` "
            "or `Why does step 2 work?` Only you can continue your reply chain. After a long "
            "pause or bot restart, start again with `/ask`."
        ),
        inline=False,
    )

    embed.add_field(
        name="Learning mode and quizzes",
        value=(
            "`/mode` — choose **Standard** (direct), **Guided** (step-by-step), or "
            "**Socratic** (learn through questions).\n\n"
            "`/quiz` — create an interactive practice quiz. Set 1–20 questions, difficulty, "
            "and type; then select **Start Quiz** and answer each prompt for results."
        ),
        inline=False,
    )

    embed.add_field(
        name="LMS account link and recovery",
        value=(
            "Already use LMS? Generate its one-use link code in LMS Account settings, then run "
            "`/link code:<CRAG-XXXXX-XXXXX>`.\n\n"
            "Lost LMS browser data? Run `/generate` for a private one-use recovery code. Enter "
            "it in LMS Account settings within 10 minutes; do not share it."
        ),
        inline=False,
    )

    embed.set_footer(text="Commands: /auth • /ask • /mode • /quiz • /link • /generate • /help")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# Tree-level error handler
# ---------------------------------------------------------------------------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logger.error("Command error (%s): %s", interaction.command, error)
    msg = "❌ Something went wrong. Please try again."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Shared embed builder
# ---------------------------------------------------------------------------
def _build_answer_embed(
    course_name: str,
    question: str,
    answer: str,
    sources: list,
    is_verified: bool,
    *,
    asked_by: str = "",
    footer_override: Optional[str] = None,
    learning_mode: str = "standard",
    follow_up_count: int = 0,
) -> discord.Embed:
    title = f"💬 {course_name}"

    question_display = question if len(question) <= 256 else question[:253] + "..."

    mode_colors = {
        "standard": discord.Color.blue(),
        "guided": discord.Color.green(),
        "socratic": discord.Color.gold(),
    }
    color = discord.Color.green() if is_verified else mode_colors.get(learning_mode, discord.Color.blue())

    embed = discord.Embed(
        title=title,
        color=color,
    )
    embed.add_field(name="❓ Question", value=question_display, inline=False)
    embed.add_field(
        name="💡 Answer",
        value=answer[:1024] if len(answer) <= 1024 else answer[:1021] + "...",
        inline=False,
    )

    if len(answer) > 1024:
        remaining = answer[1024:]
        while remaining:
            chunk = remaining[:1024]
            remaining = remaining[1024:]
            embed.add_field(name="\u200b", value=chunk, inline=False)

    if is_verified:
        embed.add_field(name="\u200b", value="✅ Verified Answer", inline=False)
    else:
        if sources:
            sources_text = ""
            for i, source in enumerate(sources[:3], 1):
                source_name = source.get("source", "Unknown")
                page = source.get("page_number", "?")
                similarity = source.get("similarity", 0)
                sources_text += f"**{i}.** {source_name} (Page {page}) - {similarity:.2%} match\n"
            embed.add_field(name="📖 Sources", value=sources_text, inline=False)
        embed.add_field(name="\u200b", value="⚠️ Unverified Answer", inline=False)

    mode_label = _MODE_LABELS.get(learning_mode, "📘 Standard")
    default_footer = f"Asked by {asked_by} • {course_name} • {mode_label}"
    conversation_label = (
        f"Follow-up {follow_up_count}" if follow_up_count else "Reply to continue"
    )
    embed.set_footer(
        text=f"{footer_override or default_footer} • {conversation_label}"
    )
    return embed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting bot...")
    bot.run(config.DISCORD_BOT_TOKEN)
